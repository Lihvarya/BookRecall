"""decision policy 抽象接口。

三种实现都实现 `next_action(state, registry) -> Decision`：
- RuleBasedPolicy：确定性多步路由（默认）
- LLMReActPolicy：调云端模型决定下一步（需 API key）
- LangGraphPolicy：可选图策略，需 pip install langgraph
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..state import AgentState
    from ..tools import ToolRegistry


@dataclass
class ToolCall:
    name: str
    arguments: dict
    thought: str = ""


@dataclass
class Decision:
    """policy 输出。要么调工具，要么直接终止给答案。"""

    is_terminal: bool
    tool_call: ToolCall | None = None
    answer: str | None = None
    summary: str | None = None
    intent_override: str | None = None
    suggestions: list[str] | None = field(default=None)
    entity_name: str | None = None  # 终止时可指定实体名（finalize 回落到 MemoryCard）


class DecisionPolicy(ABC):
    """ReAct 决策器。无状态（每次 ask 新建），但可读 state。"""

    @abstractmethod
    def next_action(self, state: "AgentState", registry: "ToolRegistry") -> Decision:
        """基于当前 state 给出下一步决策。"""

    @abstractmethod
    def name(self) -> str:
        """policy 名，用于 trace/日志。"""
