import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.agent import BookRecallAgent
from bookrecall.agent.state import AgentState
from bookrecall.agent.tools import build_default_registry
from bookrecall.chunking import build_chunk_hierarchy
from bookrecall.config import DEFAULT_CHUNK_SETTINGS, DEFAULT_SEARCH_SETTINGS
from bookrecall.entity_index import build_entity_records, build_relation_records
from bookrecall.parser import parse_chapters
from bookrecall.retrieval import LocalRetriever
from bookrecall.storage import BookRecallStore


SAMPLE_TEXT = """第1章 起点

林澈在旧书里看到【星辰之匙】的名字。

第2章 阴影

黑衣人在雨里出现，林澈与黑衣人对峙。

第3章 回声

黑衣人再次提到【星辰之匙】，林澈决定追查。

第4章 同行

林澈和黑衣人一起穿过旧城，黑衣人帮助林澈避开追兵。
"""


class RelationLayerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "bookrecall.db")
        self.store = BookRecallStore(self.db_path)
        self.store.initialize()

        self.chapters = parse_chapters(SAMPLE_TEXT)
        parents, children = build_chunk_hierarchy("sample", self.chapters, DEFAULT_CHUNK_SETTINGS)
        self.entity_records = build_entity_records(
            self.chapters,
            {"林澈": [], "黑衣人": ["黑袍人"], "星辰之匙": ["钥匙"]},
            DEFAULT_CHUNK_SETTINGS,
        )
        self.relation_records = build_relation_records(
            self.chapters,
            self.entity_records,
            DEFAULT_CHUNK_SETTINGS,
        )
        self.store.replace_book(
            book_id="sample",
            title="关系测试书",
            source_path="memory",
            chapters=self.chapters,
            parent_chunks=parents,
            child_chunks=children,
            entity_records=self.entity_records,
            relation_records=self.relation_records,
        )
        self.agent = BookRecallAgent(self.store)
        self.registry = build_default_registry(
            self.store,
            LocalRetriever(self.store, DEFAULT_SEARCH_SETTINGS),
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_build_relation_records_from_chapter_cooccurrence(self) -> None:
        pairs = {
            (record.source_entity, record.target_entity, record.relation_type)
            for record in self.relation_records
        }

        self.assertIn(("林澈", "黑衣人", "冲突"), pairs)

    def test_store_get_relation_mentions_resolves_aliases_and_progress(self) -> None:
        visible_rows = self.store.get_relation_mentions(
            "sample",
            "林澈",
            "黑袍人",
            max_chapter=4,
        )
        blocked_rows = self.store.get_relation_mentions(
            "sample",
            "林澈",
            "黑衣人",
            max_chapter=1,
        )

        self.assertGreaterEqual(len(visible_rows), 1)
        self.assertEqual(visible_rows[0]["relation_type"], "冲突")
        self.assertIn("对峙", visible_rows[0]["excerpt"])
        self.assertEqual(blocked_rows, [])

    def test_lookup_relations_tool_returns_fragments(self) -> None:
        state = AgentState(
            book_id="sample",
            question="林澈和黑衣人是什么关系？",
            progress_chapter=4,
            matched_entities=["林澈", "黑衣人"],
            primary_entity="林澈",
        )
        tool = self.registry.get("lookup_relations")
        self.assertIsNotNone(tool)

        result = tool.run(state, {"source_entity": "林澈", "target_entity": "黑衣人"})

        self.assertTrue(result["found"])
        self.assertEqual(result["relations"][0]["relation_type"], "冲突")
        self.assertGreaterEqual(len(result["relations"][0]["fragments"]), 1)
        self.assertIn("stages", result["relations"][0])
        self.assertIn("evolution_summary", result["relations"][0])
        self.assertGreaterEqual(len(result["relations"][0]["stages"]), 1)

    def test_agent_answers_relation_question_with_evidence_and_trace(self) -> None:
        card = self.agent.ask_card(
            book_id="sample",
            question="林澈和黑衣人是什么关系？",
            progress_chapter=4,
            session_id="relation-session",
        )
        turns = self.store.list_agent_turns("sample", "default", "relation-session", limit=10)
        trace = turns[-1]["trace"]

        self.assertEqual(card.intent, "人物关系回忆")
        self.assertIn("林澈", card.answer)
        self.assertIn("黑衣人", card.answer)
        self.assertIn("冲突", card.answer)
        self.assertIn("关系阶段", card.answer)
        self.assertIn("总体变化", card.answer)
        self.assertGreaterEqual(len(card.evidence), 1)
        self.assertIn("对峙", card.evidence[0].excerpt)
        self.assertTrue(any(item["tool_name"] == "lookup_relations" for item in trace))

    def test_agent_lists_related_entities_for_single_entity_relation_question(self) -> None:
        card = self.agent.ask_card(
            book_id="sample",
            question="黑衣人还和谁有关？",
            progress_chapter=4,
            session_id="relation-list-session",
        )
        turns = self.store.list_agent_turns("sample", "default", "relation-list-session", limit=10)
        trace = turns[-1]["trace"]

        self.assertEqual(card.intent, "人物关系回忆")
        self.assertIn("黑衣人", card.answer)
        self.assertIn("林澈", card.answer)
        self.assertTrue(any(item["tool_name"] == "lookup_relations" for item in trace))


if __name__ == "__main__":
    unittest.main()
