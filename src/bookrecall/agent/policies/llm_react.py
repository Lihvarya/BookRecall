"""LLM ReAct 策略：调云端模型决定下一步工具调用。

仅在 BOOKRECALL_API_KEY / OPENAI_API_KEY 存在时启用。模型用 key:value 文本输出
thought/action/arguments/final_answer，核心循环用简易解析器读取。

设计要点：
- 工具清单通过 registry.describe_for_llm() 注入
- 进度边界在 prompt 里强约束，且由 core 的 _clamp_max_chapter 二次钳制
- 解析失败返回「无效决策」，交给 core 重试；连续失败由 core 回退 RuleBasedPolicy
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .base import Decision, DecisionPolicy, ToolCall

if TYPE_CHECKING:
    from ...cloud import OpenAICompatibleReasoner
    from ..state import AgentState
    from ..tools import ToolRegistry


_SYSTEM_PROMPT = """你是 BookRecall 的回忆助手。基于给定的工具和已观察证据回答用户关于小说的问题。
严格规则：
1) 优先使用原生 tool calling；如果模型不支持，再回退到文本协议。
2) 只能调用工具清单里的工具，任何参数里的章节号不能超过阅读进度。
3) 任何超出阅读进度的内容都视为剧透，不许在最终回答里输出。
4) 证据不足时继续调用工具；证据足够时返回最终答案。
5) 回答必须基于已观察证据，不许虚构章节号或事件。
回退到文本协议时，请用下面的严格格式输出，不要额外解释：
thought: <一句话思考>
action: <工具名 或 finish>
arguments: <JSON 对象，例如 {"entity":"方源"}；finish 时填 {}>
final_answer: <仅当 action=finish 时填，否则留空>
summary: <可选>
"""


class LLMReActPolicy(DecisionPolicy):
    def __init__(self, reasoner: "OpenAICompatibleReasoner") -> None:
        self.reasoner = reasoner

    def name(self) -> str:
        return "llm_react"

    def next_action(self, state: "AgentState", registry: "ToolRegistry") -> Decision:
        response = self.reasoner.chat(
            messages=self._build_messages(state, registry),
            tools=registry.describe_for_openai_tools(),
            tool_choice="auto",
        )
        if not response:
            return Decision(is_terminal=False, tool_call=None)  # 无效决策，由 core 重试/回退
        tool_calls = response.get("tool_calls") or []
        if isinstance(tool_calls, list) and tool_calls:
            return _decision_from_tool_calls(tool_calls, registry)
        raw = str(response.get("content") or "").strip()
        if not raw:
            return Decision(is_terminal=False, tool_call=None)
        parsed = _parse_react(raw)
        if parsed is None:
            return Decision(is_terminal=True, answer=raw, summary=None, suggestions=None)
        action = parsed.get("action", "").strip().lower()
        if action == "finish" or action == "":
            return Decision(
                is_terminal=True,
                answer=parsed.get("final_answer") or None,
                summary=parsed.get("summary") or None,
                suggestions=None,
            )
        tool = registry.get(action)
        if tool is None:
            # 模型编造了工具名：回传无效决策让 core 处理
            return Decision(is_terminal=False, tool_call=None)
        arguments = parsed.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        return Decision(
            is_terminal=False,
            tool_call=ToolCall(name=action, arguments=arguments, thought=parsed.get("thought", "")),
        )

    def _build_messages(self, state: "AgentState", registry: "ToolRegistry") -> list[dict[str, str]]:
        trace_lines = [
            f"- step{tr.step} {tr.tool_name}({tr.arguments}) => 命中{tr.hit_count}条, spoiler_blocked={tr.spoiler_blocked}"
            for tr in state.trace
        ]
        trace_text = "\n".join(trace_lines) if trace_lines else "（尚未调用任何工具）"
        recent_turns_text = self._format_recent_turns(state)
        preference_text = self._format_user_preferences(state)
        evidence_chapters = [e.chapter_number for e in state.evidence]
        tools_json = json.dumps(registry.describe_for_llm(), ensure_ascii=False, indent=2)
        prompt = (
            f"{_SYSTEM_PROMPT}\n"
            f"问题：{state.question}\n"
            f"阅读进度：第 {state.progress_chapter} 章（任何章节号都不许超过它）\n"
            f"用户长期偏好：\n{preference_text}\n"
            f"已识别实体：{state.matched_entities} / 规范名：{state.primary_entity}\n"
            f"已识别主题：{state.matched_themes}\n"
            f"同会话最近几轮：\n{recent_turns_text}\n"
            f"已调用工具：\n{trace_text}\n"
            f"当前累积证据章节：{evidence_chapters}\n\n"
            f"可用工具：\n{tools_json}\n"
        )
        return [
            {"role": "system", "content": "你是 BookRecall 的回忆助手。优先使用 tools 完成多步检索；如果证据已经足够，可以直接给出最终回答。回答必须基于证据，不得剧透超过阅读进度的内容。"},
            {"role": "user", "content": prompt},
        ]

    @staticmethod
    def _format_recent_turns(state: "AgentState") -> str:
        if not state.recent_turns:
            return "（无）"
        lines: list[str] = []
        for turn in state.recent_turns[-3:]:
            question = str(turn.get("question", "")).strip()
            entity = str(turn.get("entity_name", "")).strip()
            answer = str(turn.get("answer", "")).strip()
            short_answer = answer[:120] + ("..." if len(answer) > 120 else "")
            lines.append(f"- 问：{question} | 实体：{entity or '无'} | 答：{short_answer}")
        return "\n".join(lines)

    @staticmethod
    def _format_user_preferences(state: "AgentState") -> str:
        preferences = state.user_preferences or {}
        parts: list[str] = []
        style = str(preferences.get("answer_style") or "").strip()
        focus = str(preferences.get("focus") or "").strip()
        custom = str(preferences.get("custom_prompt") or "").strip()
        if style:
            parts.append(f"- 回答风格：{style}")
        if focus:
            parts.append(f"- 关注重点：{focus}")
        if custom:
            parts.append(f"- 自定义说明：{custom}")
        return "\n".join(parts) if parts else "（无）"


def _decision_from_tool_calls(tool_calls: list[object], registry: "ToolRegistry") -> Decision:
    first = tool_calls[0]
    if not isinstance(first, dict):
        return Decision(is_terminal=False, tool_call=None)
    tool_name = str(first.get("name") or "").strip()
    if not tool_name or registry.get(tool_name) is None:
        return Decision(is_terminal=False, tool_call=None)
    arguments = _parse_tool_arguments(first.get("arguments"))
    return Decision(
        is_terminal=False,
        tool_call=ToolCall(name=tool_name, arguments=arguments, thought="tool_call"),
    )


def _parse_tool_arguments(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _parse_react(raw: str) -> dict | None:
    """简易 key:value 解析器，纯标准库。

    支持 thought / action / arguments / final_answer / summary。
    arguments 尝试按 JSON 解析；失败则当成空 dict。
    """
    pattern = re.compile(
        r"^\s*(?P<key>thought|action|arguments|final_answer|summary)\s*[:：]\s*(?P<val>.*?)(?=\n\s*(?:thought|action|arguments|final_answer|summary)\s*[:：]|\Z)",
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )
    result: dict = {}
    for match in pattern.finditer(raw):
        key = match.group("key").lower()
        val = match.group("val").strip()
        if key == "arguments":
            parsed_args: dict | None = None
            try:
                candidate = json.loads(val)
                if isinstance(candidate, dict):
                    parsed_args = candidate
            except (json.JSONDecodeError, ValueError):
                # 退一步：尝试匹配 key:value 列表
                argument_pairs = {}
                for pair in re.finditer(r'["\']?(?P<k>[^"\':,]+)["\']?\s*[:：]\s*["\']?(?P<v>[^"\',}]+)["\']?', val):
                    argument_pairs[pair.group("k").strip()] = pair.group("v").strip()
                if argument_pairs:
                    parsed_args = argument_pairs
            result[key] = parsed_args if parsed_args is not None else val
        else:
            result[key] = val
    if not result:
        return None
    return result
