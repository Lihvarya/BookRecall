import math
import re

from .config import SearchSettings
from .models import SearchHit
from .storage import BookRecallStore

WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    normalized = text.lower()
    tokens = set(WORD_PATTERN.findall(normalized))
    cjk_chars = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
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


class LocalRetriever:
    def __init__(self, store: BookRecallStore, settings: SearchSettings) -> None:
        self.store = store
        self.settings = settings

    def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
        rows = self.store.iter_search_rows(book_id, max_chapter=max_chapter)
        hits: list[SearchHit] = []
        for row in rows:
            score = lexical_score(query, row["text"])
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    score=score,
                    chapter_number=int(row["chapter_number"]),
                    chapter_title=row["chapter_title"],
                    parent_id=row["parent_id"],
                    child_text=row["text"],
                    parent_text=row["parent_text"],
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

