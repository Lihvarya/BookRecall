"""BookRecall Agent 包。

将原有的单文件 `agent.py` 重构为可插拔 policy 的 ReAct 状态机：
- state.py     Agent 运行态
- tools.py     工具注册表与 6 个工具
- core.py       BookRecallAgent + ReAct 循环 + 防剧透剪枝 + 输出契约
- render.py     render_text / render_json / to_payload（对外契约不变）
- policies/     决策策略（规则版默认 / LLM-ReAct 可选 / LangGraph 预留）

为兼容旧导入路径 `from bookrecall.agent import BookRecallAgent`，
本包再导出核心类与意图分类函数。
"""

from .core import BookRecallAgent
from .policies.base import Decision, DecisionPolicy, ToolCall
from .policies.rule_based import RuleBasedPolicy, classify_intent

__all__ = [
    "BookRecallAgent",
    "Decision",
    "DecisionPolicy",
    "ToolCall",
    "RuleBasedPolicy",
    "classify_intent",
]
