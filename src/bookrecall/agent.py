import json

from .models import EvidenceCard, MemoryCard
from .cloud import OpenAICompatibleReasoner
from .config import DEFAULT_SEARCH_SETTINGS
from .retrieval import LocalRetriever
from .storage import BookRecallStore


def classify_intent(question: str, matched_entities: list[str]) -> str:
    if matched_entities and any(keyword in question for keyword in ("第一次", "首次", "最早", "初次")):
        return "first_appearance"
    if matched_entities and any(keyword in question for keyword in ("还有出现", "后来", "再次", "轨迹", "出现过吗")):
        return "entity_timeline"
    if any(keyword in question for keyword in ("变化", "对比", "前后")):
        return "compare"
    if any(keyword in question for keyword in ("怎么", "如何", "为什么", "原因")):
        return "causal"
    return "semantic_search"


class BookRecallAgent:
    def __init__(self, store: BookRecallStore) -> None:
        self.store = store
        self.retriever = LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)
        self.reasoner = OpenAICompatibleReasoner()

    def ask_card(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
        progress_chapter: int | None = None,
    ) -> MemoryCard:
        effective_progress = progress_chapter
        if effective_progress is None:
            effective_progress = self.store.get_progress(book_id, user_id)
        if effective_progress is None:
            effective_progress = self.store.get_max_chapter(book_id)

        entities = self.store.list_entities(book_id)
        matched_entities = self._match_entities(book_id, question, entities)
        intent = classify_intent(question, matched_entities)

        if intent == "first_appearance" and matched_entities:
            return self._answer_first_appearance(question, book_id, matched_entities[0], effective_progress)
        if intent == "entity_timeline" and matched_entities:
            return self._answer_entity_timeline(question, book_id, matched_entities[0], effective_progress)
        return self._answer_semantic(book_id, question, effective_progress, intent)

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

    def render_text(self, card: MemoryCard) -> str:
        lines = [
            f"问题类型：{card.intent}",
            f"阅读进度保护：已限制到第 {card.progress_chapter} 章",
        ]
        if card.entity_name:
            lines.append(f"关联实体：{card.entity_name}")
        lines.append(f"结论：{card.answer}")
        if card.summary:
            lines.append(f"补充说明：{card.summary}")
        if card.evidence:
            lines.append("证据定位：")
            lines.extend(
                f"- 第 {item.chapter_number} 章《{item.chapter_title}》：{item.excerpt}"
                for item in card.evidence
            )
        if card.suggestions:
            lines.append("你接下来还可以问：")
            lines.extend(f"- {item}" for item in card.suggestions)
        return "\n".join(lines)

    def to_payload(self, card: MemoryCard) -> dict[str, object]:
        return {
            "question": card.question,
            "intent": card.intent,
            "answer": card.answer,
            "progress_chapter": card.progress_chapter,
            "spoiler_blocked": card.spoiler_blocked,
            "entity_name": card.entity_name,
            "summary": card.summary,
            "evidence": [
                {
                    "chapter_number": item.chapter_number,
                    "chapter_title": item.chapter_title,
                    "excerpt": item.excerpt,
                    "reason": item.reason,
                }
                for item in card.evidence
            ],
            "suggestions": card.suggestions,
        }

    def render_json(self, card: MemoryCard) -> str:
        return json.dumps(self.to_payload(card), ensure_ascii=False, indent=2)

    def _match_entities(self, book_id: str, question: str, entities: list[str]) -> list[str]:
        matched = self.store.match_entity_candidates(book_id, question)
        if matched:
            return matched

        compact_question = question.replace("【", "").replace("】", "").replace("《", "").replace("》", "")
        matched = self.store.match_entity_candidates(book_id, compact_question)
        if matched:
            return matched

        fallback: list[str] = []
        for raw in question.replace("？", " ").replace("。", " ").replace("，", " ").split():
            resolved = self.store.resolve_entity_name(book_id, raw)
            if resolved and resolved not in fallback:
                fallback.append(resolved)
        return fallback

    def _answer_first_appearance(
        self,
        question: str,
        book_id: str,
        entity_name: str,
        progress_chapter: int,
    ) -> MemoryCard:
        entity = self.store.get_entity(book_id, entity_name)
        if entity is None:
            return MemoryCard(
                question=question,
                intent="实体首次出现",
                answer=f"我在当前索引里还没找到“{entity_name}”这个实体，建议先补充实体词表后重新建索引。",
                progress_chapter=progress_chapter,
                entity_name=entity_name,
                suggestions=[f"{entity_name} 可能还有哪些别名？", "能否给这本书补一份实体词表？"],
            )

        first_chapter = int(entity["first_chapter_number"])
        if first_chapter > progress_chapter:
            return MemoryCard(
                question=question,
                intent="实体首次出现",
                answer=f"在你当前已读范围内，“{entity_name}”还没有出现。",
                progress_chapter=progress_chapter,
                spoiler_blocked=True,
                entity_name=entity_name,
                summary=f"为了防止剧透，我没有暴露它在第 {progress_chapter} 章之后的首次登场位置。",
                suggestions=[f"把阅读进度推进后再问：{entity_name} 第一次出现在哪一章？"],
            )

        mentions = self.store.get_entity_mentions(book_id, entity_name, max_chapter=progress_chapter)
        excerpt = mentions[0]["excerpt"] if mentions else "暂无摘录。"
        return MemoryCard(
            question=question,
            intent="实体首次出现",
            answer=f"“{entity_name}”第一次出现于第 {first_chapter} 章。",
            progress_chapter=progress_chapter,
            entity_name=entity_name,
            evidence=[
                EvidenceCard(
                    chapter_number=first_chapter,
                    chapter_title=self._chapter_title(book_id, first_chapter),
                    excerpt=excerpt,
                    reason="首次提及该实体的正文片段",
                )
            ],
            suggestions=self._entity_followups(entity_name),
        )

    def _answer_entity_timeline(
        self,
        question: str,
        book_id: str,
        entity_name: str,
        progress_chapter: int,
    ) -> MemoryCard:
        visible_mentions = self.store.get_entity_mentions(book_id, entity_name, max_chapter=progress_chapter)
        if not visible_mentions:
            return MemoryCard(
                question=question,
                intent="实体轨迹追踪",
                answer=f"截至第 {progress_chapter} 章，我还没有检索到“{entity_name}”的出现记录。",
                progress_chapter=progress_chapter,
                entity_name=entity_name,
                suggestions=[f"{entity_name} 第一次出现在哪一章？"],
            )

        visible_chapters: list[int] = []
        evidence: list[EvidenceCard] = []
        for mention in visible_mentions:
            chapter_number = int(mention["chapter_number"])
            if chapter_number not in visible_chapters:
                visible_chapters.append(chapter_number)
            if len(evidence) < 3:
                evidence.append(
                    EvidenceCard(
                        chapter_number=chapter_number,
                        chapter_title=self._chapter_title(book_id, chapter_number),
                        excerpt=mention["excerpt"],
                        reason="实体在当前阅读范围内的出现片段",
                    )
                )

        chapter_list = "、".join(f"第 {number} 章" for number in visible_chapters)
        return MemoryCard(
            question=question,
            intent="实体轨迹追踪",
            answer=f"“{entity_name}”在你当前已读范围内出现在：{chapter_list}。",
            progress_chapter=progress_chapter,
            entity_name=entity_name,
            summary=f"共追踪到 {len(visible_chapters)} 个章节节点。",
            evidence=evidence,
            suggestions=self._timeline_followups(entity_name),
        )

    def _answer_semantic(self, book_id: str, question: str, progress_chapter: int, intent: str) -> MemoryCard:
        hits = self.retriever.search(book_id, question, max_chapter=progress_chapter)
        if not hits:
            return MemoryCard(
                question=question,
                intent="语义回忆",
                answer=f"截至第 {progress_chapter} 章，我没有找到足够相关的证据。",
                progress_chapter=progress_chapter,
                summary="你可以试着换一个更具体的问题，或者补充实体词表后重建索引。",
                suggestions=[
                    "把问题改成更明确的实体或章节线索。",
                    "补充实体词表后重新 build。",
                ],
            )

        evidence_lines = [
            f"第 {hit.chapter_number} 章《{hit.chapter_title}》：{hit.child_text}"
            for hit in hits
        ]
        prompt = (
            f"用户问题：{question}\n"
            f"当前只允许使用第 1 章到第 {progress_chapter} 章的证据，不能剧透后文。\n"
            f"问题类型：{intent}\n"
            "请先给结论，再给简短解释。\n"
            "证据：\n"
            + "\n".join(f"- {line}" for line in evidence_lines)
        )
        cloud_answer = self.reasoner.answer(prompt)
        if cloud_answer:
            return MemoryCard(
                question=question,
                intent="语义回忆",
                answer=cloud_answer,
                progress_chapter=progress_chapter,
                evidence=[
                    EvidenceCard(
                        chapter_number=hit.chapter_number,
                        chapter_title=hit.chapter_title,
                        excerpt=hit.child_text,
                        reason="与当前问题最相关的检索命中",
                    )
                    for hit in hits
                ],
                suggestions=self._build_suggestions(question, intent),
            )

        summary = "；".join(
            f"第 {hit.chapter_number} 章重点提到：{hit.child_text[:80].strip()}"
            for hit in hits[:3]
        )
        chapter_summaries = self.store.get_chapter_summaries(book_id, max_chapter=progress_chapter)
        context_summary = "；".join(
            f"第 {row['chapter_number']} 章：{row['summary']}"
            for row in chapter_summaries[:2]
        )
        return MemoryCard(
            question=question,
            intent="语义回忆",
            answer=f"基于已读范围内的证据，我认为最相关的线索是：{summary}",
            progress_chapter=progress_chapter,
            summary=context_summary if intent == "compare" else None,
            evidence=[
                EvidenceCard(
                    chapter_number=hit.chapter_number,
                    chapter_title=hit.chapter_title,
                    excerpt=hit.child_text,
                    reason="与当前问题最相关的检索命中",
                )
                for hit in hits
            ],
            suggestions=self._build_suggestions(question, intent),
        )

    def _build_suggestions(self, question: str, intent: str) -> list[str]:
        if intent == "compare":
            return ["把问题改成两个明确章节做对比。", "围绕同一主题继续问：它第一次被提出是在什么时候？"]
        if intent == "causal":
            return ["继续追问：这件事之前发生了什么？", "继续追问：这件事之后有什么后果？"]
        return [f"换个问法继续问：{question}", "把问题指向具体实体，会更容易得到精准回忆。"]

    def _entity_followups(self, entity_name: str) -> list[str]:
        if entity_name.endswith(("人", "者")):
            return [
                f"{entity_name}后来还有出现过吗？",
                f"{entity_name}和主角之间后来发生了什么？",
            ]
        return [
            f"{entity_name}后来还有出现过吗？",
            f"{entity_name}最后是怎么被拿到或使用的？",
        ]

    def _timeline_followups(self, entity_name: str) -> list[str]:
        if entity_name.endswith(("人", "者")):
            return [
                f"{entity_name} 第一次出现在哪一章？",
                f"{entity_name} 在已读范围里的关键作用是什么？",
            ]
        return [
            f"{entity_name} 第一次出现在哪一章？",
            f"{entity_name} 在已读范围里的关键作用是什么？",
        ]

    def _chapter_title(self, book_id: str, chapter_number: int) -> str:
        summaries = self.store.get_chapter_summaries(book_id, max_chapter=chapter_number)
        for row in summaries:
            if int(row["chapter_number"]) == chapter_number:
                return str(row["chapter_title"])
        return f"第 {chapter_number} 章"
