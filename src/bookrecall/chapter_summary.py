"""LLM-assisted chapter and stage summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .models import Chapter


class JsonCompleter(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class ChapterSummary:
    summary: str = ""
    key_entities: list[str] = field(default_factory=list)
    key_events: list[str] = field(default_factory=list)
    foreshadowing: list[str] = field(default_factory=list)
    state_changes: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def render(self) -> str:
        parts = []
        if self.summary:
            parts.append(f"摘要：{self.summary}")
        if self.key_entities:
            parts.append(f"关键人物/实体：{'、'.join(self.key_entities[:8])}")
        if self.key_events:
            parts.append(f"关键事件：{'；'.join(self.key_events[:4])}")
        if self.foreshadowing:
            parts.append(f"伏笔/线索：{'；'.join(self.foreshadowing[:3])}")
        if self.state_changes:
            parts.append(f"状态变化：{'；'.join(self.state_changes[:3])}")
        return " | ".join(parts) if parts else ""


def build_chapter_summaries_with_llm(
    chapters: list[Chapter],
    client: JsonCompleter,
    *,
    max_chapters: int = 0,
    stage_size: int = 10,
    chapter_stride: int = 1,
    progress_callback: Callable[[str, int, int, Chapter], None] | None = None,
) -> dict[int, str]:
    selected = chapters[:max_chapters] if max_chapters and max_chapters > 0 else chapters
    selected = _stride_chapters(selected, chapter_stride)
    total = len(selected)
    summaries: dict[int, str] = {}
    structured: dict[int, ChapterSummary] = {}
    for index, chapter in enumerate(selected, start=1):
        if progress_callback:
            progress_callback("智能章节摘要", index, total, chapter)
        try:
            payload = client.complete_json(_chapter_prompt(chapter))
        except Exception:
            # Local small models occasionally produce malformed JSON for one
            # chapter.  Keep the import alive and let later chapters index.
            continue
        parsed = parse_chapter_summary_payload(payload)
        rendered = parsed.render()
        if rendered:
            summaries[chapter.number] = rendered
            structured[chapter.number] = parsed

    if summaries:
        for stage in _stage_windows(selected, max(2, min(20, stage_size))):
            stage_lines = [
                f"第 {chapter.number} 章《{chapter.title}》：{summaries.get(chapter.number, '')}"
                for chapter in stage
                if summaries.get(chapter.number)
            ]
            if not stage_lines:
                continue
            try:
                payload = client.complete_json(_stage_prompt(stage, stage_lines))
                stage_summary = str(payload.get("stage_summary") or "").strip()
                if stage_summary:
                    end_chapter = stage[-1].number
                    previous = summaries.get(end_chapter, "")
                    summaries[end_chapter] = f"{previous} | 阶段回顾（第 {stage[0].number}-{end_chapter} 章）：{stage_summary[:240]}"
            except Exception:
                # Stage summaries are an enhancement; chapter summaries remain useful on failure.
                continue
    return summaries


def parse_chapter_summary_payload(payload: dict[str, Any]) -> ChapterSummary:
    return ChapterSummary(
        summary=str(payload.get("summary") or "").strip()[:180],
        key_entities=_clean_strings(payload.get("key_entities"), max_items=10, max_len=24),
        key_events=_clean_strings(payload.get("key_events"), max_items=6, max_len=80),
        foreshadowing=_clean_strings(payload.get("foreshadowing"), max_items=5, max_len=80),
        state_changes=_clean_strings(payload.get("state_changes"), max_items=5, max_len=80),
        confidence=max(0.0, min(1.0, _float(payload.get("confidence"), 0.0))),
    )


def _chapter_prompt(chapter: Chapter) -> str:
    content = " ".join(chapter.content.split())[:3600]
    return f"""
你是 BookRecall 的中文长篇小说章节摘要器。请为章节生成阅读记忆索引。

要求：
1. summary 概括本章发生了什么，不超过 80 字。
2. key_entities 只列人物、地点、组织、重要物品、功法、核心概念。
3. key_events 只列推动剧情或揭示信息的事件。
4. foreshadowing 记录伏笔、谜团、未解释线索；没有则空数组。
5. state_changes 记录人物关系、道具归属、身份、目标或局势变化。
6. 只输出 JSON object，不要解释，不要 markdown，不要思考过程。

章节：第 {chapter.number} 章《{chapter.title}》
正文：
{content}

输出格式：
{{"summary":"","key_entities":[],"key_events":[],"foreshadowing":[],"state_changes":[],"confidence":0.0}}
""".strip()


def _stage_prompt(chapters: list[Chapter], stage_lines: list[str]) -> str:
    return f"""
你是 BookRecall 的阶段回顾助手。请基于章节摘要，归纳这一阶段读者需要记住的主线。

要求：
1. stage_summary 不超过 160 字。
2. 只基于给定摘要，不要补充新事实。
3. 只输出 JSON object，不要解释，不要 markdown。

阶段范围：第 {chapters[0].number} 章到第 {chapters[-1].number} 章
章节摘要：
{chr(10).join(stage_lines)}

输出格式：
{{"stage_summary":"","confidence":0.0}}
""".strip()


def _stage_windows(chapters: list[Chapter], size: int) -> list[list[Chapter]]:
    return [chapters[index : index + size] for index in range(0, len(chapters), size)]


def _stride_chapters(chapters: list[Chapter], stride: int) -> list[Chapter]:
    step = max(1, int(stride or 1))
    if step <= 1 or len(chapters) <= 2:
        return chapters
    selected = chapters[::step]
    if chapters[-1] not in selected:
        selected.append(chapters[-1])
    return selected


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
