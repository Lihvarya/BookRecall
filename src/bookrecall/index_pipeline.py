"""Recoverable indexing pipeline helpers.

The pipeline cache stores expensive smart-index intermediate results under the
local BookRecall workspace.  It is intentionally JSON-only so a failed import
can be retried without trusting pickle or executing cached code.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Chapter, EventRecord, RelationMention, RelationRecord


PIPELINE_VERSION = "2026-07-qwen-pipeline-v1"


class IndexPipelineCache:
    def __init__(
        self,
        *,
        db_path: str,
        book_id: str,
        chapters: list[Chapter],
        smart_index: dict[str, object] | None,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.book_id = book_id
        self.fingerprint = pipeline_fingerprint(chapters, smart_index)
        safe_book_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", book_id).strip("_") or "book"
        self.root = Path(db_path).resolve().parent / "pipelines" / safe_book_id / self.fingerprint
        self._report: dict[str, object] = {
            "enabled": enabled,
            "version": PIPELINE_VERSION,
            "book_id": book_id,
            "fingerprint": self.fingerprint,
            "cache_dir": str(self.root),
            "stages": {},
        }

    def load_stage(self, stage: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._stage_path(stage)
        if not path.exists():
            self.mark_stage(stage, "pending", from_cache=False)
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.mark_stage(stage, "corrupt", from_cache=False)
            return None
        self.mark_stage(stage, "completed", from_cache=True)
        return payload if isinstance(payload, dict) else None

    def save_stage(self, stage: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._stage_path(stage).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.mark_stage(stage, "completed", from_cache=False)

    def mark_stage(self, stage: str, status: str, *, from_cache: bool = False, detail: str = "") -> None:
        stages = self._report.setdefault("stages", {})
        if isinstance(stages, dict):
            stages[stage] = {
                "status": status,
                "from_cache": from_cache,
                "detail": detail,
            }

    def quality_report(
        self,
        *,
        chapter_count: int,
        entity_count: int,
        relation_count: int,
        event_count: int,
        theme_count: int,
        summary_count: int,
        vector_status: str,
    ) -> dict[str, object]:
        report = dict(self._report)
        report["quality"] = {
            "chapter_count": chapter_count,
            "entity_count": entity_count,
            "relation_count": relation_count,
            "event_count": event_count,
            "theme_count": theme_count,
            "summary_count": summary_count,
            "vector_index": vector_status,
            "warnings": _quality_warnings(
                chapter_count=chapter_count,
                entity_count=entity_count,
                event_count=event_count,
                summary_count=summary_count,
                vector_status=vector_status,
            ),
        }
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return report

    def _stage_path(self, stage: str) -> Path:
        return self.root / f"{stage}.json"


def pipeline_fingerprint(chapters: list[Chapter], smart_index: dict[str, object] | None) -> str:
    hasher = hashlib.sha256()
    hasher.update(PIPELINE_VERSION.encode("utf-8"))
    settings = smart_index or {}
    for key in ("enabled", "max_chapters", "batch_chapters", "model_path", "endpoint"):
        hasher.update(str(settings.get(key, "")).encode("utf-8"))
        hasher.update(b"\0")
    for chapter in chapters:
        hasher.update(str(chapter.number).encode("utf-8"))
        hasher.update(chapter.title.encode("utf-8"))
        hasher.update(chapter.content.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()[:24]


def relation_records_to_payload(records: list[RelationRecord]) -> dict[str, Any]:
    return {"relations": [asdict(record) for record in records]}


def relation_records_from_payload(payload: dict[str, Any]) -> list[RelationRecord]:
    records: list[RelationRecord] = []
    for raw in payload.get("relations", []):
        if not isinstance(raw, dict):
            continue
        mentions = [
            RelationMention(
                source_entity=str(item.get("source_entity", "")),
                target_entity=str(item.get("target_entity", "")),
                relation_type=str(item.get("relation_type", "")),
                chapter_number=int(item.get("chapter_number") or 0),
                excerpt=str(item.get("excerpt", "")),
            )
            for item in raw.get("mentions", [])
            if isinstance(item, dict)
        ]
        records.append(
            RelationRecord(
                source_entity=str(raw.get("source_entity", "")),
                target_entity=str(raw.get("target_entity", "")),
                relation_type=str(raw.get("relation_type", "")),
                first_chapter_number=int(raw.get("first_chapter_number") or 0),
                mentions=mentions,
            )
        )
    return records


def event_records_to_payload(records: list[EventRecord]) -> dict[str, Any]:
    return {"events": [asdict(record) for record in records]}


def event_records_from_payload(payload: dict[str, Any]) -> list[EventRecord]:
    records: list[EventRecord] = []
    for raw in payload.get("events", []):
        if not isinstance(raw, dict):
            continue
        records.append(
            EventRecord(
                chapter_number=int(raw.get("chapter_number") or 0),
                chapter_title=str(raw.get("chapter_title", "")),
                event_type=str(raw.get("event_type", "")),
                summary=str(raw.get("summary", "")),
                excerpt=str(raw.get("excerpt", "")),
                entities=[str(item) for item in raw.get("entities", []) if str(item).strip()],
            )
        )
    return records


def _quality_warnings(
    *,
    chapter_count: int,
    entity_count: int,
    event_count: int,
    summary_count: int,
    vector_status: str,
) -> list[str]:
    warnings: list[str] = []
    if chapter_count <= 1:
        warnings.append("章节数量较少，可能是目录解析没有识别到真实章节。")
    if entity_count == 0:
        warnings.append("实体索引为空，建议检查实体抽取或补充实体词表。")
    if event_count == 0:
        warnings.append("事件索引为空，复杂情节追踪能力会受限。")
    if summary_count == 0:
        warnings.append("章节摘要为空，建议启用本地 Qwen 智能摘要。")
    if vector_status != "built":
        warnings.append("向量索引未内联构建；可在模型页手动构建 embedding 索引。")
    return warnings
