"""Local-LLM answer validation.

The validator is a guardrail, not an answer generator.  It checks whether the
final answer is supported by the already-pruned evidence and whether it appears
to overstep the locked reading progress.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class JsonCompleter(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class AnswerValidation:
    supported: bool = True
    spoiler_safe: bool = True
    speculation_risk: str = "low"
    issues: list[str] = field(default_factory=list)
    suggested_note: str = ""
    confidence: float = 0.0
    source: str = "skipped"
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "spoiler_safe": self.spoiler_safe,
            "speculation_risk": self.speculation_risk,
            "issues": self.issues,
            "suggested_note": self.suggested_note,
            "confidence": self.confidence,
            "source": self.source,
            "error": self.error,
        }

    @property
    def risky(self) -> bool:
        return (not self.supported) or (not self.spoiler_safe) or self.speculation_risk == "high"


def validate_answer_with_llm(
    *,
    question: str,
    answer: str,
    progress_chapter: int,
    evidence: list[dict[str, Any]],
    client: JsonCompleter,
) -> AnswerValidation:
    if not answer.strip():
        return AnswerValidation(
            supported=False,
            spoiler_safe=True,
            speculation_risk="high",
            issues=["答案为空。"],
            confidence=1.0,
            source="rules",
        )
    if not evidence:
        return AnswerValidation(
            supported=False,
            spoiler_safe=True,
            speculation_risk="medium",
            issues=["没有可用于支撑答案的证据片段。"],
            suggested_note="当前回答缺少直接证据，建议仅作为检索提示，不要当作确定结论。",
            confidence=0.8,
            source="rules",
        )
    try:
        payload = client.complete_json(_prompt(question, answer, progress_chapter, evidence))
        return parse_answer_validation_payload(payload)
    except Exception as exc:  # noqa: BLE001 - validation must never break QA
        return AnswerValidation(source="local_llm", error=str(exc))


def parse_answer_validation_payload(payload: dict[str, Any]) -> AnswerValidation:
    risk = str(payload.get("speculation_risk") or "low").strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "low"
    confidence = max(0.0, min(1.0, _float(payload.get("confidence"), 0.0)))
    return AnswerValidation(
        supported=bool(payload.get("supported", True)),
        spoiler_safe=bool(payload.get("spoiler_safe", True)),
        speculation_risk=risk,
        issues=_clean_strings(payload.get("issues"), max_items=6, max_len=120),
        suggested_note=str(payload.get("suggested_note") or "").strip()[:160],
        confidence=confidence,
        source="local_llm",
    )


def _prompt(question: str, answer: str, progress_chapter: int, evidence: list[dict[str, Any]]) -> str:
    evidence_lines: list[str] = []
    for index, item in enumerate(evidence[:8], start=1):
        evidence_lines.append(
            f"[{index}] 第 {item.get('chapter_number')} 章《{item.get('chapter_title', '')}》：{str(item.get('excerpt', ''))[:500]}"
        )
    return f"""
你是 BookRecall 的答案校验器。请只判断答案是否被证据支持，不能补充新事实。

检查目标：
1. supported：答案中的核心结论是否能从证据直接支持。
2. spoiler_safe：答案是否只使用第 1 章到第 {progress_chapter} 章范围内的信息。
3. speculation_risk：答案是否把推测说成事实，取 low/medium/high。
4. issues：列出问题，最多 6 条。
5. suggested_note：如果有风险，给一句面向用户的温和提示；无风险可为空。

只输出 JSON object，不要解释，不要 markdown，不要思考过程。

用户问题：{question}
当前阅读进度：第 {progress_chapter} 章
待校验答案：{answer}

证据：
{chr(10).join(evidence_lines)}

输出格式：
{{"supported":true,"spoiler_safe":true,"speculation_risk":"low","issues":[],"suggested_note":"","confidence":0.0}}
""".strip()


def _clean_strings(value: object, *, max_items: int, max_len: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text[:max_len])
        if len(result) >= max_items:
            break
    return result


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
