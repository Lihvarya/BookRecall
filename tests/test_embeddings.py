import json
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
from bookrecall.embeddings import (
    EmbeddingRetriever,
    RerankingRetriever,
    build_embedding_index,
    configure_local_model_cache,
    default_cache_root,
    default_sentence_transformers_cache_dir,
    faiss_index_paths,
    get_vector_index_info,
)
from bookrecall.entity_index import build_entity_records
from bookrecall.models import SearchHit
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


class CapturingReranker:
    max_chars = 60

    def __init__(self) -> None:
        self.documents: list[str] = []

    def score(self, query: str, documents: list[str]) -> list[float]:
        self.documents = documents
        return [1.0, 0.5]


class StaticRetriever:
    def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
        child = "关键命中片段"
        return [
            SearchHit(
                score=0.8,
                chapter_number=1,
                chapter_title="答案",
                parent_id="p1",
                child_text=child,
                parent_text=f"{'前文' * 80}{child}{'后文' * 80}",
            ),
            SearchHit(
                score=0.7,
                chapter_number=2,
                chapter_title="背景",
                parent_id="p2",
                child_text="普通背景",
                parent_text="普通背景" * 30,
            ),
        ]


class FakeFaissIndex:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.vectors: list[list[float]] = []
        self.ntotal = 0

    def add(self, matrix) -> None:
        self.vectors = matrix.tolist()
        self.ntotal = len(self.vectors)

    def search(self, matrix, top_k: int):
        query = matrix[0].tolist()
        scored = []
        for idx, row in enumerate(self.vectors):
            score = sum(a * b for a, b in zip(row, query))
            scored.append((score, idx))
        scored.sort(key=lambda item: item[0], reverse=True)
        picked = scored[:top_k]
        scores = [[item[0] for item in picked] + [-1.0] * max(0, top_k - len(picked))]
        indices = [[item[1] for item in picked] + [-1] * max(0, top_k - len(picked))]
        import numpy as np

        return np.asarray(scores, dtype=np.float32), np.asarray(indices, dtype=np.int64)


class FakeFaissModule:
    IndexFlatIP = FakeFaissIndex

    def __init__(self) -> None:
        self._saved: dict[str, FakeFaissIndex] = {}

    def write_index(self, index: FakeFaissIndex, path: str) -> None:
        self._saved[path] = index
        Path(path).write_text("fake-faiss", encoding="utf-8")

    def read_index(self, path: str) -> FakeFaissIndex:
        return self._saved[path]


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

    def test_build_and_read_numpy_vector_index_info(self) -> None:
        info = build_embedding_index(
            store=self.store,
            book_id="sample",
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
            prefer_backend="numpy",
        )
        self.assertEqual(info.book_id, "sample")
        self.assertEqual(info.dimension, 3)
        self.assertEqual(info.backend, "numpy")
        self.assertTrue(Path(info.path).exists())

        loaded = get_vector_index_info(self.vector_dir, "sample")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.model_name, "test-tiny-embedder")
        self.assertEqual(loaded.backend, "numpy")

    def test_build_faiss_index_when_module_available(self) -> None:
        fake_faiss = FakeFaissModule()
        info = build_embedding_index(
            store=self.store,
            book_id="sample",
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
            prefer_backend="faiss",
            faiss_module=fake_faiss,
        )
        index_path, chunk_meta_path, _ = faiss_index_paths(self.vector_dir, "sample")
        self.assertEqual(info.backend, "faiss")
        self.assertTrue(index_path.exists())
        self.assertTrue(chunk_meta_path.exists())
        self.assertTrue(json.loads(chunk_meta_path.read_text(encoding="utf-8")))

    def test_embedding_retriever_searches_saved_numpy_vectors(self) -> None:
        build_embedding_index(
            store=self.store,
            book_id="sample",
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
            prefer_backend="numpy",
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

    def test_embedding_retriever_searches_saved_faiss_vectors(self) -> None:
        fake_faiss = FakeFaissModule()
        build_embedding_index(
            store=self.store,
            book_id="sample",
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
            prefer_backend="faiss",
            faiss_module=fake_faiss,
        )
        retriever = EmbeddingRetriever(
            self.store,
            DEFAULT_SEARCH_SETTINGS,
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
            faiss_module=fake_faiss,
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
            prefer_backend="numpy",
        )
        retriever = EmbeddingRetriever(
            self.store,
            DEFAULT_SEARCH_SETTINGS,
            index_dir=self.vector_dir,
            embedder=TinyEmbedder(),
        )
        hits = retriever.search("sample", "黑袍人再次", max_chapter=2)
        self.assertTrue(all(hit.chapter_number <= 2 for hit in hits))

    def test_reranker_centers_context_on_matched_child(self) -> None:
        reranker = CapturingReranker()
        retriever = RerankingRetriever(
            StaticRetriever(),
            reranker,  # type: ignore[arg-type]
            DEFAULT_SEARCH_SETTINGS,
        )

        hits = retriever.search("sample", "关键问题")

        self.assertTrue(hits)
        self.assertIn("关键命中片段", reranker.documents[0])
        self.assertTrue(all(len(document) <= reranker.max_chars for document in reranker.documents))

    def test_default_cache_paths_follow_project_layout(self) -> None:
        db_path = Path(self.tempdir.name) / ".bookrecall" / "bookrecall.db"
        expected_root = (Path(self.tempdir.name) / ".cache").resolve()
        self.assertEqual(default_cache_root(db_path), expected_root)
        self.assertEqual(
            default_sentence_transformers_cache_dir(db_path),
            expected_root / "huggingface" / "sentence-transformers",
        )

    def test_configure_local_model_cache_sets_envs(self) -> None:
        cache_root = Path(self.tempdir.name) / ".cache"
        report = configure_local_model_cache(cache_root)
        self.assertEqual(Path(report["HF_HOME"]), (cache_root / "huggingface").resolve())
        self.assertEqual(
            Path(report["SENTENCE_TRANSFORMERS_HOME"]),
            (cache_root / "huggingface" / "sentence-transformers").resolve(),
        )
        self.assertEqual(Path(report["TORCH_HOME"]), (cache_root / "torch").resolve())


if __name__ == "__main__":
    unittest.main()
