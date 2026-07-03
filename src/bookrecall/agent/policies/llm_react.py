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
1) 只能调用工具清单里的工具，任何参数里的章节号不能超过阅读进度。
2) 任何超出阅读进度的内容都视为剧透，不许在 final_answer 里输出。
3) 证据不足时继续调用工具（action=工具名）；证据足够时返回最终答案（action=finish）。
4) 回答必须基于已观察证据，不许虚构章节号或事件。
请用下面的严格格式输出，不要额外解释：
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
        prompt = self._build_prompt(state, registry)
        raw = self.reasoner.answer(prompt)
        if not raw:
            return Decision(is_terminal=False, tool_call=None)  # 无效决策，由 core 重试/回退
        parsed = _parse_react(raw)
        if parsed is None:
            return Decision(is_terminal=False, tool_call=None)
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

    def _build_prompt(self, state: "AgentState", registry: "ToolRegistry") -> str:
        trace_lines = [
            f"- step{tr.step} {tr.tool_name}({tr.arguments}) => 命中{tr.hit_count}条, spoiler_blocked={tr.spoiler_blocked}"
            for tr in state.trace
        ]
        trace_text = "\n".join(trace_lines) if trace_lines else "（尚未调用任何工具）"
        evidence_chapters = [e.chapter_number for e in state.evidence]
        tools_json = json.dumps(registry.describe_for_llm(), ensure_ascii=False, indent=2)
        return (
            f"{_SYSTEM_PROMPT}\n"
            f"问题：{state.question}\n"
            f"阅读进度：第 {state.progress_chapter} 章（任何章节号都不许超过它）\n"
            f"已识别实体：{state.matched_entities} / 规范名：{state.primary_entity}\n"
            f"已调用工具：\n{trace_text}\n"
            f"当前累积证据章节：{evidence_chapters}\n\n"
            f"可用工具：\n{tools_json}\n"
        )


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
