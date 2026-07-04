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
from bookrecall.entity_index import auto_discover_themes, build_entity_records, build_theme_records
from bookrecall.parser import parse_chapters
from bookrecall.retrieval import LocalRetriever
from bookrecall.storage import BookRecallStore


SAMPLE_TEXT = """第1章 起点

林澈第一次听见自由意志这个词，但他还以为命运早已写定。

第2章 选择

自由意志不再只是口号，林澈开始相信每一次选择都会改变道路。

第3章 回声

长老说自由意志意味着承担代价，林澈终于明白自由不是逃避。
"""


class ThemeLayerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "bookrecall.db")
        self.store = BookRecallStore(self.db_path)
        self.store.initialize()

        self.chapters = parse_chapters(SAMPLE_TEXT)
        parents, children = build_chunk_hierarchy("theme-book", self.chapters, DEFAULT_CHUNK_SETTINGS)
        entity_records = build_entity_records(
            self.chapters,
            {"林澈": []},
            DEFAULT_CHUNK_SETTINGS,
        )
        self.theme_names = auto_discover_themes(SAMPLE_TEXT)
        self.theme_records = build_theme_records(self.chapters, self.theme_names, DEFAULT_CHUNK_SETTINGS)
        self.store.replace_book(
            book_id="theme-book",
            title="主题测试书",
            source_path="memory",
            chapters=self.chapters,
            parent_chunks=parents,
            child_chunks=children,
            entity_records=entity_records,
            theme_records=self.theme_records,
        )
        self.registry = build_default_registry(
            self.store,
            LocalRetriever(self.store, DEFAULT_SEARCH_SETTINGS),
        )
        self.agent = BookRecallAgent(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_auto_discover_and_build_theme_records(self) -> None:
        names = {record.name for record in self.theme_records}

        self.assertIn("自由意志", names)
        self.assertGreaterEqual(
            next(record for record in self.theme_records if record.name == "自由意志").first_chapter_number,
            1,
        )

    def test_store_get_theme_mentions_respects_progress(self) -> None:
        visible_rows = self.store.get_theme_mentions("theme-book", "自由意志", max_chapter=2)
        blocked_rows = self.store.get_theme_mentions("theme-book", "自由意志", max_chapter=0)

        self.assertEqual([int(row["chapter_number"]) for row in visible_rows], [1, 2])
        self.assertEqual(blocked_rows, [])

    def test_search_theme_tool_returns_fragments(self) -> None:
        state = AgentState(
            book_id="theme-book",
            question="自由意志的观点前后有什么变化？",
            progress_chapter=3,
            matched_themes=["自由意志"],
        )
        tool = self.registry.get("search_theme")
        self.assertIsNotNone(tool)

        result = tool.run(state, {"theme": "自由意志"})

        self.assertTrue(result["found"])
        self.assertEqual(result["theme_name"], "自由意志")
        self.assertEqual(result["chapters"], [1, 2, 3])
        self.assertGreaterEqual(len(result["fragments"]), 3)
        self.assertGreaterEqual(len(result["stages"]), 3)
        self.assertIn("evolution_summary", result)
        self.assertIn("自由意志", result["evolution_summary"])

    def test_agent_answers_theme_question_with_evidence_and_trace(self) -> None:
        card = self.agent.ask_card(
            book_id="theme-book",
            question="自由意志的观点前后有什么变化？",
            progress_chapter=3,
            session_id="theme-session",
        )
        turns = self.store.list_agent_turns("theme-book", "default", "theme-session", limit=10)
        trace = turns[-1]["trace"]

        self.assertEqual(card.intent, "主题线索回忆")
        self.assertIn("自由意志", card.answer)
        self.assertIn("阶段线索", card.answer)
        self.assertIn("总体演化", card.answer)
        self.assertGreaterEqual(len(card.evidence), 1)
        self.assertTrue(any(item["tool_name"] == "search_theme" for item in trace))


if __name__ == "__main__":
    unittest.main()
