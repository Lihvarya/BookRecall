import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.chunking import build_chunk_hierarchy
from bookrecall.config import DEFAULT_CHUNK_SETTINGS, DEFAULT_SEARCH_SETTINGS
from bookrecall.parser import parse_chapters
from bookrecall.retrieval import LocalRetriever
from bookrecall.storage import BookRecallStore


class RetrievalRegressionTest(unittest.TestCase):
    def test_chinese_condition_query_uses_broad_candidates(self) -> None:
        text = """第1章 成尊的四个条件

陆畏因道：“要成就尊者，须得满足四个条件。”
“第一，蛊仙的仙窍本源产出白荔仙元。”
“第二，蛊仙主修流派的道痕，至少有三十万规模。”
“第三，蛊仙的主修流派的境界，必须是无上大宗师。”
“第四，蛊仙拥有前三项条件后，须得突破天道封锁。”
"""
        with tempfile.TemporaryDirectory() as tempdir:
            store = BookRecallStore(str(Path(tempdir) / "bookrecall.db"))
            store.initialize()
            try:
                chapters = parse_chapters(text)
                parents, children = build_chunk_hierarchy("sample", chapters, DEFAULT_CHUNK_SETTINGS)
                store.replace_book(
                    book_id="sample",
                    title="测试书",
                    source_path="memory",
                    chapters=chapters,
                    parent_chunks=parents,
                    child_chunks=children,
                    entity_records=[],
                )
                hits = LocalRetriever(store, DEFAULT_SEARCH_SETTINGS).search("sample", "成为尊者条件是什么")
            finally:
                store.close()

        self.assertTrue(hits)
        self.assertIn("四个条件", hits[0].parent_text)


if __name__ == "__main__":
    unittest.main()
