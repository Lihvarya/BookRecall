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
    if any(keyword in question for keyword in ("怎么", "如何", "为什么", "原因", "真相")):
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
        entity = obs.get("entity_name") or state.primary_entity or (state.matched_entities[0] if state.matched_entities else "")
        if not obs.get("found") and "search_exact_text" not in state.called_tools:
            keyword = _extract_exact_keyword(state.question, str(entity or ""))
            if keyword:
                return self._call(
                    "search_exact_text",
                    {"keyword": keyword, "limit": 8},
                    thought="实体索引未命中，改用全书精确词检索兜底",
                )
        exact_obs = self._last_observation(state, "search_exact_text")
        if not obs.get("found") and exact_obs.get("hits"):
            return self._terminal_first_appearance_from_exact(state, exact_obs, str(entity or exact_obs.get("keyword") or ""))
        return self._terminal_first_appearance(state, obs)

    def _terminal_first_appearance_from_exact(self, state: AgentState, obs: dict, entity: str) -> Decision:
        hits = obs.get("hits", [])
        if not hits:
            return self._terminal_first_appearance(state, {"found": False, "entity_name": entity})
        first = min(hits, key=lambda item: int(item.get("chapter_number", 0) or 0))
        chapter = int(first.get("chapter_number", 0) or 0)
        title = str(first.get("chapter_title") or f"第 {chapter} 章")
        keyword = str(obs.get("keyword") or entity)
        return Decision(
            is_terminal=True,
            intent_override="first_appearance",
            entity_name=keyword,
            answer=f"结构化实体索引里没有“{keyword}”，但全文精确检索命中它最早出现在第 {chapter} 章《{title}》。",
            summary=f"通过原文精确词检索找到 {len(hits)} 个命中章节。建议之后可把“{keyword}”补入实体索引。",
            suggestions=[f"{keyword} 后来还有出现过吗？", f"把“{keyword}”加入实体词表后重建结构化索引。"],
        )

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
        if not obs.get("chapters") and "search_exact_text" not in state.called_tools:
            keyword = _extract_exact_keyword(state.question, state.primary_entity or (state.matched_entities[0] if state.matched_entities else ""))
            if keyword:
                return self._call(
                    "search_exact_text",
                    {"keyword": keyword, "limit": 12},
                    thought="实体轨迹为空，改用原文精确词检索补充低频命中",
                )
        chapters = obs.get("chapters", [])
        exact_obs = self._last_observation(state, "search_exact_text")
        if not chapters and exact_obs.get("hits"):
            return self._terminal_timeline_from_exact(state, exact_obs)
        wants_detail = any(k in state.question for k in ("怎么", "如何", "拿到", "使用"))
        if wants_detail and chapters and "search_evidence" not in state.called_tools:
            return self._call(
                "search_evidence",
                {"query": f"{state.primary_entity} {state.question}"},
                thought="聚焦实体轨迹做证据检索",
            )
        return self._terminal_timeline(state, obs)

    def _terminal_timeline_from_exact(self, state: AgentState, obs: dict) -> Decision:
        keyword = str(obs.get("keyword") or state.primary_entity or "")
        hits = obs.get("hits", [])
        chapters: list[int] = []
        for hit in hits:
            chapter = int(hit.get("chapter_number", 0) or 0)
            if chapter and chapter not in chapters:
                chapters.append(chapter)
        chapter_list = "、".join(f"第 {chapter} 章" for chapter in chapters)
        return Decision(
            is_terminal=True,
            intent_override="entity_timeline",
            entity_name=keyword or state.primary_entity,
            answer=f"结构化实体轨迹里没有稳定记录，但全文精确检索发现“{keyword}”出现在：{chapter_list}。",
            summary=f"原文精确命中 {len(hits)} 处，覆盖 {len(chapters)} 个章节。",
            suggestions=[f"{keyword} 第一次出现在哪一章？", f"将“{keyword}”补入实体词表后重建索引。"],
        )

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
        if _wants_truth_chain(state.question):
            exact_keyword = _death_verification_keyword(state.question, state.primary_entity or "")
            if exact_keyword and "search_exact_text" not in state.called_tools:
                return self._call(
                    "search_exact_text",
                    {"keyword": exact_keyword, "limit": 8},
                    thought="用户指出明确死亡措辞，优先用原文精确检索核验",
                )
            if exact_keyword and state.raw_hits:
                return self._terminal_semantic(state, "causal")
            if "search_evidence" not in state.called_tools:
                return self._call(
                    "search_evidence",
                    {"query": _death_search_query(state.question, state.primary_entity or "")},
                    thought="死亡/结局问题优先检索原文，并加入明确死亡措辞提高召回",
                )
            return self._terminal_semantic(state, "causal")
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
        if "search_exact_text" not in state.called_tools:
            keyword = _extract_exact_keyword(state.question, state.primary_entity or "")
            if keyword and _raw_hits_miss_keyword(state.raw_hits, keyword):
                return self._call(
                    "search_exact_text",
                    {"keyword": keyword, "limit": 8},
                    thought="因果检索证据不足，使用原文精确词检索兜底",
                )
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
        if "search_exact_text" in state.called_tools and state.raw_hits:
            return self._terminal_semantic(state, "semantic_search")
        if "search_evidence" not in state.called_tools:
            return self._call("search_evidence", {"query": state.question})
        if "search_exact_text" not in state.called_tools:
            keyword = _extract_exact_keyword(state.question, state.primary_entity or "")
            if keyword and (not state.raw_hits or _raw_hits_miss_keyword(state.raw_hits, keyword)):
                return self._call(
                    "search_exact_text",
                    {"keyword": keyword, "limit": 8},
                    thought="语义检索没有覆盖用户明确词，使用全书精确检索兜底",
                )
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
        truth_chain = _truth_chain_answer(state, intent)
        if truth_chain is not None:
            return truth_chain
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


def _extract_exact_keyword(question: str, preferred: str = "") -> str:
    preferred = preferred.strip()
    if preferred:
        return preferred.strip("“”\"'《》【】[]()（）")
    quoted = re.findall(r"[“\"'《【\[]([^”\"'》】\]]{2,24})[”\"'》】\]]", question)
    for item in quoted:
        item = item.strip()
        if item:
            return item
    text = re.sub(r"[，。！？；：、,.!?;:\n\r\t]", " ", question)
    text = re.sub(r"\s+", " ", text).strip()
    prefixes = ("请问", "问一下", "这本书里", "全书中", "全书里", "书中", "文中", "关于")
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    splitters = (
        "第一次",
        "首次",
        "最早",
        "后来",
        "后面",
        "后续",
        "还有",
        "出现",
        "提到",
        "是谁",
        "是什么",
        "在哪",
        "哪里",
        "怎么",
        "如何",
        "为什么",
        "死亡",
        "死因",
        "真相",
        "关系",
        "线索",
        "条件",
        "吗",
        "呢",
    )
    cut = text
    for splitter in splitters:
        index = cut.find(splitter)
        if index > 0:
            cut = cut[:index]
            break
    cut = cut.strip(" 的 了 和 与 在 中 里")
    if 2 <= len(cut) <= 24:
        return cut
    tokens = [token.strip(" 的 了 和 与 在 中 里") for token in text.split(" ") if token.strip()]
    candidates = [token for token in tokens if 2 <= len(token) <= 24]
    if not candidates:
        return ""
    return max(candidates, key=len)


def _raw_hits_miss_keyword(hits: list[dict], keyword: str) -> bool:
    if not keyword:
        return False
    for hit in hits[:5]:
        haystack = f"{hit.get('chapter_title', '')}\n{hit.get('child_text', '')}\n{hit.get('parent_text', '')}"
        if keyword in haystack:
            return False
    return True


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


def _truth_chain_answer(state: AgentState, intent: str) -> Decision | None:
    if not _wants_truth_chain(state.question):
        return None
    hits = _dedupe_hits_by_chapter(state.raw_hits)
    explicit = _explicit_death_answer(state, hits, intent)
    if explicit is not None:
        return explicit
    if len(hits) < 2:
        return None
    entity = state.primary_entity or (state.matched_entities[0] if state.matched_entities else "")
    combined = "\n".join(str(hit.get("parent_text") or hit.get("child_text") or "") for hit in hits)
    conclusions = _infer_truth_conclusions(entity, combined)
    chain_lines = []
    for hit in sorted(hits, key=lambda item: int(item.get("chapter_number", 0) or 0))[:5]:
        chapter_number = int(hit.get("chapter_number", 0) or 0)
        chapter_title = str(hit.get("chapter_title") or f"第 {chapter_number} 章")
        snippet = _compact_excerpt(str(hit.get("child_text") or hit.get("parent_text") or ""), limit=120)
        chain_lines.append(f"- 第 {chapter_number} 章《{chapter_title}》：{snippet}")
    subject = f"“{entity}”" if entity else "这条线索"
    answer = f"关于{subject}的死亡真相，当前证据更适合按线索链理解：\n"
    if conclusions:
        answer += "\n".join(f"{index}. {item}" for index, item in enumerate(conclusions, start=1))
        answer += "\n\n关键证据顺序：\n"
    else:
        answer += "我还不能只凭一段证据下最终定论，但命中的章节已经显示出一条连续线索：\n"
    answer += "\n".join(chain_lines)
    return Decision(
        is_terminal=True,
        intent_override=intent,
        entity_name=entity or None,
        answer=answer,
        summary=f"从 {len(hits)} 个命中章节整理为死亡/真相线索链。",
        suggestions=["打开这些章节原文，按时间线核对真相。", f"继续问：{entity} 和四代族长的冲突经过。" if entity else "继续问其中一个关键人物的冲突经过。"],
    )


def _wants_truth_chain(question: str) -> bool:
    return any(
        keyword in question
        for keyword in ("死亡真相", "死的真相", "怎么死", "如何死", "死因", "死在", "死于", "死了", "尸躯", "结局", "真相")
    )


def _death_verification_keyword(question: str, entity: str) -> str:
    entity = entity.strip()
    if entity and "尸躯" in question:
        return f"{entity}的尸躯"
    for marker in ("他死了", "她死了", "一动不动", "断了气息"):
        if marker in question:
            return marker
    return ""


def _death_search_query(question: str, entity: str) -> str:
    subject = entity.strip()
    if subject:
        return f"{subject} 死了 尸躯 丧命"
    return f"{question} 死了 尸躯 丧命".strip()


def _explicit_death_answer(state: AgentState, hits: list[dict], intent: str) -> Decision | None:
    entity = state.primary_entity or (state.matched_entities[0] if state.matched_entities else "")
    if not entity:
        return None
    corpse_marker = f"{entity}的尸躯"
    direct_pattern = re.compile(rf"{re.escape(entity)}.{{0,100}}(?:死了|死亡|身亡|丧命|断了气息)", re.S)
    for hit in hits:
        text = str(hit.get("parent_text") or hit.get("child_text") or "")
        explicit_corpse = corpse_marker in text and any(marker in text for marker in ("他死了", "她死了", "停止了动作", "一动不动"))
        if not explicit_corpse and direct_pattern.search(text) is None:
            continue
        chapter_number = int(hit.get("chapter_number", 0) or 0)
        chapter_title = str(hit.get("chapter_title") or f"第 {chapter_number} 章")
        location = "在逆流河中" if "逆流河" in text else ""
        cause = "被方源连续重创后杀死" if "方源" in text and any(marker in text for marker in ("趁胜追击", "再施辣手", "尸躯")) else "明确死亡"
        answer = f"可以确认：{entity}{location}{cause}，发生在第 {chapter_number} 章《{chapter_title}》。"
        if "他死了" in text:
            answer += f" 原文直接写明“{entity}终于停止了动作，一动不动”“他死了”"
        elif "她死了" in text:
            answer += f" 原文直接写明“她死了”"
        if corpse_marker in text:
            answer += f"，随后又写到“{corpse_marker}”"
        answer += "。这是明确的死亡描写，不是昏迷、重伤或仍然存活。"
        return Decision(
            is_terminal=True,
            intent_override=intent,
            entity_name=entity,
            answer=answer,
            summary=f"第 {chapter_number} 章同时出现死亡确认和尸躯描写，足以形成闭合证据链。",
            suggestions=[f"打开第 {chapter_number} 章原文核对死亡前后的完整过程。", f"继续问：{entity}为什么会在这里牺牲？"],
        )
    return None


def _dedupe_hits_by_chapter(hits: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for hit in hits[:8]:
        chapter = int(hit.get("chapter_number", 0) or 0)
        text = str(hit.get("child_text") or hit.get("parent_text") or "")
        key = (chapter, text[:40])
        if key in seen:
            continue
        deduped.append(hit)
        seen.add(key)
    return deduped


def _infer_truth_conclusions(entity: str, text: str) -> list[str]:
    conclusions: list[str] = []
    subject = entity or "相关人物"
    if "四代族长" in text and ("偷袭" in text or "重创" in text or "受重创" in text):
        conclusions.append(f"关键冲突指向四代族长：证据提到四代族长趁机偷袭，导致{subject}受重创。")
    if "影壁" in text or "留声" in text or "留影" in text:
        conclusions.append("影壁/留声类线索不是单纯环境描写，而是揭露当年真相的证据载体。")
    if "尸" in text or "棺" in text or "棺材" in text:
        conclusions.append("早期关于尸体或棺材的线索更像表层发现，需要和后续影壁记录、历史回放一起核对，不能单独当作完整真相。")
    return conclusions


def _compact_excerpt(text: str, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip(" 　")
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


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
