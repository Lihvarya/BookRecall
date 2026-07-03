import re
from collections import Counter
from pathlib import Path

from .config import ChunkSettings
from .models import Chapter, EntityMention, EntityRecord

AUTO_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"【([^】]{1,20})】"),
    re.compile(r"《([^》]{1,20})》"),
    re.compile(r"「([^」]{1,20})」"),
)

# 高频但要排除的常见词。网文里“我们/一个/什么”这种高频词不是专名。
# 同时排除纯标点、数字、以及常见结构词的前缀组合。
_STOPWORDS: set[str] = {
    "我们", "他们", "一个", "什么", "这个", "那样", "因为", "所以", "如果", "虽然",
    "然后", "只是", "这样", "不过", "可以", "已经", "什么", "自己", "现在", "之后",
    "的话", "时候", "如何", "为何", "为何", "从而", "因而", "为了", "这一", "那种",
    "那些", "这些", "这是", "那是", "那里", "这里", "于是", "可是", "而且", "或许",
    "一些", "所有", "这是", "不是", "不得", "但也", "不论", "以上", "以下", "以为",
    "一时", "一方", "大事", "大事", "之中", "之间", "之内", "之外", "以后", "以前",
}

# 仅保留同时出现在多种上下（高频专名往往不局限在标点里），简单用“包含至少一个 CJK 字符”过滤。


def _is_candidate_name(token: str) -> bool:
    if len(token) < 2:
        return False
    if token in _STOPWORDS:
        return False
    if not any(ch >= "一" and ch <= "鿿" for ch in token):
        return False
    # 排除全数字或纯标点
    if all(ch in "零一二三四五六七八九十百千万两〇0123456789" for ch in token):
        return False
    return True


def discover_entities_by_frequency(text: str, *, top_k: int = 60) -> dict[str, list[str]]:
    """按全文字符 n-gram 词频挖掘候选专名。

    只在「连续 CJK 字符片段」上做 2/3/4-gram，避免跨标点/空白产生垃圾 token
    （例如“方。源”这种）。排除停用词与纯数字，取 TopK。代价是会混入一些高频普通词，
    但实体索引建错只影响召回质量，不影响防剧透与首次出现的正确性——且这些候选会
    再和【】《》「」的强调符实体合并去重。
    """
    counter: Counter = Counter()
    # 只取连续的 CJK 字符段为切片基准。
    cjk_spans = re.findall(r"[一-鿿]{4,}", text)
    for span in cjk_spans:
        for n in (2, 3, 4):
            for index in range(len(span) - n + 1):
                counter[span[index:index + n]] += 1
    candidates = [
        (token, freq)
        for token, freq in counter.most_common()
        if freq >= 8 and _is_candidate_name(token)
    ]
    chosen: list[str] = []
    for token, _freq in candidates:
        if len(chosen) >= top_k:
            break
        chosen.append(token)
    chosen.sort(key=lambda item: (-len(item), item))
    return {item: [] for item in chosen}


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


def auto_discover_entities(text: str, *, top_k: int = 60) -> dict[str, list[str]]:
    """自动挖掘候选实体：强调符实体（【】《》「」）+ 高频 n-gram 专名。

    两者合并去重，长的优先。强调符实体通常是人手用括号强调的关键名词，准确度最高；
    高频 n-gram 补充那些未加强调但反复出现的角色/蛊虫/门派名。
    """
    found: set[str] = set()
    for pattern in AUTO_ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            candidate = match.group(1).strip()
            if 1 < len(candidate) <= 20:
                found.add(candidate)
    frequency_based = discover_entities_by_frequency(text, top_k=top_k)
    found.update(frequency_based.keys())
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
