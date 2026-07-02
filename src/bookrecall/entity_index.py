import re
from pathlib import Path

from .config import ChunkSettings
from .models import Chapter, EntityMention, EntityRecord

AUTO_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"【([^】]{1,20})】"),
    re.compile(r"《([^》]{1,20})》"),
    re.compile(r"「([^」]{1,20})」"),
)


def load_entity_lexicon(path: str | None) -> dict[str, list[str]]:
    if not path:
        return {}
    entity_file = Path(path)
    if not entity_file.exists():
        return {}
    entities: dict[str, list[str]] = {}
    for line in entity_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if "|" in stripped:
                canonical, alias_blob = stripped.split("|", 1)
                aliases = [alias.strip() for alias in re.split(r"[,，]", alias_blob) if alias.strip()]
                entities[canonical.strip()] = aliases
            else:
                entities[stripped] = []
    return entities


def auto_discover_entities(text: str) -> dict[str, list[str]]:
    found: set[str] = set()
    for pattern in AUTO_ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            candidate = match.group(1).strip()
            if 1 < len(candidate) <= 20:
                found.add(candidate)
    ordered = sorted(found, key=lambda item: (-len(item), item))
    return {item: [] for item in ordered}


def build_entity_records(
    chapters: list[Chapter],
    entities: dict[str, list[str]] | list[str],
    settings: ChunkSettings,
) -> list[EntityRecord]:
    records: list[EntityRecord] = []
    if isinstance(entities, dict):
        entity_map = {
            name.strip(): [alias.strip() for alias in aliases if alias.strip()]
            for name, aliases in entities.items()
            if name.strip()
        }
    else:
        entity_map = {name.strip(): [] for name in entities if name.strip()}

    unique_entities = sorted(entity_map, key=lambda item: (-len(item), item))

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
                    aliases=entity_map.get(entity_name, []),
                    first_chapter_number=mentions[0].chapter_number,
                    mentions=mentions,
                )
            )

    return records
