"""Local Qwen planner policy.

This policy asks a local JSON-capable model for a bounded tool plan, validates
the plan against the registry, executes planned tools one by one, then delegates
answer synthesis back to the deterministic rule-based policy.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from .base import Decision, DecisionPolicy, ToolCall
from .rule_based import RuleBasedPolicy

if False:  # pragma: no cover - typing only without runtime import cycle
    from ..state import AgentState
    from ..tools import ToolRegistry


class JsonCompleter(Protocol):
    def complete_json(self, prompt: str) -> dict[str, Any]:
        ...


class LocalPlannerPolicy(DecisionPolicy):
    def __init__(self, client: JsonCompleter, *, fallback: DecisionPolicy | None = None) -> None:
        self.client = client
        self.fallback = fallback or RuleBasedPolicy(reasoner=None)

    def name(self) -> str:
        return "local_planner"

    def next_action(self, state: "AgentState", registry: "ToolRegistry") -> Decision:
        if _should_delegate_to_rules(state):
            return self.fallback.next_action(state, registry)
        if not hasattr(state, "_local_planner_plan"):
            plan = self._build_plan(state, registry)
            setattr(state, "_local_planner_plan", plan)
            setattr(state, "_local_planner_index", 0)
        plan = getattr(state, "_local_planner_plan", [])
        index = int(getattr(state, "_local_planner_index", 0))
        while index < len(plan):
            call = plan[index]
            setattr(state, "_local_planner_index", index + 1)
            index += 1
            if call.name in state.called_tools:
                continue
            return Decision(is_terminal=False, tool_call=self._resolve_dynamic_arguments(call, state))
        return self.fallback.next_action(state, registry)

    def _build_plan(self, state: "AgentState", registry: "ToolRegistry") -> list[ToolCall]:
        try:
            payload = self.client.complete_json(_planner_prompt(state, registry))
        except Exception:  # noqa: BLE001 - local planner must be optional
            return []
        return parse_local_plan(payload, registry)

    @staticmethod
    def _resolve_dynamic_arguments(call: ToolCall, state: "AgentState") -> ToolCall:
        args = dict(call.arguments)
        for key in ("entity", "source_entity"):
            if args.get(key) in {"$primary_entity", "$entity", ""} and state.primary_entity:
                args[key] = state.primary_entity
        if args.get("target_entity") in {"$second_entity", ""} and len(state.matched_entities) >= 2:
            args["target_entity"] = state.matched_entities[1]
        if args.get("query") in {"$question", ""}:
            args["query"] = state.question
        return ToolCall(name=call.name, arguments=args, thought=call.thought)


def _should_delegate_to_rules(state: "AgentState") -> bool:
    question = state.question
    if state.matched_entities or state.matched_themes:
        return False
    return any(keyword in question for keyword in ("条件", "标准", "要求", "步骤", "有哪些", "是什么"))


def parse_local_plan(payload: dict[str, Any], registry: "ToolRegistry") -> list[ToolCall]:
    raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list):
        raw_calls = payload.get("plan")
    if not isinstance(raw_calls, list):
        return []
    plan: list[ToolCall] = []
    seen: set[str] = set()
    for raw in raw_calls[:6]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("tool") or raw.get("name") or "").strip()
        if not name or registry.get(name) is None or name in seen:
            continue
        arguments = raw.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        plan.append(
            ToolCall(
                name=name,
                arguments=dict(arguments),
                thought=str(raw.get("thought") or raw.get("reason") or "local planner").strip()[:120],
            )
        )
        seen.add(name)
    return plan


def _planner_prompt(state: "AgentState", registry: "ToolRegistry") -> str:
    tools_json = json.dumps(registry.describe_for_llm(), ensure_ascii=False, indent=2)
    understanding = json.dumps(state.query_understanding or {}, ensure_ascii=False)
    recent = [
        {
            "question": turn.get("question", ""),
            "entity_name": turn.get("entity_name", ""),
            "summary": turn.get("summary", ""),
        }
        for turn in state.recent_turns[-3:]
    ]
    return f"""
你是 BookRecall 的本地 Agent Planner。请为用户问题选择需要调用的工具顺序。

规则：
1. 只允许使用工具清单里的工具名。
2. 只规划检索工具，不要生成最终答案。
3. 工具调用数量最多 4 个；简单问题 1-2 个即可。
4. 如果问题涉及实体别名，优先调用 lookup_entity_aliases。
5. 参数章节号不能超过阅读进度；不确定 max_chapter 时可省略。
6. 可用占位符：$primary_entity、$second_entity、$question。
7. 只输出 JSON object，不要解释，不要 markdown。

用户问题：{state.question}
阅读进度：第 {state.progress_chapter} 章
已识别实体：{state.matched_entities}
主实体：{state.primary_entity or ""}
已识别主题：{state.matched_themes}
查询理解：{understanding}
最近会话：{json.dumps(recent, ensure_ascii=False)}

工具清单：
{tools_json}

输出格式：
{{"tool_calls":[{{"tool":"lookup_entity_aliases","arguments":{{"entity":"$primary_entity"}},"thought":"先解析实体别名"}}]}}
""".strip()
