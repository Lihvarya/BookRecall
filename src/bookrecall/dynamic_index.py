"""On-demand Qwen indexing over retrieved evidence.

Two-phase indexing keeps import fast: phase one builds local chunks/vectors,
phase two asks the local LLM to structure only the evidence retrieved for a
real user question.  The output is validated against the retrieved text before
it can be written back into SQLite.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from .models import EntityMention, EntityRecord, EventRecord, RelationMention, RelationRecord
from .smart_index import EVENT_TYPES, RELATION_TYPES


class JsonCompleter(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        ...


def build_dynamic_index_records(
    *,
    question: str,
    hits: list[dict[str, Any]],
    client: JsonCompleter,
    known_entities: list[str] | None = None,
    max_hits: int = 5,
    min_confidence: float = 0.56,
) -> tuple[list[EntityRecord], list[RelationRecord], list[EventRecord], dict[str, object]]:
    selected_hits = _select_hits(hits, max_hits)
    if not selected_hits:
        return [], [], [], {"used": False, "reason": "no_hits"}
    try:
        payload = client.complete_json(_prompt(question, selected_hits, known_entities or []))
    except Exception as exc:  # noqa: BLE001 - dynamic indexing must not break QA
        return [], [], [], {"used": False, "error": str(exc)}

    entities = _parse_entities(payload, selected_hits, min_confidence)
    entity_names = {record.name for record in entities}
    entity_names.update(name for name in (known_entities or []) if name)
    relations = _parse_relations(payload, selected_hits, entity_names, min_confidence)
    events = _parse_events(payload, selected_hits, entity_names, min_confidence)
    return (
        entities,
        relations,
        events,
        {
            "used": True,
            "hits": len(selected_hits),
            "entities": len(entities),
            "relations": len(relations),
            "events": len(events),
        },
    )


def _select_hits(hits: list[dict[str, Any]], max_hits: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for hit in hits:
        text = str(hit.get("child_text") or hit.get("excerpt") or "").strip()
        chapter = int(hit.get("chapter_number") or 0)
        if not text or chapter <= 0:
            continue
        key = (chapter, text[:80])
        if key in seen:
            continue
        seen.add(key)
        selected.append(hit)
        if len(selected) >= max(1, max_hits):
            break
    return selected


def _prompt(question: str, hits: list[dict[str, Any]], known_entities: list[str]) -> str:
    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        chapter = int(hit.get("chapter_number") or 0)
        title = str(hit.get("chapter_title") or "")
        text = str(hit.get("child_text") or hit.get("excerpt") or "")[:900]
        blocks.append(f"[{index}] 第 {chapter} 章《{title}》\n{text}")
    known = "、".join(known_entities[:80]) or "无"
    return f"""
你是 BookRecall 的按需结构化索引器。请只基于候选片段回答，不要使用外部知识。

任务：
1. 抽取与用户问题有关的专名实体、实体关系、关键事件。
2. evidence 必须尽量复制候选片段中的原文短句。
3. 不确定就不要输出；不要因为同段出现就建立关系。
4. 最多输出 8 个实体、6 条关系、6 条事件。
5. 只输出 JSON object，不要解释，不要 markdown，不要思考过程。

用户问题：{question}
已知实体：{known}

候选片段：
{chr(10).join(blocks)}

输出格式：
{{"entities":[{{"name":"实体名","aliases":[],"evidence":"原文短句","confidence":0.0}}],
"relations":[{{"source":"实体A","target":"实体B","type":"冲突","evidence":"原文短句","confidence":0.0}}],
"events":[{{"type":"因果链","summary":"不超过60字摘要","evidence":"原文短句","entities":["实体A"],"confidence":0.0}}]}}
""".strip()


def _parse_entities(payload: dict[str, Any], hits: list[dict[str, Any]], min_confidence: float) -> list[EntityRecord]:
    records: dict[str, EntityRecord] = {}
    for item in _as_list(payload.get("entities")):
        if not isinstance(item, dict) or _float(item.get("confidence"), 0.0) < min_confidence:
            continue
        name = str(item.get("name") or "").strip()
        if not _valid_name(name):
            continue
        evidence, hit = _validated_evidence(item.get("evidence"), hits, [name])
        if not evidence or hit is None:
            continue
        aliases = [str(alias).strip() for alias in _as_list(item.get("aliases")) if _valid_name(str(alias))]
        record = records.setdefault(
            name,
            EntityRecord(name=name, first_chapter_number=int(hit.get("chapter_number") or 0), aliases=[]),
        )
        record.first_chapter_number = min(record.first_chapter_number, int(hit.get("chapter_number") or 0))
        for alias in aliases:
            if alias != name and alias not in record.aliases:
                record.aliases.append(alias)
        if not any(mention.excerpt == evidence for mention in record.mentions):
            record.mentions.append(
                EntityMention(
                    entity_name=name,
                    chapter_number=int(hit.get("chapter_number") or 0),
                    excerpt=evidence,
                    position_in_chapter=0,
                )
            )
    return list(records.values())


def _parse_relations(
    payload: dict[str, Any],
    hits: list[dict[str, Any]],
    entity_names: set[str],
    min_confidence: float,
) -> list[RelationRecord]:
    grouped: dict[tuple[str, str, str], RelationRecord] = {}
    for item in _as_list(payload.get("relations")):
        if not isinstance(item, dict) or _float(item.get("confidence"), 0.0) < min_confidence:
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if source == target or not _valid_dynamic_entity(source, entity_names) or not _valid_dynamic_entity(target, entity_names):
            continue
        relation_type = str(item.get("type") or "共现/关联").strip()
        if relation_type not in RELATION_TYPES:
            relation_type = "共现/关联"
        evidence, hit = _validated_evidence(item.get("evidence"), hits, [source, target])
        if not evidence or hit is None:
            continue
        ordered_source, ordered_target = sorted((source, target))
        key = (ordered_source, ordered_target, relation_type)
        record = grouped.setdefault(
            key,
            RelationRecord(
                source_entity=ordered_source,
                target_entity=ordered_target,
                relation_type=relation_type,
                first_chapter_number=int(hit.get("chapter_number") or 0),
            ),
        )
        record.first_chapter_number = min(record.first_chapter_number, int(hit.get("chapter_number") or 0))
        record.mentions.append(
            RelationMention(
                source_entity=ordered_source,
                target_entity=ordered_target,
                relation_type=relation_type,
                chapter_number=int(hit.get("chapter_number") or 0),
                excerpt=evidence,
            )
        )
    return list(grouped.values())


def _parse_events(
    payload: dict[str, Any],
    hits: list[dict[str, Any]],
    entity_names: set[str],
    min_confidence: float,
) -> list[EventRecord]:
    records: list[EventRecord] = []
    seen: set[tuple[int, str]] = set()
    for item in _as_list(payload.get("events")):
        if not isinstance(item, dict) or _float(item.get("confidence"), 0.0) < min_confidence:
            continue
        event_type = str(item.get("type") or "事件").strip()
        if event_type not in EVENT_TYPES:
            event_type = "事件"
        raw_entities = [str(value).strip() for value in _as_list(item.get("entities"))]
        entities = [name for name in raw_entities if _valid_dynamic_entity(name, entity_names)]
        evidence, hit = _validated_evidence(item.get("evidence"), hits, entities)
        if not evidence or hit is None:
            continue
        key = (int(hit.get("chapter_number") or 0), evidence)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            EventRecord(
                chapter_number=int(hit.get("chapter_number") or 0),
                chapter_title=str(hit.get("chapter_title") or ""),
                event_type=event_type,
                summary=str(item.get("summary") or evidence).strip()[:120],
                excerpt=evidence,
                entities=entities,
            )
        )
    return records


def _validated_evidence(raw: object, hits: list[dict[str, Any]], required_names: list[str]) -> tuple[str, dict[str, Any] | None]:
    evidence = " ".join(str(raw or "").split()).strip()
    required = [name for name in required_names if name]
    for hit in hits:
        text = " ".join(str(hit.get("child_text") or hit.get("excerpt") or "").split())
        if evidence and evidence in text and all(name in evidence or name in text for name in required):
            return evidence[:500], hit
    for hit in hits:
        text = " ".join(str(hit.get("child_text") or hit.get("excerpt") or "").split())
        if required and all(name in text for name in required):
            return text[:500], hit
    return "", None


def _valid_dynamic_entity(name: str, known: set[str]) -> bool:
    return name in known or _valid_name(name)


def _valid_name(name: str) -> bool:
    cleaned = name.strip()
    if not (2 <= len(cleaned) <= 24):
        return False
    if not any("\u4e00" <= char <= "\u9fff" for char in cleaned):
        return False
    if re.search(r"[，。！？、；：,.!?;:\s]", cleaned):
        return False
    return True


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
