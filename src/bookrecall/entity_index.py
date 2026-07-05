import re
from collections import Counter
from pathlib import Path

from .config import ChunkSettings
from .models import (
    Chapter,
    EntityMention,
    EntityRecord,
    EventRecord,
    RelationMention,
    RelationRecord,
    ThemeMention,
    ThemeRecord,
)

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
    "就是", "没有", "他的", "她的", "它的", "时间", "一声", "还有", "中的", "心中",
    "之前", "手段", "有一", "还是", "都是", "正在", "突然", "不能", "不会", "不是",
    "然而", "显然", "似乎", "其中", "这种", "那种", "如此", "如此", "当中", "时候",
    "起来", "下去", "过来", "过去", "出来", "进去", "所有人", "其他人", "年轻人",
}

DEFAULT_THEME_TERMS: tuple[str, ...] = (
    "自由意志",
    "命运",
    "选择",
    "自由",
    "权力",
    "秩序",
    "混乱",
    "信仰",
    "人性",
    "神性",
    "文明",
    "记忆",
    "身份",
    "牺牲",
    "救赎",
    "复仇",
    "成长",
    "孤独",
    "真相",
    "谎言",
)

EVENT_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("获得/失去", ("拿到", "得到", "获得", "失去", "夺走", "交出", "打开")),
    ("冲突/危机", ("对峙", "追杀", "争斗", "袭击", "背叛", "怀疑", "危机")),
    ("揭示/真相", ("发现", "揭开", "真相", "意识到", "明白", "提到", "告诉")),
    ("选择/决定", ("决定", "选择", "相信", "拒绝", "承认", "承担")),
    ("协作/同行", ("一起", "同行", "帮助", "救", "穿过", "合作")),
)

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
    if len(token) <= 3 and any(ch in token for ch in "的是了在有也就都很还没不"):
        return False
    if token.startswith(("第", "这", "那", "有", "没", "不", "很", "还", "又", "再")) and len(token) <= 3:
        return False
    if token.endswith(("的", "了", "着", "过", "中", "里", "上", "下")) and len(token) <= 3:
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
    candidates.sort(key=lambda item: (-len(item[0]), -item[1], item[0]))
    chosen: list[str] = []
    for token, _freq in candidates:
        if len(chosen) >= top_k:
            break
        if any(token in existing and len(existing) > len(token) for existing in chosen):
            continue
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


def load_theme_lexicon(path: str | None) -> dict[str, list[str]]:
    """读取主题词表，格式与实体词表一致：标准名|别名1,别名2。"""
    return load_entity_lexicon(path)


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


def auto_discover_themes(text: str, *, extra_terms: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """自动发现第一版主题词。

    主题不像人物/道具那样适合纯 n-gram 自动挖掘，所以 MVP 先用一组常见主题词，
    再合并用户主题词表。这样“自由意志/命运/权力”类问题可以走结构化索引，
    同时避免把大量普通名词误当主题。
    """
    themes: dict[str, list[str]] = {}
    for term in DEFAULT_THEME_TERMS:
        if term in text:
            themes[term] = []
    if extra_terms:
        for name, aliases in extra_terms.items():
            cleaned = name.strip()
            if cleaned:
                themes[cleaned] = [alias.strip() for alias in aliases if alias.strip()]
    return dict(sorted(themes.items(), key=lambda item: (-len(item[0]), item[0])))


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


def build_theme_records(
    chapters: list[Chapter],
    themes: dict[str, list[str]] | list[str],
    settings: ChunkSettings,
) -> list[ThemeRecord]:
    records: list[ThemeRecord] = []
    if isinstance(themes, dict):
        theme_map = {
            name.strip(): [alias.strip() for alias in aliases if alias.strip()]
            for name, aliases in themes.items()
            if name.strip()
        }
    else:
        theme_map = {name.strip(): [] for name in themes if name.strip()}

    for theme_name in sorted(theme_map, key=lambda item: (-len(item), item)):
        mentions: list[ThemeMention] = []
        terms = [theme_name, *theme_map.get(theme_name, [])]
        unique_terms = []
        for term in terms:
            if term and term not in unique_terms:
                unique_terms.append(term)
        for chapter in chapters:
            chapter_mentions: list[ThemeMention] = []
            for term in unique_terms:
                pattern = re.compile(re.escape(term))
                for match in pattern.finditer(chapter.content):
                    start = max(0, match.start() - settings.max_excerpt_chars // 2)
                    end = min(len(chapter.content), match.end() + settings.max_excerpt_chars // 2)
                    excerpt = chapter.content[start:end].strip().replace("\n", " ")
                    chapter_mentions.append(
                        ThemeMention(
                            theme_name=theme_name,
                            chapter_number=chapter.number,
                            excerpt=excerpt,
                            position_in_chapter=match.start(),
                        )
                    )
            chapter_mentions.sort(key=lambda item: item.position_in_chapter)
            mentions.extend(chapter_mentions)
        if mentions:
            records.append(
                ThemeRecord(
                    name=theme_name,
                    aliases=theme_map.get(theme_name, []),
                    first_chapter_number=mentions[0].chapter_number,
                    mentions=mentions,
                )
            )
    return records


def build_relation_records(
    chapters: list[Chapter],
    entity_records: list[EntityRecord],
    settings: ChunkSettings,
) -> list[RelationRecord]:
    relation_mentions: dict[tuple[str, str, str], list[RelationMention]] = {}
    entity_names = [record.name for record in entity_records]

    for chapter in chapters:
        for sentence in _iter_relation_windows(chapter.content, settings.max_excerpt_chars):
            names = sorted(name for name in entity_names if name in sentence)
            if len(names) < 2:
                continue
            for left_index, source in enumerate(names):
                for target in names[left_index + 1 :]:
                    relation_type = _infer_relation_type(sentence)
                    if relation_type == "共现/关联":
                        continue
                    ordered_source, ordered_target = sorted((source, target))
                    key = (ordered_source, ordered_target, relation_type)
                    relation_mentions.setdefault(key, []).append(
                        RelationMention(
                            source_entity=ordered_source,
                            target_entity=ordered_target,
                            relation_type=relation_type,
                            chapter_number=chapter.number,
                            excerpt=sentence,
                        )
                    )

    if not relation_mentions:
        # 最后兜底：只有当两个实体距离很近时，才认为它们有弱关系。
        mentions_by_chapter: dict[int, dict[str, list[EntityMention]]] = {}
        for record in entity_records:
            for mention in record.mentions:
                mentions_by_chapter.setdefault(mention.chapter_number, {}).setdefault(record.name, []).append(mention)
        chapter_by_number = {chapter.number: chapter for chapter in chapters}
        for chapter_number, entity_map in mentions_by_chapter.items():
            names = sorted(entity_map)
            if len(names) < 2:
                continue
            chapter = chapter_by_number.get(chapter_number)
            content = chapter.content if chapter is not None else ""
            for left_index, source in enumerate(names):
                for target in names[left_index + 1 :]:
                    source_pos = entity_map[source][0].position_in_chapter
                    target_pos = entity_map[target][0].position_in_chapter
                    if abs(source_pos - target_pos) > settings.max_excerpt_chars:
                        continue
                    excerpt = _relation_excerpt(content, source_pos, target_pos, settings.max_excerpt_chars)
                    if not excerpt:
                        continue
                    if _infer_relation_type(excerpt) == "共现/关联":
                        continue
                    relation_type = _infer_relation_type(excerpt)
                    ordered_source, ordered_target = sorted((source, target))
                    key = (ordered_source, ordered_target, relation_type)
                    relation_mentions.setdefault(key, []).append(
                        RelationMention(
                            source_entity=ordered_source,
                            target_entity=ordered_target,
                            relation_type=relation_type,
                            chapter_number=chapter_number,
                            excerpt=excerpt,
                        )
                    )

    records: list[RelationRecord] = []
    for (source, target, relation_type), mentions in sorted(relation_mentions.items()):
        mentions.sort(key=lambda item: item.chapter_number)
        records.append(
            RelationRecord(
                source_entity=source,
                target_entity=target,
                relation_type=relation_type,
                first_chapter_number=mentions[0].chapter_number,
                mentions=mentions,
            )
        )
    return records


def build_event_records(
    chapters: list[Chapter],
    entity_records: list[EntityRecord],
    settings: ChunkSettings,
) -> list[EventRecord]:
    entity_names = [record.name for record in entity_records]
    records: list[EventRecord] = []
    seen: set[tuple[int, str]] = set()
    for chapter in chapters:
        for sentence in _iter_event_sentences(chapter.content):
            event_type = _infer_event_type(sentence)
            entities = [name for name in entity_names if name in sentence]
            if event_type == "事件" and len(entities) < 2:
                continue
            key = (chapter.number, sentence)
            if key in seen:
                continue
            seen.add(key)
            excerpt = sentence
            if len(excerpt) > settings.max_excerpt_chars:
                excerpt = excerpt[: settings.max_excerpt_chars].rstrip() + "..."
            records.append(
                EventRecord(
                    chapter_number=chapter.number,
                    chapter_title=chapter.title,
                    event_type=event_type,
                    summary=_event_summary(sentence),
                    excerpt=excerpt,
                    entities=entities,
                )
            )
    return records


def _iter_event_sentences(content: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s*|\n+", content)
    sentences: list[str] = []
    for part in parts:
        cleaned = " ".join(part.split()).strip()
        if 8 <= len(cleaned) <= 220:
            sentences.append(cleaned)
    return sentences


def _iter_relation_windows(content: str, max_chars: int) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", content)
    windows: list[str] = []
    for part in parts:
        cleaned = " ".join(part.split()).strip()
        if 8 <= len(cleaned) <= max_chars:
            windows.append(cleaned)
        elif len(cleaned) > max_chars:
            for start in range(0, len(cleaned), max_chars):
                window = cleaned[start : start + max_chars].strip()
                if len(window) >= 8:
                    windows.append(window)
    return windows


def _infer_event_type(sentence: str) -> str:
    for event_type, keywords in EVENT_TYPE_KEYWORDS:
        if any(keyword in sentence for keyword in keywords):
            return event_type
    return "事件"


def _event_summary(sentence: str, max_chars: int = 72) -> str:
    cleaned = sentence.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _relation_excerpt(content: str, source_pos: int, target_pos: int, max_chars: int) -> str:
    if not content:
        return ""
    start_pos = min(source_pos, target_pos)
    end_pos = max(source_pos, target_pos)
    padding = max_chars // 2
    start = max(0, start_pos - padding)
    end = min(len(content), end_pos + padding)
    return content[start:end].strip().replace("\n", " ")


def _infer_relation_type(excerpt: str) -> str:
    if any(token in excerpt for token in ("师父", "师尊", "弟子", "传授", "教导")):
        return "师徒/传承"
    if any(token in excerpt for token in ("敌", "追杀", "背叛", "杀", "冲突", "争斗", "对峙")):
        return "冲突"
    if any(token in excerpt for token in ("朋友", "同伴", "一起", "同行", "帮助", "救")):
        return "同伴/协作"
    if any(token in excerpt for token in ("父", "母", "兄", "姐", "弟", "妹", "家族")):
        return "亲缘/家族"
    return "共现/关联"
