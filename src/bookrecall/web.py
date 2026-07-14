from __future__ import annotations

import json
import mimetypes
import os
import threading
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.parse import parse_qs, urlparse
from typing import Callable

from .agent import BookRecallAgent
from .agent.policies.base import DecisionPolicy
from .agent.policies.langgraph import LangGraphPolicy, LangGraphUnavailableError, is_langgraph_available
from .agent.policies.llm_react import LLMReActPolicy
from .agent.policies.local_planner import LocalPlannerPolicy
from .agent.policies.rule_based import RuleBasedPolicy
from .agent.state import AgentState
from .agent.tools import build_default_registry
from .chapter_summary import build_chapter_summaries_with_llm
from .chunking import build_chunk_hierarchy
from .cloud import OpenAICompatibleReasoner
from .config import DEFAULT_CHUNK_SETTINGS, DEFAULT_EMBEDDING_SETTINGS, DEFAULT_RERANK_SETTINGS, DEFAULT_SEARCH_SETTINGS, SearchSettings
from .embeddings import (
    CrossEncoderReranker,
    EmbeddingRetriever,
    LocalModelError,
    RerankingRetriever,
    SentenceTransformerEmbedder,
    build_embedding_index,
    configure_local_model_cache,
    delete_vector_index,
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
from .local_llm import LocalChatClient, LocalLLMError, LocalLLMSettings
from .parser import parse_chapters
from .retrieval import LocalRetriever, Retriever, invalidate_local_retriever_cache
from .smart_index import build_smart_relation_event_records, discover_entities_with_llm
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


def _make_smart_index_client(config: dict[str, object] | None) -> LocalChatClient | None:
    if not config or not bool(config.get("enabled")):
        return None
    model_path = str(config.get("model_path", "") or "").strip()
    model = str(config.get("model", "") or "").strip()
    endpoint = str(config.get("endpoint", "") or "").strip()
    if not model_path and not endpoint:
        raise ValueError("已开启智能索引，但没有填写 Qwen3 GGUF 模型路径或本地服务 endpoint。")
    return LocalChatClient(
        LocalLLMSettings(
            model=model,
            model_path=model_path,
            endpoint=endpoint,
            n_ctx=int(config.get("n_ctx") or 4096),
            max_tokens=int(config.get("max_tokens") or 2048),
        )
    )


def _smart_index_profile(config: dict[str, object] | None) -> dict[str, object]:
    raw_profile = str((config or {}).get("profile") or "balanced").strip().lower()
    if raw_profile not in {"fast", "balanced", "deep"}:
        raw_profile = "balanced"
    defaults = {
        "fast": {"batch_chapters": 6, "summary_stride": 0, "relation_stride": 4, "label": "快速"},
        "balanced": {"batch_chapters": 4, "summary_stride": 4, "relation_stride": 2, "label": "均衡"},
        "deep": {"batch_chapters": 2, "summary_stride": 1, "relation_stride": 1, "label": "深度"},
    }[raw_profile]
    batch_chapters = int((config or {}).get("batch_chapters") or defaults["batch_chapters"])
    summary_stride = int((config or {}).get("summary_stride") or defaults["summary_stride"])
    relation_stride = int((config or {}).get("relation_stride") or defaults["relation_stride"])
    return {
        "profile": raw_profile,
        "label": defaults["label"],
        "batch_chapters": max(1, min(6, batch_chapters)),
        "summary_stride": max(0, summary_stride),
        "relation_stride": max(1, relation_stride),
    }


def _search_settings_for_rerank(config: dict[str, object] | None) -> SearchSettings:
    candidate_count = int((config or {}).get("candidates") or DEFAULT_RERANK_SETTINGS.candidate_count)
    candidate_count = max(DEFAULT_SEARCH_SETTINGS.top_k_parents, min(100, candidate_count))
    return SearchSettings(
        top_k_children=DEFAULT_SEARCH_SETTINGS.top_k_children,
        top_k_parents=candidate_count,
    )


class IndexJobManager:
    def __init__(self, service: "BookRecallWebService") -> None:
        self.service = service
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, object]] = {}

    def start(self, label: str, worker: Callable[[Callable[[dict[str, object]], None]], dict[str, object]]) -> dict[str, object]:
        job_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        job = {
            "job_id": job_id,
            "label": label,
            "status": "running",
            "stage": "排队中",
            "message": "任务已创建，等待后台线程启动。",
            "percent": 0,
            "current": 0,
            "total": 0,
            "result": None,
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(target=self._run, args=(job_id, worker), daemon=True)
        thread.start()
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"没有找到任务：{job_id}")
            return dict(job)

    def _run(self, job_id: str, worker: Callable[[Callable[[dict[str, object]], None]], dict[str, object]]) -> None:
        def update(patch: dict[str, object]) -> None:
            with self._lock:
                job = self._jobs[job_id]
                job.update(patch)
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")

        try:
            update({"stage": "启动", "message": "后台索引任务已开始。", "percent": 1})
            result = worker(update)
            update(
                {
                    "status": "succeeded",
                    "stage": "完成",
                    "message": "索引构建完成。",
                    "percent": 100,
                    "result": result,
                }
            )
        except Exception as exc:  # noqa: BLE001 - background job must report any failure
            update(
                {
                    "status": "failed",
                    "stage": "失败",
                    "message": str(exc),
                    "error": str(exc),
                    "percent": 100,
                }
            )


class BookRecallWebService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.jobs = IndexJobManager(self)

    def _get_local_retriever(self, store: BookRecallStore, book_id: str, settings: SearchSettings) -> LocalRetriever:
        return LocalRetriever(store, settings)

    def _invalidate_retriever_cache(self, book_id: str) -> None:
        invalidate_local_retriever_cache(self.db_path, book_id)

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
                    "book_group": item.book_group,
                    "tags": item.tags,
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
        smart_index: dict[str, object] | None = None,
        vector_index: dict[str, object] | None = None,
        overwrite: bool = False,
        source_path: str = "web://imported-text",
        progress_callback: Callable[[dict[str, object]], None] | None = None,
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
            if progress_callback:
                progress_callback(
                    {
                        "stage": "章节解析",
                        "message": f"已解析 {len(chapters)} 个章节。",
                        "percent": 5,
                        "current": len(chapters),
                        "total": len(chapters),
                    }
                )
            index_payload = self._build_index_payload(
                book_id=book_id,
                chapters=chapters,
                text=text,
                entity_lexicon=entity_lexicon,
                theme_lexicon=theme_lexicon,
                smart_index=smart_index,
                progress_callback=progress_callback,
            )
            if progress_callback:
                progress_callback({"stage": "写入数据库", "message": "正在替换本地 SQLite 索引。", "percent": 94})
            store.replace_book(
                book_id=book_id,
                title=title or book_id,
                source_path=source_path,
                chapters=chapters,
                parent_chunks=index_payload["parent_chunks"],
                child_chunks=index_payload["child_chunks"],
                entity_records=index_payload["entity_records"],
                relation_records=index_payload["relation_records"],
                theme_records=index_payload["theme_records"],
                event_records=index_payload["event_records"],
                chapter_summaries=index_payload["chapter_summaries"],
            )
            self._invalidate_retriever_cache(book_id)
            vector_result: dict[str, object] | None = None
            if isinstance(vector_index, dict) and bool(vector_index.get("enabled")):
                if progress_callback:
                    progress_callback(
                        {
                            "stage": "向量索引",
                            "message": "正在加载本地 embedding 模型并构建 Two-Phase 召回索引。",
                            "percent": 96,
                            "current": 0,
                            "total": len(index_payload["child_chunks"]),
                        }
                    )
                vector_result = self._try_build_vector_index_with_store(
                    store,
                    book_id,
                    vector_index,
                    progress_callback=progress_callback,
                )
            return {
                "book_id": book_id,
                "title": title or book_id,
                "chapter_count": len(chapters),
                "parent_chunks": len(index_payload["parent_chunks"]),
                "child_chunks": len(index_payload["child_chunks"]),
                "entities": len(index_payload["entity_records"]),
                "relations": len(index_payload["relation_records"]),
                "themes": len(index_payload["theme_records"]),
                "events": len(index_payload["event_records"]),
                "overwritten": overwrite,
                "vector_index": vector_result,
            }
        finally:
            store.close()

    def rebuild_book_index(
        self,
        *,
        book_id: str,
        entity_lexicon: str = "",
        theme_lexicon: str = "",
        smart_index: dict[str, object] | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            info = store.get_book(book_id)
            if info is None:
                raise ValueError(f"没有找到 book_id={book_id}。")
            chapters = store.list_chapter_records(book_id)
            if not chapters:
                raise ValueError("这本书没有可重建的章节正文。")
            if progress_callback:
                progress_callback(
                    {
                        "stage": "读取章节",
                        "message": f"已载入 {len(chapters)} 个章节正文。",
                        "percent": 5,
                        "current": len(chapters),
                        "total": len(chapters),
                    }
                )
            text = "\n\n".join(chapter.content for chapter in chapters)
            index_payload = self._build_index_payload(
                book_id=book_id,
                chapters=chapters,
                text=text,
                entity_lexicon=entity_lexicon,
                theme_lexicon=theme_lexicon,
                smart_index=smart_index,
                progress_callback=progress_callback,
            )
            if progress_callback:
                progress_callback({"stage": "写入数据库", "message": "正在替换本地 SQLite 索引。", "percent": 94})
            store.replace_book(
                book_id=book_id,
                title=info.title,
                source_path=info.source_path,
                chapters=chapters,
                parent_chunks=index_payload["parent_chunks"],
                child_chunks=index_payload["child_chunks"],
                entity_records=index_payload["entity_records"],
                relation_records=index_payload["relation_records"],
                theme_records=index_payload["theme_records"],
                event_records=index_payload["event_records"],
                chapter_summaries=index_payload["chapter_summaries"],
            )
            self._invalidate_retriever_cache(book_id)
            return {
                "book_id": book_id,
                "title": info.title,
                "chapter_count": len(chapters),
                "parent_chunks": len(index_payload["parent_chunks"]),
                "child_chunks": len(index_payload["child_chunks"]),
                "entities": len(index_payload["entity_records"]),
                "relations": len(index_payload["relation_records"]),
                "themes": len(index_payload["theme_records"]),
                "events": len(index_payload["event_records"]),
            }
        finally:
            store.close()

    def start_build_book_job(self, **kwargs) -> dict[str, object]:
        return self.jobs.start("导入并构建索引", lambda progress: self.build_book(progress_callback=progress, **kwargs))

    def start_rebuild_book_job(self, **kwargs) -> dict[str, object]:
        return self.jobs.start("重建结构化索引", lambda progress: self.rebuild_book_index(progress_callback=progress, **kwargs))

    def get_index_job(self, job_id: str) -> dict[str, object]:
        return self.jobs.get(job_id)

    def delete_book(self, book_id: str) -> dict[str, object]:
        store = self._open_store()
        try:
            if store.get_book(book_id) is None:
                raise ValueError(f"没有找到 book_id={book_id}。")
            chunk_count = store.delete_book(book_id)
        finally:
            store.close()
        self._invalidate_retriever_cache(book_id)
        vector_delete = delete_vector_index(default_vector_dir(self.db_path), book_id)
        return {
            "book_id": book_id,
            "deleted_chunks": chunk_count,
            "vector_index": vector_delete,
        }

    def delete_vector_index(self, book_id: str) -> dict[str, object]:
        return delete_vector_index(default_vector_dir(self.db_path), book_id)

    def _try_build_vector_index_with_store(
        self,
        store: BookRecallStore,
        book_id: str,
        config: dict[str, object],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        try:
            configure_local_model_cache(default_cache_root(self.db_path))
            model_name = str(config.get("model") or DEFAULT_EMBEDDING_SETTINGS.model_name)
            backend = str(config.get("backend") or "auto")
            limit_raw = config.get("limit_chunks")
            limit_chunks = int(limit_raw) if limit_raw not in (None, "") else None

            def vector_progress(current: int, total: int) -> None:
                if progress_callback is None:
                    return
                percent = 96 + int((min(current, total) / max(total, 1)) * 3)
                progress_callback(
                    {
                        "stage": "向量索引",
                        "message": f"正在编码 embedding chunk：{current} / {total}",
                        "percent": min(99, percent),
                        "current": current,
                        "total": total,
                    }
                )

            embedder = SentenceTransformerEmbedder(
                model_name,
                cache_dir=default_sentence_transformers_cache_dir(self.db_path),
            )
            info = build_embedding_index(
                store=store,
                book_id=book_id,
                index_dir=default_vector_dir(self.db_path),
                embedder=embedder,
                batch_size=DEFAULT_EMBEDDING_SETTINGS.batch_size,
                limit_chunks=limit_chunks,
                prefer_backend=backend,
                progress_callback=vector_progress,
            )
            return {
                "ok": True,
                "model_name": info.model_name,
                "backend": info.backend,
                "chunk_count": info.chunk_count,
                "dimension": info.dimension,
                "path": info.path,
            }
        except Exception as exc:  # noqa: BLE001 - vector pre-index should not break import
            return {"ok": False, "error": str(exc)}

    def _build_index_payload(
        self,
        *,
        book_id: str,
        chapters,
        text: str,
        entity_lexicon: str = "",
        theme_lexicon: str = "",
        smart_index: dict[str, object] | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        if progress_callback:
            progress_callback({"stage": "切分文本", "message": "正在构建 parent/child chunks。", "percent": 8})
        parent_chunks, child_chunks = build_chunk_hierarchy(book_id, chapters, DEFAULT_CHUNK_SETTINGS)
        smart_client = _make_smart_index_client(smart_index)
        smart_profile = _smart_index_profile(smart_index)
        max_chapters = int((smart_index or {}).get("max_chapters") or 0)
        batch_chapters = int(smart_profile["batch_chapters"])
        summary_stride = int(smart_profile["summary_stride"])
        relation_stride = int(smart_profile["relation_stride"])
        if progress_callback:
            progress_callback(
                {
                    "stage": "实体候选",
                    "message": "正在生成本地实体候选。",
                    "percent": 12,
                    "current": 0,
                    "total": len(chapters),
                }
            )
        entity_names = _parse_inline_lexicon(entity_lexicon)
        if not entity_names:
            entity_names = auto_discover_entities(text)
        if smart_client is not None:
            entity_total = min(max_chapters or len(chapters), len(chapters))

            def entity_progress(stage: str, current: int, total: int, chapter) -> None:
                if progress_callback:
                    span_total = total or entity_total or 1
                    percent = 15 + int(35 * (current / span_total))
                    progress_callback(
                        {
                            "stage": stage,
                            "message": f"{smart_profile['label']}模式 · 第 {chapter.number} 章《{chapter.title}》",
                            "percent": min(50, percent),
                            "current": current,
                            "total": span_total,
                        }
                    )

            entity_names = discover_entities_with_llm(
                chapters,
                smart_client,
                seed_entities=entity_names,
                max_chapters=max_chapters,
                batch_chapters=batch_chapters,
                progress_callback=entity_progress,
            )
        if progress_callback:
            progress_callback({"stage": "实体落库准备", "message": "正在扫描实体出现位置。", "percent": 52})
        entity_records = build_entity_records(chapters, entity_names, DEFAULT_CHUNK_SETTINGS)
        if progress_callback:
            progress_callback({"stage": "主题索引", "message": "正在构建主题线索索引。", "percent": 58})
        theme_names = auto_discover_themes(text, extra_terms=_parse_inline_lexicon(theme_lexicon))
        theme_records = build_theme_records(chapters, theme_names, DEFAULT_CHUNK_SETTINGS)
        chapter_summaries: dict[int, str] = {}
        if smart_client is not None and summary_stride > 0:
            summary_total = max(1, (min(max_chapters or len(chapters), len(chapters)) + summary_stride - 1) // summary_stride)

            def summary_progress(stage: str, current: int, total: int, chapter) -> None:
                if progress_callback:
                    span_total = total or summary_total or 1
                    percent = 60 + int(10 * (current / span_total))
                    progress_callback(
                        {
                            "stage": stage,
                            "message": f"{smart_profile['label']}模式 · 第 {chapter.number} 章《{chapter.title}》",
                            "percent": min(70, percent),
                            "current": current,
                            "total": span_total,
                        }
                    )

            chapter_summaries = build_chapter_summaries_with_llm(
                chapters,
                smart_client,
                max_chapters=max_chapters,
                stage_size=10,
                chapter_stride=summary_stride,
                progress_callback=summary_progress,
            )
        elif smart_client is not None and progress_callback:
            progress_callback(
                {
                    "stage": "智能章节摘要",
                    "message": f"{smart_profile['label']}模式已跳过逐章摘要，以优先提升导入速度。",
                    "percent": 70,
                }
            )
        if smart_client is not None:
            relation_total = max(1, (min(max_chapters or len(chapters), len(chapters)) + relation_stride - 1) // relation_stride)

            def relation_progress(stage: str, current: int, total: int, chapter) -> None:
                if progress_callback:
                    span_total = total or relation_total or 1
                    percent = 72 + int(18 * (current / span_total))
                    progress_callback(
                        {
                            "stage": stage,
                            "message": f"{smart_profile['label']}模式 · 第 {chapter.number} 章《{chapter.title}》",
                            "percent": min(90, percent),
                            "current": current,
                            "total": span_total,
                        }
                    )

            relation_records, event_records = build_smart_relation_event_records(
                chapters,
                entity_records,
                DEFAULT_CHUNK_SETTINGS,
                smart_client,
                max_chapters=max_chapters,
                chapter_stride=relation_stride,
                progress_callback=relation_progress,
            )
            if not relation_records:
                relation_records = build_relation_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
            if not event_records:
                event_records = build_event_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
        else:
            if progress_callback:
                progress_callback({"stage": "关系/事件索引", "message": "正在构建本地规则关系和事件索引。", "percent": 72})
            relation_records = build_relation_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
            event_records = build_event_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
        return {
            "parent_chunks": parent_chunks,
            "child_chunks": child_chunks,
            "entity_records": entity_records,
            "relation_records": relation_records,
            "theme_records": theme_records,
            "event_records": event_records,
            "chapter_summaries": chapter_summaries,
        }

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
            "agent_policies": [
                {"id": "auto", "name": "自动选择", "ready": True},
                {"id": "rule_based", "name": "本地规则 ReAct", "ready": True},
                {"id": "local_planner", "name": "本地 Qwen Planner", "ready": True},
                {"id": "llm_react", "name": "云端 LLM ReAct", "ready": True},
                {"id": "langgraph", "name": "LangGraph ReAct", "ready": is_langgraph_available()},
            ],
        }

    def diagnostics(self) -> dict[str, object]:
        store = self._open_store()
        try:
            books = store.list_books()
            total_stats = {
                "books": len(books),
                "chapters": sum(item.chapter_count for item in books),
                "entities": sum(item.entity_count for item in books),
            }
        finally:
            store.close()

        dist_index = _frontend_dist_root() / "index.html"
        legacy_index = _asset_root() / "index.html"
        return {
            "ok": True,
            "database": {
                "path": self.db_path,
                "exists": Path(self.db_path).exists(),
            },
            "frontend": {
                "mode": "vue_dist" if dist_index.exists() else "legacy_static",
                "dist_index": str(dist_index),
                "dist_built": dist_index.exists(),
                "legacy_index": str(legacy_index),
                "legacy_available": legacy_index.exists(),
            },
            "storage": {
                "vector_dir": str(default_vector_dir(self.db_path)),
                "model_cache_dir": str(default_sentence_transformers_cache_dir(self.db_path)),
            },
            "dependencies": dependency_report(),
            "stats": total_stats,
            "thread": threading.current_thread().name,
        }

    def list_agent_tools(self) -> dict[str, object]:
        store = self._open_store()
        try:
            registry = build_default_registry(store, LocalRetriever(store, DEFAULT_SEARCH_SETTINGS))
            tools = registry.describe_for_llm()
        finally:
            store.close()
        return {
            "tools": tools,
            "count": len(tools),
        }

    def run_agent_tool(
        self,
        *,
        book_id: str,
        user_id: str,
        session_id: str | None,
        tool_name: str,
        arguments: dict[str, object],
        question: str = "",
        progress_chapter: int | None = None,
        retriever_mode: str = "lexical",
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            if store.get_book(book_id) is None:
                raise ValueError(f"没有找到 book_id={book_id}。")
            effective_progress = progress_chapter
            if effective_progress is None:
                effective_progress = store.get_progress(book_id, user_id)
            if effective_progress is None:
                effective_progress = store.get_max_chapter(book_id)
            retriever = self._make_retriever(store, book_id, retriever_mode)
            registry = build_default_registry(store, retriever)
            tool = registry.get(tool_name)
            if tool is None:
                raise ValueError(f"未知工具：{tool_name}")
            safe_args = dict(arguments)
            if tool.schema.parameters.get("max_chapter"):
                given = safe_args.get("max_chapter")
                if given not in (None, ""):
                    safe_args["max_chapter"] = min(int(given), int(effective_progress or 0))
                else:
                    safe_args["max_chapter"] = int(effective_progress or 0)
            matched_entities = store.match_entity_candidates(book_id, question) if question else []
            matched_themes = store.match_theme_candidates(book_id, question) if question else []
            state = AgentState(
                book_id=book_id,
                question=question or f"调试工具 {tool_name}",
                user_id=user_id,
                session_id=session_id,
                progress_chapter=int(effective_progress or 0),
                matched_entities=matched_entities,
                matched_themes=matched_themes,
                primary_entity=matched_entities[0] if matched_entities else None,
                recent_turns=store.list_agent_turns(book_id, user_id, session_id, limit=4) if session_id else [],
                user_preferences=store.get_user_preferences(book_id, user_id),
            )
            started = perf_counter()
            result = tool.run(state, safe_args)
            elapsed_ms = round((perf_counter() - started) * 1000, 2)
            return {
                "book_id": book_id,
                "user_id": user_id,
                "session_id": session_id,
                "tool_name": tool_name,
                "arguments": safe_args,
                "progress_chapter": int(effective_progress or 0),
                "retriever": retriever_mode,
                "elapsed_ms": elapsed_ms,
                "status": "blocked" if result.get("spoiler_blocked") else "ok",
                "result": result,
            }
        finally:
            store.close()

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

    def update_book_metadata(self, book_id: str, book_group: str = "", tags: list[str] | None = None) -> dict[str, object]:
        store = self._open_store()
        try:
            info = store.update_book_metadata(book_id, book_group=book_group, tags=tags)
            if info is None:
                raise ValueError(f"没有找到 book_id={book_id}。")
            return {
                "book_id": info.book_id,
                "title": info.title,
                "source_path": info.source_path,
                "chapter_count": info.chapter_count,
                "entity_count": info.entity_count,
                "book_group": info.book_group,
                "tags": info.tags,
            }
        finally:
            store.close()

    def get_user_preferences(self, book_id: str, user_id: str) -> dict[str, object]:
        store = self._open_store()
        try:
            return store.get_user_preferences(book_id, user_id)
        finally:
            store.close()

    def set_user_preferences(
        self,
        *,
        book_id: str,
        user_id: str,
        answer_style: str = "",
        focus: str = "",
        custom_prompt: str = "",
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            return store.set_user_preferences(
                book_id=book_id,
                user_id=user_id,
                answer_style=answer_style,
                focus=focus,
                custom_prompt=custom_prompt,
            )
        finally:
            store.close()

    def get_chapter(self, book_id: str, chapter_number: int) -> dict[str, object]:
        store = self._open_store()
        try:
            row = store.get_chapter(book_id, chapter_number)
            if row is None:
                raise ValueError(f"没有找到第 {chapter_number} 章。")
            return {
                "book_id": book_id,
                "chapter_number": int(row["chapter_number"]),
                "title": row["title"],
                "content": row["content"],
                "start_offset": int(row["start_offset"]),
                "end_offset": int(row["end_offset"]),
            }
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

    def list_sessions(self, book_id: str, user_id: str, *, limit: int = 30) -> dict[str, object]:
        store = self._open_store()
        try:
            sessions = store.list_agent_sessions(book_id, user_id, limit=limit)
        finally:
            store.close()
        return {
            "book_id": book_id,
            "user_id": user_id,
            "sessions": sessions,
        }

    def get_session_digest(
        self,
        book_id: str,
        user_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            turns = store.list_agent_turns(book_id, user_id, session_id, limit=limit)
        finally:
            store.close()
        entities = _collect_turn_entities(turns)
        tools = _collect_trace_tools(turns)
        intents = sorted({str(turn.get("intent") or "") for turn in turns if turn.get("intent")})
        progress_values = [int(turn["progress_chapter"]) for turn in turns if turn.get("progress_chapter")]
        first_turn = turns[0] if turns else None
        last_turn = turns[-1] if turns else None
        question_samples = [str(turn.get("question") or "") for turn in turns[-5:] if turn.get("question")]
        latest_summary = str(last_turn.get("summary") or "") if last_turn else ""
        synopsis_parts = []
        if turns:
            synopsis_parts.append(f"该会话共有 {len(turns)} 轮。")
        if entities:
            synopsis_parts.append(f"主要实体：{'、'.join(entities[:8])}。")
        if intents:
            synopsis_parts.append(f"覆盖意图：{'、'.join(intents[:6])}。")
        if latest_summary:
            synopsis_parts.append(f"最近摘要：{latest_summary}")
        return {
            "book_id": book_id,
            "user_id": user_id,
            "session_id": session_id,
            "turn_count": len(turns),
            "first_question": str(first_turn.get("question") or "") if first_turn else "",
            "last_question": str(last_turn.get("question") or "") if last_turn else "",
            "latest_summary": latest_summary,
            "progress_min": min(progress_values) if progress_values else None,
            "progress_max": max(progress_values) if progress_values else None,
            "entities": entities,
            "tools": tools,
            "intents": intents,
            "recent_questions": question_samples,
            "synopsis": "".join(synopsis_parts) if synopsis_parts else "当前会话还没有可摘要的记忆。",
        }

    def delete_session(self, *, book_id: str, user_id: str, session_id: str) -> dict[str, object]:
        if not session_id:
            raise ValueError("session_id 不能为空。")
        store = self._open_store()
        try:
            deleted = store.delete_agent_session(book_id=book_id, user_id=user_id, session_id=session_id)
        finally:
            store.close()
        return {
            "book_id": book_id,
            "user_id": user_id,
            "session_id": session_id,
            "deleted_turns": deleted,
        }

    def compare_sessions(
        self,
        *,
        book_id: str,
        user_id: str,
        left_session_id: str,
        right_session_id: str,
        limit: int = 100,
    ) -> dict[str, object]:
        if not left_session_id or not right_session_id:
            raise ValueError("请选择两个要对比的会话。")
        if left_session_id == right_session_id:
            raise ValueError("请选择两个不同的会话进行对比。")
        store = self._open_store()
        try:
            left_turns = store.list_agent_turns(book_id, user_id, left_session_id, limit=limit)
            right_turns = store.list_agent_turns(book_id, user_id, right_session_id, limit=limit)
        finally:
            store.close()
        if not left_turns:
            raise ValueError(f"会话 {left_session_id} 没有可对比的历史。")
        if not right_turns:
            raise ValueError(f"会话 {right_session_id} 没有可对比的历史。")

        common_prefix = _common_turn_prefix(left_turns, right_turns)
        left_unique = left_turns[common_prefix:]
        right_unique = right_turns[common_prefix:]
        left_entities = _collect_turn_entities(left_turns)
        right_entities = _collect_turn_entities(right_turns)
        left_tools = _collect_trace_tools(left_turns)
        right_tools = _collect_trace_tools(right_turns)
        entity_delta = _build_delta(left_entities, right_entities)
        tool_delta = _build_delta(left_tools, right_tools)
        turn_diffs = _build_turn_diffs(left_unique, right_unique)
        diff_insights = _build_session_diff_insights(
            common_prefix=common_prefix,
            left_session_id=left_session_id,
            right_session_id=right_session_id,
            left_unique=left_unique,
            right_unique=right_unique,
            entity_delta=entity_delta,
            tool_delta=tool_delta,
        )
        return {
            "book_id": book_id,
            "user_id": user_id,
            "left_session_id": left_session_id,
            "right_session_id": right_session_id,
            "common_prefix_turns": common_prefix,
            "divergence_turn": common_prefix + 1,
            "left_turn_count": len(left_turns),
            "right_turn_count": len(right_turns),
            "left_unique_turns": left_unique,
            "right_unique_turns": right_unique,
            "left_entities": left_entities,
            "right_entities": right_entities,
            "shared_entities": entity_delta["shared"],
            "entity_delta": entity_delta,
            "left_tools": left_tools,
            "right_tools": right_tools,
            "shared_tools": tool_delta["shared"],
            "tool_delta": tool_delta,
            "turn_diffs": turn_diffs,
            "diff_insights": diff_insights,
            "summary": (
                f"两个会话共有 {common_prefix} 轮相同前缀；"
                f"{left_session_id} 独有 {len(left_unique)} 轮，"
                f"{right_session_id} 独有 {len(right_unique)} 轮。"
            ),
        }

    def merge_sessions(
        self,
        *,
        book_id: str,
        user_id: str,
        left_session_id: str,
        right_session_id: str,
        target_session_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        if not left_session_id or not right_session_id:
            raise ValueError("请选择两个要合并的会话。")
        if left_session_id == right_session_id:
            raise ValueError("请选择两个不同的会话进行合并。")
        target_session = target_session_id or _merge_session_id(left_session_id, right_session_id)
        if target_session in {left_session_id, right_session_id}:
            raise ValueError("合并目标会话不能覆盖原分支。")

        store = self._open_store()
        try:
            left_turns = store.list_agent_turns(book_id, user_id, left_session_id, limit=limit)
            right_turns = store.list_agent_turns(book_id, user_id, right_session_id, limit=limit)
            if not left_turns:
                raise ValueError(f"会话 {left_session_id} 没有可合并的历史。")
            if not right_turns:
                raise ValueError(f"会话 {right_session_id} 没有可合并的历史。")
            existing_target_turns = store.list_agent_turns(book_id, user_id, target_session, limit=1)
            if existing_target_turns:
                raise ValueError(f"目标会话 {target_session} 已存在，请换一个新的会话 ID。")

            common_prefix = _common_turn_prefix(left_turns, right_turns)
            common_turns = left_turns[:common_prefix]
            left_unique = left_turns[common_prefix:]
            right_unique = right_turns[common_prefix:]
            copied = store.copy_agent_turns_to_session(
                book_id=book_id,
                user_id=user_id,
                source_turns=[*common_turns, *left_unique, *right_unique],
                target_session_id=target_session,
            )
        finally:
            store.close()

        session = self.get_session_history(book_id, user_id, target_session, limit=limit)
        return {
            "book_id": book_id,
            "user_id": user_id,
            "left_session_id": left_session_id,
            "right_session_id": right_session_id,
            "target_session_id": target_session,
            "common_prefix_turns": common_prefix,
            "left_unique_turns": len(left_unique),
            "right_unique_turns": len(right_unique),
            "copied_turns": copied,
            "session": session,
            "summary": (
                f"已创建合并会话 {target_session}：保留共同前缀 {common_prefix} 轮，"
                f"追加左分支 {len(left_unique)} 轮、右分支 {len(right_unique)} 轮。"
            ),
        }

    def update_session_turn(
        self,
        *,
        book_id: str,
        user_id: str,
        session_id: str,
        turn_id: int,
        question: str,
        answer: str,
        summary: str | None = None,
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            turn = store.update_agent_turn(
                turn_id=turn_id,
                book_id=book_id,
                user_id=user_id,
                session_id=session_id,
                question=question,
                answer=answer,
                summary=summary,
            )
            if turn is None:
                raise ValueError("没有找到要修改的对话轮次。")
            return {
                "book_id": book_id,
                "user_id": user_id,
                "session_id": session_id,
                "turn": turn,
            }
        finally:
            store.close()

    def search_evidence(
        self,
        *,
        book_id: str,
        query: str,
        retriever_mode: str = "auto",
        rerank_config: dict[str, object] | None = None,
        progress_chapter: int | None = None,
        limit: int = 8,
    ) -> dict[str, object]:
        if not query.strip():
            raise ValueError("query 不能为空。")
        store = self._open_store()
        try:
            if store.get_book(book_id) is None:
                raise ValueError(f"没有找到 book_id={book_id}。")
            retriever = self._make_retriever(store, book_id, retriever_mode, rerank_config=rerank_config)
            hits = retriever.search(book_id, query, max_chapter=progress_chapter)[:limit]
            return {
                "book_id": book_id,
                "query": query,
                "retriever": retriever_mode,
                "effective_retriever": type(retriever).__name__,
                "progress_chapter": progress_chapter,
                "hits": [
                    {
                        "score": hit.score,
                        "chapter_number": hit.chapter_number,
                        "chapter_title": hit.chapter_title,
                        "parent_id": hit.parent_id,
                        "child_text": hit.child_text,
                        "parent_text": hit.parent_text,
                    }
                    for hit in hits
                ],
            }
        finally:
            store.close()

    def delete_session_turn(self, *, book_id: str, user_id: str, session_id: str, turn_id: int) -> dict[str, object]:
        store = self._open_store()
        try:
            deleted = store.delete_agent_turn(
                turn_id=turn_id,
                book_id=book_id,
                user_id=user_id,
                session_id=session_id,
            )
            if deleted <= 0:
                raise ValueError("没有找到要删除的对话轮次。")
            return {
                "book_id": book_id,
                "user_id": user_id,
                "session_id": session_id,
                "deleted": deleted,
            }
        finally:
            store.close()

    def rerun_session_from_turn(
        self,
        *,
        book_id: str,
        user_id: str,
        session_id: str,
        turn_id: int,
        question: str | None = None,
        progress_chapter: int | None = None,
        retriever_mode: str = "lexical",
        agent_policy: str = "auto",
        cloud_config: dict[str, object] | None = None,
        local_llm_config: dict[str, object] | None = None,
        rerank_config: dict[str, object] | None = None,
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            turn = store.get_agent_turn(
                turn_id=turn_id,
                book_id=book_id,
                user_id=user_id,
                session_id=session_id,
            )
            if turn is None:
                raise ValueError("没有找到要重算的对话轮次。")
            deleted = store.delete_agent_turns_from(
                turn_id=turn_id,
                book_id=book_id,
                user_id=user_id,
                session_id=session_id,
            )
        finally:
            store.close()
        answer = self.ask(
            book_id=book_id,
            question=(question or str(turn["question"])).strip(),
            user_id=user_id,
            session_id=session_id,
            progress_chapter=progress_chapter,
            retriever_mode=retriever_mode,
            agent_policy=agent_policy,
            cloud_config=cloud_config,
            local_llm_config=local_llm_config,
            rerank_config=rerank_config,
        )
        answer["rerun"] = {
            "from_turn_id": turn_id,
            "from_turn_index": turn["turn_index"],
            "deleted_turns": deleted,
            "question": question or str(turn["question"]),
        }
        return answer

    def branch_session_from_turn(
        self,
        *,
        book_id: str,
        user_id: str,
        session_id: str,
        turn_id: int,
        question: str | None = None,
        target_session_id: str | None = None,
        progress_chapter: int | None = None,
        retriever_mode: str = "lexical",
        agent_policy: str = "auto",
        cloud_config: dict[str, object] | None = None,
        local_llm_config: dict[str, object] | None = None,
        rerank_config: dict[str, object] | None = None,
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            turn = store.get_agent_turn(
                turn_id=turn_id,
                book_id=book_id,
                user_id=user_id,
                session_id=session_id,
            )
            if turn is None:
                raise ValueError("没有找到要创建分支的对话轮次。")
            prefix_turns = store.list_agent_turns_before(
                turn_id=turn_id,
                book_id=book_id,
                user_id=user_id,
                session_id=session_id,
            )
            target_session = target_session_id or _branch_session_id(session_id, int(turn["turn_index"]))
            copied = store.copy_agent_turns_to_session(
                book_id=book_id,
                user_id=user_id,
                source_turns=prefix_turns,
                target_session_id=target_session,
            )
        finally:
            store.close()
        answer = self.ask(
            book_id=book_id,
            question=(question or str(turn["question"])).strip(),
            user_id=user_id,
            session_id=target_session,
            progress_chapter=progress_chapter,
            retriever_mode=retriever_mode,
            agent_policy=agent_policy,
            cloud_config=cloud_config,
            local_llm_config=local_llm_config,
            rerank_config=rerank_config,
        )
        answer["branch"] = {
            "source_session_id": session_id,
            "target_session_id": target_session,
            "from_turn_id": turn_id,
            "from_turn_index": turn["turn_index"],
            "copied_turns": copied,
            "question": question or str(turn["question"]),
        }
        return answer

    def ask(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
        session_id: str | None = None,
        progress_chapter: int | None = None,
        retriever_mode: str = "lexical",
        agent_policy: str = "auto",
        cloud_config: dict[str, object] | None = None,
        local_llm_config: dict[str, object] | None = None,
        rerank_config: dict[str, object] | None = None,
        force_exact_search: bool = False,
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            retriever = self._make_retriever(store, book_id, retriever_mode, rerank_config=rerank_config)
            reasoner = self._make_reasoner(cloud_config)
            query_understanding_client = _make_smart_index_client(local_llm_config)
            policy = self._make_policy(agent_policy, reasoner, query_understanding_client)
            agent = BookRecallAgent(
                store,
                policy=policy,
                retriever=retriever,
                reasoner=reasoner,
                query_understanding_client=query_understanding_client,
                force_exact_search=force_exact_search,
            )
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
                "agent_policy": agent_policy,
                "effective_policy": self._effective_policy_name(agent_policy, reasoner, query_understanding_client),
                "cloud_reasoner_enabled": reasoner.enabled,
                "cloud_model": reasoner.model if reasoner.enabled else None,
                "local_query_understanding_enabled": query_understanding_client is not None,
                "force_exact_search": force_exact_search,
            }
            if session_id:
                session_data = {
                    "book_id": book_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "turns": store.list_agent_turns(book_id, user_id, session_id, limit=50),
                }
                payload["session"] = session_data
                payload["trace"] = session_data["turns"][-1]["trace"] if session_data["turns"] else []
            else:
                payload["session"] = None
                payload["trace"] = []
            return payload
        finally:
            store.close()

    def _make_retriever(
        self,
        store: BookRecallStore,
        book_id: str,
        mode: str,
        *,
        rerank_config: dict[str, object] | None = None,
    ) -> Retriever:
        if mode not in {"lexical", "embedding", "auto"}:
            raise LocalModelError("未知检索器，请选择 lexical、embedding 或 auto。")
        rerank_enabled = bool((rerank_config or {}).get("enabled"))
        search_settings = _search_settings_for_rerank(rerank_config) if rerank_enabled else DEFAULT_SEARCH_SETTINGS
        if mode == "lexical":
            base: Retriever = self._get_local_retriever(store, book_id, search_settings)
            return self._wrap_reranker(base, rerank_config)

        vector_dir = default_vector_dir(self.db_path)
        info = get_vector_index_info(vector_dir, book_id)
        if info is None:
            if mode == "auto":
                base = self._get_local_retriever(store, book_id, search_settings)
                return self._wrap_reranker(base, rerank_config)
            raise LocalModelError("这本书还没有向量索引，请先运行 embed-build，或在网页端选择倒排检索。")

        try:
            configure_local_model_cache(default_cache_root(self.db_path))
            embedder = SentenceTransformerEmbedder(
                info.model_name,
                cache_dir=default_sentence_transformers_cache_dir(self.db_path),
            )
            base = EmbeddingRetriever(store, search_settings, index_dir=vector_dir, embedder=embedder)
            return self._wrap_reranker(base, rerank_config)
        except LocalModelError:
            if mode == "auto":
                base = self._get_local_retriever(store, book_id, search_settings)
                return self._wrap_reranker(base, rerank_config)
            raise

    def _wrap_reranker(self, base: Retriever, config: dict[str, object] | None) -> Retriever:
        if not config or not bool(config.get("enabled")):
            return base
        try:
            configure_local_model_cache(default_cache_root(self.db_path))
            reranker = CrossEncoderReranker(
                str(config.get("model") or DEFAULT_RERANK_SETTINGS.model_name),
                cache_dir=default_sentence_transformers_cache_dir(self.db_path),
                batch_size=int(config.get("batch_size") or DEFAULT_RERANK_SETTINGS.batch_size),
            )
            return RerankingRetriever(base, reranker, DEFAULT_SEARCH_SETTINGS)
        except Exception as exc:  # noqa: BLE001 - reranker is an optional precision layer
            if bool(config.get("required")):
                raise LocalModelError(f"本地 reranker 加载失败：{exc}") from exc
            return base

    def _make_policy(
        self,
        mode: str,
        reasoner: OpenAICompatibleReasoner | _DisabledReasoner,
        local_client: object | None = None,
    ) -> DecisionPolicy | None:
        normalized = (mode or "auto").strip().lower()
        if normalized == "auto":
            if local_client is not None:
                return LocalPlannerPolicy(local_client)
            return None
        if normalized == "rule_based":
            return RuleBasedPolicy(reasoner=None)
        if normalized == "llm_react":
            if not reasoner.enabled:
                raise LocalModelError("LLM ReAct 策略需要先启用云端大模型配置。")
            return LLMReActPolicy(reasoner)
        if normalized == "local_planner":
            if local_client is None:
                raise LocalModelError("本地 Qwen Planner 需要先启用本地 Qwen 配置。")
            return LocalPlannerPolicy(local_client)
        if normalized == "langgraph":
            delegate: DecisionPolicy
            if reasoner.enabled:
                delegate = LLMReActPolicy(reasoner)
            else:
                delegate = RuleBasedPolicy(reasoner=None)
            try:
                return LangGraphPolicy(delegate=delegate)
            except LangGraphUnavailableError as exc:
                raise LocalModelError(str(exc)) from exc
        raise LocalModelError("未知 Agent 执行策略，请选择 auto、rule_based、local_planner、llm_react 或 langgraph。")

    @staticmethod
    def _effective_policy_name(
        mode: str,
        reasoner: OpenAICompatibleReasoner | _DisabledReasoner,
        local_client: object | None = None,
    ) -> str:
        normalized = (mode or "auto").strip().lower()
        if normalized == "auto":
            if local_client is not None:
                return "local_planner"
            return "llm_react" if reasoner.enabled else "rule_based"
        if normalized == "local_planner":
            return "local_planner"
        if normalized == "langgraph":
            return "langgraph(llm_react)" if reasoner.enabled else "langgraph(rule_based)"
        return normalized

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

        if path == "/api/diagnostics":
            self._send_json(self.service.diagnostics())
            return

        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/") :].strip("/")
            try:
                self._send_json({"job": self.service.get_index_job(job_id)})
            except ValueError as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            return

        if path == "/api/agent/tools":
            self._send_json(self.service.list_agent_tools())
            return

        if path.startswith("/api/books/") and path.endswith("/entities"):
            book_id = path[len("/api/books/") : -len("/entities")].strip("/")
            self._send_json({"book_id": book_id, "entities": self.service.list_entities(book_id)})
            return

        if path.startswith("/api/books/") and "/chapters/" in path:
            book_part, chapter_part = path[len("/api/books/") :].split("/chapters/", 1)
            book_id = book_part.strip("/")
            try:
                chapter_number = int(chapter_part.strip("/"))
                self._send_json({"chapter": self.service.get_chapter(book_id, chapter_number)})
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
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

        if path.startswith("/api/books/") and path.endswith("/preferences"):
            book_id = path[len("/api/books/") : -len("/preferences")].strip("/")
            query = parse_qs(parsed.query)
            user_id = query.get("user", ["default"])[0]
            self._send_json({"preferences": self.service.get_user_preferences(book_id, user_id)})
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

        if path.startswith("/api/books/") and path.endswith("/session/digest"):
            book_id = path[len("/api/books/") : -len("/session/digest")].strip("/")
            query = parse_qs(parsed.query)
            user_id = query.get("user", ["default"])[0]
            session_id = query.get("session", ["default-session"])[0]
            limit_raw = query.get("limit", ["100"])[0]
            try:
                limit = max(1, min(300, int(limit_raw)))
            except ValueError:
                limit = 100
            self._send_json({"digest": self.service.get_session_digest(book_id, user_id, session_id, limit=limit)})
            return

        if path.startswith("/api/books/") and path.endswith("/sessions/compare"):
            book_id = path[len("/api/books/") : -len("/sessions/compare")].strip("/")
            query = parse_qs(parsed.query)
            user_id = query.get("user", ["default"])[0]
            left_session_id = query.get("left", [""])[0].strip()
            right_session_id = query.get("right", [""])[0].strip()
            limit_raw = query.get("limit", ["100"])[0]
            try:
                limit = max(1, min(200, int(limit_raw)))
            except ValueError:
                limit = 100
            self._send_json(
                {
                    "comparison": self.service.compare_sessions(
                        book_id=book_id,
                        user_id=user_id,
                        left_session_id=left_session_id,
                        right_session_id=right_session_id,
                        limit=limit,
                    )
                }
            )
            return

        if path.startswith("/api/books/") and path.endswith("/sessions"):
            book_id = path[len("/api/books/") : -len("/sessions")].strip("/")
            query = parse_qs(parsed.query)
            user_id = query.get("user", ["default"])[0]
            limit_raw = query.get("limit", ["30"])[0]
            try:
                limit = max(1, min(100, int(limit_raw)))
            except ValueError:
                limit = 30
            self._send_json(self.service.list_sessions(book_id, user_id, limit=limit))
            return

        if path == "/health":
            self._send_json({"ok": True, "thread": threading.current_thread().name})
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "接口不存在。")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/books/build-job":
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = str(payload.get("book_id", "")).strip()
                title = str(payload.get("title", "")).strip()
                text = str(payload.get("text", ""))
                entity_lexicon = str(payload.get("entities", ""))
                theme_lexicon = str(payload.get("themes", ""))
                overwrite = bool(payload.get("overwrite"))
                source_name = str(payload.get("source_name", "")).strip()
                smart_index = payload.get("smart_index")
                if not isinstance(smart_index, dict):
                    smart_index = None
                vector_index = payload.get("vector_index")
                if not isinstance(vector_index, dict):
                    vector_index = None
                if not book_id or not text.strip():
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "book_id 和书籍正文不能为空。")
                    return
                self._send_json(
                    {
                        "job": self.service.start_build_book_job(
                            book_id=book_id,
                            title=title,
                            text=text,
                            entity_lexicon=entity_lexicon,
                            theme_lexicon=theme_lexicon,
                            smart_index=smart_index,
                            vector_index=vector_index,
                            overwrite=overwrite,
                            source_path=f"web://file/{source_name}" if source_name else "web://imported-text",
                        )
                    },
                    status=HTTPStatus.ACCEPTED,
                )
                return

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
                source_name = str(payload.get("source_name", "")).strip()
                smart_index = payload.get("smart_index")
                if not isinstance(smart_index, dict):
                    smart_index = None
                vector_index = payload.get("vector_index")
                if not isinstance(vector_index, dict):
                    vector_index = None
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
                            smart_index=smart_index,
                            vector_index=vector_index,
                            overwrite=overwrite,
                            source_path=f"web://file/{source_name}" if source_name else "web://imported-text",
                        )
                    }
                )
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/rebuild-job"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/rebuild-job")].strip("/")
                smart_index = payload.get("smart_index")
                if not isinstance(smart_index, dict):
                    smart_index = None
                self._send_json(
                    {
                        "job": self.service.start_rebuild_book_job(
                            book_id=book_id,
                            entity_lexicon=str(payload.get("entities", "")),
                            theme_lexicon=str(payload.get("themes", "")),
                            smart_index=smart_index,
                        )
                    },
                    status=HTTPStatus.ACCEPTED,
                )
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/rebuild"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/rebuild")].strip("/")
                smart_index = payload.get("smart_index")
                if not isinstance(smart_index, dict):
                    smart_index = None
                self._send_json(
                    {
                        "book": self.service.rebuild_book_index(
                            book_id=book_id,
                            entity_lexicon=str(payload.get("entities", "")),
                            theme_lexicon=str(payload.get("themes", "")),
                            smart_index=smart_index,
                        )
                    }
                )
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/vectors/delete"):
                book_id = parsed.path[len("/api/books/") : -len("/vectors/delete")].strip("/")
                self._send_json({"vector_index": self.service.delete_vector_index(book_id)})
                return

            if (
                parsed.path.startswith("/api/books/")
                and parsed.path.endswith("/delete")
                and not parsed.path.endswith("/session/delete")
            ):
                book_id = parsed.path[len("/api/books/") : -len("/delete")].strip("/")
                self._send_json({"deleted": self.service.delete_book(book_id)})
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

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/search"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/search")].strip("/")
                progress_raw = payload.get("progress_chapter")
                limit_raw = payload.get("limit")
                progress_chapter = int(progress_raw) if progress_raw not in (None, "") else None
                limit = max(1, min(20, int(limit_raw))) if limit_raw not in (None, "") else 8
                rerank_config = payload.get("rerank_config")
                if not isinstance(rerank_config, dict):
                    rerank_config = None
                self._send_json(
                    {
                        "search": self.service.search_evidence(
                            book_id=book_id,
                            query=str(payload.get("query", "")).strip(),
                            retriever_mode=str(payload.get("retriever", "auto")).strip() or "auto",
                            rerank_config=rerank_config,
                            progress_chapter=progress_chapter,
                            limit=limit,
                        )
                    }
                )
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/metadata"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/metadata")].strip("/")
                book_group = str(payload.get("book_group", "")).strip()
                raw_tags = payload.get("tags", [])
                if isinstance(raw_tags, str):
                    tags = [item.strip() for item in raw_tags.replace("，", ",").split(",") if item.strip()]
                elif isinstance(raw_tags, list):
                    tags = [str(item).strip() for item in raw_tags if str(item).strip()]
                else:
                    tags = []
                self._send_json({"book": self.service.update_book_metadata(book_id, book_group=book_group, tags=tags)})
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/preferences"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/preferences")].strip("/")
                user_id = str(payload.get("user_id", "default")).strip() or "default"
                self._send_json(
                    {
                        "preferences": self.service.set_user_preferences(
                            book_id=book_id,
                            user_id=user_id,
                            answer_style=str(payload.get("answer_style", "")),
                            focus=str(payload.get("focus", "")),
                            custom_prompt=str(payload.get("custom_prompt", "")),
                        )
                    }
                )
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/agent/tools/run"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/agent/tools/run")].strip("/")
                tool_name = str(payload.get("tool_name", "")).strip()
                arguments = payload.get("arguments", {})
                if not isinstance(arguments, dict):
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "arguments 必须是对象。")
                    return
                progress_raw = payload.get("progress_chapter")
                progress_chapter = int(progress_raw) if progress_raw not in (None, "") else None
                self._send_json(
                    {
                        "tool_run": self.service.run_agent_tool(
                            book_id=book_id,
                            user_id=str(payload.get("user_id", "default")).strip() or "default",
                            session_id=str(payload.get("session_id", "")).strip() or None,
                            tool_name=tool_name,
                            arguments=arguments,
                            question=str(payload.get("question", "")).strip(),
                            progress_chapter=progress_chapter,
                            retriever_mode=str(payload.get("retriever", "lexical")).strip() or "lexical",
                        )
                    }
                )
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/session/delete"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/session/delete")].strip("/")
                user_id = str(payload.get("user_id", "default")).strip() or "default"
                session_id = str(payload.get("session_id", "default-session")).strip() or "default-session"
                self._send_json(
                    {
                        "session": self.service.delete_session(
                            book_id=book_id,
                            user_id=user_id,
                            session_id=session_id,
                        )
                    }
                )
                return

            if parsed.path.startswith("/api/books/") and parsed.path.endswith("/sessions/merge"):
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = parsed.path[len("/api/books/") : -len("/sessions/merge")].strip("/")
                user_id = str(payload.get("user_id", "default")).strip() or "default"
                left_session_id = str(payload.get("left_session_id", "")).strip()
                right_session_id = str(payload.get("right_session_id", "")).strip()
                target_session_id = str(payload.get("target_session_id", "")).strip() or None
                limit_raw = payload.get("limit", 100)
                limit = max(1, min(200, int(limit_raw))) if limit_raw not in (None, "") else 100
                self._send_json(
                    {
                        "merge": self.service.merge_sessions(
                            book_id=book_id,
                            user_id=user_id,
                            left_session_id=left_session_id,
                            right_session_id=right_session_id,
                            target_session_id=target_session_id,
                            limit=limit,
                        )
                    }
                )
                return

            if parsed.path.startswith("/api/books/") and "/session/turns/" in parsed.path:
                payload = self._read_json_body()
                if payload is None:
                    return
                book_part, turn_part = parsed.path[len("/api/books/") :].split("/session/turns/", 1)
                book_id = book_part.strip("/")
                turn_id = int(turn_part.strip("/"))
                user_id = str(payload.get("user_id", "default")).strip() or "default"
                session_id = str(payload.get("session_id", "default-session")).strip() or "default-session"
                operation = str(payload.get("operation", "update")).strip().lower()
                if operation == "delete":
                    self._send_json(
                        {
                            "session": self.service.delete_session_turn(
                                book_id=book_id,
                                user_id=user_id,
                                session_id=session_id,
                                turn_id=turn_id,
                            )
                        }
                    )
                    return
                if operation == "rerun":
                    progress_raw = payload.get("progress_chapter")
                    progress_chapter = int(progress_raw) if progress_raw not in (None, "") else None
                    cloud_config = payload.get("cloud_config")
                    if not isinstance(cloud_config, dict):
                        cloud_config = None
                    local_llm_config = payload.get("local_llm_config")
                    if not isinstance(local_llm_config, dict):
                        local_llm_config = None
                    rerank_config = payload.get("rerank_config")
                    if not isinstance(rerank_config, dict):
                        rerank_config = None
                    self._send_json(
                        self.service.rerun_session_from_turn(
                            book_id=book_id,
                            user_id=user_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            question=str(payload.get("question", "")).strip() or None,
                            progress_chapter=progress_chapter,
                            retriever_mode=str(payload.get("retriever", "lexical")).strip() or "lexical",
                            agent_policy=str(payload.get("agent_policy", "auto")).strip() or "auto",
                            cloud_config=cloud_config,
                            local_llm_config=local_llm_config,
                            rerank_config=rerank_config,
                        )
                    )
                    return
                if operation == "branch":
                    progress_raw = payload.get("progress_chapter")
                    progress_chapter = int(progress_raw) if progress_raw not in (None, "") else None
                    cloud_config = payload.get("cloud_config")
                    if not isinstance(cloud_config, dict):
                        cloud_config = None
                    local_llm_config = payload.get("local_llm_config")
                    if not isinstance(local_llm_config, dict):
                        local_llm_config = None
                    rerank_config = payload.get("rerank_config")
                    if not isinstance(rerank_config, dict):
                        rerank_config = None
                    target_session_id = str(payload.get("target_session_id", "")).strip() or None
                    self._send_json(
                        self.service.branch_session_from_turn(
                            book_id=book_id,
                            user_id=user_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            question=str(payload.get("question", "")).strip() or None,
                            target_session_id=target_session_id,
                            progress_chapter=progress_chapter,
                            retriever_mode=str(payload.get("retriever", "lexical")).strip() or "lexical",
                            agent_policy=str(payload.get("agent_policy", "auto")).strip() or "auto",
                            cloud_config=cloud_config,
                            local_llm_config=local_llm_config,
                            rerank_config=rerank_config,
                        )
                    )
                    return
                question = str(payload.get("question", "")).strip()
                answer = str(payload.get("answer", "")).strip()
                summary_raw = payload.get("summary")
                summary = str(summary_raw).strip() if summary_raw not in (None, "") else None
                if not question or not answer:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "question 和 answer 不能为空。")
                    return
                self._send_json(
                    {
                        "session": self.service.update_session_turn(
                            book_id=book_id,
                            user_id=user_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            question=question,
                            answer=answer,
                            summary=summary,
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
                agent_policy = str(payload.get("agent_policy", "auto")).strip() or "auto"
                progress_raw = payload.get("progress_chapter")
                progress_chapter = int(progress_raw) if progress_raw not in (None, "") else None
                cloud_config = payload.get("cloud_config")
                if not isinstance(cloud_config, dict):
                    cloud_config = None
                local_llm_config = payload.get("local_llm_config")
                if not isinstance(local_llm_config, dict):
                    local_llm_config = None
                rerank_config = payload.get("rerank_config")
                if not isinstance(rerank_config, dict):
                    rerank_config = None
                force_exact_search = bool(payload.get("force_exact_search"))
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
                        agent_policy=agent_policy,
                        cloud_config=cloud_config,
                        local_llm_config=local_llm_config,
                        rerank_config=rerank_config,
                        force_exact_search=force_exact_search,
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
        except LocalLLMError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, f"智能索引本地模型调用失败：{exc}")
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
        asset_path = _resolve_asset_path(safe_name)
        if asset_path is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "静态资源不存在。")
            return
        content_type = _asset_content_type(asset_path)
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


def _frontend_dist_root() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _build_index_html() -> str:
    dist_index = _frontend_dist_root() / "index.html"
    if dist_index.exists():
        return dist_index.read_text(encoding="utf-8")
    return (_asset_root() / "index.html").read_text(encoding="utf-8")


def _resolve_asset_path(safe_name: str) -> Path | None:
    dist_asset = _frontend_dist_root() / "assets" / safe_name
    if dist_asset.exists() and dist_asset.is_file():
        return dist_asset
    legacy_asset = _asset_root() / safe_name
    if safe_name in {"app.css", "app.js"} and legacy_asset.exists():
        return legacy_asset
    return None


def _asset_content_type(asset_path: Path) -> str:
    if asset_path.suffix == ".js":
        return "application/javascript; charset=utf-8"
    if asset_path.suffix == ".css":
        return "text/css; charset=utf-8"
    guessed, _ = mimetypes.guess_type(str(asset_path))
    return guessed or "application/octet-stream"


def _branch_session_id(session_id: str, turn_index: int) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_session = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in session_id).strip("-")
    return f"{safe_session or 'session'}-branch-t{turn_index}-{stamp}"


def _merge_session_id(left_session_id: str, right_session_id: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw = f"{left_session_id}-with-{right_session_id}"
    safe_session = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in raw).strip("-")
    return f"{safe_session or 'session'}-merge-{stamp}"


def _common_turn_prefix(left_turns: list[dict[str, object]], right_turns: list[dict[str, object]]) -> int:
    count = 0
    for left, right in zip(left_turns, right_turns):
        if str(left.get("question") or "") != str(right.get("question") or ""):
            break
        if str(left.get("answer") or "") != str(right.get("answer") or ""):
            break
        count += 1
    return count


def _collect_turn_entities(turns: list[dict[str, object]]) -> list[str]:
    entities: list[str] = []
    for turn in turns:
        entity_name = turn.get("entity_name")
        if entity_name:
            entities.append(str(entity_name))
        matched_entities = turn.get("matched_entities")
        if isinstance(matched_entities, list):
            entities.extend(str(item) for item in matched_entities if item)
    return sorted(set(entities))


def _collect_trace_tools(turns: list[dict[str, object]]) -> list[str]:
    tools: list[str] = []
    for turn in turns:
        trace = turn.get("trace")
        if not isinstance(trace, list):
            continue
        for item in trace:
            if isinstance(item, dict) and item.get("tool_name"):
                tools.append(str(item["tool_name"]))
    return sorted(set(tools))


def _build_delta(left_items: list[str], right_items: list[str]) -> dict[str, list[str]]:
    left_set = set(left_items)
    right_set = set(right_items)
    return {
        "shared": sorted(left_set & right_set),
        "left_only": sorted(left_set - right_set),
        "right_only": sorted(right_set - left_set),
    }


def _build_session_diff_insights(
    *,
    common_prefix: int,
    left_session_id: str,
    right_session_id: str,
    left_unique: list[dict[str, object]],
    right_unique: list[dict[str, object]],
    entity_delta: dict[str, list[str]],
    tool_delta: dict[str, list[str]],
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    if common_prefix == 0:
        insights.append(
            {
                "kind": "divergence",
                "title": "从第一轮开始分歧",
                "detail": "两个会话没有共同前缀，合并前建议确认它们是否真的属于同一条阅读线索。",
            }
        )
    else:
        insights.append(
            {
                "kind": "divergence",
                "title": f"共同前缀 {common_prefix} 轮",
                "detail": f"两个分支在第 {common_prefix + 1} 轮开始分歧，可重点检查后续独有轮次。",
            }
        )

    if len(left_unique) != len(right_unique):
        longer = left_session_id if len(left_unique) > len(right_unique) else right_session_id
        insights.append(
            {
                "kind": "coverage",
                "title": "分支推进长度不同",
                "detail": f"{longer} 包含更多独有轮次，可能覆盖了更多追问上下文。",
            }
        )
    elif left_unique and right_unique:
        insights.append(
            {
                "kind": "coverage",
                "title": "两侧都有独有推进",
                "detail": "两个分支都包含独有轮次，适合先对比差异，再合并为新的会话继续追问。",
            }
        )

    left_only_entities = entity_delta.get("left_only") or []
    right_only_entities = entity_delta.get("right_only") or []
    if left_only_entities or right_only_entities:
        insights.append(
            {
                "kind": "entity",
                "title": "实体焦点不同",
                "detail": (
                    f"左侧独有实体：{', '.join(left_only_entities) or '无'}；"
                    f"右侧独有实体：{', '.join(right_only_entities) or '无'}。"
                ),
            }
        )

    left_only_tools = tool_delta.get("left_only") or []
    right_only_tools = tool_delta.get("right_only") or []
    if left_only_tools or right_only_tools:
        insights.append(
            {
                "kind": "tool",
                "title": "工具调用路径不同",
                "detail": (
                    f"左侧独有工具：{', '.join(left_only_tools) or '无'}；"
                    f"右侧独有工具：{', '.join(right_only_tools) or '无'}。"
                ),
            }
        )
    return insights


def _build_turn_diffs(
    left_turns: list[dict[str, object]],
    right_turns: list[dict[str, object]],
) -> list[dict[str, object]]:
    diffs: list[dict[str, object]] = []
    max_len = max(len(left_turns), len(right_turns))
    for index in range(max_len):
        left = left_turns[index] if index < len(left_turns) else None
        right = right_turns[index] if index < len(right_turns) else None
        left_question = str(left.get("question") or "") if left else ""
        right_question = str(right.get("question") or "") if right else ""
        if left is None:
            status = "right_only"
        elif right is None:
            status = "left_only"
        elif left_question == right_question:
            status = "same_question"
        else:
            status = "different_question"
        diffs.append(
            {
                "offset": index + 1,
                "status": status,
                "left_turn_index": left.get("turn_index") if left else None,
                "right_turn_index": right.get("turn_index") if right else None,
                "left_question": left_question,
                "right_question": right_question,
                "left_answer_excerpt": _excerpt(str(left.get("answer") or "")) if left else "",
                "right_answer_excerpt": _excerpt(str(right.get("answer") or "")) if right else "",
                "left_summary": str(left.get("summary") or "") if left else "",
                "right_summary": str(right.get("summary") or "") if right else "",
                "left_tools": _turn_tool_names(left) if left else [],
                "right_tools": _turn_tool_names(right) if right else [],
            }
        )
    return diffs


def _turn_tool_names(turn: dict[str, object]) -> list[str]:
    trace = turn.get("trace")
    if not isinstance(trace, list):
        return []
    names = [str(item["tool_name"]) for item in trace if isinstance(item, dict) and item.get("tool_name")]
    return list(dict.fromkeys(names))


def _excerpt(text: str, limit: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."
