from .config import ChunkSettings
from .models import Chapter, ChildChunk, ParentChunk


def _windowed_chunks(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    chunks: list[str] = []
    start = 0
    text_length = len(stripped)
    while start < text_length:
        end = min(text_length, start + target_chars)
        if end < text_length:
            boundary = max(
                stripped.rfind("。", start, min(text_length, end + 80)),
                stripped.rfind("\n", start, min(text_length, end + 80)),
            )
            if boundary > start + int(target_chars * 0.6):
                end = boundary + 1
        chunk = stripped[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        next_start = max(end - overlap_chars, start + 1)
        start = next_start
    return chunks


def build_chunk_hierarchy(
    book_id: str,
    chapters: list[Chapter],
    settings: ChunkSettings,
) -> tuple[list[ParentChunk], list[ChildChunk]]:
    parent_chunks: list[ParentChunk] = []
    child_chunks: list[ChildChunk] = []

    for chapter in chapters:
        parent_texts = _windowed_chunks(
            chapter.content,
            target_chars=settings.parent_target_chars,
            overlap_chars=settings.parent_overlap_chars,
        )
        for parent_index, parent_text in enumerate(parent_texts, start=1):
            parent_id = f"{book_id}:p:{chapter.number}:{parent_index}"
            parent = ParentChunk(
                chunk_id=parent_id,
                book_id=book_id,
                chapter_number=chapter.number,
                chapter_title=chapter.title,
                chunk_index=parent_index,
                text=parent_text,
            )
            parent_chunks.append(parent)

            child_texts = _windowed_chunks(
                parent_text,
                target_chars=settings.child_target_chars,
                overlap_chars=settings.child_overlap_chars,
            )
            for child_index, child_text in enumerate(child_texts, start=1):
                child_chunks.append(
                    ChildChunk(
                        chunk_id=f"{parent_id}:c:{child_index}",
                        parent_id=parent_id,
                        book_id=book_id,
                        chapter_number=chapter.number,
                        chunk_index=child_index,
                        text=child_text,
                    )
                )

    return parent_chunks, child_chunks

