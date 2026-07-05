"""Optional LangGraph-backed decision policy.

The core project still runs without third-party dependencies.  When
``langgraph`` is installed, this policy compiles a tiny StateGraph that wraps
an existing DecisionPolicy and returns one ReAct decision per invocation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from .base import Decision, DecisionPolicy
from .rule_based import RuleBasedPolicy

if TYPE_CHECKING:
    from ..state import AgentState
    from ..tools import ToolRegistry


class LangGraphUnavailableError(ImportError):
    """Raised when the optional langgraph dependency is not installed."""


class _GraphState(TypedDict, total=False):
    agent_state: Any
    registry: Any
    decision: Decision


def is_langgraph_available() -> bool:
    try:
        import langgraph.graph  # type: ignore[import-not-found]

        return True
    except Exception:
        return False


class LangGraphPolicy(DecisionPolicy):
    """DecisionPolicy adapter powered by a compiled LangGraph StateGraph.

    This is intentionally a thin graph at the policy layer: BookRecall's core
    loop still owns tool execution, spoiler clamping and evidence pruning.
    The graph owns the decision node, which lets us introduce checkpointing or
    multi-node routing later without changing the public Agent contract.
    """

    def __init__(
        self,
        delegate: DecisionPolicy | None = None,
        *,
        graph_config: dict | None = None,
    ) -> None:
        self.delegate = delegate or RuleBasedPolicy()
        self.graph_config = graph_config or {}
        self._graph_app = self._compile_graph()

    def name(self) -> str:
        return "langgraph"

    def next_action(self, state: "AgentState", registry: "ToolRegistry") -> Decision:
        result = self._graph_app.invoke(
            {
                "agent_state": state,
                "registry": registry,
            },
            config=self.graph_config or None,
        )
        decision = result.get("decision")
        if isinstance(decision, Decision):
            return decision
        return Decision(is_terminal=False, tool_call=None)

    def _compile_graph(self):
        try:
            from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise LangGraphUnavailableError(
                "LangGraphPolicy 不可用：请先安装可选依赖 langgraph，"
                "例如 pip install -e .[full] 或单独安装 langgraph。"
            ) from exc

        def decide(graph_state: _GraphState) -> _GraphState:
            agent_state = graph_state["agent_state"]
            registry = graph_state["registry"]
            return {
                "decision": self.delegate.next_action(agent_state, registry),
            }

        graph = StateGraph(_GraphState)
        graph.add_node("decide", decide)
        graph.set_entry_point("decide")
        graph.add_edge("decide", END)
        return graph.compile()
