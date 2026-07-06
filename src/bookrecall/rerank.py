"""Local-LLM evidence reranking.

Reranking is deliberately constrained: the model may only return indexes of
existing hits.  It cannot invent evidence or change chapter boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class JsonCompleter(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class RerankItem:
    index: int
    relevance: float = 0.0
    reason: str = ""


@dataclass(slots=True)
class RerankResult:
    hits: list[dict[str, Any]]
    items: list[RerankItem] = field(default_factory=list)
    used: bool = False
    error: str = ""

    def metadata(self) -> dict[str, object]:
        return {
            "used": self.used,
            "error": self.error,
            "items": [
                {
                    "index": item.index,
                    "relevance": item.relevance,
                    "reason": item.reason,
                }
                for item in self.items
            ],
        }


def rerank_evidence_hits(
    question: str,
    hits: list[dict[str, Any]],
    client: JsonCompleter,
    *,
    max_hits: int = 8,
) -> RerankResult:
    if len(hits) <= 1:
        return RerankResult(hits=list(hits), used=False)
    candidates = list(hits[:max_hits])
    try:
        payload = client.complete_json(_prompt(question, candidates))
        items = parse_rerank_payload(payload, len(candidates))
        if not items:
            return RerankResult(hits=list(hits), used=False, error="本地模型没有返回有效重排结果。")
        ranked = _apply_ranking(hits, items)
        return RerankResult(hits=ranked, items=items, used=True)
    except Exception as exc:  # noqa: BLE001 - rerank must never break QA
        return RerankResult(hits=list(hits), used=False, error=str(exc))


def parse_rerank_payload(payload: dict[str, Any], hit_count: int) -> list[RerankItem]:
    raw_items = payload.get("ranked_hits")
    if not isinstance(raw_items, list):
        raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return []
    items: list[RerankItem] = []
    seen: set[int] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        index = _optional_int(raw.get("index"))
        if index is None or not (1 <= index <= hit_count) or index in seen:
            continue
        seen.add(index)
        items.append(
            RerankItem(
                index=index,
                relevance=max(0.0, min(1.0, _float(raw.get("relevance"), 0.0))),
                reason=str(raw.get("reason") or "").strip()[:80],
            )
        )
    return items


def _apply_ranking(hits: list[dict[str, Any]], items: list[RerankItem]) -> list[dict[str, Any]]:
    by_index = {index + 1: hit for index, hit in enumerate(hits)}
    ranked: list[dict[str, Any]] = []
    used_indexes: set[int] = set()
    for item in sorted(items, key=lambda value: (-value.relevance, value.index)):
        hit = by_index.get(item.index)
        if hit is None:
            continue
        enriched = dict(hit)
        enriched["rerank_relevance"] = item.relevance
        enriched["rerank_reason"] = item.reason
        ranked.append(enriched)
        used_indexes.add(item.index)
    for index, hit in by_index.items():
        if index not in used_indexes:
            ranked.append(hit)
    return ranked


def _prompt(question: str, hits: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        text = str(hit.get("child_text") or "")[:500]
        chapter = hit.get("chapter_number")
        title = hit.get("chapter_title") or ""
        blocks.append(f"[{index}] 第 {chapter} 章《{title}》：{text}")
    return f"""
你是 BookRecall 的证据重排器。请判断哪些候选片段最能回答用户问题。

规则：
1. 只能使用候选片段编号，不能新增证据。
2. 不要因为章节靠前或分数高就优先，优先回答问题相关性。
3. relevance 是 0 到 1 的相关度。
4. 只输出 JSON object，不要解释，不要 markdown，不要思考过程。

用户问题：{question}

候选片段：
{chr(10).join(blocks)}

输出格式：
{{"ranked_hits":[{{"index":1,"relevance":0.0,"reason":"简短原因"}}]}}
""".strip()


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
