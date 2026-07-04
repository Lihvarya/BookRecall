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
from bookrecall.entity_index import build_entity_records, build_event_records
from bookrecall.parser import parse_chapters
from bookrecall.retrieval import LocalRetriever
from bookrecall.storage import BookRecallStore


SAMPLE_TEXT = """第1章 起点

林澈在旧书里看到星辰之匙的名字，并决定追查它的来历。

第2章 阴影

黑衣人在雨里出现，林澈与黑衣人对峙。

第3章 石门

林澈拿到星辰之匙，星辰之匙打开了石门。
"""


class EventLayerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "bookrecall.db")
        self.store = BookRecallStore(self.db_path)
        self.store.initialize()

        chapters = parse_chapters(SAMPLE_TEXT)
        parents, children = build_chunk_hierarchy("event-book", chapters, DEFAULT_CHUNK_SETTINGS)
        entity_records = build_entity_records(
            chapters,
            {"林澈": [], "黑衣人": [], "星辰之匙": []},
            DEFAULT_CHUNK_SETTINGS,
        )
        self.event_records = build_event_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
        self.store.replace_book(
            book_id="event-book",
            title="事件测试书",
            source_path="memory",
            chapters=chapters,
            parent_chunks=parents,
            child_chunks=children,
            entity_records=entity_records,
            event_records=self.event_records,
        )
        self.registry = build_default_registry(
            self.store,
            LocalRetriever(self.store, DEFAULT_SEARCH_SETTINGS),
        )
        self.agent = BookRecallAgent(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_build_event_records(self) -> None:
        summaries = [record.summary for record in self.event_records]

        self.assertTrue(any("决定追查" in summary for summary in summaries))
        self.assertTrue(any("打开了石门" in summary for summary in summaries))

    def test_search_events_tool(self) -> None:
        state = AgentState(
            book_id="event-book",
            question="星辰之匙涉及哪些关键事件？",
            progress_chapter=3,
            matched_entities=["星辰之匙"],
            primary_entity="星辰之匙",
        )
        tool = self.registry.get("search_events")
        self.assertIsNotNone(tool)

        result = tool.run(state, {"query": "星辰之匙关键事件", "entity": "星辰之匙"})

        self.assertTrue(result["found"])
        self.assertGreaterEqual(result["count"], 2)
        self.assertIn("chain_summary", result)

    def test_agent_answers_event_chain_question(self) -> None:
        card = self.agent.ask_card(
            book_id="event-book",
            question="星辰之匙涉及哪些关键事件？",
            progress_chapter=3,
            session_id="event-session",
        )
        turns = self.store.list_agent_turns("event-book", "default", "event-session", limit=10)
        trace = turns[-1]["trace"]

        self.assertEqual(card.intent, "事件链回忆")
        self.assertIn("星辰之匙", card.answer)
        self.assertGreaterEqual(len(card.evidence), 1)
        self.assertTrue(any(item["tool_name"] == "search_events" for item in trace))


if __name__ == "__main__":
    unittest.main()
