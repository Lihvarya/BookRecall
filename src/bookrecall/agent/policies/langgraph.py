"""LangGraph 预留 policy（未实现）。

本项目坚持零运行时依赖，不 import langgraph。这里仅留接口签名，与
DecisionPolicy 完全对齐；未来允许 pip install langgraph 时，把 next_action
体内换成基于 langgraph.StateGraph 编排的路由即可，核心循环零改动。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Decision, DecisionPolicy

if TYPE_CHECKING:
    from ..state import AgentState
    from ..tools import ToolRegistry


class LangGraphPolicy(DecisionPolicy):
    def __init__(self, registry=None, *, graph_config: dict | None = None) -> None:
        # 故意不 import langgraph：避免污染默认 policy 选择。
        raise ImportError(
            "LangGraphPolicy 未启用：需要先 pip install langgraph。"
            "当前项目坚持零依赖路线，请使用 RuleBasedPolicy 或 LLMReActPolicy。"
        )

    def next_action(self, state: "AgentState", registry: "ToolRegistry") -> Decision:  # pragma: no cover
        # 计划实现：在 __init__ 里 build 一个 StateGraph[AgentState]，
        # 节点 = 每个工具 + 一个复用 next_action 判定的路由节点；
        # 这里把 graph.invoke(state) 的输出映射成 Decision。
        raise NotImplementedError

    def name(self) -> str:  # pragma: no cover
        return "langgraph"
