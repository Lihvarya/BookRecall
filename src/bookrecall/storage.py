import sqlite3
from pathlib import Path

from .models import Chapter, ChildChunk, EntityRecord, ParentChunk


def _normalize_entity_name(name: str) -> str:
    return "".join(name.lower().split())


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

            CREATE TABLE IF NOT EXISTS reader_state (
                book_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                progress_chapter INTEGER NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, user_id)
            );
            """
        )
        self.connection.commit()

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
    ) -> None:
        cursor = self.connection.cursor()
        cursor.execute("INSERT OR REPLACE INTO books(book_id, title, source_path) VALUES (?, ?, ?)", (book_id, title, source_path))
        cursor.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM parent_chunks WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM child_chunks WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM entity_mentions WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM entities WHERE book_id = ?", (book_id,))

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

        self.connection.commit()

    def list_entities(self, book_id: str) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM entities WHERE book_id = ? ORDER BY length(name) DESC, name ASC",
            (book_id,),
        ).fetchall()
        return [row["name"] for row in rows]

    def get_entity(self, book_id: str, entity_name: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT entity_id, name, first_chapter_number, mention_count
            FROM entities
            WHERE book_id = ? AND normalized_name = ?
            """,
            (book_id, _normalize_entity_name(entity_name)),
        ).fetchone()

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

    def get_max_chapter(self, book_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(chapter_number), 0) AS max_chapter FROM chapters WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        return int(row["max_chapter"])

