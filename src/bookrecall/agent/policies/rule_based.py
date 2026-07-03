"""规则版决策策略：确定性多步路由。

保留旧 agent.py 的 `classify_intent` 关键词分类，升级为 stateful 多步：
依据「已调用工具集合」做状态机迁移，而非一步到底。无观察回退，杜绝死循环。
"""

from __future__ import annotations

from ...models import EvidenceCard
from ..state import AgentState, ToolCallTrace
from ..tools import ToolRegistry
from .base import Decision, DecisionPolicy, ToolCall


def classify_intent(question: str, matched_entities: list[str]) -> str:
    """与旧版完全一致的关键词意图分类。"""
    if matched_entities and any(keyword in question for keyword in ("第一次", "首次", "最早", "初次")):
        return "first_appearance"
    if matched_entities and any(keyword in question for keyword in ("还有出现", "后来", "再次", "轨迹", "出现过吗")):
        return "entity_timeline"
    if any(keyword in question for keyword in ("变化", "对比", "前后")):
        return "compare"
    if any(keyword in question for keyword in ("怎么", "如何", "为什么", "原因")):
        return "causal"
    return "semantic_search"


# 旧 agent.py 用的意图中文标签。finalize 映射到 MemoryCard.intent。
INTENT_LABELS: dict[str, str] = {
    "first_appearance": "实体首次出现",
    "entity_timeline": "实体轨迹追踪",
    "compare": "对比分析",
    "causal": "因果回忆",
    "semantic_search": "语义回忆",
}


class RuleBasedPolicy(DecisionPolicy):
    def __init__(self, reasoner=None) -> None:
        # reasoner 仅在 semantic 终止步可选地出答案，与旧版 _answer_semantic 的 cloud 分支对齐
        self.reasoner = reasoner

    def name(self) -> str:
        return "rule_based"

    # -- 小工具 --
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
        # step 0：分类 + 别名解析预热
        if state.step == 0:
            if not state.matched_entities:
                state.intent = classify_intent(state.question, [])
                return self._call("search_evidence", {"query": state.question}, thought="无实体命中，直接语义检索")
            # 别名不可靠时 primary_entity 已由 core 从 matched_entities[0] 预设；调一次 aliases 工具规范它
            if "lookup_entity_aliases" not in state.called_tools:
                return self._call(
                    "lookup_entity_aliases",
                    {"entity": state.primary_entity or state.matched_entities[0]},
                    thought="先解析别名拿到规范实体名",
                )
            # 别名工具已跑但还没分类（极小概率首次 step0 就两次）
            state.intent = state.intent if state.intent != "semantic_search" else classify_intent(state.question, state.matched_entities)

        intent = state.intent

        if intent == "first_appearance":
            return self._route_first_appearance(state)
        if intent == "entity_timeline":
            return self._route_timeline(state)
        if intent == "compare":
            return self._route_compare(state)
        if intent == "causal":
            return self._route_causal(state)
        return self._route_semantic(state)

    # -- 分支路由 --

    def _route_first_appearance(self, state: AgentState) -> Decision:
        if "lookup_first_appearance" not in state.called_tools:
            return self._call("lookup_first_appearance",
                              {"entity": state.primary_entity or state.matched_entities[0]})
        obs = self._last_observation(state, "lookup_first_appearance")
        return self._terminal_first_appearance(state, obs)

    def _terminal_first_appearance(self, state: AgentState, obs: dict) -> Decision:
        entity = obs.get("entity_name") or state.primary_entity or (state.matched_entities[0] if state.matched_entities else "")
        if not obs.get("found"):
            return Decision(
                is_terminal=True,
                answer=f"我在当前索引里还没找到“{entity}”这个实体，建议先补充实体词表后重新建索引。",
                intent_override="first_appearance",
                entity_name=entity,  # 若工具不识别则 core fallback 用 matched
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
            return self._call("lookup_timeline",
                              {"entity": state.primary_entity or state.matched_entities[0]})
        obs = self._last_observation(state, "lookup_timeline")
        chapters = obs.get("chapters", [])
        # 若问“怎么拿到/使用”且已拿到末次章节，再做一次聚焦检索
        wants_detail = any(k in state.question for k in ("怎么", "如何", "拿到", "使用"))
        if wants_detail and chapters and "search_evidence" not in state.called_tools:
            return self._call(
                "search_evidence",
                {"query": f"{state.primary_entity} {state.question}"},
                thought="聚焦末次出现章节做证据检索",
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

    def _route_compare(self, state: AgentState) -> Decision:
        if "search_evidence" not in state.called_tools:
            return self._call("search_evidence", {"query": state.question})
        if "get_chapter_summary" not in state.called_tools and state.raw_hits:
            last_c = state.raw_hits[-1]["chapter_number"]
            return self._call("get_chapter_summary", {"chapter": int(last_c)},
                              thought="取最晚命中章节摘要做前后对比")
        return self._terminal_compare(state)

    def _terminal_compare(self, state: AgentState) -> Decision:
        if not state.evidence and not state.raw_hits:
            return Decision(
                is_terminal=True,
                intent_override="compare",
                answer=f"截至第 {state.progress_chapter} 章，我没有找到足够相关的证据。",
                summary="可以把问题改成两个明确章节做对比。",
                suggestions=["把问题改成两个明确章节做对比。", "围绕同一主题继续问：它第一次被提出是在什么时候？"],
            )
        if self.reasoner and self.reasoner.enabled:
            prompt = _semantic_prompt(state, "compare")
            ans = self.reasoner.answer(prompt)
            if ans:
                return Decision(is_terminal=True, intent_override="compare", answer=ans,
                                suggestions=["把问题改成两个明确章节做对比。", "围绕同一主题继续问：它第一次被提出是在什么时候？"])
        summary = "；".join(f"第 {h['chapter_number']} 章重点提到：{h['child_text'][:80].strip()}" for h in state.raw_hits[:3])
        return Decision(
            is_terminal=True,
            intent_override="compare",
            answer=f"基于已读范围内的证据，我认为最相关的线索是：{summary}",
            suggestions=["把问题改成两个明确章节做对比。", "围绕同一主题继续问：它第一次被提出是在什么时候？"],
        )

    def _route_causal(self, state: AgentState) -> Decision:
        if "search_evidence" not in state.called_tools:
            return self._call("search_evidence", {"query": state.question})
        if state.primary_entity and "lookup_timeline" not in state.called_tools:
            return self._call("lookup_timeline", {"entity": state.primary_entity},
                              thought="拿实体轨迹辅助定位关键章节")
        return self._terminal_causal(state)

    def _terminal_causal(self, state: AgentState) -> Decision:
        if not state.evidence and not state.raw_hits:
            return Decision(
                is_terminal=True,
                intent_override="causal",
                answer=f"截至第 {state.progress_chapter} 章，我没有找到足够相关的证据。",
                summary="可以换一个更具体的实体或章节线索。",
                suggestions=["继续追问：这件事之前发生了什么？", "继续追问：这件事之后有什么后果？"],
            )
        if self.reasoner and self.reasoner.enabled:
            prompt = _semantic_prompt(state, "causal")
            ans = self.reasoner.answer(prompt)
            if ans:
                return Decision(is_terminal=True, intent_override="causal", answer=ans,
                                suggestions=["继续追问：这件事之前发生了什么？", "继续追问：这件事之后有什么后果？"])
        summary = "；".join(f"第 {h['chapter_number']} 章重点提到：{h['child_text'][:80].strip()}" for h in state.raw_hits[:3])
        return Decision(
            is_terminal=True,
            intent_override="causal",
            answer=f"基于已读范围内的证据，我认为最相关的线索是：{summary}",
            suggestions=["继续追问：这件事之前发生了什么？", "继续追问：这件事之后有什么后果？"],
        )

    def _route_semantic(self, state: AgentState) -> Decision:
        if "search_evidence" not in state.called_tools:
            return self._call("search_evidence", {"query": state.question})
        if not state.evidence and not state.raw_hits:
            return Decision(
                is_terminal=True,
                intent_override="semantic_search",
                answer=f"截至第 {state.progress_chapter} 章，我没有找到足够相关的证据。",
                summary="你可以试着换一个更具体的问题，或者补充实体词表后重建索引。",
                suggestions=["把问题改成更明确的实体或章节线索。", "补充实体词表后重新 build。"],
            )
        if self.reasoner and self.reasoner.enabled and not state.answer:
            prompt = _semantic_prompt(state, "semantic_search")
            ans = self.reasoner.answer(prompt)
            if ans:
                return Decision(is_terminal=True, intent_override="semantic_search", answer=ans,
                                suggestions=_semantic_suggestions(state.question))
        summary = "；".join(f"第 {h['chapter_number']} 章重点提到：{h['child_text'][:80].strip()}" for h in state.raw_hits[:3])
        return Decision(
            is_terminal=True,
            intent_override="semantic_search",
            answer=f"基于已读范围内的证据，我认为最相关的线索是：{summary}",
            suggestions=_semantic_suggestions(state.question),
        )


# ---------- prompt / suggestions 工具（与旧版语义对齐） ----------

def _semantic_prompt(state: AgentState, intent: str) -> str:
    evidence_lines = [
        f"第 {h['chapter_number']} 章《{h['chapter_title']}》：{h['child_text']}"
        for h in state.raw_hits
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


def _entity_followups(entity_name: str) -> list[str]:
    if entity_name.endswith(("人", "者")):
        return [
            f"{entity_name}后来还有出现过吗？",
            f"{entity_name}和主角之间后来发生了什么？",
        ]
    return [
        f"{entity_name}后来还有出现过吗？",
        f"{entity_name}最后是怎么被拿到或使用的？",
    ]


def _timeline_followups(entity_name: str) -> list[str]:
    if entity_name.endswith(("人", "者")):
        return [
            f"{entity_name} 第一次出现在哪一章？",
            f"{entity_name} 在已读范围里的关键作用是什么？",
        ]
    return [
        f"{entity_name} 第一次出现在哪一章？",
        f"{entity_name} 在已读范围里的关键作用是什么？",
    ]
