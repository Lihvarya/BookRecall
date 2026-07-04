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
from bookrecall.embeddings import EmbeddingRetriever, build_embedding_index, get_vector_index_info
from bookrecall.entity_index import build_entity_records
from bookrecall.parser import parse_chapters
from bookrecall.storage import BookRecallStore


SAMPLE_TEXT = """第1章 起点

林澈在旧书里看到【星辰之匙】的名字。

第2章 阴影

黑衣人在雨里出现。

第3章 回声

黑衣人再次提到【星辰之匙】。
"""


class TinyEmbedder:
    model_name = "test-tiny-embedder"

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(
                [
                    float(text.count("星辰之匙") + text.count("钥匙")),
                    float(text.count("黑衣人") + text.count("黑袍人")),
                    float(text.count("林澈")),
                ]
            )
        return vectors


class EmbeddingIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "bookrecall.db")
        self.vector_dir = Path(self.tempdir.name) / "vectors"
        self.store = BookRecallStore(self.db_path)
        self.store.initialize()

        chapters = parse_chapters(SAMPLE_TEXT)
        parents, children = build_chunk_hierarchy("sample", chapters, DEFAULT_CHUNK_SETTINGS)
        entity_records = build_entity_records(
            chapters,
            {"星辰之匙": ["钥匙"], "黑衣人": ["黑袍人"]},
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

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_build_and_read_vector_index_info(self) -> None:
        info = build_embedding_index(
            store=self.store,
            book_id="sample",
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
        )
        self.assertEqual(info.book_id, "sample")
        self.assertEqual(info.dimension, 3)
        self.assertTrue(Path(info.path).exists())

        loaded = get_vector_index_info(self.vector_dir, "sample")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.model_name, "test-tiny-embedder")

    def test_embedding_retriever_searches_saved_vectors(self) -> None:
        build_embedding_index(
            store=self.store,
            book_id="sample",
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
        )
        retriever = EmbeddingRetriever(
            self.store,
            DEFAULT_SEARCH_SETTINGS,
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
        )
        hits = retriever.search("sample", "黑袍人出现", max_chapter=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chapter_number, 2)

    def test_embedding_retriever_respects_progress(self) -> None:
        build_embedding_index(
            store=self.store,
            book_id="sample",
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
        )
        retriever = EmbeddingRetriever(
            self.store,
            DEFAULT_SEARCH_SETTINGS,
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
        )
        hits = retriever.search("sample", "黑袍人再次", max_chapter=2)
        self.assertTrue(all(hit.chapter_number <= 2 for hit in hits))


if __name__ == "__main__":
    unittest.main()
