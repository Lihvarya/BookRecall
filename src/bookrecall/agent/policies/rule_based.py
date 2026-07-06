"""Deterministic decision policy for BookRecall's local ReAct loop."""

from __future__ import annotations

import re

from ..state import AgentState
from ..tools import ToolRegistry
from .base import Decision, DecisionPolicy, ToolCall


def classify_intent(
    question: str,
    matched_entities: list[str],
    matched_themes: list[str] | None = None,
) -> str:
    matched_themes = matched_themes or []
    relation_keywords = ("关系", "之间", "和", "与", "有关", "相关")
    if matched_themes and any(keyword in question for keyword in ("主题", "观点", "变化", "前后", "线索", "含义", "意义")):
        return "theme_explore"
    if matched_themes and not matched_entities:
        return "theme_explore"
    if matched_entities and any(keyword in question for keyword in relation_keywords):
        return "relation_lookup"
    if any(keyword in question for keyword in ("关键事件", "事件链", "主线", "发生了什么", "涉及哪些事件")):
        return "event_chain"
    if matched_entities and any(keyword in question for keyword in ("第一次", "首次", "最早", "初次")):
        return "first_appearance"
    if matched_entities and any(keyword in question for keyword in ("还有出现", "后来", "后面", "后续", "再次", "轨迹", "出现过吗")):
        return "entity_timeline"
    if any(keyword in question for keyword in ("变化", "对比", "前后")):
        return "compare"
    if any(keyword in question for keyword in ("怎么", "如何", "为什么", "原因")):
        return "causal"
    return "semantic_search"


INTENT_LABELS: dict[str, str] = {
    "first_appearance": "实体首次出现",
    "entity_timeline": "实体轨迹追踪",
    "relation_lookup": "人物关系回忆",
    "theme_explore": "主题线索回忆",
    "event_chain": "事件链回忆",
    "compare": "对比分析",
    "causal": "因果回忆",
    "semantic_search": "语义回忆",
}


class RuleBasedPolicy(DecisionPolicy):
    def __init__(self, reasoner=None) -> None:
        self.reasoner = reasoner

    def name(self) -> str:
        return "rule_based"

    @staticmethod
    def _last_observation(state: AgentState, tool_name: str) -> dict:
        for trace in reversed(state.trace):
            if trace.tool_name == tool_name:
                return trace.observation()
        return {}

    @staticmethod
    def _call(tool_name: str, arguments: dict, thought: str = "") -> Decision:
        return Decision(
            is_terminal=False,
            tool_call=ToolCall(name=tool_name, arguments=arguments, thought=thought),
        )

    def next_action(self, state: AgentState, registry: ToolRegistry) -> Decision:
        if state.step == 0:
            understood_intent = str((state.query_understanding or {}).get("intent") or "")
            if understood_intent and understood_intent != "semantic_search":
                state.intent = understood_intent
            if not state.matched_entities and not state.matched_themes:
                if state.intent not in {"event_chain", "compare", "causal"}:
                    state.intent = classify_intent(state.question, [], [])
                    return self._call("search_evidence", {"query": state.question}, thought="无实体命中，直接语义检索")
                # event/compare/causal can still be useful without a resolved entity.
            if not state.matched_entities and not state.matched_themes and state.intent == "semantic_search":
                return self._call("search_evidence", {"query": state.question}, thought="无实体命中，直接语义检索")
            if not state.matched_entities and state.matched_themes:
                state.intent = understood_intent or classify_intent(state.question, [], state.matched_themes)
            if state.matched_entities and "lookup_entity_aliases" not in state.called_tools:
                return self._call(
                    "lookup_entity_aliases",
                    {"entity": state.primary_entity or state.matched_entities[0]},
                    thought="先解析别名拿到规范实体名",
                )
            state.intent = state.intent if state.intent != "semantic_search" else classify_intent(
                state.question,
                state.matched_entities,
                state.matched_themes,
            )

        intent = state.intent
        if intent == "first_appearance":
            return self._route_first_appearance(state)
        if intent == "entity_timeline":
            return self._route_timeline(state)
        if intent == "relation_lookup":
            return self._route_relation(state)
        if intent == "theme_explore":
            return self._route_theme(state)
        if intent == "event_chain":
            return self._route_events(state, "event_chain")
        if intent == "compare":
            return self._route_compare(state)
        if intent == "causal":
            return self._route_causal(state)
        return self._route_semantic(state)

    def _route_first_appearance(self, state: AgentState) -> Decision:
        if "lookup_first_appearance" not in state.called_tools:
            return self._call("lookup_first_appearance", {"entity": state.primary_entity or state.matched_entities[0]})
        obs = self._last_observation(state, "lookup_first_appearance")
        return self._terminal_first_appearance(state, obs)

    def _terminal_first_appearance(self, state: AgentState, obs: dict) -> Decision:
        entity = obs.get("entity_name") or state.primary_entity or (state.matched_entities[0] if state.matched_entities else "")
        if not obs.get("found"):
            return Decision(
                is_terminal=True,
                answer=f"我在当前索引里还没找到“{entity}”这个实体，建议补充实体词表后重新 build。",
                intent_override="first_appearance",
                entity_name=entity,
                suggestions=[f"{entity} 可能还有哪些别名？", "能否给这本书补一份实体词表？"],
            )
        first = obs.get("first_chapter_number")
        if obs.get("spoiler_blocked") or (first is not None and int(first) > state.progress_chapter):
            return Decision(
                is_terminal=True,
                intent_override="first_appearance",
                answer=f"在你当前已读范围内，“{entity}”还没有出现。",
                summary=f"为了防止剧透，我没有暴露它在第 {state.progress_chapter} 章之后的首次登场位置。",
                suggestions=[f"把阅读进度推进后再问：{entity} 第一次出现在哪一章？"],
            )
        return Decision(
            is_terminal=True,
            intent_override="first_appearance",
            answer=f"“{entity}”第一次出现于第 {int(first)} 章。",
            suggestions=_entity_followups(entity),
        )

    def _route_timeline(self, state: AgentState) -> Decision:
        if "lookup_timeline" not in state.called_tools:
            return self._call("lookup_timeline", {"entity": state.primary_entity or state.matched_entities[0]})
        obs = self._last_observation(state, "lookup_timeline")
        chapters = obs.get("chapters", [])
        wants_detail = any(k in state.question for k in ("怎么", "如何", "拿到", "使用"))
        if wants_detail and chapters and "search_evidence" not in state.called_tools:
            return self._call(
                "search_evidence",
                {"query": f"{state.primary_entity} {state.question}"},
                thought="聚焦实体轨迹做证据检索",
            )
        return self._terminal_timeline(state, obs)

    def _terminal_timeline(self, state: AgentState, obs: dict) -> Decision:
        entity = state.primary_entity or (state.matched_entities[0] if state.matched_entities else "")
        chapters = obs.get("chapters", [])
        if not chapters:
            return Decision(
                is_terminal=True,
                intent_override="entity_timeline",
                answer=f"截至第 {state.progress_chapter} 章，我还没有检索到“{entity}”的出现记录。",
                suggestions=[f"{entity} 第一次出现在哪一章？"],
            )
        chapter_list = "、".join(f"第 {n} 章" for n in chapters)
        return Decision(
            is_terminal=True,
            intent_override="entity_timeline",
            answer=f"“{entity}”在你当前已读范围内出现在：{chapter_list}。",
            summary=f"共追踪到 {len(chapters)} 个章节节点。",
            suggestions=_timeline_followups(entity),
        )

    def _route_relation(self, state: AgentState) -> Decision:
        if not state.matched_entities:
            if "search_evidence" not in state.called_tools:
                return self._call("search_evidence", {"query": state.question})
            return self._terminal_semantic(state, "relation_lookup")
        if "lookup_relations" not in state.called_tools:
            args = {"source_entity": state.matched_entities[0]}
            if len(state.matched_entities) >= 2:
                args["target_entity"] = state.matched_entities[1]
            return self._call(
                "lookup_relations",
                args,
                thought="查询两个实体之间的结构化关系",
            )
        obs = self._last_observation(state, "lookup_relations")
        return self._terminal_relation(state, obs)

    def _terminal_relation(self, state: AgentState, obs: dict) -> Decision:
        source = state.matched_entities[0] if state.matched_entities else ""
        target = state.matched_entities[1] if len(state.matched_entities) > 1 else ""
        relations = obs.get("relations", [])
        if not relations:
            if not target:
                return Decision(
                    is_terminal=True,
                    intent_override="relation_lookup",
                    entity_name=source,
                    answer=f"截至第 {state.progress_chapter} 章，我还没有找到“{source}”的相关实体关系记录。",
                    suggestions=[f"补充与 {source} 同章出现的人物词表后重新 build。", f"{source} 后来还有出现过吗？"],
                )
            return Decision(
                is_terminal=True,
                intent_override="relation_lookup",
                entity_name=source,
                answer=f"截至第 {state.progress_chapter} 章，我还没有找到“{source}”和“{target}”的明确关系记录。",
                suggestions=[f"检索 {source} 和 {target} 同时出现的章节", f"{source} 后来还有出现过吗？"],
            )
        if not target:
            relation_lines = []
            for relation in relations[:5]:
                other = relation.get("target_entity")
                if other == source:
                    other = relation.get("source_entity")
                relation_lines.append(
                    f"{other}（{relation.get('relation_type', '关联')}，最早第 {int(relation.get('first_chapter_number', 0))} 章）"
                )
            return Decision(
                is_terminal=True,
                intent_override="relation_lookup",
                entity_name=source,
                answer=f"截至第 {state.progress_chapter} 章，“{source}”已索引到这些相关实体：" + "、".join(relation_lines) + "。",
                summary=f"关系索引命中 {len(relations)} 个相关实体/关系类型。",
                suggestions=[f"{source} 和其中某个人是什么关系？", f"{source} 后来关系有什么变化？"],
            )
        relation = relations[0]
        relation_type = relation.get("relation_type", "关联")
        first = relation.get("first_chapter_number")
        answer = f"截至第 {state.progress_chapter} 章，“{source}”和“{target}”的关系可归纳为：{relation_type}。"
        if first:
            answer += f" 这条关系最早在第 {int(first)} 章附近被索引到。"
        stages = relation.get("stages", [])
        if stages:
            stage_text = "；".join(
                f"{stage.get('label')}（第 {int(stage.get('chapter_start', 0))} 到 {int(stage.get('chapter_end', 0))} 章）：{stage.get('summary')}"
                for stage in stages[:3]
            )
            answer += f" 关系阶段：{stage_text}"
        evolution = str(relation.get("evolution_summary") or "").strip()
        if evolution:
            answer += f" 总体变化：{evolution}"
        return Decision(
            is_terminal=True,
            intent_override="relation_lookup",
            entity_name=source,
            answer=answer,
            summary=f"关系索引命中 {len(relations)} 类关系；主关系归纳为 {len(stages)} 个阶段。",
            suggestions=[f"{source} 和 {target} 后来关系有什么变化？", f"{source} 还和谁有关？"],
        )

    def _route_theme(self, state: AgentState) -> Decision:
        theme = state.matched_themes[0] if state.matched_themes else ""
        if not theme:
            if "search_evidence" not in state.called_tools:
                return self._call("search_evidence", {"query": state.question})
            return self._terminal_semantic(state, "theme_explore")
        if "search_theme" not in state.called_tools:
            return self._call(
                "search_theme",
                {"theme": theme},
                thought="查询主题线索在已读范围内的出现与变化",
            )
        obs = self._last_observation(state, "search_theme")
        return self._terminal_theme(state, obs)

    def _terminal_theme(self, state: AgentState, obs: dict) -> Decision:
        theme = str(obs.get("theme_name") or (state.matched_themes[0] if state.matched_themes else ""))
        chapters = obs.get("chapters", [])
        fragments = obs.get("fragments", [])
        stages = obs.get("stages", [])
        if not fragments:
            return Decision(
                is_terminal=True,
                intent_override="theme_explore",
                answer=f"截至第 {state.progress_chapter} 章，我还没有找到主题“{theme}”的明确线索。",
                suggestions=[f"换成更具体的主题词继续问：{theme}", "补充主题词表后重新 build。"],
            )
        chapter_list = "、".join(f"第 {int(chapter)} 章" for chapter in chapters[:5])
        answer = f"截至第 {state.progress_chapter} 章，主题“{theme}”主要出现在：{chapter_list}。"
        if stages:
            stage_text = "；".join(
                f"{stage.get('label')}（第 {int(stage.get('chapter_start', 0))} 到 {int(stage.get('chapter_end', 0))} 章）：{stage.get('summary')}"
                for stage in stages[:3]
            )
            answer += f" 阶段线索：{stage_text}"
        else:
            first_excerpt = str(fragments[0].get("excerpt", "")).strip()
            answer += f" 关键线索是：“{first_excerpt[:80]}”。"
        evolution = str(obs.get("evolution_summary") or "").strip()
        if evolution:
            answer += f" 总体演化：{evolution}"
        return Decision(
            is_terminal=True,
            intent_override="theme_explore",
            answer=answer,
            summary=f"主题索引命中 {len(fragments)} 条证据片段，归纳为 {len(stages)} 个阶段。",
            suggestions=[f"{theme} 在后续章节还有变化吗？", f"对比 {theme} 前后观点变化。"],
        )

    def _route_compare(self, state: AgentState) -> Decision:
        if "search_evidence" not in state.called_tools:
            return self._call("search_evidence", {"query": state.question})
        if "get_chapter_summary" not in state.called_tools and state.raw_hits:
            last_c = state.raw_hits[-1]["chapter_number"]
            return self._call("get_chapter_summary", {"chapter": int(last_c)}, thought="取命中章节摘要辅助对比")
        return self._terminal_semantic(state, "compare")

    def _route_causal(self, state: AgentState) -> Decision:
        if "search_events" not in state.called_tools:
            return self._call(
                "search_events",
                {"query": state.question, "entity": state.primary_entity or ""},
                thought="先查结构化事件链辅助因果定位",
            )
        event_obs = self._last_observation(state, "search_events")
        if event_obs.get("events"):
            return self._terminal_events(state, event_obs, "causal")
        if "search_evidence" not in state.called_tools:
            return self._call("search_evidence", {"query": state.question})
        if state.primary_entity and "lookup_timeline" not in state.called_tools:
            return self._call("lookup_timeline", {"entity": state.primary_entity}, thought="拿实体轨迹辅助定位关键章节")
        return self._terminal_semantic(state, "causal")

    def _route_events(self, state: AgentState, intent: str) -> Decision:
        if "search_events" not in state.called_tools:
            return self._call(
                "search_events",
                {"query": state.question, "entity": state.primary_entity or ""},
                thought="查询结构化事件链",
            )
        obs = self._last_observation(state, "search_events")
        return self._terminal_events(state, obs, intent)

    def _terminal_events(self, state: AgentState, obs: dict, intent: str) -> Decision:
        events = obs.get("events", [])
        if not events:
            return Decision(
                is_terminal=True,
                intent_override=intent,
                answer=f"截至第 {state.progress_chapter} 章，我还没有找到足够清晰的事件链记录。",
                suggestions=["换成具体人物、道具或章节线索继续问。", "补充实体词表后重新 build。"],
            )
        lines = [
            f"第 {int(event['chapter_number'])} 章《{event['chapter_title']}》[{event['event_type']}]：{event['summary']}"
            for event in events[:5]
        ]
        answer = "；".join(lines)
        chain_summary = str(obs.get("chain_summary") or "").strip()
        if chain_summary:
            answer = f"{chain_summary} 关键节点：{answer}"
        return Decision(
            is_terminal=True,
            intent_override=intent,
            answer=answer,
            summary=f"事件索引命中 {len(events)} 个节点。",
            suggestions=["继续追问其中一个事件的前因后果。", "对比这条主线前后有什么变化。"],
        )

    def _route_semantic(self, state: AgentState) -> Decision:
        if "search_evidence" not in state.called_tools:
            return self._call("search_evidence", {"query": state.question})
        return self._terminal_semantic(state, "semantic_search")

    def _terminal_semantic(self, state: AgentState, intent: str) -> Decision:
        if not state.evidence and not state.raw_hits:
            return Decision(
                is_terminal=True,
                intent_override=intent,
                answer=f"截至第 {state.progress_chapter} 章，我没有找到足够相关的证据。",
                summary="可以换一个更具体的实体或章节线索继续问。",
                suggestions=["把问题改成更明确的实体或章节线索。", "补充实体词表后重新 build。"],
            )
        structured = _structured_list_answer(state, intent)
        if structured is not None:
            return structured
        if self.reasoner and self.reasoner.enabled and not state.answer:
            ans = self.reasoner.answer(_semantic_prompt(state, intent))
            if ans:
                return Decision(is_terminal=True, intent_override=intent, answer=ans, suggestions=_semantic_suggestions(state.question))
        summary = "；".join(
            f"第 {hit['chapter_number']} 章重点提到：{str(hit['child_text'])[:80].strip()}"
            for hit in state.raw_hits[:3]
        )
        return Decision(
            is_terminal=True,
            intent_override=intent,
            answer=f"基于已读范围内的证据，最相关的线索是：{summary}",
            suggestions=_semantic_suggestions(state.question),
        )


def _semantic_prompt(state: AgentState, intent: str) -> str:
    evidence_lines = [
        f"第 {hit['chapter_number']} 章《{hit['chapter_title']}》：{hit['child_text']}"
        for hit in state.raw_hits
    ]
    return (
        f"用户问题：{state.question}\n"
        f"当前只允许使用第 1 章到第 {state.progress_chapter} 章的证据，不能剧透后文。\n"
        f"问题类型：{intent}\n"
        "请先给结论，再给简短解释。\n"
        "证据：\n"
        + "\n".join(f"- {line}" for line in evidence_lines)
    )


def _semantic_suggestions(question: str) -> list[str]:
    return [f"换个问法继续问：{question}", "把问题指向具体实体，会更容易得到精准回忆。"]


def _structured_list_answer(state: AgentState, intent: str) -> Decision | None:
    if not _wants_structured_list(state.question):
        return None
    best: tuple[dict, list[str]] | None = None
    for hit in state.raw_hits[:4]:
        text = str(hit.get("parent_text") or hit.get("child_text") or "")
        items = _extract_numbered_items(text)
        if len(items) < 2:
            continue
        if best is None or len(items) > len(best[1]):
            best = (hit, items)
    if best is None:
        return None
    hit, items = best
    chapter_number = int(hit.get("chapter_number", 0) or 0)
    chapter_title = str(hit.get("chapter_title") or f"第 {chapter_number} 章")
    lines = "\n".join(f"{index}. {item}" for index, item in enumerate(items[:6], start=1))
    return Decision(
        is_terminal=True,
        intent_override=intent,
        answer=f"根据第 {chapter_number} 章《{chapter_title}》，可以定位到这些条件：\n{lines}",
        summary=f"从同一证据段中抽取到 {len(items)} 条枚举条件。",
        suggestions=_semantic_suggestions(state.question),
    )


def _wants_structured_list(question: str) -> bool:
    return any(keyword in question for keyword in ("条件", "标准", "要求", "步骤", "有哪些", "是什么"))


def _extract_numbered_items(text: str) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"[“\"']?(第[一二三四五六七八九十]+)[，,、：:]\s*([^”\"'\n]+)")
    for match in pattern.finditer(text):
        body = match.group(2).strip()
        body = re.split(r"[。！？!?][”\"']?", body, maxsplit=1)[0].strip()
        body = body.strip(" “”\"'。；;，,")
        if not body:
            continue
        item = f"{match.group(1)}，{body}。"
        if item not in seen:
            items.append(item)
            seen.add(item)
    return items


def _entity_followups(entity_name: str) -> list[str]:
    return [
        f"{entity_name} 后来还有出现过吗？",
        f"{entity_name} 和主角之间后来发生了什么？",
    ]


def _timeline_followups(entity_name: str) -> list[str]:
    return [
        f"{entity_name} 第一次出现在哪一章？",
        f"{entity_name} 在已读范围里的关键作用是什么？",
    ]
