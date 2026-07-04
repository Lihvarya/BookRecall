"""BookRecallAgent: ReAct 核心执行器。"""

from __future__ import annotations

from ..cloud import OpenAICompatibleReasoner
from ..config import DEFAULT_SEARCH_SETTINGS
from ..models import EvidenceCard, MemoryCard
from ..retrieval import LocalRetriever, Retriever
from ..storage import BookRecallStore
from .policies.base import Decision, DecisionPolicy
from .policies.rule_based import INTENT_LABELS, RuleBasedPolicy
from .render import render_json, render_text, to_payload
from .state import AgentState, ToolCallTrace
from .tools import ToolRegistry, build_default_registry


class BookRecallAgent:
    def __init__(
        self,
        store: BookRecallStore,
        *,
        policy: DecisionPolicy | None = None,
        retriever: Retriever | None = None,
        reasoner: OpenAICompatibleReasoner | None = None,
    ) -> None:
        self.store = store
        self.retriever = retriever or LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)
        self.reasoner = reasoner or OpenAICompatibleReasoner()
        self._injected_policy = policy

    def _select_policy(self) -> DecisionPolicy:
        if self._injected_policy is not None:
            return self._injected_policy
        if self.reasoner.enabled:
            try:
                from .policies.llm_react import LLMReActPolicy

                return LLMReActPolicy(self.reasoner)
            except Exception:  # pragma: no cover
                return RuleBasedPolicy(self.reasoner)
        return RuleBasedPolicy(self.reasoner)

    def ask_card(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
        progress_chapter: int | None = None,
        session_id: str | None = None,
    ) -> MemoryCard:
        state = self._init_state(book_id, question, user_id, progress_chapter, session_id)
        registry = build_default_registry(self.store, self.retriever)
        policy = self._select_policy()

        llm_failures = 0
        max_llm_failures = 2
        use_rule_fallback = False
        fallback_policy = RuleBasedPolicy(reasoner=None)

        while not state.terminal and state.step < state.max_steps:
            chosen = fallback_policy if use_rule_fallback else policy
            decision = chosen.next_action(state, registry)
            state.step += 1

            if decision.is_terminal:
                self._apply_terminal(state, decision)
                break

            if decision.tool_call is None:
                if chosen is fallback_policy:
                    state.terminal = True
                    break
                llm_failures += 1
                state.trace.append(_bad_tool_trace(state.step, "（无效决策已记录）"))
                if llm_failures >= max_llm_failures:
                    use_rule_fallback = True
                continue

            tool = registry.get(decision.tool_call.name)
            if tool is None:
                state.trace.append(_bad_tool_trace(state.step, decision.tool_call.name))
                continue

            arguments = self._clamp_max_chapter(tool, decision.tool_call.arguments, state.progress_chapter)
            result = tool.run(state, arguments)
            self._ingest_result(state, tool.schema.name, decision.tool_call, result)
            self._prune_evidence(state)

        card = self._finalize_memory_card(state)
        self._persist_session_turn(state, card)
        return card

    def ask(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
        progress_chapter: int | None = None,
        session_id: str | None = None,
    ) -> str:
        return self.render_text(
            self.ask_card(
                book_id=book_id,
                question=question,
                user_id=user_id,
                progress_chapter=progress_chapter,
                session_id=session_id,
            )
        )

    def render_text(self, card: MemoryCard) -> str:
        return render_text(card)

    def render_json(self, card: MemoryCard) -> str:
        return render_json(card)

    def to_payload(self, card: MemoryCard) -> dict[str, object]:
        return to_payload(card)

    def _init_state(
        self,
        book_id: str,
        question: str,
        user_id: str,
        progress: int | None,
        session_id: str | None,
    ) -> AgentState:
        effective = progress
        if effective is None:
            effective = self.store.get_progress(book_id, user_id)
        if effective is None:
            effective = self.store.get_max_chapter(book_id)

        matched = self._match_entities(book_id, question, self.store.list_entities(book_id))
        matched_themes = self.store.match_theme_candidates(book_id, question)
        recent_turns: list[dict[str, object]] = []
        if session_id:
            recent_turns = self.store.list_agent_turns(book_id, user_id, session_id, limit=4)
        if not matched:
            recent_entity = self._recent_session_entity(recent_turns)
            if recent_entity:
                matched = [recent_entity]

        state = AgentState(
            book_id=book_id,
            question=question,
            user_id=user_id,
            session_id=session_id,
            progress_chapter=int(effective or 0),
            matched_entities=matched,
            matched_themes=matched_themes,
            primary_entity=matched[0] if matched else None,
            recent_turns=recent_turns,
        )
        state.intent = "semantic_search"
        return state

    @staticmethod
    def _recent_session_entity(recent_turns: list[dict[str, object]]) -> str | None:
        for turn in reversed(recent_turns):
            entity_name = turn.get("entity_name")
            if isinstance(entity_name, str) and entity_name.strip():
                return entity_name.strip()
        return None

    def _match_entities(self, book_id: str, question: str, _entities: list[str]) -> list[str]:
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

    def _ingest_result(self, state: AgentState, tool_name: str, call, result: dict) -> None:
        state.called_tools.add(tool_name)
        trace = _trace_for(state.step, tool_name, call, result)
        trace._observation = result  # type: ignore[attr-defined]
        state.trace.append(trace)

        if result.get("spoiler_blocked"):
            state.spoiler_blocked = True

        if tool_name == "lookup_entity_aliases" and result.get("found"):
            canonical = result.get("canonical_name")
            if canonical:
                state.primary_entity = canonical
                if canonical not in state.matched_entities:
                    state.matched_entities.insert(0, canonical)
            from .policies.rule_based import classify_intent

            state.intent = classify_intent(state.question, state.matched_entities, state.matched_themes)

        if tool_name == "lookup_first_appearance":
            self._add_evidence(state, result, reason="首次提及该实体的正文片段")

        if tool_name == "lookup_timeline":
            for frag in result.get("fragments", []):
                self._add_evidence_from(
                    state,
                    int(frag["chapter_number"]),
                    str(frag.get("chapter_title", "")),
                    str(frag.get("excerpt", "")),
                    "实体在当前阅读范围内的出现片段",
                )

        if tool_name == "lookup_relations":
            for relation in result.get("relations", []):
                for frag in relation.get("fragments", []):
                    chapter_number = int(frag["chapter_number"])
                    self._add_evidence_from(
                        state,
                        chapter_number,
                        self._chapter_title(state, chapter_number),
                        str(frag.get("excerpt", "")),
                        f"{relation.get('source_entity')} 与 {relation.get('target_entity')} 的关系证据",
                    )

        if tool_name == "search_theme":
            theme_name = str(result.get("theme_name", ""))
            for frag in result.get("fragments", []):
                self._add_evidence_from(
                    state,
                    int(frag["chapter_number"]),
                    str(frag.get("chapter_title", "")),
                    str(frag.get("excerpt", "")),
                    f"主题“{theme_name}”的线索片段",
                )

        if tool_name == "search_events":
            for event in result.get("events", []):
                self._add_evidence_from(
                    state,
                    int(event["chapter_number"]),
                    str(event.get("chapter_title", "")),
                    str(event.get("excerpt", "")),
                    f"{event.get('event_type', '事件')}事件链证据",
                )

        if tool_name == "search_evidence":
            state.raw_hits = result.get("hits", [])
            state.last_query = call.arguments.get("query")
            for hit in state.raw_hits:
                self._add_evidence_from(
                    state,
                    int(hit["chapter_number"]),
                    str(hit.get("chapter_title", "")),
                    str(hit.get("child_text", "")),
                    "与当前问题最相关的检索命中",
                )

    def _add_evidence(self, state: AgentState, result: dict, reason: str) -> None:
        chapter = result.get("first_chapter_number")
        if chapter is None or result.get("spoiler_blocked"):
            return
        self._add_evidence_from(
            state,
            int(chapter),
            str(result.get("chapter_title", "")),
            str(result.get("excerpt", "")),
            reason,
        )

    def _add_evidence_from(
        self,
        state: AgentState,
        chapter_number: int,
        chapter_title: str,
        excerpt: str,
        reason: str,
    ) -> None:
        if not excerpt:
            return
        key = (chapter_number, excerpt[:40])
        for evidence in state.evidence:
            if (evidence.chapter_number, evidence.excerpt[:40]) == key:
                return
        state.evidence.append(
            EvidenceCard(
                chapter_number=chapter_number,
                chapter_title=chapter_title or self._chapter_title(state, chapter_number),
                excerpt=excerpt,
                reason=reason,
            )
        )

    def _clamp_max_chapter(self, tool, arguments: dict, progress: int) -> dict:
        if tool.schema.parameters.get("max_chapter"):
            given = arguments.get("max_chapter")
            if given is not None:
                arguments = dict(arguments)
                arguments["max_chapter"] = min(int(given), progress)
        return arguments

    def _prune_evidence(self, state: AgentState) -> None:
        kept: list[EvidenceCard] = []
        for evidence in state.evidence:
            if evidence.chapter_number <= state.progress_chapter:
                kept.append(evidence)
            else:
                state.spoiler_blocked = True
        state.evidence = kept

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
        if state.answer is None:
            state.answer = self._fallback_answer(state)
        intent_label = INTENT_LABELS.get(state.intent, "语义回忆")

        evidence = state.evidence
        if state.intent == "entity_timeline":
            evidence = evidence[:3]
        elif state.intent == "first_appearance":
            evidence = evidence[:1]
        elif state.intent in {"theme_explore", "compare", "causal", "semantic_search"}:
            evidence = evidence[: DEFAULT_SEARCH_SETTINGS.top_k_parents]

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

    def _persist_session_turn(self, state: AgentState, card: MemoryCard) -> None:
        if not state.session_id:
            return
        self.store.append_agent_turn(
            book_id=state.book_id,
            user_id=state.user_id,
            session_id=state.session_id,
            question=state.question,
            intent=state.intent,
            entity_name=card.entity_name,
            answer=card.answer,
            summary=card.summary,
            progress_chapter=state.progress_chapter,
            matched_entities=state.matched_entities,
            trace=self._serialize_trace(state.trace),
        )

    @staticmethod
    def _serialize_trace(trace_items: list[ToolCallTrace]) -> list[dict[str, object]]:
        return [
            {
                "step": trace.step,
                "tool_name": trace.tool_name,
                "arguments": dict(trace.arguments),
                "thought": trace.thought,
                "observation_summary": trace.observation_summary,
                "spoiler_blocked": trace.spoiler_blocked,
                "hit_count": trace.hit_count,
            }
            for trace in trace_items
        ]

    def _fallback_answer(self, state: AgentState) -> str:
        if not state.evidence and not state.raw_hits:
            return f"截至第 {state.progress_chapter} 章，我没有找到足够相关的证据。"
        if state.raw_hits:
            return "；".join(
                f"第 {hit['chapter_number']} 章重点提到：{str(hit['child_text'])[:80].strip()}"
                for hit in state.raw_hits[:3]
            )
        return "；".join(
            f"第 {evidence.chapter_number} 章提到：{evidence.excerpt[:80].strip()}"
            for evidence in state.evidence[:3]
        )

    def _chapter_title(self, state: AgentState, chapter_number: int) -> str:
        rows = self.store.get_chapter_summaries(state.book_id, max_chapter=chapter_number)
        for row in rows:
            if int(row["chapter_number"]) == chapter_number:
                return str(row["chapter_title"])
        return f"第 {chapter_number} 章"


def _trace_for(step: int, tool_name: str, call, result: dict) -> ToolCallTrace:
    hit_count = 0
    if "hits" in result:
        hit_count = len(result.get("hits", []))
    elif "chapters" in result:
        hit_count = len(result.get("chapters", []))
    elif "count" in result:
        hit_count = int(result.get("count", 0))
    return ToolCallTrace(
        step=step,
        tool_name=tool_name,
        arguments=dict(call.arguments),
        thought=call.thought,
        observation_summary=_summarize_observation(result),
        spoiler_blocked=bool(result.get("spoiler_blocked")),
        hit_count=hit_count,
    )


def _bad_tool_trace(step: int, note: str) -> ToolCallTrace:
    return ToolCallTrace(step=step, tool_name=note, arguments={}, thought="(无效)", observation_summary="")


def _summarize_observation(result: dict) -> str:
    if "events" in result:
        return f"events={len(result.get('events', []))}"
    if "theme_name" in result:
        return f"theme={result.get('theme_name')}, fragments={len(result.get('fragments', []))}"
    if "relations" in result:
        return f"relations={len(result.get('relations', []))}"
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
