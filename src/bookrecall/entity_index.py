import re
from pathlib import Path

from .config import ChunkSettings
from .models import Chapter, EntityMention, EntityRecord

AUTO_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"【([^】]{1,20})】"),
    re.compile(r"《([^》]{1,20})》"),
    re.compile(r"「([^」]{1,20})」"),
)


def load_entity_lexicon(path: str | None) -> list[str]:
    if not path:
        return []
    entity_file = Path(path)
    if not entity_file.exists():
        return []
    entities: list[str] = []
    for line in entity_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entities.append(stripped)
    return entities


def auto_discover_entities(text: str) -> list[str]:
    found: set[str] = set()
    for pattern in AUTO_ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            candidate = match.group(1).strip()
            if 1 < len(candidate) <= 20:
                found.add(candidate)
    return sorted(found, key=lambda item: (-len(item), item))


def build_entity_records(
    chapters: list[Chapter],
    entities: list[str],
    settings: ChunkSettings,
) -> list[EntityRecord]:
    records: list[EntityRecord] = []
    unique_entities = sorted({name.strip() for name in entities if name.strip()}, key=lambda item: (-len(item), item))

    for entity_name in unique_entities:
        mentions: list[EntityMention] = []
        pattern = re.compile(re.escape(entity_name))
        for chapter in chapters:
            for match in pattern.finditer(chapter.content):
                start = max(0, match.start() - settings.max_excerpt_chars // 2)
                end = min(len(chapter.content), match.end() + settings.max_excerpt_chars // 2)
                excerpt = chapter.content[start:end].strip().replace("\n", " ")
                mentions.append(
                    EntityMention(
                        entity_name=entity_name,
                        chapter_number=chapter.number,
                        excerpt=excerpt,
                        position_in_chapter=match.start(),
                    )
                )
        if mentions:
            records.append(
                EntityRecord(
                    name=entity_name,
                    first_chapter_number=mentions[0].chapter_number,
                    mentions=mentions,
                )
            )

    return records

