"""测试 6 个工具的入参/出参与防剧透行为。

工具层是纯逻辑（直接复用 storage/retrieval），不联网，故全程本地可测。
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.agent.state import AgentState
from bookrecall.agent.tools import build_default_registry
from bookrecall.chunking import build_chunk_hierarchy
from bookrecall.config import DEFAULT_CHUNK_SETTINGS, DEFAULT_SEARCH_SETTINGS
from bookrecall.entity_index import (
    auto_discover_themes,
    build_entity_records,
    build_event_records,
    build_relation_records,
    build_theme_records,
)
from bookrecall.parser import parse_chapters
from bookrecall.retrieval import LocalRetriever
from bookrecall.storage import BookRecallStore

SAMPLE = """第1章 雨夜
林澈在雨夜里看到黑衣人，他手里握着星辰之匙。
黑衣人转身走进灰塔。

第2章 灰塔
灰塔的旧书库里藏着关于自由意志的批注。
林澈最终拿到了星辰之匙。

第3章 远行
星辰之匙打开了石门，自由意志的含义就此浮现。
月影罗盘只在这一章短暂出现过一次。
"""


class AgentToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "t.db")
        self.store = BookRecallStore(self.db_path)
        self.store.initialize()
        chapters = parse_chapters(SAMPLE)
        parents, children = build_chunk_hierarchy("b", chapters, DEFAULT_CHUNK_SETTINGS)
        records = build_entity_records(
            chapters,
            {"星辰之匙": ["钥匙"], "黑衣人": ["黑袍人"], "林澈": []},
            DEFAULT_CHUNK_SETTINGS,
        )
        relation_records = build_relation_records(chapters, records, DEFAULT_CHUNK_SETTINGS)
        theme_records = build_theme_records(chapters, auto_discover_themes(SAMPLE), DEFAULT_CHUNK_SETTINGS)
        event_records = build_event_records(chapters, records, DEFAULT_CHUNK_SETTINGS)
        self.store.replace_book(
            book_id="b", title="测", source_path="mem",
            chapters=chapters, parent_chunks=parents, child_chunks=children,
            entity_records=records, relation_records=relation_records,
            theme_records=theme_records, event_records=event_records,
        )
        self.retriever = LocalRetriever(self.store, DEFAULT_SEARCH_SETTINGS)
        self.registry = build_default_registry(self.store, self.retriever)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _state(self, progress: int = 3, question: str = "") -> AgentState:
        return AgentState(book_id="b", question=question, progress_chapter=progress, primary_entity="黑衣人")

    def run_tool(self, name: str, state: AgentState, args: dict) -> dict:
        tool = self.registry.get(name)
        self.assertIsNotNone(tool)
        return tool.run(state, args)

    def test_lookup_first_appearance(self) -> None:
        r = self.run_tool("lookup_first_appearance", self._state(3), {"entity": "星辰之匙"})
        self.assertTrue(r["found"])
        self.assertEqual(int(r["first_chapter_number"]), 1)
        self.assertFalse(r["spoiler_blocked"])

    def test_lookup_first_appearance_spoiler(self) -> None:
        # 进度只到第1章，但黑衣人首次在第1章——这里测星辰之匙(第1章)应不触发
        # 改测一个首章在第3章之后的实体不适用；改用进度=0 验证 spoiler
        r = self.run_tool("lookup_first_appearance", self._state(0), {"entity": "星辰之匙"})
        self.assertTrue(r["found"])
        self.assertTrue(int(r["first_chapter_number"]) >= 1)
        # progress=0 → 任何首章都 >0 → spoiler
        self.assertTrue(r["spoiler_blocked"])

    def test_lookup_timeline(self) -> None:
        r = self.run_tool("lookup_timeline", self._state(3), {"entity": "黑衣人"})
        self.assertEqual(r["chapters"], [1])
        self.assertGreaterEqual(r["count"], 1)
        self.assertLessEqual(len(r["fragments"]), 3)

    def test_lookup_timeline_progress_filter(self) -> None:
        r = self.run_tool("lookup_timeline", self._state(1), {"entity": "星辰之匙"})
        # 第1章提到星辰之匙，第2、3章也提；进度=1 只应见第1章
        self.assertEqual(r["chapters"], [1])

    def test_search_evidence(self) -> None:
        r = self.run_tool("search_evidence", self._state(3), {"query": "自由意志"})
        self.assertGreater(r["count"], 0)
        self.assertIn("自由意志", r["hits"][0]["child_text"] + r["hits"][0]["chapter_title"])

    def test_search_evidence_progress_filter(self) -> None:
        r = self.run_tool("search_evidence", self._state(1), {"query": "自由意志"})
        for hit in r["hits"]:
            self.assertLessEqual(hit["chapter_number"], 1)

    def test_search_exact_text_finds_unindexed_low_frequency_term(self) -> None:
        r = self.run_tool("search_exact_text", self._state(3), {"keyword": "月影罗盘"})
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["hits"][0]["chapter_number"], 3)
        self.assertIn("月影罗盘", r["hits"][0]["child_text"])

    def test_search_exact_text_progress_filter(self) -> None:
        r = self.run_tool("search_exact_text", self._state(2), {"keyword": "月影罗盘"})
        self.assertEqual(r["count"], 0)

    def test_lookup_entity_aliases(self) -> None:
        r = self.run_tool("lookup_entity_aliases", self._state(3), {"entity": "黑袍人"})
        self.assertTrue(r["found"])
        self.assertEqual(r["canonical_name"], "黑衣人")
        self.assertIn("黑袍人", r["aliases"])

    def test_lookup_relations(self) -> None:
        r = self.run_tool(
            "lookup_relations",
            self._state(3, "林澈和黑衣人是什么关系？"),
            {"source_entity": "林澈", "target_entity": "黑衣人"},
        )
        self.assertTrue(r["found"])
        self.assertGreaterEqual(r["count"], 1)
        self.assertIn("fragments", r["relations"][0])

    def test_search_theme(self) -> None:
        r = self.run_tool(
            "search_theme",
            self._state(3, "自由意志的观点前后有什么变化？"),
            {"theme": "自由意志"},
        )
        self.assertTrue(r["found"])
        self.assertEqual(r["theme_name"], "自由意志")
        self.assertEqual(r["chapters"], [2, 3])
        self.assertGreaterEqual(r["count"], 2)

    def test_search_events(self) -> None:
        r = self.run_tool(
            "search_events",
            self._state(3, "星辰之匙涉及哪些关键事件？"),
            {"query": "星辰之匙关键事件", "entity": "星辰之匙"},
        )
        self.assertTrue(r["found"])
        self.assertGreaterEqual(r["count"], 2)
        self.assertIn("chain_summary", r)

    def test_get_chapter_summary(self) -> None:
        r = self.run_tool("get_chapter_summary", self._state(3), {"chapter": 1})
        self.assertTrue(r["found"])
        self.assertFalse(r["spoiler_blocked"])
        self.assertTrue(r["summary"])

    def test_get_chapter_summary_spoiler(self) -> None:
        r = self.run_tool("get_chapter_summary", self._state(1), {"chapter": 3})
        self.assertTrue(r["spoiler_blocked"])

    def test_list_entities(self) -> None:
        r = self.run_tool("list_entities", self._state(3), {})
        self.assertGreaterEqual(r["count"], 3)

    def test_registry_describe_for_llm(self) -> None:
        desc = self.registry.describe_for_llm()
        self.assertEqual(len(desc), 10)
        names = {d["name"] for d in desc}
        self.assertEqual(names, {
            "lookup_first_appearance", "lookup_timeline", "search_evidence", "search_exact_text",
            "lookup_relations", "search_theme", "search_events",
            "lookup_entity_aliases", "get_chapter_summary", "list_entities",
        })


if __name__ == "__main__":
    unittest.main()
