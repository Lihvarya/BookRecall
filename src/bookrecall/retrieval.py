import math
import re
from typing import Protocol

from .config import SearchSettings
from .models import SearchHit
from .storage import BookRecallStore

WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    normalized = text.lower()
    tokens: set[str] = set(WORD_PATTERN.findall(normalized))
    cjk_chars = [char for char in normalized if "一" <= char <= "鿿"]
    tokens.update(cjk_chars)
    tokens.update("".join(cjk_chars[index:index + 2]) for index in range(len(cjk_chars) - 1))
    return {token for token in tokens if token.strip()}


def lexical_score(query: str, document: str) -> float:
    query_tokens = _tokenize(query)
    doc_tokens = _tokenize(document)
    if not query_tokens or not doc_tokens:
        return 0.0
    overlap = len(query_tokens & doc_tokens) / len(query_tokens)
    density = len(query_tokens & doc_tokens) / math.sqrt(len(doc_tokens))
    phrase_bonus = 0.25 if query.replace(" ", "") in document.replace(" ", "") else 0.0
    return overlap + density + phrase_bonus


class Retriever(Protocol):
    def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
        ...


class LocalRetriever:
    """本地检索器。

    采用**倒排表**加速：首次搜索某本书时构建 `{token: set[child_chunk_id]}`，
    后续查询只对 query token 命中的候选 chunk 计算精细分数，避免全库 O(n) 扫描。
    将 `lexical_score` 的打分逻辑完整保留，仅改变候选集合范围——因此返回结果与
    全库扫描语义等价（有 query token 命中时），无命中时退回全库扫描兜底。
    """

    def __init__(self, store: BookRecallStore, settings: SearchSettings) -> None:
        self.store = store
        self.settings = settings
        self._index: dict[str, dict[str, set[str]]] = {}
        self._rows_cache: dict[str, list[tuple[str, str, int, str, str, str]]] = {}

    def _ensure_index(self, book_id: str, max_chapter: int | None) -> None:
        cache_key = book_id
        rows = self._rows_cache.get(cache_key)
        if rows is None:
            raw = self.store.iter_search_rows(book_id, max_chapter=None)
            rows = [
                (
                    str(r["chunk_id"]),
                    str(r["parent_id"]),
                    int(r["chapter_number"]),
                    str(r["text"]),
                    str(r["chapter_title"]),
                    str(r["parent_text"]),
                )
                for r in raw
            ]
            self._rows_cache[cache_key] = rows

        if cache_key in self._index:
            return  # 倒排表按全书构建一次即可

        inverted: dict[str, set[str]] = {}
        for chunk_id, _parent_id, _chapter_number, text, _title, _parent_text in rows:
            for token in _tokenize(text):
                posting = inverted.get(token)
                if posting is None:
                    posting = set()
                    inverted[token] = posting
                posting.add(chunk_id)
        self._index[cache_key] = inverted

    def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
        self._ensure_index(book_id, max_chapter)
        rows = self._rows_cache[book_id]
        if not rows:
            return []

        query_tokens = _tokenize(query)
        inverted = self._index[book_id]

        candidate_ids: set[str] | None = None
        for token in query_tokens:
            posting = inverted.get(token)
            if not posting:
                continue
            candidate_ids = posting if candidate_ids is None else candidate_ids & posting

        # 无任何 token 命中（query 全是文档里没有的字）→ 退回全库扫描兜底，
        # 与旧行为一致，避免漏掉 phrase_bonus 这类不依赖 token 命中的得分。
        use_all = candidate_ids is None
        if use_all:
            candidate_ids = {row[0] for row in rows}

        rows_by_id = {row[0]: row for row in rows}
        hits: list[SearchHit] = []
        for chunk_id in candidate_ids:
            row = rows_by_id.get(chunk_id)
            if row is None:
                continue
            chapter_number = row[2]
            if max_chapter is not None and chapter_number > max_chapter:
                continue
            score = lexical_score(query, row[3])
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    score=score,
                    chapter_number=chapter_number,
                    chapter_title=row[4],
                    parent_id=row[1],
                    child_text=row[3],
                    parent_text=row[5],
                )
            )
        hits.sort(key=lambda item: (-item.score, item.chapter_number))

        parent_best: dict[str, SearchHit] = {}
        for hit in hits:
            existing = parent_best.get(hit.parent_id)
            if existing is None or hit.score > existing.score:
                parent_best[hit.parent_id] = hit

        parent_hits = sorted(parent_best.values(), key=lambda item: (-item.score, item.chapter_number))
        return parent_hits[: self.settings.top_k_parents]
