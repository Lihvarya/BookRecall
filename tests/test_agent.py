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
from bookrecall.models import MemoryCard
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


if __name__ == "__main__":
    unittest.main()
