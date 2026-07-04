from __future__ import annotations

import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agent import BookRecallAgent
from .chunking import build_chunk_hierarchy
from .cloud import OpenAICompatibleReasoner
from .config import DEFAULT_CHUNK_SETTINGS, DEFAULT_EMBEDDING_SETTINGS, DEFAULT_SEARCH_SETTINGS
from .embeddings import (
    EmbeddingRetriever,
    LocalModelError,
    SentenceTransformerEmbedder,
    build_embedding_index,
    configure_local_model_cache,
    default_cache_root,
    default_sentence_transformers_cache_dir,
    default_vector_dir,
    dependency_report,
    get_vector_index_info,
)
from .entity_index import (
    auto_discover_entities,
    auto_discover_themes,
    build_entity_records,
    build_event_records,
    build_relation_records,
    build_theme_records,
)
from .parser import parse_chapters
from .retrieval import LocalRetriever, Retriever
from .storage import BookRecallStore


class _DisabledReasoner:
    enabled = False
    model = None

    def answer(self, prompt: str) -> str | None:
        return None


def _parse_inline_lexicon(raw: str) -> dict[str, list[str]]:
    items: dict[str, list[str]] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "|" in stripped:
            canonical, alias_blob = stripped.split("|", 1)
            aliases = [alias.strip() for alias in alias_blob.replace("，", ",").split(",") if alias.strip()]
            if canonical.strip():
                items[canonical.strip()] = aliases
        else:
            items[stripped] = []
    return items


class BookRecallWebService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _open_store(self) -> BookRecallStore:
        store = BookRecallStore(self.db_path)
        store.initialize()
        return store

    def list_books(self) -> list[dict[str, object]]:
        store = self._open_store()
        try:
            return [
                {
                    "book_id": item.book_id,
                    "title": item.title,
                    "source_path": item.source_path,
                    "chapter_count": item.chapter_count,
                    "entity_count": item.entity_count,
                }
                for item in store.list_books()
            ]
        finally:
            store.close()

    def build_book(
        self,
        *,
        book_id: str,
        title: str,
        text: str,
        entity_lexicon: str = "",
        theme_lexicon: str = "",
        overwrite: bool = False,
    ) -> dict[str, object]:
        if not book_id:
            raise ValueError("book_id 不能为空。")
        if not text.strip():
            raise ValueError("书籍正文不能为空。")

        store = self._open_store()
        try:
            if store.get_book(book_id) is not None and not overwrite:
                raise ValueError("book_id 已存在。若要重建，请勾选覆盖已有索引。")

            chapters = parse_chapters(text)
            parent_chunks, child_chunks = build_chunk_hierarchy(book_id, chapters, DEFAULT_CHUNK_SETTINGS)
            entity_names = _parse_inline_lexicon(entity_lexicon)
            if not entity_names:
                entity_names = auto_discover_entities(text)
            entity_records = build_entity_records(chapters, entity_names, DEFAULT_CHUNK_SETTINGS)
            relation_records = build_relation_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
            theme_names = auto_discover_themes(text, extra_terms=_parse_inline_lexicon(theme_lexicon))
            theme_records = build_theme_records(chapters, theme_names, DEFAULT_CHUNK_SETTINGS)
            event_records = build_event_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)

            store.replace_book(
                book_id=book_id,
                title=title or book_id,
                source_path="web://pasted-text",
                chapters=chapters,
                parent_chunks=parent_chunks,
                child_chunks=child_chunks,
                entity_records=entity_records,
                relation_records=relation_records,
                theme_records=theme_records,
                event_records=event_records,
            )
            return {
                "book_id": book_id,
                "title": title or book_id,
                "chapter_count": len(chapters),
                "parent_chunks": len(parent_chunks),
                "child_chunks": len(child_chunks),
                "entities": len(entity_records),
                "relations": len(relation_records),
                "themes": len(theme_records),
                "events": len(event_records),
                "overwritten": overwrite,
            }
        finally:
            store.close()

    def runtime_status(self) -> dict[str, object]:
        report = dependency_report()
        vector_dir = default_vector_dir(self.db_path)
        cache_dir = default_sentence_transformers_cache_dir(self.db_path)
        store = self._open_store()
        try:
            books = store.list_books()
        finally:
            store.close()

        vector_indexes = []
        for book in books:
            info = get_vector_index_info(vector_dir, book.book_id)
            vector_indexes.append(
                {
                    "book_id": book.book_id,
                    "built": info is not None,
                    "model_name": info.model_name if info else None,
                    "backend": info.backend if info else None,
                    "chunk_count": info.chunk_count if info else 0,
                    "dimension": info.dimension if info else 0,
                    "path": info.path if info else None,
                }
            )

        endpoint = os.getenv("BOOKRECALL_API_ENDPOINT") or "https://api.openai.com/v1/chat/completions"
        model = os.getenv("BOOKRECALL_MODEL") or "gpt-4o-mini"
        return {
            "dependencies": report,
            "vector_dir": str(vector_dir),
            "model_cache_dir": str(cache_dir),
            "vector_indexes": vector_indexes,
            "cloud": {
                "env_key_available": bool(os.getenv("BOOKRECALL_API_KEY") or os.getenv("OPENAI_API_KEY")),
                "endpoint": endpoint,
                "model": model,
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "endpoint": "https://api.deepseek.com/v1/chat/completions",
                        "model": "deepseek-chat",
                    },
                    {
                        "id": "openai",
                        "name": "OpenAI",
                        "endpoint": "https://api.openai.com/v1/chat/completions",
                        "model": "gpt-4o-mini",
                    },
                    {
                        "id": "custom",
                        "name": "OpenAI-compatible",
                        "endpoint": endpoint,
                        "model": model,
                    },
                ],
            },
            "retrievers": [
                {"id": "lexical", "name": "倒排检索", "ready": True},
                {
                    "id": "embedding",
                    "name": "本地 embedding",
                    "ready": bool(report["numpy"] and report["sentence_transformers"]),
                },
                {"id": "auto", "name": "自动选择", "ready": True},
            ],
        }

    def list_entities(self, book_id: str) -> list[dict[str, object]]:
        store = self._open_store()
        try:
            rows = store.list_entities_with_aliases(book_id)
            return [
                {
                    "name": row["name"],
                    "first_chapter_number": int(row["first_chapter_number"]),
                    "mention_count": int(row["mention_count"]),
                    "aliases": row["aliases"].split("、") if row["aliases"] else [],
                }
                for row in rows
            ]
        finally:
            store.close()

    def list_chapters(self, book_id: str, limit: int = 50) -> list[dict[str, object]]:
        store = self._open_store()
        try:
            titles = store.get_chapter_titles(book_id, limit=limit)
            summaries = {int(r["chapter_number"]): str(r["summary"]) for r in store.get_chapter_summaries(book_id)}
            return [
                {
                    "chapter_number": int(row["chapter_number"]),
                    "title": row["title"],
                    "summary": summaries.get(int(row["chapter_number"]), ""),
                }
                for row in titles
            ]
        finally:
            store.close()

    def get_book_stats(self, book_id: str) -> dict[str, int]:
        store = self._open_store()
        try:
            return store.get_stats(book_id)
        finally:
            store.close()

    def list_themes(self, book_id: str) -> list[dict[str, object]]:
        store = self._open_store()
        try:
            rows = store.list_themes_with_aliases(book_id)
            return [
                {
                    "name": row["name"],
                    "first_chapter_number": int(row["first_chapter_number"]),
                    "mention_count": int(row["mention_count"]),
                    "aliases": row["aliases"].split("、") if row["aliases"] else [],
                }
                for row in rows
            ]
        finally:
            store.close()

    def list_events(
        self,
        book_id: str,
        *,
        entity_name: str | None = None,
        max_chapter: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        store = self._open_store()
        try:
            rows = store.search_events(
                book_id,
                entity_name=entity_name,
                max_chapter=max_chapter,
                limit=limit,
            )
            return [
                {
                    "chapter_number": int(row["chapter_number"]),
                    "chapter_title": row["chapter_title"],
                    "event_type": row["event_type"],
                    "summary": row["summary"],
                    "excerpt": row["excerpt"],
                    "entities": row["entities"].split("、") if row["entities"] else [],
                }
                for row in rows
            ]
        finally:
            store.close()

    def list_relations(
        self,
        book_id: str,
        *,
        entity_name: str | None = None,
        max_chapter: int | None = None,
        limit: int = 40,
    ) -> list[dict[str, object]]:
        store = self._open_store()
        try:
            if entity_name:
                rows = store.list_relations_for_entity(book_id, entity_name, max_chapter=max_chapter)
            else:
                rows = store.list_relations(book_id, max_chapter=max_chapter, limit=limit)
            return [
                {
                    "source_entity": row["source_entity"],
                    "target_entity": row["target_entity"],
                    "relation_type": row["relation_type"],
                    "first_chapter_number": int(row["first_chapter_number"]),
                    "mention_count": int(row["mention_count"]),
                }
                for row in rows[:limit]
            ]
        finally:
            store.close()

    def build_vector_index(
        self,
        *,
        book_id: str,
        model_name: str | None = None,
        batch_size: int | None = None,
        limit_chunks: int | None = None,
        backend: str = "auto",
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            if store.get_book(book_id) is None:
                raise LocalModelError(f"没有找到 book_id={book_id}，请先创建书籍索引。")
            configure_local_model_cache(default_cache_root(self.db_path))
            embedder = SentenceTransformerEmbedder(
                model_name or DEFAULT_EMBEDDING_SETTINGS.model_name,
                cache_dir=default_sentence_transformers_cache_dir(self.db_path),
            )
            info = build_embedding_index(
                store=store,
                book_id=book_id,
                index_dir=default_vector_dir(self.db_path),
                embedder=embedder,
                batch_size=batch_size or DEFAULT_EMBEDDING_SETTINGS.batch_size,
                limit_chunks=limit_chunks,
                prefer_backend=backend,
            )
            return {
                "book_id": info.book_id,
                "model_name": info.model_name,
                "backend": info.backend,
                "chunk_count": info.chunk_count,
                "dimension": info.dimension,
                "path": info.path,
            }
        finally:
            store.close()

    def get_progress(self, book_id: str, user_id: str) -> dict[str, object]:
        store = self._open_store()
        try:
            progress = store.get_progress(book_id, user_id)
            max_chapter = store.get_max_chapter(book_id)
            return {
                "book_id": book_id,
                "user_id": user_id,
                "progress_chapter": progress,
                "max_chapter": max_chapter,
            }
        finally:
            store.close()

    def set_progress(self, book_id: str, user_id: str, chapter: int) -> dict[str, object]:
        store = self._open_store()
        try:
            store.set_progress(book_id, user_id, chapter)
            max_chapter = store.get_max_chapter(book_id)
            return {
                "book_id": book_id,
                "user_id": user_id,
                "progress_chapter": chapter,
                "max_chapter": max_chapter,
            }
        finally:
            store.close()

    def get_session_history(
        self,
        book_id: str,
        user_id: str,
        session_id: str,
        *,
        limit: int = 10,
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            turns = store.list_agent_turns(book_id, user_id, session_id, limit=limit)
        finally:
            store.close()
        return {
            "book_id": book_id,
            "user_id": user_id,
            "session_id": session_id,
            "turns": turns,
        }

    def ask(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
        session_id: str | None = None,
        progress_chapter: int | None = None,
        retriever_mode: str = "lexical",
        cloud_config: dict[str, object] | None = None,
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            retriever = self._make_retriever(store, book_id, retriever_mode)
            reasoner = self._make_reasoner(cloud_config)
            agent = BookRecallAgent(store, retriever=retriever, reasoner=reasoner)
            card = agent.ask_card(
                book_id=book_id,
                question=question,
                user_id=user_id,
                session_id=session_id,
                progress_chapter=progress_chapter,
            )
            payload = agent.to_payload(card)
            payload["rendered_text"] = agent.render_text(card)
            payload["runtime"] = {
                "retriever": retriever_mode,
                "cloud_reasoner_enabled": reasoner.enabled,
                "cloud_model": reasoner.model if reasoner.enabled else None,
            }
            if session_id:
                session_data = {
                    "book_id": book_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "turns": store.list_agent_turns(book_id, user_id, session_id, limit=10),
                }
                payload["session"] = session_data
                payload["trace"] = session_data["turns"][-1]["trace"] if session_data["turns"] else []
            else:
                payload["session"] = None
                payload["trace"] = []
            return payload
        finally:
            store.close()

    def _make_retriever(self, store: BookRecallStore, book_id: str, mode: str) -> Retriever:
        if mode not in {"lexical", "embedding", "auto"}:
            raise LocalModelError("未知检索器，请选择 lexical、embedding 或 auto。")
        if mode == "lexical":
            return LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)

        vector_dir = default_vector_dir(self.db_path)
        info = get_vector_index_info(vector_dir, book_id)
        if info is None:
            if mode == "auto":
                return LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)
            raise LocalModelError("这本书还没有向量索引，请先运行 embed-build，或在网页端选择倒排检索。")

        try:
            configure_local_model_cache(default_cache_root(self.db_path))
            embedder = SentenceTransformerEmbedder(
                info.model_name,
                cache_dir=default_sentence_transformers_cache_dir(self.db_path),
            )
            return EmbeddingRetriever(store, DEFAULT_SEARCH_SETTINGS, index_dir=vector_dir, embedder=embedder)
        except LocalModelError:
            if mode == "auto":
                return LocalRetriever(store, DEFAULT_SEARCH_SETTINGS)
            raise

    def _make_reasoner(self, cloud_config: dict[str, object] | None) -> OpenAICompatibleReasoner | _DisabledReasoner:
        if not cloud_config:
            return OpenAICompatibleReasoner()
        enabled = bool(cloud_config.get("enabled"))
        if not enabled:
            return _DisabledReasoner()
        return OpenAICompatibleReasoner(
            api_key=str(cloud_config.get("api_key") or "").strip(),
            endpoint=str(cloud_config.get("endpoint") or "").strip() or None,
            model=str(cloud_config.get("model") or "").strip() or None,
        )


class BookRecallHandler(BaseHTTPRequestHandler):
    service: BookRecallWebService
    server_version = "BookRecallHTTP/0.2"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_html(_build_index_html())
            return

        if path.startswith("/assets/"):
            self._send_asset(path[len("/assets/") :])
            return

        if path == "/api/books":
            self._send_json({"books": self.service.list_books()})
            return

        if path == "/api/runtime":
            self._send_json(self.service.runtime_status())
            return

        if path.startswith("/api/books/") and path.endswith("/entities"):
            book_id = path[len("/api/books/") : -len("/entities")].strip("/")
            self._send_json({"book_id": book_id, "entities": self.service.list_entities(book_id)})
            return

        if path.startswith("/api/books/") and path.endswith("/chapters"):
            book_id = path[len("/api/books/") : -len("/chapters")].strip("/")
            query = parse_qs(parsed.query)
            limit_raw = query.get("limit", ["50"])[0]
            try:
                limit = max(1, min(500, int(limit_raw)))
            except ValueError:
                limit = 50
            self._send_json({"book_id": book_id, "chapters": self.service.list_chapters(book_id, limit)})
            return

        if path.startswith("/api/books/") and path.endswith("/stats"):
            book_id = path[len("/api/books/") : -len("/stats")].strip("/")
            self._send_json({"book_id": book_id, "stats": self.service.get_book_stats(book_id)})
            return

        if path.startswith("/api/books/") and path.endswith("/themes"):
            book_id = path[len("/api/books/") : -len("/themes")].strip("/")
            self._send_json({"book_id": book_id, "themes": self.service.list_themes(book_id)})
            return

        if path.startswith("/api/books/") and path.endswith("/events"):
            book_id = path[len("/api/books/") : -len("/events")].strip("/")
            query = parse_qs(parsed.query)
            entity_name = query.get("entity", [""])[0].strip() or None
            limit_raw = query.get("limit", ["20"])[0]
            max_chapter_raw = query.get("max_chapter", [""])[0]
            try:
                limit = max(1, min(100, int(limit_raw)))
                max_chapter = int(max_chapter_raw) if max_chapter_raw else None
            except ValueError:
                limit = 20
                max_chapter = None
            self._send_json(
                {
                    "book_id": book_id,
                    "events": self.service.list_events(
                        book_id,
                        entity_name=entity_name,
                        max_chapter=max_chapter,
                        limit=limit,
                    ),
                }
            )
            return

        if path.startswith("/api/books/") and path.endswith("/relations"):
            book_id = path[len("/api/books/") : -len("/relations")].strip("/")
            query = parse_qs(parsed.query)
            entity_name = query.get("entity", [""])[0].strip() or None
            limit_raw = query.get("limit", ["40"])[0]
            max_chapter_raw = query.get("max_chapter", [""])[0]
            try:
                limit = max(1, min(100, int(limit_raw)))
                max_chapter = int(max_chapter_raw) if max_chapter_raw else None
            except ValueError:
                limit = 40
                max_chapter = None
            self._send_json(
                {
                    "book_id": book_id,
                    "relations": self.service.list_relations(
                        book_id,
                        entity_name=entity_name,
                        max_chapter=max_chapter,
                        limit=limit,
                    ),
                }
            )
            return

        if path.startswith("/api/books/") and path.endswith("/progress"):
            book_id = path[len("/api/books/") : -len("/progress")].strip("/")
            query = parse_qs(parsed.query)
            user_id = query.get("user", ["default"])[0]
            self._send_json(self.service.get_progress(book_id, user_id))
            return

        if path.startswith("/api/books/") and path.endswith("/session"):
            book_id = path[len("/api/books/") : -len("/session")].strip("/")
            query = parse_qs(parsed.query)
            user_id = query.get("user", ["default"])[0]
            session_id = query.get("session", ["default-session"])[0]
            limit_raw = query.get("limit", ["10"])[0]
            try:
                limit = max(1, min(50, int(limit_raw)))
            except ValueError:
                limit = 10
            self._send_json(self.service.get_session_history(book_id, user_id, session_id, limit=limit))
            return

        if path == "/health":
            self._send_json({"ok": True, "thread": threading.current_thread().name})
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "接口不存在。")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/books/build":
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = str(payload.get("book_id", "")).strip()
                title = str(payload.get("title", "")).strip()
                text = str(payload.get("text", ""))
                entity_lexicon = str(payload.get("entities", ""))
                theme_lexicon = str(payload.get("themes", ""))
                overwrite = bool(payload.get("overwrite"))
                if not book_id or not text.strip():
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "book_id 和书籍正文不能为空。")
                    return
                self._send_json(
                    {
                        "book": self.service.build_book(
                            book_id=book_id,
                            title=title,
                            text=text,
                            entity_lexicon=entity_lexicon,
                            theme_lexicon=theme_lexicon,
                            overwrite=overwrite,
                        )
                    }
                )
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/vectors"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/vectors")].strip("/")
                model_name = str(payload.get("model", "")).strip() or None
                backend = str(payload.get("backend", "auto")).strip() or "auto"
                batch_raw = payload.get("batch_size")
                limit_raw = payload.get("limit_chunks")
                batch_size = int(batch_raw) if batch_raw not in (None, "") else None
                limit_chunks = int(limit_raw) if limit_raw not in (None, "") else None
                self._send_json(
                    {
                        "vector_index": self.service.build_vector_index(
                            book_id=book_id,
                            model_name=model_name,
                            batch_size=batch_size,
                            limit_chunks=limit_chunks,
                            backend=backend,
                        )
                    }
                )
                return

            if parsed.path == "/api/ask":
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = str(payload.get("book_id", "")).strip()
                question = str(payload.get("question", "")).strip()
                user_id = str(payload.get("user_id", "default")).strip() or "default"
                session_id = str(payload.get("session_id", "")).strip() or None
                retriever_mode = str(payload.get("retriever", "lexical")).strip() or "lexical"
                progress_raw = payload.get("progress_chapter")
                progress_chapter = int(progress_raw) if progress_raw not in (None, "") else None
                cloud_config = payload.get("cloud_config")
                if not isinstance(cloud_config, dict):
                    cloud_config = None
                if not book_id or not question:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "book_id 和 question 不能为空。")
                    return
                self._send_json(
                    self.service.ask(
                        book_id=book_id,
                        question=question,
                        user_id=user_id,
                        session_id=session_id,
                        progress_chapter=progress_chapter,
                        retriever_mode=retriever_mode,
                        cloud_config=cloud_config,
                    )
                )
                return

            if parsed.path == "/api/progress":
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = str(payload.get("book_id", "")).strip()
                user_id = str(payload.get("user_id", "default")).strip() or "default"
                chapter_raw = payload.get("progress_chapter")
                if not book_id or chapter_raw in (None, ""):
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "book_id 和 progress_chapter 不能为空。")
                    return
                self._send_json(self.service.set_progress(book_id, user_id, int(chapter_raw)))
                return
        except LocalModelError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except (TypeError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, f"请求参数不合法：{exc}")
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "接口不存在。")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json_body(self) -> dict[str, object] | None:
        length_header = self.headers.get("Content-Length", "0").strip() or "0"
        length = int(length_header)
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "请求体不是合法 JSON。")
            return None

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_asset(self, asset_name: str) -> None:
        safe_name = Path(asset_name).name
        asset_path = _asset_root() / safe_name
        if safe_name not in {"app.css", "app.js"} or not asset_path.exists():
            self._send_error_json(HTTPStatus.NOT_FOUND, "静态资源不存在。")
            return
        content_type = "text/css; charset=utf-8" if safe_name.endswith(".css") else "application/javascript; charset=utf-8"
        encoded = asset_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message, "status": int(status)}, status=status)


def make_server(host: str, port: int, db_path: str) -> ThreadingHTTPServer:
    service = BookRecallWebService(db_path)

    class BoundHandler(BookRecallHandler):
        pass

    BoundHandler.service = service
    return ThreadingHTTPServer((host, port), BoundHandler)


def run_server(host: str, port: int, db_path: str) -> None:
    server = make_server(host, port, db_path)
    try:
        print(f"BookRecall Web 已启动：http://{host}:{server.server_port}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBookRecall Web 已停止。")
    finally:
        server.server_close()


def _asset_root() -> Path:
    return Path(__file__).with_name("web_assets")


def _build_index_html() -> str:
    return (_asset_root() / "index.html").read_text(encoding="utf-8")
