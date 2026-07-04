from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .config import DEFAULT_EMBEDDING_SETTINGS, SearchSettings
from .models import SearchHit
from .storage import BookRecallStore


class LocalModelError(RuntimeError):
    """Raised when an optional local model dependency or index is unavailable."""


class Embedder(Protocol):
    model_name: str

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        ...


@dataclass(slots=True)
class VectorIndexInfo:
    book_id: str
    model_name: str
    backend: str
    chunk_count: int
    dimension: int
    path: str


def dependency_report() -> dict[str, object]:
    return {
        "numpy": importlib.util.find_spec("numpy") is not None,
        "sentence_transformers": importlib.util.find_spec("sentence_transformers") is not None,
        "torch": importlib.util.find_spec("torch") is not None,
        "faiss": importlib.util.find_spec("faiss") is not None,
        "recommended_embedding_model": DEFAULT_EMBEDDING_SETTINGS.model_name,
    }


def default_vector_dir(db_path: str) -> Path:
    db = Path(db_path)
    return db.parent / DEFAULT_EMBEDDING_SETTINGS.vector_dir_name


def safe_book_id(book_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", book_id).strip("_") or "book"


def vector_index_paths(index_dir: str | Path, book_id: str) -> tuple[Path, Path]:
    root = Path(index_dir)
    stem = safe_book_id(book_id)
    return root / f"{stem}.npz", root / f"{stem}.json"


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = DEFAULT_EMBEDDING_SETTINGS.model_name) -> None:
        if importlib.util.find_spec("sentence_transformers") is None:
            raise LocalModelError(
                "缺少 sentence-transformers，无法加载本地 embedding 模型。"
                "请先按项目文档安装可选依赖后再运行 embed-build。"
            )
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()


def _require_numpy():
    if importlib.util.find_spec("numpy") is None:
        raise LocalModelError("缺少 numpy，无法构建或读取本地向量索引。")
    import numpy as np

    return np


def _normalize_rows(vectors):
    np = _require_numpy()
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim != 2:
        raise LocalModelError("embedding 输出必须是二维向量。")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def build_embedding_index(
    *,
    store: BookRecallStore,
    book_id: str,
    index_dir: str | Path,
    embedder: Embedder,
    batch_size: int = DEFAULT_EMBEDDING_SETTINGS.batch_size,
    limit_chunks: int | None = None,
) -> VectorIndexInfo:
    np = _require_numpy()
    rows = store.iter_search_rows(book_id, max_chapter=None)
    if limit_chunks is not None:
        rows = rows[:limit_chunks]
    if not rows:
        raise LocalModelError(f"book_id={book_id} 没有 child chunk，无法构建向量索引。")

    chunk_ids: list[str] = []
    texts: list[str] = []
    for row in rows:
        chunk_ids.append(str(row["chunk_id"]))
        texts.append(str(row["text"]))

    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        vectors.extend(embedder.encode(batch, batch_size=batch_size))
    matrix = _normalize_rows(vectors)

    index_path, meta_path = vector_index_paths(index_dir, book_id)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(index_path, chunk_ids=np.asarray(chunk_ids), vectors=matrix)

    info = VectorIndexInfo(
        book_id=book_id,
        model_name=embedder.model_name,
        backend="sentence-transformers",
        chunk_count=len(chunk_ids),
        dimension=int(matrix.shape[1]),
        path=str(index_path),
    )
    meta_path.write_text(json.dumps(asdict(info), ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def get_vector_index_info(index_dir: str | Path, book_id: str) -> VectorIndexInfo | None:
    index_path, meta_path = vector_index_paths(index_dir, book_id)
    if not index_path.exists() or not meta_path.exists():
        return None
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    return VectorIndexInfo(
        book_id=str(raw["book_id"]),
        model_name=str(raw["model_name"]),
        backend=str(raw["backend"]),
        chunk_count=int(raw["chunk_count"]),
        dimension=int(raw["dimension"]),
        path=str(raw["path"]),
    )


class EmbeddingRetriever:
    def __init__(
        self,
        store: BookRecallStore,
        settings: SearchSettings,
        *,
        index_dir: str | Path,
        embedder: Embedder,
    ) -> None:
        self.store = store
        self.settings = settings
        self.index_dir = Path(index_dir)
        self.embedder = embedder
        self._loaded: dict[str, tuple[object, list[str]]] = {}

    def _load(self, book_id: str):
        if book_id in self._loaded:
            return self._loaded[book_id]
        np = _require_numpy()
        index_path, meta_path = vector_index_paths(self.index_dir, book_id)
        if not index_path.exists() or not meta_path.exists():
            raise LocalModelError(f"book_id={book_id} 还没有向量索引，请先运行 embed-build。")
        data = np.load(index_path, allow_pickle=False)
        vectors = data["vectors"].astype(np.float32)
        chunk_ids = [str(item) for item in data["chunk_ids"].tolist()]
        self._loaded[book_id] = (vectors, chunk_ids)
        return vectors, chunk_ids

    def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
        np = _require_numpy()
        vectors, chunk_ids = self._load(book_id)
        query_vector = _normalize_rows(self.embedder.encode([query], batch_size=1))[0]
        scores = vectors @ query_vector
        if scores.size == 0:
            return []

        candidate_count = min(
            scores.size,
            max(self.settings.top_k_children * 8, self.settings.top_k_parents * 12, 32),
        )
        if candidate_count < scores.size:
            candidate_indices = np.argpartition(scores, -candidate_count)[-candidate_count:]
            candidate_indices = candidate_indices[np.argsort(scores[candidate_indices])[::-1]]
        else:
            candidate_indices = np.argsort(scores)[::-1]

        rows = self.store.iter_search_rows(book_id, max_chapter=None)
        rows_by_id = {str(row["chunk_id"]): row for row in rows}
        hits: list[SearchHit] = []
        for index in candidate_indices:
            chunk_id = chunk_ids[int(index)]
            row = rows_by_id.get(chunk_id)
            if row is None:
                continue
            chapter_number = int(row["chapter_number"])
            if max_chapter is not None and chapter_number > max_chapter:
                continue
            score = float(scores[int(index)])
            hits.append(
                SearchHit(
                    score=score,
                    chapter_number=chapter_number,
                    chapter_title=str(row["chapter_title"]),
                    parent_id=str(row["parent_id"]),
                    child_text=str(row["text"]),
                    parent_text=str(row["parent_text"]),
                )
            )

        parent_best: dict[str, SearchHit] = {}
        for hit in hits:
            existing = parent_best.get(hit.parent_id)
            if existing is None or hit.score > existing.score:
                parent_best[hit.parent_id] = hit
        return sorted(parent_best.values(), key=lambda item: (-item.score, item.chapter_number))[: self.settings.top_k_parents]
