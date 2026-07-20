from dataclasses import dataclass, field


@dataclass(slots=True)
class Chapter:
    number: int
    title: str
    content: str
    start_offset: int = 0
    end_offset: int = 0


@dataclass(slots=True)
class ParentChunk:
    chunk_id: str
    book_id: str
    chapter_number: int
    chapter_title: str
    chunk_index: int
    text: str


@dataclass(slots=True)
class ChildChunk:
    chunk_id: str
    parent_id: str
    book_id: str
    chapter_number: int
    chunk_index: int
    text: str


@dataclass(slots=True)
class EntityMention:
    entity_name: str
    chapter_number: int
    excerpt: str
    position_in_chapter: int


@dataclass(slots=True)
class EntityRecord:
    name: str
    first_chapter_number: int
    aliases: list[str] = field(default_factory=list)
    mentions: list[EntityMention] = field(default_factory=list)
    confidence: float | None = None


@dataclass(slots=True)
class RelationMention:
    source_entity: str
    target_entity: str
    relation_type: str
    chapter_number: int
    excerpt: str


@dataclass(slots=True)
class RelationRecord:
    source_entity: str
    target_entity: str
    relation_type: str
    first_chapter_number: int
    mentions: list[RelationMention] = field(default_factory=list)
    confidence: float | None = None


@dataclass(slots=True)
class ThemeMention:
    theme_name: str
    chapter_number: int
    excerpt: str
    position_in_chapter: int


@dataclass(slots=True)
class ThemeRecord:
    name: str
    first_chapter_number: int
    aliases: list[str] = field(default_factory=list)
    mentions: list[ThemeMention] = field(default_factory=list)


@dataclass(slots=True)
class EventRecord:
    chapter_number: int
    chapter_title: str
    event_type: str
    summary: str
    excerpt: str
    entities: list[str] = field(default_factory=list)
    confidence: float | None = None


@dataclass(slots=True)
class SearchHit:
    score: float
    chapter_number: int
    chapter_title: str
    parent_id: str
    child_text: str
    parent_text: str


@dataclass(slots=True)
class BookInfo:
    book_id: str
    title: str
    source_path: str
    chapter_count: int
    entity_count: int
    book_group: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceCard:
    chapter_number: int
    chapter_title: str
    excerpt: str
    reason: str


@dataclass(slots=True)
class MemoryCard:
    question: str
    intent: str
    answer: str
    progress_chapter: int
    spoiler_blocked: bool = False
    entity_name: str | None = None
    summary: str | None = None
    evidence: list[EvidenceCard] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    user_preferences: dict[str, object] = field(default_factory=dict)
    query_understanding: dict[str, object] = field(default_factory=dict)
    answer_synthesis: dict[str, object] = field(default_factory=dict)
    answer_validation: dict[str, object] = field(default_factory=dict)
