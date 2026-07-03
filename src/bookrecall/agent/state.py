"""Agent 运行态。

一次 `ask_card` 调用对应一个 `AgentState` 生命周期：
入口锁定阅读进度 → 在 ReAct 循环里被各 policy 读写 → finalize 成 MemoryCard。
"""

from dataclasses import dataclass, field

from ..models import EvidenceCard


@dataclass
class ToolCallTrace:
    """单步工具调用的记录，仅用于调试与 LLM policy 的上下文，不进 MemoryCard。"""

    step: int
    tool_name: str
    arguments: dict
    thought: str
    observation_summary: str
    spoiler_blocked: bool = False
    hit_count: int = 0

    def observation(self) -> dict:
        """把摘要还原为结构化观察（policy 内部用），由工具在 ingest 时挂上去。"""
        return getattr(self, "_observation", {})


@dataclass
class AgentState:
    book_id: str
    question: str
    user_id: str = "default"
    # 有效阅读进度：所有工具的 max_charter 都据此，入口一次性锁定。
    progress_chapter: int = 0
    intent: str = "semantic_search"  # 初始分类，循环中可被 policy 修正
    matched_entities: list[str] = field(default_factory=list)
    primary_entity: str | None = None  # 别名解析后的规范名

    evidence: list[EvidenceCard] = field(default_factory=list)
    spoiler_blocked: bool = False
    raw_hits: list[dict] = field(default_factory=list)  # 供 policy 决策的中间观察
    last_query: str | None = None

    trace: list[ToolCallTrace] = field(default_factory=list)
    called_tools: set[str] = field(default_factory=set)
    step: int = 0
    max_steps: int = 6  # RuleBased 上限 6；LLM 上限 8

    # 终止信号
    terminal: bool = False
    answer: str | None = None
    summary: str | None = None
    suggestions: list[str] = field(default_factory=list)
