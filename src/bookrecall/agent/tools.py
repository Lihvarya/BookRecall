"""工具注册表与 6 个工具实现。

每个工具：
- 有 schema（入参/出参/是否进度保护）
- `run(state, args) -> dict` 返回结构化观察
- 复用 storage/retrieval 既有方法，不重写检索逻辑
- 证据沉淀由 core 统一剪枝，工具只在返回里带 evidence 字段
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..retrieval import LocalRetriever
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


def build_default_registry(store: BookRecallStore, retriever: LocalRetriever) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_tool_lookup_first_appearance(store))
    registry.register(_tool_lookup_timeline(store))
    registry.register(_tool_search_evidence(retriever))
    registry.register(_tool_lookup_entity_aliases(store))
    registry.register(_tool_get_chapter_summary(store))
    registry.register(_tool_list_entities(store))
    return registry
