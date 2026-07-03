"""BookRecallAgent：ReAct 核心。

- ask_card: 一次性锁定进度 → 建 registry → 选 policy → 循环决策/调工具/剪枝 → finalize
- ask/ask_card 签名与旧版完全一致
- render_text/render_json/to_payload 仍挂在类上，转发到 render 模块
- 三重防剧透：(a) 工具内部用进度做 max_chapter (b) _clamp_max_chapter 钳制 (c) _prune_evidence 兜底
"""

from __future__ import annotations

from ..cloud import OpenAICompatibleReasoner
from ..config import DEFAULT_SEARCH_SETTINGS
from ..models import EvidenceCard, MemoryCard
from ..retrieval import LocalRetriever
from ..storage import BookRecallStore
from .render import render_json, render_text, to_payload
from .state import AgentState
from .tools import build_default_registry, ToolRegistry
from .policies.base import Decision, DecisionPolicy
from .policies.rule_based import RuleBasedPolicy, INTENT_LABELS


class BookRecallAgent:
    def __init__(
        self,
        store: BookRecallStore,
        *,
        policy: DecisionPolicy | None = None,
        retriever: LocalRetriever | None = None,
        reasoner: OpenAICompatibleReasoner | None = None,
    ) -> None:
        self.store = store
        self.retriever = retriever or LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)
        self.reasoner = reasoner or OpenAICompatibleReasoner()
        self._injected_policy = policy

    # -- policy 选择 --
    def _select_policy(self) -> DecisionPolicy:
        if self._injected_policy is not None:
            return self._injected_policy
        if self.reasoner.enabled:
            try:
                from .policies.llm_react import LLMReActPolicy  # 局部 import，避免无 key 时的多余开销
                return LLMReActPolicy(self.reasoner)
            except Exception:  # pragma: no cover - 防御性
                return RuleBasedPolicy(self.reasoner)
        return RuleBasedPolicy(self.reasoner)

    # -- 对外入口，签名与旧版完全一致 --
    def ask_card(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
        progress_chapter: int | None = None,
    ) -> MemoryCard:
        state = self._init_state(book_id, question, user_id, progress_chapter)
        registry = build_default_registry(self.store, self.retriever)
        policy = self._select_policy()

        llm_failures = 0
        MAX_LLM_FAILURES = 2
        use_rule_fallback = False
        fallback_policy = RuleBasedPolicy(reasoner=None)  # 关掉 reasoner 的纯规则版

        while not state.terminal and state.step < state.max_steps:
            chosen = fallback_policy if use_rule_fallback else policy
            decision = chosen.next_action(state, registry)
            state.step += 1

            if decision.is_terminal:
                self._apply_terminal(state, decision)
                break

            if decision.tool_call is None:
                # 无效决策（LLM 解析失败或编造工具）
                if chosen is fallback_policy:
                    # 规则策略也不给工具 = 兜底终止
                    state.terminal = True
                    break
                llm_failures += 1
                state.trace.append(_bad_tool_trace(state.step, "（无效决策已记）"))
                if llm_failures >= MAX_LLM_FAILURES:
                    use_rule_fallback = True
                continue

            tool = registry.get(decision.tool_call.name)
            if tool is None:
                state.trace.append(_bad_tool_trace(state.step, decision.tool_call.name))
                continue

            args = self._clamp_max_chapter(tool, decision.tool_call.arguments, state.progress_chapter)
            result = tool.run(state, args)
            self._ingest_result(state, tool.schema.name, decision.tool_call, result)
            self._prune_evidence(state)

        return self._finalize_memory_card(state)

    def ask(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
        progress_chapter: int | None = None,
    ) -> str:
        return self.render_text(
            self.ask_card(
                book_id=book_id,
                question=question,
                user_id=user_id,
                progress_chapter=progress_chapter,
            )
        )

    # render 类方法（保持旧 API）
    def render_text(self, card: MemoryCard) -> str:
        return render_text(card)

    def render_json(self, card: MemoryCard) -> str:
        return render_json(card)

    def to_payload(self, card: MemoryCard) -> dict[str, object]:
        return to_payload(card)

    # -- 状态初始化 --
    def _init_state(self, book_id: str, question: str, user_id: str, progress: int | None) -> AgentState:
        effective = progress
        if effective is None:
            effective = self.store.get_progress(book_id, user_id)
        if effective is None:
            effective = self.store.get_max_chapter(book_id)

        entities = self.store.list_entities(book_id)
        matched = self._match_entities(book_id, question, entities)

        state = AgentState(
            book_id=book_id,
            question=question,
            user_id=user_id,
            progress_chapter=int(effective or 0),
            matched_entities=matched,
            primary_entity=matched[0] if matched else None,
        )
        # 初始意图占位；policy 的 step0 会基于 matched_entities 正式分类。
        state.intent = "semantic_search"
        return state

    def _match_entities(self, book_id: str, question: str, _entities: list[str]) -> list[str]:
        """与旧 agent._match_entities 行为完全一致。"""
        matched = self.store.match_entity_candidates(book_id, question)
        if matched:
            return matched
        compact = question.replace("【", "").replace("】", "").replace("《", "").replace("》", "")
        matched = self.store.match_entity_candidates(book_id, compact)
        if matched:
            return matched
        fallback: list[str] = []
        for raw in question.replace("？", " ").replace("。", " ").replace("，", " ").split():
            resolved = self.store.resolve_entity_name(book_id, raw)
            if resolved and resolved not in fallback:
                fallback.append(resolved)
        return fallback

    # -- 工具结果摄入 --
    def _ingest_result(self, state: AgentState, tool_name: str, call, result: dict) -> None:
        state.called_tools.add(tool_name)
        # 把结构化观察挂到 trace（policy 会回读）
        trace = _trace_for(state, state.step, tool_name, call, result)
        trace._observation = result  # type: ignore[attr-defined]
        state.trace.append(trace)

        # 任何工具一旦触发 spoiler_blocked，传染到整次回答的标记。
        if result.get("spoiler_blocked"):
            state.spoiler_blocked = True

        # 别名工具：把规范名写回 primary_entity
        if tool_name == "lookup_entity_aliases" and result.get("found"):
            canonical = result.get("canonical_name")
            if canonical:
                state.primary_entity = canonical
                if canonical not in state.matched_entities:
                    state.matched_entities.insert(0, canonical)
            # 别名解析完成后才正式分类（matched_entities 已确定）
            from .policies.rule_based import classify_intent
            state.intent = classify_intent(state.question, state.matched_entities)

        # first_appearance 工具：若有 excerpt 则沉淀一条证据
        if tool_name == "lookup_first_appearance":
            self._add_evidence(state, result, kind="first")

        # timeline 工具：把 fragments 沉淀为证据
        if tool_name == "lookup_timeline":
            for frag in result.get("fragments", []):
                self._add_evidence_from(state, int(frag["chapter_number"]), frag.get("chapter_title", ""),
                                        frag.get("excerpt", ""), "实体在当前阅读范围内的出现片段")

        # search_evidence 工具：把 hits 沉淀为 raw_hits 与证据
        if tool_name == "search_evidence":
            state.raw_hits = result.get("hits", [])
            state.last_query = call.arguments.get("query")
            for hit in state.raw_hits:
                self._add_evidence_from(
                    state,
                    int(hit["chapter_number"]),
                    hit.get("chapter_title", ""),
                    hit.get("child_text", ""),
                    "与当前问题最相关的检索命中",
                )

    def _add_evidence(self, state: AgentState, result: dict, kind: str) -> None:
        chapter = result.get("first_chapter_number")
        if chapter is None or result.get("spoiler_blocked"):
            return
        self._add_evidence_from(
            state,
            int(chapter),
            result.get("chapter_title", ""),
            result.get("excerpt", ""),
            "首次提及该实体的正文片段" if kind == "first" else "实体在当前阅读范围内的出现片段",
        )

    def _add_evidence_from(
        self, state, chapter_number: int, chapter_title: str, excerpt: str, reason: str
    ) -> None:
        if not excerpt:
            return
        # 去重（章节号+摘录前40字）
        key = (chapter_number, excerpt[:40])
        for ev in state.evidence:
            if (ev.chapter_number, ev.excerpt[:40]) == key:
                return
        state.evidence.append(
            EvidenceCard(
                chapter_number=chapter_number,
                chapter_title=chapter_title or self._chapter_title(state, chapter_number),
                excerpt=excerpt,
                reason=reason,
            )
        )

    # -- 防剧透三重保险 --
    def _clamp_max_chapter(self, tool, arguments: dict, progress: int) -> dict:
        if tool.schema.parameters.get("max_chapter"):
            given = arguments.get("max_chapter")
            if given is not None:
                arguments = dict(arguments)
                arguments["max_chapter"] = min(int(given), progress)
        return arguments

    def _prune_evidence(self, state: AgentState) -> None:
        kept: list[EvidenceCard] = []
        for ev in state.evidence:
            if ev.chapter_number <= state.progress_chapter:
                kept.append(ev)
            else:
                state.spoiler_blocked = True
        state.evidence = kept

    # -- 终止与 finalize --
    def _apply_terminal(self, state: AgentState, decision: Decision) -> None:
        state.terminal = True
        if decision.answer is not None:
            state.answer = decision.answer
        if decision.summary is not None:
            state.summary = decision.summary
        if decision.suggestions is not None:
            state.suggestions = decision.suggestions
        if decision.intent_override is not None:
            state.intent = decision.intent_override
        if decision.entity_name is not None:
            state.primary_entity = decision.entity_name

    def _finalize_memory_card(self, state: AgentState) -> MemoryCard:
        # 没有显式 answer（步数耗尽等）→ 用已有证据兜底
        if state.answer is None:
            state.answer = self._fallback_answer(state)
        intent_label = INTENT_LABELS.get(state.intent, "语义回忆")

        # 证据数量按意图收口（与旧版一致）
        evidence = state.evidence
        if state.intent == "entity_timeline":
            evidence = state.evidence[:3]
        elif state.intent == "first_appearance":
            evidence = state.evidence[:1]
        elif state.intent in ("compare", "causal", "semantic_search"):
            evidence = state.evidence[: DEFAULT_SEARCH_SETTINGS.top_k_parents]

        return MemoryCard(
            question=state.question,
            intent=intent_label,
            answer=state.answer,
            progress_chapter=state.progress_chapter,
            spoiler_blocked=state.spoiler_blocked,
            entity_name=state.primary_entity,
            summary=state.summary,
            evidence=evidence,
            suggestions=state.suggestions or [],
        )

    def _fallback_answer(self, state: AgentState) -> str:
        if not state.evidence and not state.raw_hits:
            return f"截至第 {state.progress_chapter} 章，我没有找到足够相关的证据。"
        if state.raw_hits:
            return "；".join(
                f"第 {h['chapter_number']} 章重点提到：{h['child_text'][:80].strip()}"
                for h in state.raw_hits[:3]
            )
        return "；".join(
            f"第 {ev.chapter_number} 章提到：{ev.excerpt[:80].strip()}" for ev in state.evidence[:3]
        )

    def _chapter_title(self, state: AgentState, chapter_number: int) -> str:
        rows = self.store.get_chapter_summaries(state.book_id, max_chapter=chapter_number)
        for row in rows:
            if int(row["chapter_number"]) == chapter_number:
                return str(row["chapter_title"])
        return f"第 {chapter_number} 章"


# -- trace 工具函数 --

def _trace_for(state, step, tool_name, call, result):
    from .state import ToolCallTrace

    hit_count = 0
    if "hits" in result:
        hit_count = len(result.get("hits", []))
    elif "chapters" in result:
        hit_count = len(result.get("chapters", []))
    elif "count" in result:
        hit_count = int(result.get("count", 0))
    spoiler = bool(result.get("spoiler_blocked"))
    obs_summary = _summarize_observation(result)
    return ToolCallTrace(
        step=step,
        tool_name=tool_name,
        arguments=dict(call.arguments),
        thought=call.thought,
        observation_summary=obs_summary,
        spoiler_blocked=spoiler,
        hit_count=hit_count,
    )


def _bad_tool_trace(step: int, note: str):
    from .state import ToolCallTrace

    return ToolCallTrace(step=step, tool_name=note, arguments={}, thought="(无效)", observation_summary="")


def _summarize_observation(result: dict) -> str:
    if "found" in result:
        return f"found={result.get('found')}, first={result.get('first_chapter_number')}"
    if "chapters" in result:
        return f"chapters={result.get('chapters', [])[:5]}"
    if "hits" in result:
        return f"hits={len(result.get('hits', []))}"
    if "canonical_name" in result:
        return f"canonical={result.get('canonical_name')}"
    if "summary" in result:
        return f"summary_len={len(str(result.get('summary', '')))}"
    return ""
