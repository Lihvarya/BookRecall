"""工具注册表与默认工具实现。

每个工具：
- 有 schema（入参/出参/是否进度保护）
- `run(state, args) -> dict` 返回结构化观察
- 复用 storage/retrieval 既有方法，不重写检索逻辑
- 证据沉淀由 core 统一剪枝，工具只在返回里带 evidence 字段
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..retrieval import Retriever
from ..storage import BookRecallStore
from .state import AgentState


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict = field(default_factory=dict)  # {"entity": {"type": "str", "required": True, "desc": "..."}}
    returns: dict = field(default_factory=dict)
    progress_protected: bool = True


@dataclass
class Tool:
    schema: ToolSchema
    run: Callable[[AgentState, dict], dict]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.schema.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def describe_for_llm(self) -> list[dict]:
        """转成 OpenAI function-calling 兼容的 JSON 清单。"""
        items: list[dict] = []
        for tool in self._tools.values():
            s = tool.schema
            items.append(
                {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.parameters,
                    "returns": s.returns,
                    "progress_protected": s.progress_protected,
                }
            )
        return items

    def describe_for_openai_tools(self) -> list[dict]:
        tools: list[dict] = []
        for tool in self._tools.values():
            schema = tool.schema
            properties: dict[str, dict[str, object]] = {}
            required: list[str] = []
            for name, meta in schema.parameters.items():
                type_name = str(meta.get("type", "str"))
                json_type = {
                    "str": "string",
                    "int": "integer",
                    "float": "number",
                    "bool": "boolean",
                    "list": "array",
                    "dict": "object",
                }.get(type_name, "string")
                prop: dict[str, object] = {"type": json_type}
                desc = meta.get("desc")
                if desc:
                    prop["description"] = str(desc)
                properties[name] = prop
                if bool(meta.get("required")):
                    required.append(name)
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": schema.name,
                        "description": schema.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return tools


# ---------- helpers ----------

def _normalize_max_chapter(state: AgentState, given: int | None) -> int:
    if given is None:
        return state.progress_chapter
    return min(int(given), state.progress_chapter)


def _chapter_title(store: BookRecallStore, book_id: str, chapter_number: int) -> str:
    rows = store.get_chapter_summaries(book_id, max_chapter=chapter_number)
    for row in rows:
        if int(row["chapter_number"]) == chapter_number:
            return str(row["chapter_title"])
    return f"第 {chapter_number} 章"


# ---------- 工具实现 ----------

def _tool_lookup_first_appearance(store: BookRecallStore) -> Tool:
    def run(state: AgentState, args: dict) -> dict:
        entity = str(args.get("entity", "")).strip()
        if not entity:
            return {"found": False, "spoiler_blocked": False}
        row = store.get_entity(state.book_id, entity)
        if row is None:
            return {"found": False, "entity_name": entity, "spoiler_blocked": False}
        first_chapter = int(row["first_chapter_number"])
        if first_chapter > state.progress_chapter:
            return {
                "found": True,
                "entity_name": str(row["name"]),
                "first_chapter_number": first_chapter,
                "spoiler_blocked": True,
            }
        mentions = store.get_entity_mentions(state.book_id, str(row["name"]), max_chapter=state.progress_chapter)
        excerpt = str(mentions[0]["excerpt"]) if mentions else ""
        return {
            "found": True,
            "entity_name": str(row["name"]),
            "first_chapter_number": first_chapter,
            "chapter_title": _chapter_title(store, state.book_id, first_chapter),
            "excerpt": excerpt,
            "spoiler_blocked": False,
        }

    return Tool(
        schema=ToolSchema(
            name="lookup_first_appearance",
            description="查找某实体首次出现的章节、标题与正文摘录。命中超出阅读进度时返回 spoiler_blocked。",
            parameters={
                "entity": {"type": "str", "required": True, "desc": "实体名（建议传解析后的规范名）"},
            },
            returns={
                "found": "bool",
                "entity_name": "str",
                "first_chapter_number": "int|null",
                "chapter_title": "str",
                "excerpt": "str",
                "spoiler_blocked": "bool",
            },
        ),
        run=run,
    )


def _tool_lookup_timeline(store: BookRecallStore) -> Tool:
    def run(state: AgentState, args: dict) -> dict:
        entity = str(args.get("entity", "")).strip()
        if not entity:
            return {"chapters": [], "fragments": [], "count": 0, "spoiler_blocked": False}
        progress = _normalize_max_chapter(state, args.get("max_chapter"))
        mentions = store.get_entity_mentions(state.book_id, entity, max_chapter=progress)
        if not mentions:
            return {
                "chapters": [],
                "fragments": [],
                "count": 0,
                "spoiler_blocked": False,
                "note": f"截至第 {progress} 节没有检索到该实体",
            }
        chapters: list[int] = []
        fragments: list[dict] = []
        cap = 3  # 与旧版 timeline 一致：证据片段最多 3 条
        for m in mentions:
            chapter_number = int(m["chapter_number"])
            if chapter_number not in chapters:
                chapters.append(chapter_number)
            if len(fragments) < cap:
                fragments.append(
                    {
                        "chapter_number": chapter_number,
                        "excerpt": str(m["excerpt"]),
                        "chapter_title": _chapter_title(store, state.book_id, chapter_number),
                    }
                )
        return {
            "chapters": chapters,
            "fragments": fragments,
            "count": len(chapters),
            "spoiler_blocked": False,
        }

    return Tool(
        schema=ToolSchema(
            name="lookup_timeline",
            description="查找某实体在阅读进度范围内的出现章节轨迹与(最多3条)正文片段。",
            parameters={
                "entity": {"type": "str", "required": True},
                "max_chapter": {"type": "int", "required": False, "desc": "不传则用阅读进度"},
            },
            returns={
                "chapters": "list[int]",
                "fragments": "list[{chapter_number,excerpt,chapter_title}]",
                "count": "int",
                "spoiler_blocked": "bool",
            },
        ),
        run=run,
    )


def _tool_search_evidence(retriever: LocalRetriever) -> Tool:
    def run(state: AgentState, args: dict) -> dict:
        query = str(args.get("query", "")).strip()
        if not query:
            return {"hits": [], "count": 0, "spoiler_blocked": False}
        progress = _normalize_max_chapter(state, args.get("max_chapter"))
        hits = retriever.search(state.book_id, query, max_chapter=progress)
        raw: list[dict] = []
        for hit in hits:
            raw.append(
                {
                    "chapter_number": hit.chapter_number,
                    "chapter_title": hit.chapter_title,
                    "child_text": hit.child_text,
                    "parent_id": hit.parent_id,
                    "score": hit.score,
                }
            )
        return {"hits": raw, "count": len(raw), "spoiler_blocked": False}

    return Tool(
        schema=ToolSchema(
            name="search_evidence",
            description="对问题做语义检索，返回阅读进度范围内最相关的正文片段(child chunks)。",
            parameters={
                "query": {"type": "str", "required": True},
                "max_chapter": {"type": "int", "required": False},
            },
            returns={
                "hits": "list[{chapter_number,chapter_title,child_text,score}]",
                "count": "int",
            },
        ),
        run=run,
    )


def _tool_lookup_relations(store: BookRecallStore, retriever: Retriever) -> Tool:
    def run(state: AgentState, args: dict) -> dict:
        source = str(args.get("source_entity", "")).strip()
        target = str(args.get("target_entity", "")).strip()
        progress = _normalize_max_chapter(state, args.get("max_chapter"))
        if not source:
            return {"found": False, "relations": [], "count": 0, "spoiler_blocked": False}

        if target:
            rows = store.get_relation_mentions(state.book_id, source, target, max_chapter=progress)
            relations = _relation_mentions_to_payload(rows)
            if not relations:
                relations = _relation_search_fallback(store, retriever, state.book_id, source, target, progress)
        else:
            rows = store.list_relations_for_entity(state.book_id, source, max_chapter=progress)
            relations = [
                {
                    "source_entity": str(row["source_entity"]),
                    "target_entity": str(row["target_entity"]),
                    "relation_type": str(row["relation_type"]),
                    "first_chapter_number": int(row["first_chapter_number"]),
                    "mention_count": int(row["mention_count"]),
                    "fragments": [],
                    "stages": [],
                    "evolution_summary": "",
                }
                for row in rows
            ]
        return {
            "found": bool(relations),
            "relations": relations,
            "count": len(relations),
            "spoiler_blocked": False,
        }

    return Tool(
        schema=ToolSchema(
            name="lookup_relations",
            description="查询两个实体之间的结构化关系，或列出某个实体的相关实体。",
            parameters={
                "source_entity": {"type": "str", "required": True},
                "target_entity": {"type": "str", "required": False},
                "max_chapter": {"type": "int", "required": False},
            },
            returns={
                "found": "bool",
                "relations": "list[{source_entity,target_entity,relation_type,first_chapter_number,fragments,stages,evolution_summary}]",
                "count": "int",
                "spoiler_blocked": "bool",
            },
        ),
        run=run,
    )


def _relation_search_fallback(
    store: BookRecallStore,
    retriever: Retriever,
    book_id: str,
    source: str,
    target: str,
    progress: int,
) -> list[dict]:
    hits = retriever.search(book_id, f"{source} {target} 关系 互动", max_chapter=progress)
    fragments: list[dict] = []
    for hit in hits:
        text = hit.child_text or hit.parent_text
        if source not in text or target not in text:
            continue
        fragments.append(
            {
                "chapter_number": hit.chapter_number,
                "excerpt": text,
            }
        )
        if len(fragments) >= 3:
            break
    if not fragments:
        source_mentions = store.get_entity_mentions(book_id, source, max_chapter=progress)
        target_mentions = store.get_entity_mentions(book_id, target, max_chapter=progress)
        for left in source_mentions:
            for right in target_mentions:
                if int(left["chapter_number"]) != int(right["chapter_number"]):
                    continue
                if abs(int(left["position_in_chapter"]) - int(right["position_in_chapter"])) > 220:
                    continue
                excerpt = str(left["excerpt"])
                if target not in excerpt:
                    excerpt = str(right["excerpt"])
                fragments.append(
                    {
                        "chapter_number": int(left["chapter_number"]),
                        "excerpt": excerpt,
                    }
                )
                break
            if len(fragments) >= 3:
                break
        if not fragments:
            return []
    return [
        {
            "source_entity": source,
            "target_entity": target,
            "relation_type": "检索证据/待确认",
            "first_chapter_number": min(int(item["chapter_number"]) for item in fragments),
            "mention_count": len(fragments),
            "fragments": fragments,
            "stages": _relation_stages(fragments),
            "evolution_summary": (
                f"结构化关系索引尚未确认 {source} 与 {target} 的关系；"
                "以下是召回层找到的同场证据，建议重建智能索引后再固定进图谱。"
            ),
        }
    ]


def _relation_mentions_to_payload(rows) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = (str(row["source_entity"]), str(row["target_entity"]), str(row["relation_type"]))
        relation = grouped.setdefault(
            key,
            {
                "source_entity": key[0],
                "target_entity": key[1],
                "relation_type": key[2],
                "first_chapter_number": int(row["first_chapter_number"]),
                "fragments": [],
                "_all_fragments": [],
            },
        )
        fragment = {
            "chapter_number": int(row["chapter_number"]),
            "excerpt": str(row["excerpt"]),
        }
        relation["_all_fragments"].append(fragment)
        if len(relation["fragments"]) < 3:
            relation["fragments"].append(fragment)
    payload: list[dict] = []
    for relation in grouped.values():
        all_fragments = relation.pop("_all_fragments")
        relation["stages"] = _relation_stages(all_fragments)
        relation["evolution_summary"] = _relation_evolution_summary(
            str(relation["source_entity"]),
            str(relation["target_entity"]),
            str(relation["relation_type"]),
            all_fragments,
        )
        payload.append(relation)
    return payload


def _relation_stages(fragments: list[dict]) -> list[dict]:
    if not fragments:
        return []
    ordered = sorted(fragments, key=lambda item: (int(item["chapter_number"]), str(item["excerpt"])))
    if len(ordered) == 1:
        return [_relation_stage_payload("关系起点", ordered)]
    if len(ordered) == 2:
        return [
            _relation_stage_payload("关系起点", ordered[:1]),
            _relation_stage_payload("近期互动", ordered[1:]),
        ]
    first_end = max(1, len(ordered) // 3)
    middle_end = max(first_end + 1, (len(ordered) * 2) // 3)
    buckets = [
        ("关系起点", ordered[:first_end]),
        ("互动推进", ordered[first_end:middle_end]),
        ("近期状态", ordered[middle_end:]),
    ]
    return [_relation_stage_payload(label, items) for label, items in buckets if items]


def _relation_stage_payload(label: str, items: list[dict]) -> dict:
    chapters = [int(item["chapter_number"]) for item in items]
    first = str(items[0].get("excerpt", "")).strip()
    last = str(items[-1].get("excerpt", "")).strip()
    if first and last and first != last:
        summary = f"从“{first[:42]}”推进到“{last[:42]}”"
    else:
        summary = f"集中体现为“{first[:72]}”"
    return {
        "label": label,
        "chapter_start": min(chapters),
        "chapter_end": max(chapters),
        "summary": summary,
        "excerpt_count": len(items),
    }


def _relation_evolution_summary(
    source: str,
    target: str,
    relation_type: str,
    fragments: list[dict],
) -> str:
    if not fragments:
        return ""
    ordered = sorted(fragments, key=lambda item: (int(item["chapter_number"]), str(item["excerpt"])))
    first = str(ordered[0].get("excerpt", "")).strip()
    last = str(ordered[-1].get("excerpt", "")).strip()
    if len(ordered) == 1 or first == last:
        return f"{source} 与 {target} 的“{relation_type}”关系目前只有一个清晰节点：“{first[:80]}”。"
    return f"{source} 与 {target} 的“{relation_type}”关系从“{first[:48]}”推进到“{last[:48]}”。"


def _tool_search_theme(store: BookRecallStore) -> Tool:
    def run(state: AgentState, args: dict) -> dict:
        theme = str(args.get("theme", "")).strip()
        progress = _normalize_max_chapter(state, args.get("max_chapter"))
        if not theme:
            return {
                "found": False,
                "theme_name": "",
                "chapters": [],
                "fragments": [],
                "count": 0,
                "spoiler_blocked": False,
            }
        rows = store.get_theme_mentions(state.book_id, theme, max_chapter=progress)
        fragments: list[dict] = []
        chapters: list[int] = []
        all_fragments: list[dict] = []
        theme_name = theme
        for row in rows:
            theme_name = str(row["name"])
            chapter_number = int(row["chapter_number"])
            if chapter_number not in chapters:
                chapters.append(chapter_number)
            all_fragments.append(
                {
                    "chapter_number": chapter_number,
                    "chapter_title": _chapter_title(store, state.book_id, chapter_number),
                    "excerpt": str(row["excerpt"]),
                }
            )
            if len(fragments) < 5:
                fragments.append(
                    {
                        "chapter_number": chapter_number,
                        "chapter_title": _chapter_title(store, state.book_id, chapter_number),
                        "excerpt": str(row["excerpt"]),
                    }
                )
        return {
            "found": bool(rows),
            "theme_name": theme_name,
            "chapters": chapters,
            "fragments": fragments,
            "stages": _theme_stages(all_fragments),
            "evolution_summary": _theme_evolution_summary(theme_name, all_fragments),
            "count": len(rows),
            "spoiler_blocked": False,
        }

    return Tool(
        schema=ToolSchema(
            name="search_theme",
            description="查询某个主题/观点在已读范围内的出现章节和证据片段，适合回答主题演化、观点变化类问题。",
            parameters={
                "theme": {"type": "str", "required": True},
                "max_chapter": {"type": "int", "required": False},
            },
            returns={
                "found": "bool",
                "theme_name": "str",
                "chapters": "list[int]",
                "fragments": "list[{chapter_number,chapter_title,excerpt}]",
                "stages": "list[{label,chapter_start,chapter_end,summary,excerpt_count}]",
                "evolution_summary": "str",
                "count": "int",
                "spoiler_blocked": "bool",
            },
        ),
        run=run,
    )


def _tool_search_events(store: BookRecallStore) -> Tool:
    def run(state: AgentState, args: dict) -> dict:
        query = str(args.get("query", state.question)).strip()
        entity = str(args.get("entity", "")).strip()
        progress = _normalize_max_chapter(state, args.get("max_chapter"))
        rows = store.search_events(
            state.book_id,
            query_text=query,
            entity_name=entity or None,
            max_chapter=progress,
            limit=8,
        )
        events = [
            {
                "chapter_number": int(row["chapter_number"]),
                "chapter_title": str(row["chapter_title"]),
                "event_type": str(row["event_type"]),
                "summary": str(row["summary"]),
                "excerpt": str(row["excerpt"]),
                "entities": [item for item in str(row["entities"] or "").split("、") if item],
            }
            for row in rows
        ]
        return {
            "found": bool(events),
            "events": events,
            "chain_summary": _event_chain_summary(events),
            "count": len(events),
            "spoiler_blocked": False,
        }

    return Tool(
        schema=ToolSchema(
            name="search_events",
            description="查询已读范围内的结构化事件链，适合回答关键事件、主线发展、因果回忆类问题。",
            parameters={
                "query": {"type": "str", "required": False},
                "entity": {"type": "str", "required": False},
                "max_chapter": {"type": "int", "required": False},
            },
            returns={
                "found": "bool",
                "events": "list[{chapter_number,chapter_title,event_type,summary,excerpt,entities}]",
                "chain_summary": "str",
                "count": "int",
                "spoiler_blocked": "bool",
            },
        ),
        run=run,
    )


def _event_chain_summary(events: list[dict]) -> str:
    if not events:
        return ""
    if len(events) == 1:
        event = events[0]
        return f"第 {event['chapter_number']} 章的关键事件是：{event['summary']}"
    first = events[0]
    last = events[-1]
    return (
        f"事件链从第 {first['chapter_number']} 章“{first['summary'][:42]}”"
        f"推进到第 {last['chapter_number']} 章“{last['summary'][:42]}”。"
    )


def _theme_stages(fragments: list[dict]) -> list[dict]:
    if not fragments:
        return []
    ordered = sorted(fragments, key=lambda item: (int(item["chapter_number"]), str(item["excerpt"])))
    if len(ordered) == 1:
        return [_theme_stage_payload("线索起点", ordered)]
    if len(ordered) == 2:
        return [
            _theme_stage_payload("线索起点", ordered[:1]),
            _theme_stage_payload("近期变化", ordered[1:]),
        ]

    first_end = max(1, len(ordered) // 3)
    middle_end = max(first_end + 1, (len(ordered) * 2) // 3)
    buckets = [
        ("线索起点", ordered[:first_end]),
        ("发展推进", ordered[first_end:middle_end]),
        ("近期变化", ordered[middle_end:]),
    ]
    return [_theme_stage_payload(label, items) for label, items in buckets if items]


def _theme_stage_payload(label: str, items: list[dict]) -> dict:
    chapters = [int(item["chapter_number"]) for item in items]
    first = str(items[0].get("excerpt", "")).strip()
    last = str(items[-1].get("excerpt", "")).strip()
    if first and last and first != last:
        summary = f"从“{first[:42]}”推进到“{last[:42]}”"
    else:
        summary = f"集中体现为“{first[:72]}”"
    return {
        "label": label,
        "chapter_start": min(chapters),
        "chapter_end": max(chapters),
        "summary": summary,
        "excerpt_count": len(items),
    }


def _theme_evolution_summary(theme_name: str, fragments: list[dict]) -> str:
    if not fragments:
        return ""
    ordered = sorted(fragments, key=lambda item: (int(item["chapter_number"]), str(item["excerpt"])))
    first = str(ordered[0].get("excerpt", "")).strip()
    last = str(ordered[-1].get("excerpt", "")).strip()
    if len(ordered) == 1 or first == last:
        return f"主题“{theme_name}”目前只有一个清晰线索节点，核心片段是“{first[:80]}”。"
    return f"主题“{theme_name}”从“{first[:48]}”逐步推进到“{last[:48]}”。"


def _tool_lookup_entity_aliases(store: BookRecallStore) -> Tool:
    def run(state: AgentState, args: dict) -> dict:
        entity = str(args.get("entity", "")).strip()
        if not entity:
            return {"found": False, "canonical_name": None, "aliases": []}
        canonical = store.resolve_entity_name(state.book_id, entity)
        if canonical is None:
            return {"found": False, "canonical_name": None, "aliases": []}
        # 取该规范实体的别名集合
        aliases: list[str] = []
        for row in store.list_entities_with_aliases(state.book_id):
            if str(row["name"]) == canonical:
                blob = str(row["aliases"]) if row["aliases"] else ""
                aliases = [item for item in blob.replace("、", ",").split(",") if item.strip()]
                break
        return {"found": True, "canonical_name": canonical, "aliases": aliases}

    return Tool(
        schema=ToolSchema(
            name="lookup_entity_aliases",
            description="把用户给出的实体名或别名解析为规范名，并返回该实体的别名清单。",
            parameters={"entity": {"type": "str", "required": True}},
            returns={
                "found": "bool",
                "canonical_name": "str|null",
                "aliases": "list[str]",
            },
        ),
        run=run,
    )


def _tool_get_chapter_summary(store: BookRecallStore) -> Tool:
    def run(state: AgentState, args: dict) -> dict:
        chapter = int(args.get("chapter", 0))
        if chapter <= 0:
            return {"found": False, "spoiler_blocked": False}
        if chapter > state.progress_chapter:
            return {
                "found": True,
                "chapter_number": chapter,
                "spoiler_blocked": True,
                "chapter_title": "",
                "summary": "",
            }
        rows = store.get_chapter_summaries(state.book_id, max_chapter=chapter)
        for row in rows:
            if int(row["chapter_number"]) == chapter:
                return {
                    "found": True,
                    "chapter_number": chapter,
                    "chapter_title": str(row["chapter_title"]),
                    "summary": str(row["summary"]),
                    "spoiler_blocked": False,
                }
        return {"found": False, "spoiler_blocked": False}

    return Tool(
        schema=ToolSchema(
            name="get_chapter_summary",
            description="取某个章节的轻量摘要。章节超出阅读进度时返回 spoiler_blocked。",
            parameters={"chapter": {"type": "int", "required": True}},
            returns={
                "found": "bool",
                "chapter_number": "int",
                "chapter_title": "str",
                "summary": "str",
                "spoiler_blocked": "bool",
            },
        ),
        run=run,
    )


def _tool_list_entities(store: BookRecallStore) -> Tool:
    def run(state: AgentState, _args: dict) -> dict:
        names = store.list_entities(state.book_id)
        return {"entities": names, "count": len(names)}

    return Tool(
        schema=ToolSchema(
            name="list_entities",
            description="列出本书已建索引的全部实体名。",
            parameters={},
            returns={"entities": "list[str]", "count": "int"},
        ),
        run=run,
    )


def build_default_registry(store: BookRecallStore, retriever: Retriever) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_tool_lookup_first_appearance(store))
    registry.register(_tool_lookup_timeline(store))
    registry.register(_tool_lookup_relations(store, retriever))
    registry.register(_tool_search_theme(store))
    registry.register(_tool_search_events(store))
    registry.register(_tool_search_evidence(retriever))
    registry.register(_tool_lookup_entity_aliases(store))
    registry.register(_tool_get_chapter_summary(store))
    registry.register(_tool_list_entities(store))
    return registry
