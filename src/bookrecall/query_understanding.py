"""Local-LLM query understanding for BookRecall.

The query understanding layer is intentionally small and defensive: it asks a
local Qwen-style JSON completer for a structured interpretation, then clamps
every field to known intents and tool names before Agent policy sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class JsonCompleter(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        ...


INTENTS = {
    "first_appearance",
    "entity_timeline",
    "relation_lookup",
    "theme_explore",
    "event_chain",
    "compare",
    "causal",
    "semantic_search",
}

TOOLS = {
    "lookup_first_appearance",
    "lookup_timeline",
    "lookup_relations",
    "search_theme",
    "search_events",
    "search_evidence",
    "search_exact_text",
    "lookup_entity_aliases",
    "get_chapter_summary",
    "list_entities",
}

_INTENT_TOOL_HINTS = {
    "first_appearance": ["lookup_entity_aliases", "lookup_first_appearance"],
    "entity_timeline": ["lookup_entity_aliases", "lookup_timeline"],
    "relation_lookup": ["lookup_entity_aliases", "lookup_relations"],
    "theme_explore": ["search_theme", "search_evidence"],
    "event_chain": ["search_events"],
    "compare": ["search_evidence", "get_chapter_summary"],
    "causal": ["search_events", "search_evidence"],
    "semantic_search": ["search_evidence"],
}


@dataclass(slots=True)
class TimeRange:
    start_chapter: int | None = None
    end_chapter: int | None = None
    relative: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "start_chapter": self.start_chapter,
            "end_chapter": self.end_chapter,
            "relative": self.relative,
        }


@dataclass(slots=True)
class QueryUnderstanding:
    intent: str = "semantic_search"
    entities: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    time_range: TimeRange = field(default_factory=TimeRange)
    spoiler_sensitive: bool = True
    tools: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "rules"

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "entities": self.entities,
            "themes": self.themes,
            "time_range": self.time_range.to_dict(),
            "spoiler_sensitive": self.spoiler_sensitive,
            "tools": self.tools,
            "confidence": self.confidence,
            "source": self.source,
        }


def understand_query_with_llm(
    question: str,
    client: JsonCompleter,
    *,
    known_entities: list[str] | None = None,
    known_themes: list[str] | None = None,
    recent_entities: list[str] | None = None,
    progress_chapter: int = 0,
    max_chapter: int = 0,
) -> QueryUnderstanding:
    payload = client.complete_json(
        _prompt(
            question=question,
            known_entities=known_entities or [],
            known_themes=known_themes or [],
            recent_entities=recent_entities or [],
            progress_chapter=progress_chapter,
            max_chapter=max_chapter,
        )
    )
    return parse_query_understanding_payload(payload)


def parse_query_understanding_payload(payload: dict[str, Any]) -> QueryUnderstanding:
    intent = str(payload.get("intent") or "semantic_search").strip()
    if intent not in INTENTS:
        intent = "semantic_search"

    entities = _clean_names(payload.get("entities"))
    themes = _clean_names(payload.get("themes"))
    tools = [tool for tool in _clean_names(payload.get("tools"), max_len=40) if tool in TOOLS]
    if not tools:
        tools = list(_INTENT_TOOL_HINTS[intent])

    time_range = payload.get("time_range")
    if not isinstance(time_range, dict):
        time_range = {}

    confidence = _float(payload.get("confidence"), 0.0)
    return QueryUnderstanding(
        intent=intent,
        entities=entities,
        themes=themes,
        time_range=TimeRange(
            start_chapter=_optional_int(time_range.get("start_chapter")),
            end_chapter=_optional_int(time_range.get("end_chapter")),
            relative=str(time_range.get("relative") or "").strip()[:30],
        ),
        spoiler_sensitive=bool(payload.get("spoiler_sensitive", True)),
        tools=tools,
        confidence=max(0.0, min(1.0, confidence)),
        source="local_llm",
    )


def understand_query_with_rules(
    question: str,
    *,
    matched_entities: list[str] | None = None,
    matched_themes: list[str] | None = None,
) -> QueryUnderstanding:
    entities = matched_entities or []
    themes = matched_themes or []
    intent = _rule_intent(question, entities, themes)
    return QueryUnderstanding(
        intent=intent,
        entities=list(entities),
        themes=list(themes),
        time_range=_rule_time_range(question),
        spoiler_sensitive=not any(keyword in question for keyword in ("可以剧透", "不用防剧透", "剧透也行")),
        tools=list(_INTENT_TOOL_HINTS[intent]),
        confidence=0.45,
        source="rules",
    )


def _rule_intent(question: str, entities: list[str], themes: list[str]) -> str:
    if themes and any(keyword in question for keyword in ("主题", "观点", "变化", "前后", "线索", "含义", "意义")):
        return "theme_explore"
    if themes and not entities:
        return "theme_explore"
    if entities and any(keyword in question for keyword in ("关系", "之间", "和", "与", "有关", "相关")):
        return "relation_lookup"
    if any(keyword in question for keyword in ("关键事件", "事件链", "主线", "发生了什么", "涉及哪些事件")):
        return "event_chain"
    if entities and any(keyword in question for keyword in ("第一次", "首次", "最早", "初次")):
        return "first_appearance"
    if entities and any(keyword in question for keyword in ("还有出现", "后来", "后面", "后续", "再次", "轨迹", "出现过吗")):
        return "entity_timeline"
    if any(keyword in question for keyword in ("变化", "对比", "前后")):
        return "compare"
    if any(keyword in question for keyword in ("怎么", "如何", "为什么", "原因")):
        return "causal"
    return "semantic_search"


def _rule_time_range(question: str) -> TimeRange:
    chapter_numbers = [int(item) for item in re.findall(r"第\s*(\d+)\s*章", question)]
    if len(chapter_numbers) >= 2:
        return TimeRange(start_chapter=min(chapter_numbers), end_chapter=max(chapter_numbers), relative="explicit")
    if len(chapter_numbers) == 1:
        return TimeRange(start_chapter=chapter_numbers[0], end_chapter=chapter_numbers[0], relative="explicit")
    if any(keyword in question for keyword in ("后来", "之后", "后面", "再")):
        return TimeRange(relative="after")
    if any(keyword in question for keyword in ("之前", "前面", "最早", "第一次", "首次")):
        return TimeRange(relative="before")
    return TimeRange(relative="")


def _prompt(
    *,
    question: str,
    known_entities: list[str],
    known_themes: list[str],
    recent_entities: list[str],
    progress_chapter: int,
    max_chapter: int,
) -> str:
    entity_hint = "、".join(known_entities[:120]) or "无"
    theme_hint = "、".join(known_themes[:60]) or "无"
    recent_hint = "、".join(recent_entities[:8]) or "无"
    return f"""
你是 BookRecall 的中文长篇阅读问题理解器。请把用户问题解析成结构化 JSON。

可选 intent 只能是：
first_appearance, entity_timeline, relation_lookup, theme_explore, event_chain, compare, causal, semantic_search

可选 tools 只能是：
lookup_first_appearance, lookup_timeline, lookup_relations, search_theme, search_events, search_evidence, search_exact_text, lookup_entity_aliases, get_chapter_summary, list_entities

规则：
1. entities 必须优先使用已知实体或最近会话实体；不要编造实体。
2. 如果问题里出现“他/她/它/那个/后来呢/然后呢”等指代，请结合最近会话实体解析。
3. spoiler_sensitive 表示是否需要严格防剧透；默认 true。
4. time_range.start_chapter/end_chapter 不确定时填 null；relative 可填 before/after/current/explicit/empty。
5. 只输出 JSON object，不要解释，不要 markdown，不要思考过程。

已知实体：{entity_hint}
已知主题：{theme_hint}
最近会话实体：{recent_hint}
当前阅读进度：第 {progress_chapter} 章
全书最大章节：第 {max_chapter} 章
用户问题：{question}

输出格式：
{{"intent":"semantic_search","entities":[],"themes":[],"time_range":{{"start_chapter":null,"end_chapter":null,"relative":""}},"spoiler_sensitive":true,"tools":["search_evidence"],"confidence":0.0}}
""".strip()


def _clean_names(value: object, *, max_len: int = 40) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        name = str(item or "").strip()
        if not name or len(name) > max_len:
            continue
        if name not in names:
            names.append(name)
    return names


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
