"""Local-LLM evidence-grounded answer synthesis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class JsonCompleter(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class SynthesizedAnswer:
    answer: str = ""
    summary: str = ""
    confidence: float = 0.0
    used: bool = False
    source: str = "skipped"
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "used": self.used,
            "source": self.source,
            "confidence": self.confidence,
            "summary": self.summary,
            "error": self.error,
        }


def synthesize_answer_with_llm(
    *,
    question: str,
    draft_answer: str,
    progress_chapter: int,
    evidence: list[dict[str, Any]],
    client: JsonCompleter,
) -> SynthesizedAnswer:
    if not draft_answer.strip() or not evidence:
        return SynthesizedAnswer()
    try:
        payload = client.complete_json(_prompt(question, draft_answer, progress_chapter, evidence))
        answer = str(payload.get("answer") or "").strip()
        if not answer:
            return SynthesizedAnswer(source="local_llm", error="本地小模型没有返回 answer。")
        return SynthesizedAnswer(
            answer=answer[:1800],
            summary=str(payload.get("summary") or "").strip()[:240],
            confidence=max(0.0, min(1.0, _float(payload.get("confidence"), 0.0))),
            used=True,
            source="local_llm",
        )
    except Exception as exc:  # noqa: BLE001 - synthesis must never break QA
        return SynthesizedAnswer(source="local_llm", error=str(exc))


def _prompt(question: str, draft_answer: str, progress_chapter: int, evidence: list[dict[str, Any]]) -> str:
    evidence_lines: list[str] = []
    for index, item in enumerate(evidence[:8], start=1):
        evidence_lines.append(
            f"[{index}] 第 {item.get('chapter_number')} 章《{item.get('chapter_title', '')}》："
            f"{str(item.get('excerpt', ''))[:700]}"
        )
    return f"""
你是 BookRecall 的最终答案整理器。你只能基于给定证据回答，不能使用外部知识，不能补充证据外事实。

任务：
1. 保留草稿中被证据支持的结论。
2. 对多段证据按章节顺序整理因果、时间线或条件关系。
3. 如果证据只能支持“接近真相”，不要强行下最终定论；请明确说“当前可确认”。
4. 回答要直接、清楚、适合中文长篇小说回忆场景。
5. 不要展示推理过程，不要 markdown 代码块。

只输出 JSON object：
{{"answer":"最终回答","summary":"一句话说明如何整理证据","confidence":0.0}}

用户问题：{question}
阅读进度：第 {progress_chapter} 章

规则草稿答案：
{draft_answer}

证据：
{chr(10).join(evidence_lines)}
""".strip()


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
