import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.agent import BookRecallAgent
from bookrecall.chunking import build_chunk_hierarchy
from bookrecall.config import DEFAULT_CHUNK_SETTINGS
from bookrecall.entity_index import build_entity_records
from bookrecall.models import MemoryCard, SearchHit
from bookrecall.parser import parse_chapters
from bookrecall.storage import BookRecallStore

SAMPLE_TEXT = """第1章 起点

林澈在旧书里看到【星辰之匙】的名字。

第2章 阴影

黑衣人在雨里出现。

第3章 回声

黑衣人再次提到【星辰之匙】。
"""


class BookRecallAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "bookrecall.db")
        self.store = BookRecallStore(self.db_path)
        self.store.initialize()

        chapters = parse_chapters(SAMPLE_TEXT)
        parents, children = build_chunk_hierarchy("sample", chapters, DEFAULT_CHUNK_SETTINGS)
        entity_records = build_entity_records(
            chapters,
            {"星辰之匙": ["钥匙"], "黑衣人": ["黑袍人"], "林澈": []},
            DEFAULT_CHUNK_SETTINGS,
        )
        self.store.replace_book(
            book_id="sample",
            title="测试书",
            source_path="memory",
            chapters=chapters,
            parent_chunks=parents,
            child_chunks=children,
            entity_records=entity_records,
        )
        self.agent = BookRecallAgent(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_first_appearance(self) -> None:
        answer = self.agent.ask(
            book_id="sample",
            question="星辰之匙第一次出现在哪一章？",
            progress_chapter=3,
        )
        self.assertIn("第 1 章", answer)

    def test_spoiler_protection(self) -> None:
        card = self.agent.ask_card(
            book_id="sample",
            question="黑衣人第一次出现在哪一章？",
            progress_chapter=1,
        )
        self.assertTrue(card.spoiler_blocked)
        self.assertIn("还没有出现", card.answer)

    def test_timeline(self) -> None:
        answer = self.agent.ask(
            book_id="sample",
            question="黑衣人后来还有出现过吗？",
            progress_chapter=3,
        )
        self.assertIn("第 2 章", answer)
        self.assertIn("第 3 章", answer)

    def test_alias_resolution(self) -> None:
        card = self.agent.ask_card(
            book_id="sample",
            question="黑袍人第一次出现在哪一章？",
            progress_chapter=3,
        )
        self.assertIsInstance(card, MemoryCard)
        self.assertEqual(card.entity_name, "黑衣人")
        self.assertIn("第 2 章", card.answer)

    def test_json_render(self) -> None:
        card = self.agent.ask_card(
            book_id="sample",
            question="星辰之匙第一次出现在哪一章？",
            progress_chapter=3,
        )
        payload = self.agent.render_json(card)
        self.assertIn('"intent"', payload)
        self.assertIn('"evidence"', payload)

    def test_session_memory_reuses_recent_entity(self) -> None:
        first = self.agent.ask_card(
            book_id="sample",
            question="黑袍人第一次出现在哪一章？",
            progress_chapter=3,
            session_id="s1",
        )
        second = self.agent.ask_card(
            book_id="sample",
            question="后来还有出现过吗？",
            progress_chapter=3,
            session_id="s1",
        )
        turns = self.store.list_agent_turns("sample", "default", "s1", limit=10)

        self.assertEqual(first.entity_name, "黑衣人")
        self.assertEqual(second.entity_name, "黑衣人")
        self.assertIn("第 2 章", second.answer)
        self.assertIn("第 3 章", second.answer)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[-1]["entity_name"], "黑衣人")

    def test_local_query_understanding_can_override_rule_intent(self) -> None:
        class FakeQueryClient:
            def complete_json(self, prompt: str) -> dict:
                return {
                    "intent": "entity_timeline",
                    "entities": ["黑衣人"],
                    "themes": [],
                    "time_range": {"start_chapter": None, "end_chapter": None, "relative": "after"},
                    "spoiler_sensitive": True,
                    "tools": ["lookup_entity_aliases", "lookup_timeline"],
                    "confidence": 0.9,
                }

        agent = BookRecallAgent(self.store, query_understanding_client=FakeQueryClient())

        card = agent.ask_card(
            book_id="sample",
            question="黑衣人呢？",
            progress_chapter=3,
        )

        self.assertEqual(card.query_understanding["source"], "local_llm")
        self.assertEqual(card.query_understanding["intent"], "entity_timeline")
        self.assertEqual(card.entity_name, "黑衣人")
        self.assertIn("第 2 章", card.answer)
        self.assertIn("第 3 章", card.answer)

    def test_local_rerank_reorders_search_evidence(self) -> None:
        class FakeRetriever:
            def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
                return [
                    SearchHit(
                        score=2.0,
                        chapter_number=1,
                        chapter_title="起点",
                        parent_id="p1",
                        child_text="旧书安静地放在桌上。",
                        parent_text="旧书安静地放在桌上。",
                    ),
                    SearchHit(
                        score=0.4,
                        chapter_number=3,
                        chapter_title="回声",
                        parent_id="p3",
                        child_text="黑衣人再次提到【星辰之匙】。",
                        parent_text="黑衣人再次提到【星辰之匙】。",
                    ),
                ]

        class FakeLocalClient:
            def complete_json(self, prompt: str) -> dict:
                if "证据重排器" in prompt:
                    return {"ranked_hits": [{"index": 2, "relevance": 0.97, "reason": "直接命中星辰之匙"}]}
                return {
                    "intent": "semantic_search",
                    "entities": [],
                    "themes": [],
                    "time_range": {"start_chapter": None, "end_chapter": None, "relative": ""},
                    "spoiler_sensitive": True,
                    "tools": ["search_evidence"],
                    "confidence": 0.7,
                }

        agent = BookRecallAgent(
            self.store,
            retriever=FakeRetriever(),
            query_understanding_client=FakeLocalClient(),
        )

        card = agent.ask_card(
            book_id="sample",
            question="那把钥匙有什么线索？",
            progress_chapter=3,
        )

        self.assertEqual(card.evidence[0].chapter_number, 3)
        self.assertIn("星辰之匙", card.evidence[0].excerpt)

    def test_local_answer_validation_adds_guardrail_note(self) -> None:
        class FakeLocalClient:
            def complete_json(self, prompt: str) -> dict:
                if "答案校验器" in prompt:
                    return {
                        "supported": False,
                        "spoiler_safe": True,
                        "speculation_risk": "high",
                        "issues": ["答案超过证据能证明的范围"],
                        "suggested_note": "这条回答的证据支撑偏弱，请以下方原文为准。",
                        "confidence": 0.9,
                    }
                return {
                    "intent": "first_appearance",
                    "entities": ["星辰之匙"],
                    "themes": [],
                    "time_range": {"start_chapter": None, "end_chapter": None, "relative": "before"},
                    "spoiler_sensitive": True,
                    "tools": ["lookup_entity_aliases", "lookup_first_appearance"],
                    "confidence": 0.8,
                }

        agent = BookRecallAgent(self.store, query_understanding_client=FakeLocalClient())

        card = agent.ask_card(
            book_id="sample",
            question="星辰之匙第一次出现在哪一章？",
            progress_chapter=3,
        )

        self.assertFalse(card.answer_validation["supported"])
        self.assertEqual(card.answer_validation["speculation_risk"], "high")
        self.assertIn("证据支撑偏弱", card.suggestions[-1])

    def test_two_phase_dynamic_index_writes_back_retrieved_events(self) -> None:
        class FakeRetriever:
            def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
                return [
                    SearchHit(
                        score=1.0,
                        chapter_number=3,
                        chapter_title="回声",
                        parent_id="p3",
                        child_text="李四被王五刺中后倒在雨里，林澈目睹了这一切。",
                        parent_text="李四被王五刺中后倒在雨里，林澈目睹了这一切。",
                    )
                ]

        class FakeLocalClient:
            def complete_json(self, prompt: str) -> dict:
                if "按需结构化索引器" in prompt:
                    return {
                        "entities": [
                            {"name": "李四", "aliases": [], "evidence": "李四被王五刺中后倒在雨里", "confidence": 0.9},
                            {"name": "王五", "aliases": [], "evidence": "李四被王五刺中后倒在雨里", "confidence": 0.9},
                        ],
                        "relations": [
                            {
                                "source": "王五",
                                "target": "李四",
                                "type": "冲突",
                                "evidence": "李四被王五刺中后倒在雨里",
                                "confidence": 0.9,
                            }
                        ],
                        "events": [
                            {
                                "type": "冲突/危机",
                                "summary": "王五刺伤李四",
                                "evidence": "李四被王五刺中后倒在雨里",
                                "entities": ["王五", "李四"],
                                "confidence": 0.9,
                            }
                        ],
                    }
                if "答案校验器" in prompt:
                    return {
                        "supported": True,
                        "spoiler_safe": True,
                        "speculation_risk": "low",
                        "issues": [],
                        "suggested_note": "",
                        "confidence": 0.9,
                    }
                return {
                    "intent": "semantic_search",
                    "entities": ["李四"],
                    "themes": [],
                    "time_range": {"start_chapter": None, "end_chapter": None, "relative": ""},
                    "spoiler_sensitive": True,
                    "tools": ["search_evidence"],
                    "confidence": 0.9,
                }

        agent = BookRecallAgent(
            self.store,
            retriever=FakeRetriever(),
            query_understanding_client=FakeLocalClient(),
        )

        agent.ask_card(book_id="sample", question="李四是怎么死的？", progress_chapter=3)
        events = self.store.search_events("sample", query_text="王五刺伤李四", entity_name="李四", max_chapter=3)
        relations = self.store.get_relation_mentions("sample", "王五", "李四", max_chapter=3)

        self.assertIn("李四", self.store.list_entities("sample"))
        self.assertTrue(events)
        self.assertTrue(relations)

    def test_structured_condition_answer_uses_parent_text(self) -> None:
        class FakeRetriever:
            def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
                parent = (
                    "陆畏因道：“要成就尊者，须得满足四个条件。”\n"
                    "“第一，蛊仙的仙窍本源产出白荔仙元。”\n"
                    "“第二，蛊仙主修流派的道痕，至少有三十万规模。”\n"
                    "“第三，蛊仙的主修流派的境界，必须是无上大宗师。”\n"
                    "“第四，蛊仙拥有前三项条件后，须得突破天道封锁。”"
                )
                return [
                    SearchHit(
                        score=1.0,
                        chapter_number=2099,
                        chapter_title="成尊的四个条件",
                        parent_id="p2099",
                        child_text="“第一，蛊仙的仙窍本源产出白荔仙元。”",
                        parent_text=parent,
                    )
                ]

        agent = BookRecallAgent(self.store, retriever=FakeRetriever())
        card = agent.ask_card(book_id="sample", question="成为尊者条件是什么", progress_chapter=3000)

        self.assertIn("白荔仙元", card.answer)
        self.assertIn("三十万规模", card.answer)
        self.assertIn("无上大宗师", card.answer)
        self.assertIn("天道封锁", card.answer)


if __name__ == "__main__":
    unittest.main()
