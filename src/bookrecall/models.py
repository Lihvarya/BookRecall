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
    mentions: list[EntityMention] = field(default_factory=list)


@dataclass(slots=True)
class SearchHit:
    score: float
    chapter_number: int
    chapter_title: str
    parent_id: str
    child_text: str
    parent_text: str

