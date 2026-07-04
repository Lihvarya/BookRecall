from __future__ import annotations

import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .agent import BookRecallAgent
from .cloud import OpenAICompatibleReasoner
from .config import DEFAULT_SEARCH_SETTINGS
from .embeddings import (
    EmbeddingRetriever,
    LocalModelError,
    SentenceTransformerEmbedder,
    configure_local_model_cache,
    default_cache_root,
    default_sentence_transformers_cache_dir,
    default_vector_dir,
    dependency_report,
    get_vector_index_info,
)
from .retrieval import LocalRetriever, Retriever
from .storage import BookRecallStore


class _DisabledReasoner:
    enabled = False
    model = None

    def answer(self, prompt: str) -> str | None:
        return None


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

    def ask(
        self,
        *,
        book_id: str,
        question: str,
        user_id: str = "default",
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
                progress_chapter=progress_chapter,
            )
            payload = agent.to_payload(card)
            payload["rendered_text"] = agent.render_text(card)
            payload["runtime"] = {
                "retriever": retriever_mode,
                "cloud_reasoner_enabled": reasoner.enabled,
                "cloud_model": reasoner.model if reasoner.enabled else None,
            }
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

        if path.startswith("/api/books/") and path.endswith("/progress"):
            book_id = path[len("/api/books/") : -len("/progress")].strip("/")
            query = parse_qs(parsed.query)
            user_id = query.get("user", ["default"])[0]
            self._send_json(self.service.get_progress(book_id, user_id))
            return

        if path == "/health":
            self._send_json({"ok": True, "thread": threading.current_thread().name})
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "接口不存在。")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/ask":
                payload = self._read_json_body()
                if payload is None:
                    return
                book_id = str(payload.get("book_id", "")).strip()
                question = str(payload.get("question", "")).strip()
                user_id = str(payload.get("user_id", "default")).strip() or "default"
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


def _build_index_html() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BookRecall Agent Console</title>
  <style>
    :root {
      --ink: #1b241f;
      --muted: rgba(27, 36, 31, 0.68);
      --paper: #f7f0df;
      --panel: rgba(255, 252, 243, 0.88);
      --panel-strong: rgba(255, 255, 255, 0.94);
      --line: rgba(36, 48, 42, 0.13);
      --green: #295b45;
      --moss: #789461;
      --clay: #bf6b3f;
      --gold: #d7a84f;
      --shadow: 0 24px 70px rgba(54, 42, 25, 0.16);
      --mono: "Cascadia Code", "SFMono-Regular", Consolas, monospace;
      --serif: "Source Han Serif SC", "Noto Serif SC", "Songti SC", Georgia, serif;
      --sans: "LXGW WenKai", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: var(--sans);
      background:
        radial-gradient(circle at 8% 0%, rgba(215, 168, 79, 0.34), transparent 28%),
        radial-gradient(circle at 88% 12%, rgba(41, 91, 69, 0.2), transparent 24%),
        linear-gradient(135deg, #efe2c7 0%, #f8f2e6 45%, #e9eddc 100%);
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: 0.35;
      background-image:
        linear-gradient(rgba(41, 91, 69, 0.06) 1px, transparent 1px),
        linear-gradient(90deg, rgba(41, 91, 69, 0.05) 1px, transparent 1px);
      background-size: 36px 36px;
      mask-image: linear-gradient(180deg, #000, transparent 82%);
    }
    .shell {
      width: min(1440px, calc(100% - 32px));
      margin: 22px auto 48px;
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr) 360px;
      gap: 18px;
      position: relative;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }
    .sidebar, .main, .settings { padding: 22px; }
    h1, h2, h3 { margin: 0; }
    h1 {
      font-family: var(--serif);
      font-size: 34px;
      letter-spacing: 0.02em;
    }
    h2 {
      font-family: var(--serif);
      font-size: 22px;
      margin-bottom: 12px;
    }
    .subtitle {
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.7;
      font-size: 14px;
    }
    .section { margin-top: 22px; }
    .label {
      display: block;
      margin-bottom: 8px;
      color: rgba(27, 36, 31, 0.72);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    select, input, textarea, button {
      width: 100%;
      border-radius: 16px;
      border: 1px solid var(--line);
      padding: 12px 13px;
      font: inherit;
      color: var(--ink);
      background: rgba(255, 255, 255, 0.78);
      outline: none;
    }
    textarea {
      min-height: 154px;
      resize: vertical;
      line-height: 1.65;
    }
    button {
      cursor: pointer;
      border: 0;
      color: #fffaf0;
      font-weight: 800;
      letter-spacing: 0.04em;
      background: linear-gradient(135deg, var(--green), #16382b);
      box-shadow: 0 14px 30px rgba(22, 56, 43, 0.25);
      transition: transform 0.18s ease, box-shadow 0.18s ease;
    }
    button:hover { transform: translateY(-1px); box-shadow: 0 18px 34px rgba(22, 56, 43, 0.28); }
    button.secondary { background: linear-gradient(135deg, #7b8d58, #52653a); }
    button.ghost {
      color: var(--green);
      background: rgba(41, 91, 69, 0.09);
      box-shadow: none;
      border: 1px solid rgba(41, 91, 69, 0.16);
    }
    .inline {
      display: grid;
      grid-template-columns: 1fr 118px;
      gap: 10px;
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: start;
      margin-bottom: 18px;
    }
    .status, .mini-card, .book-item, .entity-item, .evidence-item, .suggestion-item {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.68);
      border-radius: 18px;
      padding: 13px 15px;
    }
    .status {
      background: linear-gradient(135deg, rgba(215, 168, 79, 0.24), rgba(255, 255, 255, 0.58));
      line-height: 1.6;
    }
    .stack { display: grid; gap: 10px; }
    .books, .entities, .chapters, .model-list, .evidence, .suggestions { display: grid; gap: 10px; }
    .book-item strong, .entity-item strong, .mini-card strong { display: block; margin-bottom: 4px; }
    .muted { color: var(--muted); }
    .mono { font-family: var(--mono); font-size: 12px; }
    .pill-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .pill {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(41, 91, 69, 0.1);
      color: var(--green);
      font-size: 12px;
      font-weight: 800;
    }
    .pill.warn { background: rgba(191, 107, 63, 0.13); color: #984d2a; }
    .pill.ready { background: rgba(120, 148, 97, 0.16); color: #405c2d; }
    .answer-card {
      margin-top: 18px;
      padding: 22px;
      border-radius: 26px;
      background: linear-gradient(180deg, rgba(255,255,255,0.92), rgba(249,244,235,0.92));
      border: 1px solid var(--line);
    }
    .answer-head { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
    .answer-text { font-size: 18px; line-height: 1.75; margin: 0; white-space: pre-wrap; }
    .grid-two {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-top: 16px;
    }
    .empty {
      padding: 24px 16px;
      text-align: center;
      border: 1px dashed rgba(27, 36, 31, 0.24);
      border-radius: 18px;
      color: var(--muted);
      line-height: 1.7;
    }
    .tiny { font-size: 12px; line-height: 1.6; }
    .checkline {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
    }
    .checkline input { width: auto; }
    details summary { cursor: pointer; list-style: none; }
    details summary::-webkit-details-marker { display: none; }
    @media (max-width: 1180px) {
      .shell { grid-template-columns: 280px minmax(0, 1fr); }
      .settings { grid-column: 1 / -1; }
    }
    @media (max-width: 820px) {
      .shell { grid-template-columns: 1fr; }
      .hero, .grid-two, .inline { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="panel sidebar">
      <h1>BookRecall</h1>
      <p class="subtitle">阅读记忆 Agent 控制台。选择书籍、锁定阅读进度，再决定让本地索引、embedding 小模型或外部大模型怎么协作。</p>

      <div class="section">
        <label class="label" for="bookSelect">书籍</label>
        <select id="bookSelect"></select>
      </div>

      <div class="section">
        <label class="label" for="userInput">用户 ID</label>
        <input id="userInput" value="default">
      </div>

      <div class="section">
        <label class="label" for="progressInput">阅读进度</label>
        <div class="inline">
          <input id="progressInput" type="number" min="1" step="1" placeholder="已读章节">
          <button class="secondary" id="saveProgressBtn" type="button">保存</button>
        </div>
      </div>

      <div class="section">
        <div class="status" id="bookMeta">正在加载书库...</div>
      </div>

      <div class="section">
        <details open>
          <summary class="label">实体索引</summary>
          <div class="entities" id="entitiesPanel"></div>
        </details>
      </div>

      <div class="section">
        <details>
          <summary class="label">章节浏览</summary>
          <div class="chapters" id="chaptersPanel"></div>
        </details>
      </div>
    </aside>

    <main class="panel main">
      <div class="hero">
        <div>
          <h2>唤醒这段记忆</h2>
          <p class="subtitle">强顺序问题仍走实体索引；开放回忆可以切到 embedding；复杂推理可临时接入 DeepSeek 或其他 OpenAI-compatible API。</p>
        </div>
        <div class="pill-row">
          <span class="pill" id="retrieverPill">retriever: lexical</span>
          <span class="pill" id="cloudPill">cloud: off</span>
        </div>
      </div>

      <div class="status" id="statusBar">准备就绪。</div>

      <div class="section">
        <label class="label" for="questionInput">提问</label>
        <textarea id="questionInput" placeholder="比如：黑衣人第一次出现在哪一章？或者：开窍大典前后发生了什么？"></textarea>
      </div>

      <div class="section">
        <button id="askBtn" type="button">唤醒这段记忆</button>
      </div>

      <section class="answer-card" id="answerCard">
        <div class="empty">先选择一本书并提出问题。这里会显示结构化记忆卡片、证据片段和继续追问方向。</div>
      </section>

      <section class="section">
        <span class="label">书库总览</span>
        <div class="books" id="booksPanel"></div>
      </section>
    </main>

    <aside class="panel settings">
      <h2>Agent 设置</h2>

      <div class="section">
        <label class="label" for="retrieverSelect">证据检索器</label>
        <select id="retrieverSelect">
          <option value="lexical">倒排检索：稳定、零依赖</option>
          <option value="embedding">本地 embedding：语义召回</option>
          <option value="auto">自动：有向量索引用 embedding，否则倒排</option>
        </select>
        <p class="subtitle tiny">“第一次出现”仍由结构化实体索引回答；检索器主要影响语义回忆和对比类问题。</p>
      </div>

      <div class="section">
        <span class="label">本地模型与索引</span>
        <div class="model-list" id="modelPanel"></div>
      </div>

      <div class="section">
        <label class="label" for="providerSelect">外部 API 预设</label>
        <select id="providerSelect">
          <option value="deepseek">DeepSeek</option>
          <option value="openai">OpenAI</option>
          <option value="custom">自定义 OpenAI-compatible</option>
        </select>
      </div>

      <div class="section stack">
        <label class="label" for="apiEndpointInput">API Endpoint</label>
        <input id="apiEndpointInput" placeholder="https://api.deepseek.com/v1/chat/completions">
        <label class="label" for="apiModelInput">模型名</label>
        <input id="apiModelInput" placeholder="deepseek-chat">
        <label class="label" for="apiKeyInput">API Key</label>
        <input id="apiKeyInput" type="password" placeholder="仅在本次请求发送，不写入服务端">
        <label class="checkline">
          <input id="cloudEnabledInput" type="checkbox">
          <span>启用外部大模型 ReAct 规划</span>
        </label>
        <label class="checkline">
          <input id="rememberApiInput" type="checkbox">
          <span>把 endpoint/model/key 保存到本浏览器 localStorage</span>
        </label>
        <div class="inline">
          <button class="ghost" id="saveApiBtn" type="button">保存本地设置</button>
          <button class="ghost" id="clearApiBtn" type="button">清除</button>
        </div>
        <p class="subtitle tiny">安全边界：服务端不落盘保存密钥；如果勾选保存，只会存在当前浏览器的 localStorage。</p>
      </div>
    </aside>
  </div>

  <script>
    const state = {
      books: [],
      runtime: null,
      providers: {},
      currentBookId: "",
      currentUserId: "default"
    };

    const els = {
      bookSelect: document.getElementById("bookSelect"),
      userInput: document.getElementById("userInput"),
      progressInput: document.getElementById("progressInput"),
      questionInput: document.getElementById("questionInput"),
      statusBar: document.getElementById("statusBar"),
      bookMeta: document.getElementById("bookMeta"),
      booksPanel: document.getElementById("booksPanel"),
      entitiesPanel: document.getElementById("entitiesPanel"),
      chaptersPanel: document.getElementById("chaptersPanel"),
      answerCard: document.getElementById("answerCard"),
      retrieverSelect: document.getElementById("retrieverSelect"),
      retrieverPill: document.getElementById("retrieverPill"),
      cloudPill: document.getElementById("cloudPill"),
      modelPanel: document.getElementById("modelPanel"),
      providerSelect: document.getElementById("providerSelect"),
      apiEndpointInput: document.getElementById("apiEndpointInput"),
      apiModelInput: document.getElementById("apiModelInput"),
      apiKeyInput: document.getElementById("apiKeyInput"),
      cloudEnabledInput: document.getElementById("cloudEnabledInput"),
      rememberApiInput: document.getElementById("rememberApiInput")
    };

    function setStatus(text) {
      els.statusBar.textContent = text;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "请求失败");
      }
      return data;
    }

    function renderBooks(books) {
      if (!books.length) {
        els.booksPanel.innerHTML = '<div class="empty">当前还没有已建索引的书。先用 CLI 执行 build。</div>';
        return;
      }
      els.booksPanel.innerHTML = books.map((book) => `
        <div class="book-item">
          <strong>${escapeHtml(book.title)}</strong>
          <div class="muted mono">${escapeHtml(book.book_id)}</div>
          <div>章节 ${book.chapter_count} · 实体 ${book.entity_count}</div>
          <div class="muted mono">${escapeHtml(book.source_path)}</div>
        </div>
      `).join("");
    }

    function renderModels(runtime) {
      const deps = runtime.dependencies || {};
      const depCards = [
        ["numpy", deps.numpy],
        ["sentence-transformers", deps.sentence_transformers],
        ["torch", deps.torch],
        ["faiss", deps.faiss]
      ].map(([name, ok]) => `
        <div class="mini-card">
          <strong>${escapeHtml(name)}</strong>
          <span class="pill ${ok ? "ready" : "warn"}">${ok ? "可用" : "缺失"}</span>
        </div>
      `).join("");

      const indexCards = (runtime.vector_indexes || []).map((item) => `
        <div class="mini-card">
          <strong>${escapeHtml(item.book_id)}</strong>
          <div class="pill-row">
            <span class="pill ${item.built ? "ready" : "warn"}">${item.built ? "向量索引已构建" : "未构建"}</span>
            ${item.built ? `<span class="pill">${item.chunk_count} chunks</span>` : ""}
          </div>
          <div class="muted mono">${escapeHtml(item.model_name || "运行 embed-build 后可用")}</div>
        </div>
      `).join("");

      els.modelPanel.innerHTML = `
        <div class="stack">${depCards}</div>
        <div class="mini-card">
          <strong>推荐 embedding</strong>
          <div class="mono">${escapeHtml(deps.recommended_embedding_model || "BAAI/bge-small-zh-v1.5")}</div>
          <div class="muted mono">${escapeHtml(runtime.vector_dir)}</div>
        </div>
        <div class="stack">${indexCards || '<div class="empty">还没有书籍索引。</div>'}</div>
      `;
    }

    function renderEntities(entities) {
      if (!entities.length) {
        els.entitiesPanel.innerHTML = '<div class="empty">这本书还没有实体索引。</div>';
        return;
      }
      els.entitiesPanel.innerHTML = entities.slice(0, 14).map((entity) => `
        <div class="entity-item">
          <strong>${escapeHtml(entity.name)}</strong>
          <div>首次出现：第 ${entity.first_chapter_number} 章</div>
          <div>提及次数：${entity.mention_count}</div>
          <div class="muted">${entity.aliases.length ? "别名：" + escapeHtml(entity.aliases.join("、")) : "无别名"}</div>
        </div>
      `).join("");
    }

    function renderChapters(chapters) {
      if (!chapters.length) {
        els.chaptersPanel.innerHTML = '<div class="empty">还没有章节索引。</div>';
        return;
      }
      els.chaptersPanel.innerHTML = chapters.map((chapter) => `
        <div class="entity-item">
          <strong>第 ${chapter.chapter_number} 章 ${escapeHtml(chapter.title)}</strong>
          <div class="muted">${escapeHtml((chapter.summary || "").slice(0, 70))}${(chapter.summary || "").length > 70 ? "..." : ""}</div>
        </div>
      `).join("");
    }

    function renderAnswer(card) {
      const evidenceHtml = (card.evidence || []).length
        ? `<div class="grid-two evidence">${card.evidence.map((item) => `
            <div class="evidence-item">
              <strong>第 ${item.chapter_number} 章《${escapeHtml(item.chapter_title)}》</strong>
              <p>${escapeHtml(item.excerpt)}</p>
              <div class="muted tiny">${escapeHtml(item.reason || "")}</div>
            </div>
          `).join("")}</div>`
        : '<div class="empty">这次没有足够证据片段。</div>';

      const suggestionsHtml = (card.suggestions || []).length
        ? `<div class="section"><span class="label">继续追问</span><div class="suggestions">${card.suggestions.map((item) => `
            <button class="suggestion-item ghost" type="button" data-question="${escapeHtml(item)}">${escapeHtml(item)}</button>
          `).join("")}</div></div>`
        : "";

      const runtime = card.runtime || {};
      els.answerCard.innerHTML = `
        <div class="answer-head">
          <span class="pill">类型：${escapeHtml(card.intent)}</span>
          <span class="pill">进度：第 ${card.progress_chapter} 章</span>
          ${card.entity_name ? `<span class="pill">实体：${escapeHtml(card.entity_name)}</span>` : ""}
          ${card.spoiler_blocked ? '<span class="pill warn">已阻断剧透</span>' : ""}
          ${runtime.retriever ? `<span class="pill">检索器：${escapeHtml(runtime.retriever)}</span>` : ""}
          ${runtime.cloud_reasoner_enabled ? `<span class="pill ready">云端：${escapeHtml(runtime.cloud_model)}</span>` : ""}
        </div>
        <p class="answer-text">${escapeHtml(card.answer)}</p>
        ${card.summary ? `<p class="muted">${escapeHtml(card.summary)}</p>` : ""}
        <div class="section"><span class="label">证据定位</span>${evidenceHtml}</div>
        ${suggestionsHtml}
        <details class="section">
          <summary class="label">原始 JSON</summary>
          <pre class="mono">${escapeHtml(JSON.stringify(card, null, 2))}</pre>
        </details>
      `;
      els.answerCard.querySelectorAll("[data-question]").forEach((btn) => {
        btn.addEventListener("click", () => {
          els.questionInput.value = btn.dataset.question || "";
          askQuestion();
        });
      });
    }

    function updatePills() {
      els.retrieverPill.textContent = `retriever: ${els.retrieverSelect.value}`;
      els.cloudPill.textContent = els.cloudEnabledInput.checked ? `cloud: ${els.apiModelInput.value || "on"}` : "cloud: off";
      els.cloudPill.className = `pill ${els.cloudEnabledInput.checked ? "ready" : ""}`;
    }

    function applyProvider(providerId) {
      const provider = state.providers[providerId];
      if (!provider) return;
      els.apiEndpointInput.value = provider.endpoint || "";
      els.apiModelInput.value = provider.model || "";
      updatePills();
    }

    function loadLocalApiSettings() {
      try {
        const raw = localStorage.getItem("bookrecall.apiSettings");
        if (!raw) return;
        const saved = JSON.parse(raw);
        els.providerSelect.value = saved.provider || "deepseek";
        els.apiEndpointInput.value = saved.endpoint || "";
        els.apiModelInput.value = saved.model || "";
        els.apiKeyInput.value = saved.apiKey || "";
        els.cloudEnabledInput.checked = Boolean(saved.enabled);
        els.rememberApiInput.checked = Boolean(saved.remember);
      } catch (_) {
        localStorage.removeItem("bookrecall.apiSettings");
      }
    }

    function saveLocalApiSettings() {
      const payload = {
        provider: els.providerSelect.value,
        endpoint: els.apiEndpointInput.value.trim(),
        model: els.apiModelInput.value.trim(),
        apiKey: els.apiKeyInput.value.trim(),
        enabled: els.cloudEnabledInput.checked,
        remember: els.rememberApiInput.checked
      };
      if (els.rememberApiInput.checked) {
        localStorage.setItem("bookrecall.apiSettings", JSON.stringify(payload));
        setStatus("已保存到当前浏览器 localStorage。");
      } else {
        localStorage.removeItem("bookrecall.apiSettings");
        setStatus("未勾选保存，已清除浏览器里的 API 设置。");
      }
      updatePills();
    }

    function buildCloudConfig() {
      return {
        enabled: els.cloudEnabledInput.checked,
        endpoint: els.apiEndpointInput.value.trim(),
        model: els.apiModelInput.value.trim(),
        api_key: els.apiKeyInput.value.trim()
      };
    }

    async function loadBooks() {
      const [booksData, runtime] = await Promise.all([
        requestJson("/api/books"),
        requestJson("/api/runtime")
      ]);
      state.books = booksData.books || [];
      state.runtime = runtime;
      state.providers = Object.fromEntries((runtime.cloud.providers || []).map((item) => [item.id, item]));
      renderBooks(state.books);
      renderModels(runtime);

      els.bookSelect.innerHTML = state.books.map((book) => `<option value="${escapeHtml(book.book_id)}">${escapeHtml(book.title)}</option>`).join("");
      if (state.books.length) {
        state.currentBookId = state.books[0].book_id;
        els.bookSelect.value = state.currentBookId;
        await loadBookDetails();
      } else {
        els.bookMeta.textContent = "暂无书籍。";
      }

      applyProvider("deepseek");
      if (runtime.cloud.env_key_available) {
        els.cloudPill.textContent = `cloud env: ${runtime.cloud.model}`;
      }
      loadLocalApiSettings();
      updatePills();
    }

    async function loadBookDetails() {
      const book = state.books.find((item) => item.book_id === state.currentBookId);
      if (!book) return;
      els.bookMeta.innerHTML = `
        <strong>${escapeHtml(book.title)}</strong>
        <div>章节 ${book.chapter_count} · 实体 ${book.entity_count}</div>
        <div class="muted mono">${escapeHtml(book.book_id)}</div>
      `;
      const [entitiesData, chaptersData, progressData] = await Promise.all([
        requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/entities`),
        requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/chapters?limit=60`),
        requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/progress?user=${encodeURIComponent(state.currentUserId)}`)
      ]);
      renderEntities(entitiesData.entities || []);
      renderChapters(chaptersData.chapters || []);
      els.progressInput.value = progressData.progress_chapter || progressData.max_chapter || "";
      setStatus(`已加载《${book.title}》。`);
    }

    async function saveProgress() {
      if (!state.currentBookId) return;
      const chapter = Number(els.progressInput.value);
      if (!chapter) {
        setStatus("请输入有效章节号。");
        return;
      }
      const saved = await requestJson("/api/progress", {
        method: "POST",
        body: JSON.stringify({
          book_id: state.currentBookId,
          user_id: state.currentUserId,
          progress_chapter: chapter
        })
      });
      els.progressInput.value = saved.progress_chapter;
      setStatus(`阅读进度已保存到第 ${saved.progress_chapter} 章。`);
    }

    async function askQuestion() {
      if (!state.currentBookId) {
        setStatus("请先选择一本书。");
        return;
      }
      const question = els.questionInput.value.trim();
      if (!question) {
        setStatus("先写一个问题吧。");
        return;
      }
      setStatus("Agent 正在规划工具调用与检索证据...");
      els.answerCard.innerHTML = '<div class="empty">正在唤醒记忆，请稍等。</div>';
      const payload = {
        book_id: state.currentBookId,
        user_id: state.currentUserId,
        question,
        progress_chapter: els.progressInput.value ? Number(els.progressInput.value) : null,
        retriever: els.retrieverSelect.value,
        cloud_config: buildCloudConfig()
      };
      const card = await requestJson("/api/ask", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      renderAnswer(card);
      setStatus("完成。");
    }

    els.bookSelect.addEventListener("change", async () => {
      state.currentBookId = els.bookSelect.value;
      await loadBookDetails();
    });
    els.userInput.addEventListener("change", async () => {
      state.currentUserId = els.userInput.value.trim() || "default";
      await loadBookDetails();
    });
    els.retrieverSelect.addEventListener("change", updatePills);
    els.cloudEnabledInput.addEventListener("change", updatePills);
    els.apiModelInput.addEventListener("input", updatePills);
    els.providerSelect.addEventListener("change", () => applyProvider(els.providerSelect.value));
    document.getElementById("saveProgressBtn").addEventListener("click", () => saveProgress().catch((err) => setStatus(err.message)));
    document.getElementById("askBtn").addEventListener("click", () => askQuestion().catch((err) => {
      setStatus(err.message);
      els.answerCard.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
    }));
    document.getElementById("saveApiBtn").addEventListener("click", saveLocalApiSettings);
    document.getElementById("clearApiBtn").addEventListener("click", () => {
      localStorage.removeItem("bookrecall.apiSettings");
      els.apiKeyInput.value = "";
      els.cloudEnabledInput.checked = false;
      els.rememberApiInput.checked = false;
      applyProvider(els.providerSelect.value);
      setStatus("已清除浏览器保存的 API 设置。");
    });

    loadBooks().catch((err) => {
      console.error(err);
      setStatus(err.message);
      els.bookMeta.textContent = "加载失败。";
    });
  </script>
</body>
</html>"""
