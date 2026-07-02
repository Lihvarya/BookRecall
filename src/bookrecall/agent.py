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

    def ask(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
        progress_chapter: int | None = None,
    ) -> str:
        effective_progress = progress_chapter
        if effective_progress is None:
            effective_progress = self.store.get_progress(book_id, user_id)
        if effective_progress is None:
            effective_progress = self.store.get_max_chapter(book_id)

        entities = self.store.list_entities(book_id)
        matched_entities = [entity for entity in entities if entity in question]
        intent = classify_intent(question, matched_entities)

        if intent == "first_appearance" and matched_entities:
            return self._answer_first_appearance(book_id, matched_entities[0], effective_progress)
        if intent == "entity_timeline" and matched_entities:
            return self._answer_entity_timeline(book_id, matched_entities[0], effective_progress)
        return self._answer_semantic(book_id, question, effective_progress, intent)

    def _answer_first_appearance(self, book_id: str, entity_name: str, progress_chapter: int) -> str:
        entity = self.store.get_entity(book_id, entity_name)
        if entity is None:
            return f"我在当前索引里还没找到“{entity_name}”这个实体，建议先补充实体词表后重新建索引。"

        first_chapter = int(entity["first_chapter_number"])
        if first_chapter > progress_chapter:
            return (
                f"截至第 {progress_chapter} 章，我不会剧透“{entity_name}”的首次登场位置。\n"
                f"结论：在你当前已读范围内，它还没有出现。"
            )

        mentions = self.store.get_entity_mentions(book_id, entity_name, max_chapter=progress_chapter)
        excerpt = mentions[0]["excerpt"] if mentions else "暂无摘录。"
        return (
            f"问题类型：实体首次出现\n"
            f"阅读进度保护：已限制到第 {progress_chapter} 章\n"
            f"定位结果：“{entity_name}”第一次出现于第 {first_chapter} 章。\n"
            f"证据摘录：{excerpt}"
        )

    def _answer_entity_timeline(self, book_id: str, entity_name: str, progress_chapter: int) -> str:
        visible_mentions = self.store.get_entity_mentions(book_id, entity_name, max_chapter=progress_chapter)
        if not visible_mentions:
            return f"截至第 {progress_chapter} 章，我还没有检索到“{entity_name}”的出现记录。"

        visible_chapters: list[int] = []
        snippets: list[str] = []
        for mention in visible_mentions:
            chapter_number = int(mention["chapter_number"])
            if chapter_number not in visible_chapters:
                visible_chapters.append(chapter_number)
            if len(snippets) < 3:
                snippets.append(f"第 {chapter_number} 章：{mention['excerpt']}")

        chapter_list = "、".join(f"第 {number} 章" for number in visible_chapters)
        return (
            f"问题类型：实体轨迹追踪\n"
            f"阅读进度保护：已限制到第 {progress_chapter} 章\n"
            f"“{entity_name}”在你当前已读范围内出现在：{chapter_list}。\n"
            f"记忆唤醒片段：\n- " + "\n- ".join(snippets)
        )

    def _answer_semantic(self, book_id: str, question: str, progress_chapter: int, intent: str) -> str:
        hits = self.retriever.search(book_id, question, max_chapter=progress_chapter)
        if not hits:
            return (
                f"截至第 {progress_chapter} 章，我没有找到足够相关的证据。\n"
                "你可以试着换一个更具体的问题，或者补充实体词表后重建索引。"
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
            return (
                f"问题类型：语义回忆\n"
                f"阅读进度保护：已限制到第 {progress_chapter} 章\n"
                f"{cloud_answer}\n"
                "证据定位：\n- " + "\n- ".join(evidence_lines)
            )

        summary = "；".join(
            f"第 {hit.chapter_number} 章重点提到：{hit.child_text[:80].strip()}"
            for hit in hits[:3]
        )
        return (
            f"问题类型：语义回忆\n"
            f"阅读进度保护：已限制到第 {progress_chapter} 章\n"
            f"基于已读范围内的证据，我认为最相关的线索是：{summary}\n"
            "证据定位：\n- " + "\n- ".join(evidence_lines)
        )

