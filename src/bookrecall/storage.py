import json
import hashlib
import sqlite3
from pathlib import Path

from .models import BookInfo, Chapter, ChildChunk, EntityRecord, EventRecord, ParentChunk, RelationRecord, ThemeRecord


def _normalize_entity_name(name: str) -> str:
    return "".join(name.lower().split())


def _stable_suffix(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


class BookRecallStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS books (
                book_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_path TEXT,
                chapter_count INTEGER NOT NULL DEFAULT 0,
                entity_count INTEGER NOT NULL DEFAULT 0,
                book_group TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chapters (
                book_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                PRIMARY KEY (book_id, chapter_number)
            );

            CREATE TABLE IF NOT EXISTS parent_chunks (
                chunk_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                chapter_title TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS child_chunks (
                chunk_id TEXT PRIMARY KEY,
                parent_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                first_chapter_number INTEGER NOT NULL,
                mention_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entity_mentions (
                mention_id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                excerpt TEXT NOT NULL,
                position_in_chapter INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entity_aliases (
                alias_id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                normalized_alias TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS relations (
                relation_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                source_entity TEXT NOT NULL,
                target_entity TEXT NOT NULL,
                normalized_source TEXT NOT NULL,
                normalized_target TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                first_chapter_number INTEGER NOT NULL,
                mention_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS relation_mentions (
                mention_id TEXT PRIMARY KEY,
                relation_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                excerpt TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS themes (
                theme_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                first_chapter_number INTEGER NOT NULL,
                mention_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS theme_aliases (
                alias_id TEXT PRIMARY KEY,
                theme_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                normalized_alias TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS theme_mentions (
                mention_id TEXT PRIMARY KEY,
                theme_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                excerpt TEXT NOT NULL,
                position_in_chapter INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                chapter_title TEXT NOT NULL,
                event_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                excerpt TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_entities (
                event_entity_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                entity_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chapter_summaries (
                summary_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                chapter_title TEXT NOT NULL,
                summary TEXT NOT NULL,
                UNIQUE(book_id, chapter_number)
            );

            CREATE TABLE IF NOT EXISTS reader_state (
                book_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                progress_chapter INTEGER NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS user_preferences (
                book_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                answer_style TEXT NOT NULL DEFAULT '',
                focus TEXT NOT NULL DEFAULT '',
                custom_prompt TEXT NOT NULL DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS agent_memory (
                turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                question TEXT NOT NULL,
                intent TEXT NOT NULL,
                entity_name TEXT,
                answer TEXT NOT NULL,
                summary TEXT,
                progress_chapter INTEGER NOT NULL,
                matched_entities_json TEXT NOT NULL,
                trace_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._ensure_column("books", "chapter_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("books", "entity_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("books", "book_group", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("books", "tags_json", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("user_preferences", "answer_style", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("user_preferences", "focus", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("user_preferences", "custom_prompt", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("user_preferences", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP")
        self.connection.commit()

    def _ensure_column(self, table_name: str, column_name: str, ddl: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {row["name"] for row in rows}
        if column_name not in existing_columns:
            self.connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    def replace_book(
        self,
        *,
        book_id: str,
        title: str,
        source_path: str,
        chapters: list[Chapter],
        parent_chunks: list[ParentChunk],
        child_chunks: list[ChildChunk],
        entity_records: list[EntityRecord],
        relation_records: list[RelationRecord] | None = None,
        theme_records: list[ThemeRecord] | None = None,
        event_records: list[EventRecord] | None = None,
        chapter_summaries: dict[int, str] | None = None,
    ) -> None:
        relation_records = relation_records or []
        theme_records = theme_records or []
        event_records = event_records or []
        chapter_summaries = chapter_summaries or {}
        cursor = self.connection.cursor()
        metadata = cursor.execute(
            "SELECT book_group, tags_json FROM books WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        book_group = str(metadata["book_group"]) if metadata is not None and metadata["book_group"] else ""
        tags_json = str(metadata["tags_json"]) if metadata is not None and metadata["tags_json"] else "[]"
        cursor.execute(
            """
            INSERT OR REPLACE INTO books(book_id, title, source_path, chapter_count, entity_count, book_group, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (book_id, title, source_path, len(chapters), len(entity_records), book_group, tags_json),
        )
        cursor.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM parent_chunks WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM child_chunks WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM entity_mentions WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM entity_aliases WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM relation_mentions WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM relations WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM theme_mentions WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM theme_aliases WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM themes WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM event_entities WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM events WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM chapter_summaries WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM entities WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM agent_memory WHERE book_id = ?", (book_id,))

        cursor.executemany(
            """
            INSERT INTO chapters(book_id, chapter_number, title, content, start_offset, end_offset)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    book_id,
                    chapter.number,
                    chapter.title,
                    chapter.content,
                    chapter.start_offset,
                    chapter.end_offset,
                )
                for chapter in chapters
            ],
        )
        cursor.executemany(
            """
            INSERT INTO parent_chunks(chunk_id, book_id, chapter_number, chapter_title, chunk_index, text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.chunk_id,
                    chunk.book_id,
                    chunk.chapter_number,
                    chunk.chapter_title,
                    chunk.chunk_index,
                    chunk.text,
                )
                for chunk in parent_chunks
            ],
        )
        cursor.executemany(
            """
            INSERT INTO child_chunks(chunk_id, parent_id, book_id, chapter_number, chunk_index, text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.chunk_id,
                    chunk.parent_id,
                    chunk.book_id,
                    chunk.chapter_number,
                    chunk.chunk_index,
                    chunk.text,
                )
                for chunk in child_chunks
            ],
        )

        cursor.executemany(
            """
            INSERT INTO chapter_summaries(summary_id, book_id, chapter_number, chapter_title, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    f"{book_id}:summary:{chapter.number}",
                    book_id,
                    chapter.number,
                    chapter.title,
                    chapter_summaries.get(chapter.number) or _chapter_summary(chapter.content),
                )
                for chapter in chapters
            ],
        )

        for record in entity_records:
            entity_id = f"{book_id}:entity:{_normalize_entity_name(record.name)}"
            cursor.execute(
                """
                INSERT INTO entities(entity_id, book_id, name, normalized_name, first_chapter_number, mention_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    book_id,
                    record.name,
                    _normalize_entity_name(record.name),
                    record.first_chapter_number,
                    len(record.mentions),
                ),
            )
            cursor.executemany(
                """
                INSERT INTO entity_mentions(mention_id, entity_id, book_id, chapter_number, excerpt, position_in_chapter)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"{entity_id}:mention:{index}",
                        entity_id,
                        book_id,
                        mention.chapter_number,
                        mention.excerpt,
                        mention.position_in_chapter,
                    )
                    for index, mention in enumerate(record.mentions, start=1)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO entity_aliases(alias_id, entity_id, book_id, alias, normalized_alias)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"{entity_id}:alias:{index}",
                        entity_id,
                        book_id,
                        alias,
                        _normalize_entity_name(alias),
                    )
                    for index, alias in enumerate(record.aliases, start=1)
                ],
            )

        for record in relation_records:
            source = record.source_entity
            target = record.target_entity
            relation_id = (
                f"{book_id}:relation:{_normalize_entity_name(source)}:"
                f"{_normalize_entity_name(target)}:{record.relation_type}"
            )
            cursor.execute(
                """
                INSERT INTO relations(
                    relation_id,
                    book_id,
                    source_entity,
                    target_entity,
                    normalized_source,
                    normalized_target,
                    relation_type,
                    first_chapter_number,
                    mention_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation_id,
                    book_id,
                    source,
                    target,
                    _normalize_entity_name(source),
                    _normalize_entity_name(target),
                    record.relation_type,
                    record.first_chapter_number,
                    len(record.mentions),
                ),
            )
            cursor.executemany(
                """
                INSERT INTO relation_mentions(mention_id, relation_id, book_id, chapter_number, excerpt)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"{relation_id}:mention:{index}",
                        relation_id,
                        book_id,
                        mention.chapter_number,
                        mention.excerpt,
                    )
                    for index, mention in enumerate(record.mentions, start=1)
                ],
            )

        for record in theme_records:
            theme_id = f"{book_id}:theme:{_normalize_entity_name(record.name)}"
            cursor.execute(
                """
                INSERT INTO themes(theme_id, book_id, name, normalized_name, first_chapter_number, mention_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    theme_id,
                    book_id,
                    record.name,
                    _normalize_entity_name(record.name),
                    record.first_chapter_number,
                    len(record.mentions),
                ),
            )
            cursor.executemany(
                """
                INSERT INTO theme_mentions(mention_id, theme_id, book_id, chapter_number, excerpt, position_in_chapter)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"{theme_id}:mention:{index}",
                        theme_id,
                        book_id,
                        mention.chapter_number,
                        mention.excerpt,
                        mention.position_in_chapter,
                    )
                    for index, mention in enumerate(record.mentions, start=1)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO theme_aliases(alias_id, theme_id, book_id, alias, normalized_alias)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"{theme_id}:alias:{index}",
                        theme_id,
                        book_id,
                        alias,
                        _normalize_entity_name(alias),
                    )
                    for index, alias in enumerate(record.aliases, start=1)
                ],
            )

        for index, record in enumerate(event_records, start=1):
            event_id = f"{book_id}:event:{index}"
            cursor.execute(
                """
                INSERT INTO events(event_id, book_id, chapter_number, chapter_title, event_type, summary, excerpt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    book_id,
                    record.chapter_number,
                    record.chapter_title,
                    record.event_type,
                    record.summary,
                    record.excerpt,
                ),
            )
            cursor.executemany(
                """
                INSERT INTO event_entities(event_entity_id, event_id, book_id, entity_name, normalized_name)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"{event_id}:entity:{entity_index}",
                        event_id,
                        book_id,
                        entity_name,
                        _normalize_entity_name(entity_name),
                    )
                    for entity_index, entity_name in enumerate(record.entities, start=1)
                ],
            )

        self.connection.commit()

    def list_books(self) -> list[BookInfo]:
        rows = self.connection.execute(
            """
            SELECT book_id, title, source_path, chapter_count, entity_count, book_group, tags_json
            FROM books
            ORDER BY created_at DESC, book_id ASC
            """
        ).fetchall()
        return [
            BookInfo(
                book_id=row["book_id"],
                title=row["title"],
                source_path=row["source_path"] or "",
                chapter_count=int(row["chapter_count"]),
                entity_count=int(row["entity_count"]),
                book_group=str(row["book_group"] or ""),
                tags=_loads_string_list(row["tags_json"]),
            )
            for row in rows
        ]

    def upsert_dynamic_index_records(
        self,
        *,
        book_id: str,
        entity_records: list[EntityRecord] | None = None,
        relation_records: list[RelationRecord] | None = None,
        event_records: list[EventRecord] | None = None,
    ) -> dict[str, int]:
        entity_records = entity_records or []
        relation_records = relation_records or []
        event_records = event_records or []
        inserted = {"entities": 0, "entity_mentions": 0, "relations": 0, "relation_mentions": 0, "events": 0}
        cursor = self.connection.cursor()

        for record in entity_records:
            if not record.name:
                continue
            entity_id = f"{book_id}:entity:{_normalize_entity_name(record.name)}"
            before = cursor.execute("SELECT 1 FROM entities WHERE entity_id = ?", (entity_id,)).fetchone()
            cursor.execute(
                """
                INSERT INTO entities(entity_id, book_id, name, normalized_name, first_chapter_number, mention_count)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(entity_id) DO UPDATE SET
                    first_chapter_number = MIN(first_chapter_number, excluded.first_chapter_number)
                """,
                (entity_id, book_id, record.name, _normalize_entity_name(record.name), record.first_chapter_number),
            )
            if before is None:
                inserted["entities"] += 1
            for alias in record.aliases:
                alias_id = f"{entity_id}:alias:{_stable_suffix(alias)}"
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO entity_aliases(alias_id, entity_id, book_id, alias, normalized_alias)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (alias_id, entity_id, book_id, alias, _normalize_entity_name(alias)),
                )
            for mention in record.mentions:
                mention_id = f"{entity_id}:dynamic:{_stable_suffix(str(mention.chapter_number) + mention.excerpt)}"
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO entity_mentions(mention_id, entity_id, book_id, chapter_number, excerpt, position_in_chapter)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mention_id,
                        entity_id,
                        book_id,
                        mention.chapter_number,
                        mention.excerpt,
                        mention.position_in_chapter,
                    ),
                )
                inserted["entity_mentions"] += cursor.rowcount
            cursor.execute(
                "UPDATE entities SET mention_count = (SELECT COUNT(*) FROM entity_mentions WHERE entity_id = ?) WHERE entity_id = ?",
                (entity_id, entity_id),
            )

        for record in relation_records:
            source = record.source_entity
            target = record.target_entity
            if not source or not target:
                continue
            relation_id = (
                f"{book_id}:relation:{_normalize_entity_name(source)}:"
                f"{_normalize_entity_name(target)}:{record.relation_type}"
            )
            before = cursor.execute("SELECT 1 FROM relations WHERE relation_id = ?", (relation_id,)).fetchone()
            cursor.execute(
                """
                INSERT INTO relations(
                    relation_id, book_id, source_entity, target_entity, normalized_source,
                    normalized_target, relation_type, first_chapter_number, mention_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(relation_id) DO UPDATE SET
                    first_chapter_number = MIN(first_chapter_number, excluded.first_chapter_number)
                """,
                (
                    relation_id,
                    book_id,
                    source,
                    target,
                    _normalize_entity_name(source),
                    _normalize_entity_name(target),
                    record.relation_type,
                    record.first_chapter_number,
                ),
            )
            if before is None:
                inserted["relations"] += 1
            for mention in record.mentions:
                mention_id = f"{relation_id}:dynamic:{_stable_suffix(str(mention.chapter_number) + mention.excerpt)}"
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO relation_mentions(mention_id, relation_id, book_id, chapter_number, excerpt)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (mention_id, relation_id, book_id, mention.chapter_number, mention.excerpt),
                )
                inserted["relation_mentions"] += cursor.rowcount
            cursor.execute(
                "UPDATE relations SET mention_count = (SELECT COUNT(*) FROM relation_mentions WHERE relation_id = ?) WHERE relation_id = ?",
                (relation_id, relation_id),
            )

        for record in event_records:
            event_id = (
                f"{book_id}:event:dynamic:{record.chapter_number}:"
                f"{_stable_suffix(record.event_type + record.summary + record.excerpt)}"
            )
            cursor.execute(
                """
                INSERT OR IGNORE INTO events(event_id, book_id, chapter_number, chapter_title, event_type, summary, excerpt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    book_id,
                    record.chapter_number,
                    record.chapter_title,
                    record.event_type,
                    record.summary,
                    record.excerpt,
                ),
            )
            inserted["events"] += cursor.rowcount
            for entity_name in record.entities:
                event_entity_id = f"{event_id}:entity:{_stable_suffix(entity_name)}"
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO event_entities(event_entity_id, event_id, book_id, entity_name, normalized_name)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (event_entity_id, event_id, book_id, entity_name, _normalize_entity_name(entity_name)),
                )

        cursor.execute(
            "UPDATE books SET entity_count = (SELECT COUNT(*) FROM entities WHERE book_id = ?) WHERE book_id = ?",
            (book_id, book_id),
        )
        self.connection.commit()
        return inserted

    def get_book(self, book_id: str) -> BookInfo | None:
        row = self.connection.execute(
            """
            SELECT book_id, title, source_path, chapter_count, entity_count, book_group, tags_json
            FROM books
            WHERE book_id = ?
            """,
            (book_id,),
        ).fetchone()
        if row is None:
            return None
        return BookInfo(
            book_id=row["book_id"],
            title=row["title"],
            source_path=row["source_path"] or "",
            chapter_count=int(row["chapter_count"]),
            entity_count=int(row["entity_count"]),
            book_group=str(row["book_group"] or ""),
            tags=_loads_string_list(row["tags_json"]),
        )

    def update_book_metadata(self, book_id: str, book_group: str = "", tags: list[str] | None = None) -> BookInfo | None:
        cleaned_tags = []
        for tag in tags or []:
            cleaned = str(tag).strip()
            if cleaned and cleaned not in cleaned_tags:
                cleaned_tags.append(cleaned)
        self.connection.execute(
            """
            UPDATE books
            SET book_group = ?, tags_json = ?
            WHERE book_id = ?
            """,
            (book_group.strip(), json.dumps(cleaned_tags, ensure_ascii=False), book_id),
        )
        self.connection.commit()
        return self.get_book(book_id)

    def get_user_preferences(self, book_id: str, user_id: str) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT answer_style, focus, custom_prompt, updated_at
            FROM user_preferences
            WHERE book_id = ? AND user_id = ?
            """,
            (book_id, user_id),
        ).fetchone()
        if row is None:
            return {
                "book_id": book_id,
                "user_id": user_id,
                "answer_style": "",
                "focus": "",
                "custom_prompt": "",
                "updated_at": "",
            }
        return {
            "book_id": book_id,
            "user_id": user_id,
            "answer_style": str(row["answer_style"] or ""),
            "focus": str(row["focus"] or ""),
            "custom_prompt": str(row["custom_prompt"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def set_user_preferences(
        self,
        *,
        book_id: str,
        user_id: str,
        answer_style: str = "",
        focus: str = "",
        custom_prompt: str = "",
    ) -> dict[str, object]:
        self.connection.execute(
            """
            INSERT INTO user_preferences(book_id, user_id, answer_style, focus, custom_prompt, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id, user_id) DO UPDATE SET
                answer_style = excluded.answer_style,
                focus = excluded.focus,
                custom_prompt = excluded.custom_prompt,
                updated_at = CURRENT_TIMESTAMP
            """,
            (book_id, user_id, answer_style.strip(), focus.strip(), custom_prompt.strip()),
        )
        self.connection.commit()
        return self.get_user_preferences(book_id, user_id)

    def list_entities(self, book_id: str) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM entities WHERE book_id = ? ORDER BY length(name) DESC, name ASC",
            (book_id,),
        ).fetchall()
        return [row["name"] for row in rows]

    def list_entities_with_aliases(self, book_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
                e.name,
                e.first_chapter_number,
                e.mention_count,
                COALESCE(group_concat(a.alias, '、'), '') AS aliases
            FROM entities e
            LEFT JOIN entity_aliases a ON e.entity_id = a.entity_id
            WHERE e.book_id = ?
            GROUP BY e.entity_id, e.name, e.first_chapter_number, e.mention_count
            ORDER BY e.first_chapter_number ASC, length(e.name) DESC, e.name ASC
            """,
            (book_id,),
        ).fetchall()

    def list_themes_with_aliases(self, book_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
                t.name,
                t.first_chapter_number,
                t.mention_count,
                COALESCE(group_concat(a.alias, '、'), '') AS aliases
            FROM themes t
            LEFT JOIN theme_aliases a ON t.theme_id = a.theme_id
            WHERE t.book_id = ?
            GROUP BY t.theme_id, t.name, t.first_chapter_number, t.mention_count
            ORDER BY t.first_chapter_number ASC, length(t.name) DESC, t.name ASC
            """,
            (book_id,),
        ).fetchall()

    def get_entity(self, book_id: str, entity_name: str) -> sqlite3.Row | None:
        normalized = _normalize_entity_name(entity_name)
        return self.connection.execute(
            """
            SELECT entity_id, name, first_chapter_number, mention_count
            FROM entities
            WHERE book_id = ? AND normalized_name = ?
            """,
            (book_id, normalized),
        ).fetchone()

    def resolve_entity_name(self, book_id: str, raw_name: str) -> str | None:
        normalized = _normalize_entity_name(raw_name)
        direct = self.connection.execute(
            "SELECT name FROM entities WHERE book_id = ? AND normalized_name = ?",
            (book_id, normalized),
        ).fetchone()
        if direct is not None:
            return str(direct["name"])
        alias = self.connection.execute(
            """
            SELECT e.name
            FROM entity_aliases a
            JOIN entities e ON a.entity_id = e.entity_id
            WHERE a.book_id = ? AND a.normalized_alias = ?
            """,
            (book_id, normalized),
        ).fetchone()
        return None if alias is None else str(alias["name"])

    def match_entity_candidates(self, book_id: str, text: str) -> list[str]:
        matches: list[str] = []
        entity_rows = self.connection.execute(
            "SELECT name FROM entities WHERE book_id = ? ORDER BY length(name) DESC, name ASC",
            (book_id,),
        ).fetchall()
        for row in entity_rows:
            name = str(row["name"])
            if name in text and name not in matches:
                matches.append(name)

        alias_rows = self.connection.execute(
            """
            SELECT alias, entity_id
            FROM entity_aliases
            WHERE book_id = ?
            ORDER BY length(alias) DESC, alias ASC
            """,
            (book_id,),
        ).fetchall()
        for row in alias_rows:
            alias = str(row["alias"])
            if alias not in text:
                continue
            canonical = self.connection.execute(
                "SELECT name FROM entities WHERE entity_id = ?",
                (row["entity_id"],),
            ).fetchone()
            if canonical is None:
                continue
            canonical_name = str(canonical["name"])
            if canonical_name not in matches:
                matches.append(canonical_name)
        return matches

    def resolve_theme_name(self, book_id: str, raw_name: str) -> str | None:
        normalized = _normalize_entity_name(raw_name)
        direct = self.connection.execute(
            "SELECT name FROM themes WHERE book_id = ? AND normalized_name = ?",
            (book_id, normalized),
        ).fetchone()
        if direct is not None:
            return str(direct["name"])
        alias = self.connection.execute(
            """
            SELECT t.name
            FROM theme_aliases a
            JOIN themes t ON a.theme_id = t.theme_id
            WHERE a.book_id = ? AND a.normalized_alias = ?
            """,
            (book_id, normalized),
        ).fetchone()
        return None if alias is None else str(alias["name"])

    def match_theme_candidates(self, book_id: str, text: str) -> list[str]:
        matches: list[str] = []
        theme_rows = self.connection.execute(
            "SELECT name FROM themes WHERE book_id = ? ORDER BY length(name) DESC, name ASC",
            (book_id,),
        ).fetchall()
        for row in theme_rows:
            name = str(row["name"])
            if name in text and name not in matches:
                matches.append(name)

        alias_rows = self.connection.execute(
            """
            SELECT alias, theme_id
            FROM theme_aliases
            WHERE book_id = ?
            ORDER BY length(alias) DESC, alias ASC
            """,
            (book_id,),
        ).fetchall()
        for row in alias_rows:
            alias = str(row["alias"])
            if alias not in text:
                continue
            canonical = self.connection.execute(
                "SELECT name FROM themes WHERE theme_id = ?",
                (row["theme_id"],),
            ).fetchone()
            if canonical is None:
                continue
            canonical_name = str(canonical["name"])
            if canonical_name not in matches:
                matches.append(canonical_name)
        return matches

    def get_entity_mentions(
        self,
        book_id: str,
        entity_name: str,
        max_chapter: int | None = None,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT em.chapter_number, em.excerpt, em.position_in_chapter
            FROM entity_mentions em
            JOIN entities e ON em.entity_id = e.entity_id
            WHERE em.book_id = ? AND e.normalized_name = ?
        """
        params: list[object] = [book_id, _normalize_entity_name(entity_name)]
        if max_chapter is not None:
            query += " AND em.chapter_number <= ?"
            params.append(max_chapter)
        query += " ORDER BY em.chapter_number ASC, em.position_in_chapter ASC"
        return self.connection.execute(query, params).fetchall()

    def get_relation_mentions(
        self,
        book_id: str,
        source_entity: str,
        target_entity: str,
        max_chapter: int | None = None,
    ) -> list[sqlite3.Row]:
        source = self.resolve_entity_name(book_id, source_entity) or source_entity
        target = self.resolve_entity_name(book_id, target_entity) or target_entity
        normalized_source = _normalize_entity_name(source)
        normalized_target = _normalize_entity_name(target)
        ordered_a, ordered_b = sorted((normalized_source, normalized_target))
        query = """
            SELECT
                r.source_entity,
                r.target_entity,
                r.relation_type,
                r.first_chapter_number,
                rm.chapter_number,
                rm.excerpt
            FROM relations r
            JOIN relation_mentions rm ON r.relation_id = rm.relation_id
            WHERE r.book_id = ?
              AND r.normalized_source = ?
              AND r.normalized_target = ?
        """
        params: list[object] = [book_id, ordered_a, ordered_b]
        if max_chapter is not None:
            query += " AND rm.chapter_number <= ?"
            params.append(max_chapter)
        query += " ORDER BY rm.chapter_number ASC, rm.mention_id ASC"
        return self.connection.execute(query, params).fetchall()

    def get_theme_mentions(
        self,
        book_id: str,
        theme_name: str,
        max_chapter: int | None = None,
    ) -> list[sqlite3.Row]:
        canonical = self.resolve_theme_name(book_id, theme_name) or theme_name
        query = """
            SELECT
                t.name,
                t.first_chapter_number,
                tm.chapter_number,
                tm.excerpt,
                tm.position_in_chapter
            FROM theme_mentions tm
            JOIN themes t ON tm.theme_id = t.theme_id
            WHERE tm.book_id = ? AND t.normalized_name = ?
        """
        params: list[object] = [book_id, _normalize_entity_name(canonical)]
        if max_chapter is not None:
            query += " AND tm.chapter_number <= ?"
            params.append(max_chapter)
        query += " ORDER BY tm.chapter_number ASC, tm.position_in_chapter ASC"
        return self.connection.execute(query, params).fetchall()

    def search_events(
        self,
        book_id: str,
        query_text: str = "",
        entity_name: str | None = None,
        max_chapter: int | None = None,
        limit: int = 8,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT
                e.event_id,
                e.chapter_number,
                e.chapter_title,
                e.event_type,
                e.summary,
                e.excerpt,
                COALESCE(group_concat(ee.entity_name, '、'), '') AS entities
            FROM events e
            LEFT JOIN event_entities ee ON e.event_id = ee.event_id
            WHERE e.book_id = ?
        """
        params: list[object] = [book_id]
        if max_chapter is not None:
            query += " AND e.chapter_number <= ?"
            params.append(max_chapter)
        if entity_name:
            canonical = self.resolve_entity_name(book_id, entity_name) or entity_name
            query += """
                AND EXISTS (
                    SELECT 1
                    FROM event_entities ee_filter
                    WHERE ee_filter.event_id = e.event_id
                      AND ee_filter.normalized_name = ?
                )
            """
            params.append(_normalize_entity_name(canonical))
        tokens = [] if entity_name else _event_query_tokens(query_text)
        for token in tokens[:5]:
            query += " AND (e.summary LIKE ? OR e.excerpt LIKE ? OR e.event_type LIKE ?)"
            like = f"%{token}%"
            params.extend([like, like, like])
        query += """
            GROUP BY e.event_id, e.chapter_number, e.chapter_title, e.event_type, e.summary, e.excerpt
            ORDER BY e.chapter_number ASC, e.event_id ASC
            LIMIT ?
        """
        params.append(limit)
        return self.connection.execute(query, params).fetchall()

    def list_relations_for_entity(
        self,
        book_id: str,
        entity_name: str,
        max_chapter: int | None = None,
    ) -> list[sqlite3.Row]:
        canonical = self.resolve_entity_name(book_id, entity_name) or entity_name
        normalized = _normalize_entity_name(canonical)
        query = """
            SELECT source_entity, target_entity, relation_type, first_chapter_number, mention_count
            FROM relations
            WHERE book_id = ?
              AND (normalized_source = ? OR normalized_target = ?)
        """
        params: list[object] = [book_id, normalized, normalized]
        if max_chapter is not None:
            query += " AND first_chapter_number <= ?"
            params.append(max_chapter)
        query += " ORDER BY first_chapter_number ASC, mention_count DESC"
        return self.connection.execute(query, params).fetchall()

    def list_relations(
        self,
        book_id: str,
        max_chapter: int | None = None,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT source_entity, target_entity, relation_type, first_chapter_number, mention_count
            FROM relations
            WHERE book_id = ?
        """
        params: list[object] = [book_id]
        if max_chapter is not None:
            query += " AND first_chapter_number <= ?"
            params.append(max_chapter)
        query += " ORDER BY first_chapter_number ASC, mention_count DESC, source_entity ASC, target_entity ASC LIMIT ?"
        params.append(limit)
        return self.connection.execute(query, params).fetchall()

    def iter_search_rows(self, book_id: str, max_chapter: int | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT c.chunk_id, c.parent_id, c.chapter_number, c.text, p.chapter_title, p.text AS parent_text
            FROM child_chunks c
            JOIN parent_chunks p ON c.parent_id = p.chunk_id
            WHERE c.book_id = ?
        """
        params: list[object] = [book_id]
        if max_chapter is not None:
            query += " AND c.chapter_number <= ?"
            params.append(max_chapter)
        query += " ORDER BY c.chapter_number ASC, c.chunk_index ASC"
        return self.connection.execute(query, params).fetchall()

    def set_progress(self, book_id: str, user_id: str, progress_chapter: int) -> None:
        self.connection.execute(
            """
            INSERT INTO reader_state(book_id, user_id, progress_chapter, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id, user_id)
            DO UPDATE SET progress_chapter = excluded.progress_chapter, updated_at = CURRENT_TIMESTAMP
            """,
            (book_id, user_id, progress_chapter),
        )
        self.connection.commit()

    def get_progress(self, book_id: str, user_id: str) -> int | None:
        row = self.connection.execute(
            "SELECT progress_chapter FROM reader_state WHERE book_id = ? AND user_id = ?",
            (book_id, user_id),
        ).fetchone()
        return None if row is None else int(row["progress_chapter"])

    def list_agent_turns(
        self,
        book_id: str,
        user_id: str,
        session_id: str,
        *,
        limit: int = 4,
    ) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                turn_id,
                turn_index,
                question,
                intent,
                entity_name,
                answer,
                summary,
                progress_chapter,
                matched_entities_json,
                trace_json,
                created_at
            FROM agent_memory
            WHERE book_id = ? AND user_id = ? AND session_id = ?
            ORDER BY turn_index DESC, turn_id DESC
            LIMIT ?
            """,
            (book_id, user_id, session_id, limit),
        ).fetchall()
        turns: list[dict[str, object]] = []
        for row in reversed(rows):
            turns.append(_agent_turn_from_row(row))
        return turns

    def list_agent_sessions(self, book_id: str, user_id: str, *, limit: int = 30) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                session_id,
                COUNT(*) AS turn_count,
                MAX(turn_index) AS last_turn_index,
                MAX(created_at) AS updated_at
            FROM agent_memory
            WHERE book_id = ? AND user_id = ?
            GROUP BY session_id
            ORDER BY updated_at DESC, session_id ASC
            LIMIT ?
            """,
            (book_id, user_id, limit),
        ).fetchall()
        sessions: list[dict[str, object]] = []
        for row in rows:
            latest = self.connection.execute(
                """
                SELECT turn_id, turn_index, question, intent, entity_name, answer, summary,
                       progress_chapter, matched_entities_json, trace_json, created_at
                FROM agent_memory
                WHERE book_id = ? AND user_id = ? AND session_id = ?
                ORDER BY turn_index DESC, turn_id DESC
                LIMIT 1
                """,
                (book_id, user_id, row["session_id"]),
            ).fetchone()
            latest_turn = _agent_turn_from_row(latest) if latest is not None else None
            sessions.append(
                {
                    "session_id": str(row["session_id"]),
                    "turn_count": int(row["turn_count"]),
                    "last_turn_index": int(row["last_turn_index"] or 0),
                    "updated_at": str(row["updated_at"] or ""),
                    "last_turn": latest_turn,
                    "last_question": str(latest_turn["question"]) if latest_turn else "",
                    "last_answer": str(latest_turn["answer"]) if latest_turn else "",
                    "last_summary": str(latest_turn["summary"]) if latest_turn and latest_turn.get("summary") else "",
                }
            )
        return sessions

    def get_agent_turn(self, *, turn_id: int, book_id: str, user_id: str, session_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT turn_id, turn_index, question, intent, entity_name, answer, summary, progress_chapter,
                   matched_entities_json, trace_json, created_at
            FROM agent_memory
            WHERE turn_id = ? AND book_id = ? AND user_id = ? AND session_id = ?
            """,
            (turn_id, book_id, user_id, session_id),
        ).fetchone()
        return None if row is None else _agent_turn_from_row(row)

    def list_agent_turns_before(
        self,
        *,
        turn_id: int,
        book_id: str,
        user_id: str,
        session_id: str,
    ) -> list[dict[str, object]]:
        row = self.connection.execute(
            """
            SELECT turn_index
            FROM agent_memory
            WHERE turn_id = ? AND book_id = ? AND user_id = ? AND session_id = ?
            """,
            (turn_id, book_id, user_id, session_id),
        ).fetchone()
        if row is None:
            return []
        rows = self.connection.execute(
            """
            SELECT turn_id, turn_index, question, intent, entity_name, answer, summary, progress_chapter,
                   matched_entities_json, trace_json, created_at
            FROM agent_memory
            WHERE book_id = ? AND user_id = ? AND session_id = ? AND turn_index < ?
            ORDER BY turn_index ASC, turn_id ASC
            """,
            (book_id, user_id, session_id, int(row["turn_index"])),
        ).fetchall()
        return [_agent_turn_from_row(item) for item in rows]

    def copy_agent_turns_to_session(
        self,
        *,
        book_id: str,
        user_id: str,
        source_turns: list[dict[str, object]],
        target_session_id: str,
    ) -> int:
        copied = 0
        for turn in source_turns:
            trace = turn.get("trace")
            matched_entities = turn.get("matched_entities")
            self.append_agent_turn(
                book_id=book_id,
                user_id=user_id,
                session_id=target_session_id,
                question=str(turn.get("question") or ""),
                intent=str(turn.get("intent") or "semantic_search"),
                entity_name=str(turn["entity_name"]) if turn.get("entity_name") else None,
                answer=str(turn.get("answer") or ""),
                summary=str(turn["summary"]) if turn.get("summary") else None,
                progress_chapter=int(turn.get("progress_chapter") or 0),
                matched_entities=[str(item) for item in matched_entities] if isinstance(matched_entities, list) else [],
                trace=[dict(item) for item in trace] if isinstance(trace, list) else [],
            )
            copied += 1
        return copied

    def append_agent_turn(
        self,
        *,
        book_id: str,
        user_id: str,
        session_id: str,
        question: str,
        intent: str,
        entity_name: str | None,
        answer: str,
        summary: str | None,
        progress_chapter: int,
        matched_entities: list[str],
        trace: list[dict[str, object]],
    ) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(turn_index), 0) AS max_turn_index
            FROM agent_memory
            WHERE book_id = ? AND user_id = ? AND session_id = ?
            """,
            (book_id, user_id, session_id),
        ).fetchone()
        turn_index = int(row["max_turn_index"]) + 1
        cursor = self.connection.execute(
            """
            INSERT INTO agent_memory(
                book_id,
                user_id,
                session_id,
                turn_index,
                question,
                intent,
                entity_name,
                answer,
                summary,
                progress_chapter,
                matched_entities_json,
                trace_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                user_id,
                session_id,
                turn_index,
                question,
                intent,
                entity_name,
                answer,
                summary,
                progress_chapter,
                json.dumps(matched_entities, ensure_ascii=False),
                json.dumps(trace, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_agent_turn(
        self,
        *,
        turn_id: int,
        book_id: str,
        user_id: str,
        session_id: str,
        question: str,
        answer: str,
        summary: str | None = None,
    ) -> dict[str, object] | None:
        self.connection.execute(
            """
            UPDATE agent_memory
            SET question = ?, answer = ?, summary = ?
            WHERE turn_id = ? AND book_id = ? AND user_id = ? AND session_id = ?
            """,
            (question, answer, summary, turn_id, book_id, user_id, session_id),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT turn_id, turn_index, question, intent, entity_name, answer, summary, progress_chapter,
                   matched_entities_json, trace_json, created_at
            FROM agent_memory
            WHERE turn_id = ? AND book_id = ? AND user_id = ? AND session_id = ?
            """,
            (turn_id, book_id, user_id, session_id),
        ).fetchone()
        if row is None:
            return None
        return _agent_turn_from_row(row)

    def delete_agent_turn(self, *, turn_id: int, book_id: str, user_id: str, session_id: str) -> int:
        cursor = self.connection.execute(
            """
            DELETE FROM agent_memory
            WHERE turn_id = ? AND book_id = ? AND user_id = ? AND session_id = ?
            """,
            (turn_id, book_id, user_id, session_id),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def delete_agent_turns_from(self, *, turn_id: int, book_id: str, user_id: str, session_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT turn_index
            FROM agent_memory
            WHERE turn_id = ? AND book_id = ? AND user_id = ? AND session_id = ?
            """,
            (turn_id, book_id, user_id, session_id),
        ).fetchone()
        if row is None:
            return 0
        cursor = self.connection.execute(
            """
            DELETE FROM agent_memory
            WHERE book_id = ? AND user_id = ? AND session_id = ? AND turn_index >= ?
            """,
            (book_id, user_id, session_id, int(row["turn_index"])),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def delete_agent_session(self, *, book_id: str, user_id: str, session_id: str) -> int:
        cursor = self.connection.execute(
            """
            DELETE FROM agent_memory
            WHERE book_id = ? AND user_id = ? AND session_id = ?
            """,
            (book_id, user_id, session_id),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def get_max_chapter(self, book_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(chapter_number), 0) AS max_chapter FROM chapters WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        return int(row["max_chapter"])

    def get_chapter_summaries(self, book_id: str, max_chapter: int | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT chapter_number, chapter_title, summary
            FROM chapter_summaries
            WHERE book_id = ?
        """
        params: list[object] = [book_id]
        if max_chapter is not None:
            query += " AND chapter_number <= ?"
            params.append(max_chapter)
        query += " ORDER BY chapter_number ASC"
        return self.connection.execute(query, params).fetchall()

    def get_chapter_titles(self, book_id: str, limit: int | None = None) -> list[sqlite3.Row]:
        query = "SELECT chapter_number, title FROM chapters WHERE book_id = ? ORDER BY chapter_number ASC"
        params: list[object] = [book_id]
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        return self.connection.execute(query, params).fetchall()

    def list_chapter_records(self, book_id: str) -> list[Chapter]:
        rows = self.connection.execute(
            """
            SELECT chapter_number, title, content, start_offset, end_offset
            FROM chapters
            WHERE book_id = ?
            ORDER BY chapter_number ASC
            """,
            (book_id,),
        ).fetchall()
        return [
            Chapter(
                number=int(row["chapter_number"]),
                title=str(row["title"]),
                content=str(row["content"]),
                start_offset=int(row["start_offset"]),
                end_offset=int(row["end_offset"]),
            )
            for row in rows
        ]

    def get_chapter(self, book_id: str, chapter_number: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT chapter_number, title, content, start_offset, end_offset
            FROM chapters
            WHERE book_id = ? AND chapter_number = ?
            """,
            (book_id, chapter_number),
        ).fetchone()

    def get_stats(self, book_id: str) -> dict[str, int]:
        rows = self.connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM chapters WHERE book_id = ?) AS chapters,
              (SELECT COUNT(*) FROM parent_chunks WHERE book_id = ?) AS parents,
              (SELECT COUNT(*) FROM child_chunks WHERE book_id = ?) AS children,
              (SELECT COUNT(*) FROM entities WHERE book_id = ?) AS entities,
              (SELECT COUNT(*) FROM entity_mentions WHERE book_id = ?) AS mentions,
              (SELECT COUNT(*) FROM relations WHERE book_id = ?) AS relations,
              (SELECT COUNT(*) FROM themes WHERE book_id = ?) AS themes,
              (SELECT COUNT(*) FROM theme_mentions WHERE book_id = ?) AS theme_mentions,
              (SELECT COUNT(*) FROM events WHERE book_id = ?) AS events
            """,
            (book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id),
        ).fetchone()
        return {
            "chapters": int(rows["chapters"]),
            "parent_chunks": int(rows["parents"]),
            "child_chunks": int(rows["children"]),
            "entities": int(rows["entities"]),
            "entity_mentions": int(rows["mentions"]),
            "relations": int(rows["relations"]),
            "themes": int(rows["themes"]),
            "theme_mentions": int(rows["theme_mentions"]),
            "events": int(rows["events"]),
        }

    def delete_book(self, book_id: str) -> int:
        """删除单本书的全部索引数据，返回被清理的 chunk 数（用于提示）。按表逐个删，保留 db 自身。"""
        counts = self.get_stats(book_id)
        cursor = self.connection.cursor()
        for table in (
            "entity_mentions",
            "entity_aliases",
            "relation_mentions",
            "relations",
            "theme_mentions",
            "theme_aliases",
            "themes",
            "event_entities",
            "events",
            "entities",
            "chapter_summaries",
            "child_chunks",
            "parent_chunks",
            "chapters",
            "reader_state",
            "agent_memory",
        ):
            cursor.execute(f"DELETE FROM {table} WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM books WHERE book_id = ?", (book_id,))
        self.connection.commit()
        return counts["parent_chunks"] + counts["child_chunks"]


def _chapter_summary(text: str, max_chars: int = 140) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _loads_json_list(raw: object) -> list[object]:
    if raw in (None, ""):
        return []
    try:
        parsed = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _loads_string_list(raw: object) -> list[str]:
    values = _loads_json_list(raw)
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _agent_turn_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "turn_id": int(row["turn_id"]),
        "turn_index": int(row["turn_index"]),
        "question": str(row["question"]),
        "intent": str(row["intent"]),
        "entity_name": str(row["entity_name"]) if row["entity_name"] else None,
        "answer": str(row["answer"]),
        "summary": str(row["summary"]) if row["summary"] else None,
        "progress_chapter": int(row["progress_chapter"]),
        "matched_entities": _loads_json_list(row["matched_entities_json"]),
        "trace": _loads_json_list(row["trace_json"]),
        "created_at": str(row["created_at"]),
    }


def _event_query_tokens(query_text: str) -> list[str]:
    cleaned = str(query_text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) > 8 and not any(sep in cleaned for sep in " ，。！？、；：,.!?;:"):
        return []
    stopwords = {
        "什么", "怎么", "如何", "为什么", "哪些", "关键", "事件", "主线", "发生", "后来",
        "关系", "变化", "了吗", "吗", "的", "了", "和", "与",
    }
    raw_tokens = [item for item in re_split_query(cleaned) if item and item not in stopwords]
    return raw_tokens[:8]


def re_split_query(text: str) -> list[str]:
    import re

    return [item for item in re.split(r"[\s，。！？、；：,.!?;:《》【】\"'“”‘’]+", text) if item]
