"""MemoryCard 渲染：对外输出契约，从旧 agent.py 平移，零行为改动。"""

import json

from ..models import MemoryCard


def render_text(card: MemoryCard) -> str:
    lines = [
        f"问题类型：{card.intent}",
        f"阅读进度保护：已限制到第 {card.progress_chapter} 章",
    ]
    if card.entity_name:
        lines.append(f"关联实体：{card.entity_name}")
    lines.append(f"结论：{card.answer}")
    if card.summary:
        lines.append(f"补充说明：{card.summary}")
    if card.user_preferences:
        preference_text = _format_preferences(card.user_preferences)
        if preference_text:
            lines.append(f"已应用偏好：{preference_text}")
    if card.evidence:
        lines.append("证据定位：")
        lines.extend(
            f"- 第 {item.chapter_number} 章《{item.chapter_title}》：{item.excerpt}"
            for item in card.evidence
        )
    if card.suggestions:
        lines.append("你接下来还可以问：")
        lines.extend(f"- {item}" for item in card.suggestions)
    return "\n".join(lines)


def to_payload(card: MemoryCard) -> dict[str, object]:
    return {
        "question": card.question,
        "intent": card.intent,
        "answer": card.answer,
        "progress_chapter": card.progress_chapter,
        "spoiler_blocked": card.spoiler_blocked,
        "entity_name": card.entity_name,
        "summary": card.summary,
        "evidence": [
            {
                "chapter_number": item.chapter_number,
                "chapter_title": item.chapter_title,
                "excerpt": item.excerpt,
                "reason": item.reason,
            }
            for item in card.evidence
        ],
        "suggestions": card.suggestions,
        "user_preferences": card.user_preferences,
    }


def render_json(card: MemoryCard) -> str:
    return json.dumps(to_payload(card), ensure_ascii=False, indent=2)


def _format_preferences(preferences: dict[str, object]) -> str:
    parts: list[str] = []
    style = str(preferences.get("answer_style") or "").strip()
    focus = str(preferences.get("focus") or "").strip()
    custom = str(preferences.get("custom_prompt") or "").strip()
    if style:
        parts.append(f"风格={style}")
    if focus:
        parts.append(f"关注={focus}")
    if custom:
        parts.append(f"自定义={custom[:40]}")
    return "；".join(parts)
