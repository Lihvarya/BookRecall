import re
from typing import Iterable

from .models import Chapter

# 标题行最大长度：超过视为正文而非标题。
HEADING_MAX_LENGTH = 48

# 章节序数标识符（"第" 之后、"标题前缀" 之后的内容）。
# "卷" 是结构层，不作为真正内容章节；小节/章节才生成 Chapter。
_CONTENT_HEADING_SUFFIX = r"[章节回篇部集节]"
_VOLUME_HEADING_SUFFIX = r"[卷]"

# 标题与序数之间的分隔符：半角/全角空格、全角冒号、顿号等——网文里常见 "第一节：魔潮降临"。
_HEADING_SEP = r"[\s：、:]*"

CHAPTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"^\s*第[0-9零一二三四五六七八九十百千万两〇]+{_CONTENT_HEADING_SUFFIX}{_HEADING_SEP}.*$"),
    re.compile(r"^\s*(chapter|chap\.)\s+\d+.*$", re.IGNORECASE),
)

VOLUME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"^\s*第[0-9零一二三四五六七八九十百千万两〇]+{_VOLUME_HEADING_SUFFIX}{_HEADING_SEP}.*$"),
)

# 匹配行首「第X节/章/...」并捕获序数前缀与其后的标题文本。
_HEADING_STRIP = re.compile(
    rf"^\s*(第[0-9零一二三四五六七八九十百千万两〇]+(?:{_CONTENT_HEADING_SUFFIX}|{_VOLUME_HEADING_SUFFIX})){_HEADING_SEP}(.*)$"
)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_heading(line: str) -> str:
    # 把半角空白、全角空格(U+3000)、BOM 及其它常见 CJK 缩进一并去掉，
    # 网文段落标题常以全角空格缩进，而 re 的 \s 默认不匹配 U+3000。
    return line.strip(" \t\r\n　﻿\v\f")


def _looks_like_heading_body(line: str) -> bool:
    """一个判断：仅当该行**主要是一句标题**而不是夹带大量正文的句子时才视作标题。

    网文里正文常出现「方源是第二十八节的思想」这种把章节标记嵌进长句的情况。
    要求标题行整体较短（≤ HEADING_MAX_LENGTH），避免把长句误判为章节标题。
    """
    stripped = _strip_heading(line)
    if not stripped:
        return False
    if len(stripped) > HEADING_MAX_LENGTH:
        return False
    return any(pattern.match(stripped) for pattern in CHAPTER_PATTERNS)


def _looks_like_volume_heading(line: str) -> bool:
    stripped = _strip_heading(line)
    if not stripped:
        return False
    if len(stripped) > HEADING_MAX_LENGTH:
        return False
    return any(pattern.match(stripped) for pattern in VOLUME_PATTERNS)


def is_chapter_heading(line: str) -> bool:
    return _looks_like_heading_body(line)


def _clean_title(raw_heading: str) -> str:
    match = _HEADING_STRIP.match(_strip_heading(raw_heading))
    if match is None:
        return _strip_heading(raw_heading)
    prefix = match.group(1)
    rest = match.group(2).strip()
    return rest if rest else prefix


def _clean_volume_title(raw_heading: str) -> str:
    stripped = _strip_heading(raw_heading)
    match = _HEADING_STRIP.match(stripped)
    if match is None:
        return stripped
    prefix = match.group(1)
    rest = match.group(2).strip()
    return f"{prefix} {rest}".strip()


def _scoped_title(volume_title: str, chapter_title: str) -> str:
    if not volume_title:
        return chapter_title
    if not chapter_title:
        return volume_title
    return f"{volume_title} / {chapter_title}"


def _finalize_chapter(
    chapters: list[Chapter],
    number: int,
    title: str,
    lines: Iterable[str],
    start_offset: int,
) -> int:
    content = "\n".join(lines).strip()
    if not content and not title:
        return start_offset
    text_block = f"{title}\n{content}".strip()
    end_offset = start_offset + len(text_block)
    chapters.append(
        Chapter(
            number=number,
            title=title or f"第{number}章",
            content=content or title,
            start_offset=start_offset,
            end_offset=end_offset,
        )
    )
    # end_offset 之后预留一个换行的空隙。
    return end_offset + 2


def parse_chapters(text: str) -> list[Chapter]:
    normalized = normalize_newlines(text)
    lines = normalized.split("\n")
    chapters: list[Chapter] = []
    current_title = ""
    current_lines: list[str] = []
    current_volume = ""
    cursor = 0
    saw_heading = False

    def _flush() -> None:
        nonlocal cursor, current_title, current_lines
        if not current_lines and not current_title:
            current_title = ""
            current_lines = []
            return
        number = len(chapters) + 1  # 严格按解析顺序递增，与“第X节”真实序号解耦。
        cursor = _finalize_chapter(chapters, number, current_title, current_lines, cursor)
        current_title = ""
        current_lines = []

    for line in lines:
        if _looks_like_volume_heading(line):
            _flush()
            current_volume = _clean_volume_title(line)
            continue
        if _looks_like_heading_body(line):
            _flush()
            saw_heading = True
            current_title = _scoped_title(current_volume, _clean_title(line))
            continue
        current_lines.append(line)

    if not saw_heading:
        # 全文没有任何章节标题：保留为一个“全文”章节。
        content = normalized.strip()
        return [
            Chapter(
                number=1,
                title="全文",
                content=content,
                start_offset=0,
                end_offset=len(content),
            )
        ]

    _flush()
    return chapters
