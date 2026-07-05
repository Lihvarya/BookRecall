"""LLM-assisted entity, relation and event indexing.

The smart indexer never trusts model output blindly.  It first builds narrow
candidate windows from local text, asks a local LLM for structured JSON, then
validates names, evidence and confidence before records are written.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Protocol

from .config import ChunkSettings
from .models import Chapter, EntityRecord, EventRecord, RelationMention, RelationRecord


class JsonCompleter(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        ...


RELATION_TYPES = {
    "冲突",
    "同伴/协作",
    "师徒/传承",
    "亲缘/家族",
    "隶属/组织",
    "交易/利用",
    "因果/线索",
    "共现/关联",
}

EVENT_TYPES = {
    "获得/失去",
    "冲突/危机",
    "揭示/真相",
    "选择/决定",
    "协作/同行",
    "身份/关系变化",
    "转折/后果",
    "事件",
}

ENTITY_TYPES = {"人物", "组织", "地点", "物品", "功法", "概念", "种族", "其他"}

_BAD_ENTITY_WORDS = {
    "一个", "一些", "一下", "一声", "一切", "一定", "一直", "之前", "之后", "以后", "以前",
    "这里", "那里", "这个", "那个", "这些", "那些", "自己", "别人", "什么", "怎么", "为何",
    "因为", "所以", "但是", "不过", "只是", "如果", "虽然", "然后", "于是", "还是", "还有",
    "没有", "就是", "不是", "都是", "已经", "正在", "突然", "时间", "心中", "手段", "他的",
    "她的", "它的", "他们", "我们", "你们", "之中", "之间", "之内", "之外", "的时候",
}


def discover_entities_with_llm(
    chapters: list[Chapter],
    client: JsonCompleter,
    *,
    seed_entities: dict[str, list[str]] | None = None,
    max_chapters: int = 0,
    min_confidence: float = 0.62,
) -> dict[str, list[str]]:
    """Use a local LLM as an entity reviewer and expander.

    Seed entities are always preserved.  Model candidates must pass a strict
    lexical filter and confidence threshold before being added.
    """
    entities: dict[str, list[str]] = {
        name.strip(): [alias.strip() for alias in aliases if alias.strip()]
        for name, aliases in (seed_entities or {}).items()
        if _valid_entity_name(name)
    }
    for chapter in _limited_chapters(chapters, max_chapters):
        payload = client.complete_json(_entity_prompt(chapter))
        for item in _as_list(payload.get("entities")):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            confidence = _float(item.get("confidence"), 0.0)
            if confidence < min_confidence or not _valid_entity_name(name):
                continue
            entity_type = str(item.get("type", "其他")).strip()
            if entity_type not in ENTITY_TYPES:
                entity_type = "其他"
            aliases = [
                str(alias).strip()
                for alias in _as_list(item.get("aliases"))
                if _valid_alias(str(alias), name)
            ]
            current = entities.setdefault(name, [])
            for alias in aliases:
                if alias not in current:
                    current.append(alias)
    return dict(sorted(entities.items(), key=lambda item: (-len(item[0]), item[0])))


def build_smart_relation_event_records(
    chapters: list[Chapter],
    entity_records: list[EntityRecord],
    settings: ChunkSettings,
    client: JsonCompleter,
    *,
    max_chapters: int = 0,
    min_confidence: float = 0.58,
) -> tuple[list[RelationRecord], list[EventRecord]]:
    entity_names = [record.name for record in entity_records]
    entity_lookup = {name: name for name in entity_names}
    relation_mentions: dict[tuple[str, str, str], list[RelationMention]] = defaultdict(list)
    event_records: list[EventRecord] = []
    seen_events: set[tuple[int, str]] = set()

    for chapter in _limited_chapters(chapters, max_chapters):
        present = [name for name in entity_names if name in chapter.content]
        if not present:
            continue
        context = _chapter_context(chapter.content, present, settings.max_excerpt_chars * 8)
        if not context:
            continue
        payload = client.complete_json(_relation_event_prompt(chapter, context, present))

        for item in _as_list(payload.get("relations")):
            parsed = _parse_relation_item(item, entity_lookup, min_confidence)
            if parsed is None:
                continue
            source, target, relation_type, evidence = parsed
            evidence = _safe_evidence(chapter.content, evidence, [source, target], settings.max_excerpt_chars)
            if not evidence:
                continue
            ordered_source, ordered_target = sorted((source, target))
            relation_mentions[(ordered_source, ordered_target, relation_type)].append(
                RelationMention(
                    source_entity=ordered_source,
                    target_entity=ordered_target,
                    relation_type=relation_type,
                    chapter_number=chapter.number,
                    excerpt=evidence,
                )
            )

        for item in _as_list(payload.get("events")):
            parsed_event = _parse_event_item(item, entity_lookup, min_confidence)
            if parsed_event is None:
                continue
            event_type, summary, evidence, entities = parsed_event
            evidence = _safe_evidence(chapter.content, evidence or summary, entities, settings.max_excerpt_chars)
            if not evidence:
                continue
            key = (chapter.number, evidence)
            if key in seen_events:
                continue
            seen_events.add(key)
            event_records.append(
                EventRecord(
                    chapter_number=chapter.number,
                    chapter_title=chapter.title,
                    event_type=event_type,
                    summary=_trim(summary or evidence, 72),
                    excerpt=evidence,
                    entities=entities,
                )
            )

    relation_records: list[RelationRecord] = []
    for (source, target, relation_type), mentions in sorted(relation_mentions.items()):
        mentions.sort(key=lambda item: (item.chapter_number, item.excerpt))
        relation_records.append(
            RelationRecord(
                source_entity=source,
                target_entity=target,
                relation_type=relation_type,
                first_chapter_number=mentions[0].chapter_number,
                mentions=mentions,
            )
        )
    event_records.sort(key=lambda item: (item.chapter_number, item.summary))
    return relation_records, event_records


def _entity_prompt(chapter: Chapter) -> str:
    content = _trim(" ".join(chapter.content.split()), 3200)
    return f"""
请从下面章节中识别真正有索引价值的专名实体。

只抽取：人物、组织/门派、地点、重要物品、功法/能力、种族、核心概念。
不要抽取：代词、时间词、普通动词/形容词/副词、泛称、量词、口头禅，例如“就是、没有、他的、时间、之前、心中”。
不要抽取：作者名、作品名、校对版本、章节前言里提到的其他小说，除非它们是正文剧情中的实体。
最多输出 24 个最有价值的实体；如果没有可靠实体，输出空数组。
不要写思考过程，不要解释，不要 markdown。

输出严格 JSON：
{{"entities":[{{"name":"实体名","type":"人物|组织|地点|物品|功法|概念|种族|其他","aliases":["别名"],"evidence":"原文短句","confidence":0.0}}]}}

章节：第 {chapter.number} 章《{chapter.title}》
正文：
{content}
""".strip()


def _relation_event_prompt(chapter: Chapter, context: str, entities: list[str]) -> str:
    return f"""
请基于候选实体和原文证据，抽取有效关系与关键事件。

规则：
1. 关系必须有明确互动或叙事意义，不要因为同章出现就建立关系。
2. 事件必须推动情节、揭示信息、造成选择/冲突/获得/失去/身份变化。
3. source、target、entities 必须来自候选实体列表。
4. evidence 必须尽量复制原文短句。
5. 不确定就不要输出。
6. 最多输出 12 条关系和 12 条事件。
7. 不要写思考过程，不要解释，不要 markdown。

关系类型只能选：
冲突、同伴/协作、师徒/传承、亲缘/家族、隶属/组织、交易/利用、因果/线索、共现/关联

事件类型只能选：
获得/失去、冲突/危机、揭示/真相、选择/决定、协作/同行、身份/关系变化、转折/后果、事件

输出严格 JSON：
{{"relations":[{{"source":"实体A","target":"实体B","type":"冲突","evidence":"原文短句","confidence":0.0}}],
"events":[{{"type":"选择/决定","summary":"不超过40字摘要","evidence":"原文短句","entities":["实体A"],"confidence":0.0}}]}}

章节：第 {chapter.number} 章《{chapter.title}》
候选实体：{"、".join(entities[:80])}
原文证据窗口：
{context}
""".strip()


def _limited_chapters(chapters: list[Chapter], max_chapters: int) -> list[Chapter]:
    if max_chapters and max_chapters > 0:
        return chapters[:max_chapters]
    return chapters


def _chapter_context(content: str, entities: list[str], max_chars: int) -> str:
    sentences = _split_sentences(content)
    selected: list[str] = []
    for sentence in sentences:
        hit_count = sum(1 for name in entities if name in sentence)
        if hit_count >= 2 or (hit_count >= 1 and _looks_eventful(sentence)):
            selected.append(sentence)
        if len(" ".join(selected)) >= max_chars:
            break
    if not selected:
        selected = sentences[:8]
    return _trim(" ".join(selected), max_chars)


def _split_sentences(content: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", content)
    return [" ".join(part.split()).strip() for part in parts if 8 <= len(" ".join(part.split()).strip()) <= 240]


def _looks_eventful(sentence: str) -> bool:
    keywords = (
        "得到", "获得", "拿到", "失去", "夺走", "交出", "打开", "发现", "揭开", "真相",
        "决定", "选择", "拒绝", "承认", "相信", "背叛", "追杀", "对峙", "袭击", "帮助",
        "救", "同行", "合作", "意识到", "明白", "告诉",
    )
    return any(keyword in sentence for keyword in keywords)


def _parse_relation_item(
    item: object,
    entity_lookup: dict[str, str],
    min_confidence: float,
) -> tuple[str, str, str, str] | None:
    if not isinstance(item, dict):
        return None
    source = entity_lookup.get(str(item.get("source", "")).strip())
    target = entity_lookup.get(str(item.get("target", "")).strip())
    if not source or not target or source == target:
        return None
    relation_type = str(item.get("type", "共现/关联")).strip()
    if relation_type not in RELATION_TYPES:
        relation_type = "共现/关联"
    if _float(item.get("confidence"), 0.0) < min_confidence:
        return None
    return source, target, relation_type, str(item.get("evidence", "")).strip()


def _parse_event_item(
    item: object,
    entity_lookup: dict[str, str],
    min_confidence: float,
) -> tuple[str, str, str, list[str]] | None:
    if not isinstance(item, dict):
        return None
    if _float(item.get("confidence"), 0.0) < min_confidence:
        return None
    event_type = str(item.get("type", "事件")).strip()
    if event_type not in EVENT_TYPES:
        event_type = "事件"
    entities: list[str] = []
    for raw in _as_list(item.get("entities")):
        name = entity_lookup.get(str(raw).strip())
        if name and name not in entities:
            entities.append(name)
    if not entities:
        return None
    return (
        event_type,
        str(item.get("summary", "")).strip(),
        str(item.get("evidence", "")).strip(),
        entities,
    )


def _safe_evidence(content: str, evidence: str, entities: list[str], max_chars: int) -> str:
    cleaned = " ".join(evidence.split()).strip()
    if cleaned and cleaned in content:
        return _trim(cleaned, max_chars)
    for sentence in _split_sentences(content):
        if all(entity in sentence for entity in entities[:2]):
            return _trim(sentence, max_chars)
    for sentence in _split_sentences(content):
        if any(entity in sentence for entity in entities):
            return _trim(sentence, max_chars)
    return ""


def _valid_entity_name(name: str) -> bool:
    cleaned = name.strip()
    if not (2 <= len(cleaned) <= 20):
        return False
    if cleaned in _BAD_ENTITY_WORDS:
        return False
    if not any("\u4e00" <= ch <= "\u9fff" for ch in cleaned):
        return False
    if all(ch in "零一二三四五六七八九十百千万两〇0123456789" for ch in cleaned):
        return False
    if len(cleaned) <= 3 and any(ch in cleaned for ch in "的是了在有也就都很还没不"):
        return False
    return True


def _valid_alias(alias: str, canonical: str) -> bool:
    cleaned = alias.strip()
    return cleaned != canonical and _valid_entity_name(cleaned)


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _trim(text: str, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."
