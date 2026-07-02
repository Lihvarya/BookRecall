import re
from typing import Iterable

from .models import Chapter

CHAPTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*第[0-9零一二三四五六七八九十百千万两〇]+[章节回卷篇部集]\s*.*$"),
    re.compile(r"^\s*(chapter|chap\.)\s+\d+.*$", re.IGNORECASE),
)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def is_chapter_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(pattern.match(stripped) for pattern in CHAPTER_PATTERNS)


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
    return end_offset + 2


def parse_chapters(text: str) -> list[Chapter]:
    normalized = normalize_newlines(text)
    lines = normalized.split("\n")
    chapters: list[Chapter] = []
    current_title = ""
    current_lines: list[str] = []
    current_number = 0
    cursor = 0

    for line in lines:
        if is_chapter_heading(line):
            if current_number > 0 or current_lines:
                cursor = _finalize_chapter(
                    chapters,
                    current_number or 1,
                    current_title,
                    current_lines,
                    cursor,
                )
            current_number = len(chapters) + 1
            current_title = line.strip()
            current_lines = []
            continue
        current_lines.append(line)

    if current_number == 0:
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

    _finalize_chapter(chapters, current_number, current_title, current_lines, cursor)
    return chapters

