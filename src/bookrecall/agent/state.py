"""Agent 运行态。
一次 `ask_card` 调用对应一个 `AgentState` 生命周期：
入口锁定阅读进度 -> 在 ReAct 循环里被 policy 读写 -> finalize 成 `MemoryCard`。
"""

from dataclasses import dataclass, field

from ..models import EvidenceCard


@dataclass
class ToolCallTrace:
    """单步工具调用记录，仅用于调试与 LLM policy 上下文。"""

    step: int
    tool_name: str
    arguments: dict
    thought: str
    observation_summary: str
    spoiler_blocked: bool = False
    hit_count: int = 0
    elapsed_ms: float | None = None
    status: str = "ok"

    def observation(self) -> dict:
        """把摘要还原为结构化观察，由工具在 ingest 时挂入。"""
        return getattr(self, "_observation", {})


@dataclass
class AgentState:
    book_id: str
    question: str
    user_id: str = "default"
    session_id: str | None = None
    # 有效阅读进度：所有工具的 max_chapter 都基于此值，在入口一次性锁定。
    progress_chapter: int = 0
    intent: str = "semantic_search"
    matched_entities: list[str] = field(default_factory=list)
    matched_themes: list[str] = field(default_factory=list)
    primary_entity: str | None = None
    query_understanding: dict[str, object] = field(default_factory=dict)
    query_understanding_error: str = ""
    recent_turns: list[dict[str, object]] = field(default_factory=list)
    user_preferences: dict[str, object] = field(default_factory=dict)
    evidence: list[EvidenceCard] = field(default_factory=list)
    spoiler_blocked: bool = False
    raw_hits: list[dict] = field(default_factory=list)
    last_query: str | None = None

    trace: list[ToolCallTrace] = field(default_factory=list)
    called_tools: set[str] = field(default_factory=set)
    step: int = 0
    max_steps: int = 6

    terminal: bool = False
    answer: str | None = None
    summary: str | None = None
    suggestions: list[str] = field(default_factory=list)
    answer_synthesis: dict[str, object] = field(default_factory=dict)
