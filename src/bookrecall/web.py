import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .agent import BookRecallAgent
from .storage import BookRecallStore


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
    ) -> dict[str, object]:
        store = self._open_store()
        try:
            agent = BookRecallAgent(store)
            card = agent.ask_card(
                book_id=book_id,
                question=question,
                user_id=user_id,
                progress_chapter=progress_chapter,
            )
            payload = agent.to_payload(card)
            payload["rendered_text"] = agent.render_text(card)
            return payload
        finally:
            store.close()


class BookRecallHandler(BaseHTTPRequestHandler):
    service: BookRecallWebService
    server_version = "BookRecallHTTP/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_html(_build_index_html())
            return

        if path == "/api/books":
            self._send_json({"books": self.service.list_books()})
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
        if parsed.path == "/api/ask":
            payload = self._read_json_body()
            if payload is None:
                return
            book_id = str(payload.get("book_id", "")).strip()
            question = str(payload.get("question", "")).strip()
            user_id = str(payload.get("user_id", "default")).strip() or "default"
            progress_raw = payload.get("progress_chapter")
            progress_chapter = int(progress_raw) if progress_raw not in (None, "") else None
            if not book_id or not question:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "book_id 和 question 不能为空。")
                return
            self._send_json(
                self.service.ask(
                    book_id=book_id,
                    question=question,
                    user_id=user_id,
                    progress_chapter=progress_chapter,
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
  <title>BookRecall</title>
  <style>
    :root {
      --paper: #f5efe2;
      --ink: #1d2731;
      --accent: #c65d2e;
      --accent-soft: #f0c9a8;
      --panel: rgba(255, 251, 244, 0.86);
      --line: rgba(35, 39, 42, 0.12);
      --shadow: 0 18px 48px rgba(58, 35, 16, 0.14);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Source Han Serif SC", "Noto Serif SC", Georgia, Cambria, serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(198, 93, 46, 0.18), transparent 28%),
        radial-gradient(circle at 90% 20%, rgba(83, 121, 145, 0.14), transparent 24%),
        linear-gradient(180deg, #efe3ca 0%, #f7f1e5 50%, #f1e5cf 100%);
      min-height: 100vh;
    }
    .shell {
      width: min(1180px, calc(100% - 32px));
      margin: 24px auto 48px;
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 20px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .sidebar { padding: 22px; }
    .main { padding: 24px; }
    h1, h2, h3 { margin: 0; font-weight: 700; }
    h1 { font-size: 34px; letter-spacing: 0.02em; }
    .subtitle {
      margin-top: 8px;
      font-size: 14px;
      line-height: 1.7;
      opacity: 0.86;
    }
    .section { margin-top: 24px; }
    .label {
      display: block;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
      margin-bottom: 8px;
      text-transform: uppercase;
      color: rgba(29,39,49,0.7);
    }
    select, input, textarea, button {
      width: 100%;
      border-radius: 16px;
      border: 1px solid var(--line);
      padding: 12px 14px;
      font: inherit;
      background: rgba(255,255,255,0.8);
      color: var(--ink);
    }
    textarea {
      min-height: 128px;
      resize: vertical;
      line-height: 1.6;
    }
    button {
      cursor: pointer;
      background: linear-gradient(135deg, #d46f3b, #a84422);
      color: #fff8ef;
      border: none;
      font-weight: 700;
      letter-spacing: 0.03em;
      box-shadow: 0 10px 22px rgba(154, 66, 33, 0.28);
    }
    button.secondary {
      background: linear-gradient(135deg, #54788c, #345062);
      box-shadow: 0 10px 22px rgba(52, 80, 98, 0.22);
    }
    .inline {
      display: grid;
      grid-template-columns: 1fr 140px;
      gap: 10px;
    }
    .books, .entities, .evidence, .suggestions {
      display: grid;
      gap: 10px;
    }
    .book-item, .entity-item, .evidence-item, .suggestion-item, .status {
      border: 1px solid rgba(29,39,49,0.1);
      background: rgba(255,255,255,0.74);
      border-radius: 18px;
      padding: 14px 16px;
    }
    .book-item strong, .entity-item strong {
      display: block;
      margin-bottom: 4px;
    }
    .status {
      margin-bottom: 16px;
      background: rgba(240, 201, 168, 0.35);
    }
    .answer-card {
      margin-top: 18px;
      padding: 22px;
      border-radius: 24px;
      background: linear-gradient(180deg, rgba(255,255,255,0.92), rgba(249,244,235,0.94));
      border: 1px solid rgba(29,39,49,0.1);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.6);
    }
    .answer-head {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 12px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(198, 93, 46, 0.12);
      color: #7f3618;
      font-size: 12px;
      font-weight: 700;
    }
    .answer-text {
      font-size: 19px;
      line-height: 1.7;
      margin: 10px 0 0;
    }
    .muted { opacity: 0.72; }
    .grid-two {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-top: 18px;
    }
    .empty {
      padding: 28px 18px;
      text-align: center;
      border: 1px dashed rgba(29,39,49,0.2);
      border-radius: 18px;
      color: rgba(29,39,49,0.62);
    }
    .mono {
      font-family: "Cascadia Code", Consolas, "SFMono-Regular", monospace;
      font-size: 12px;
    }
    @media (max-width: 960px) {
      .shell {
        grid-template-columns: 1fr;
      }
      .grid-two, .inline {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="panel sidebar">
      <h1>BookRecall</h1>
      <p class="subtitle">本地阅读记忆助手。选一本书，设置阅读进度，然后像和自己的记忆做交叉索引一样提问。</p>

      <div class="section">
        <label class="label" for="bookSelect">书籍</label>
        <select id="bookSelect"></select>
      </div>

      <div class="section">
        <label class="label" for="userInput">用户 ID</label>
        <input id="userInput" value="default">
      </div>

      <div class="section">
        <label class="label" for="progressInput">阅读进度（章）</label>
        <div class="inline">
          <input id="progressInput" type="number" min="1" step="1">
          <button class="secondary" id="saveProgressBtn" type="button">保存进度</button>
        </div>
      </div>

      <div class="section">
        <div class="status" id="bookMeta">正在加载书库...</div>
      </div>

      <div class="section">
        <span class="label">实体索引</span>
        <div class="entities" id="entitiesPanel"></div>
      </div>

      <div class="section">
        <details>
          <summary class="label" style="cursor:pointer;list-style:none">章节浏览（点击展开）</summary>
          <div class="chapters" id="chaptersPanel"></div>
        </details>
      </div>
    </aside>

    <main class="panel main">
      <div class="status" id="statusBar">准备就绪。</div>

      <label class="label" for="questionInput">提问</label>
      <textarea id="questionInput" placeholder="比如：黑衣人第一次出现在哪一章？或者：这本书里关于自由意志的观点前后有什么变化？"></textarea>

      <div class="section">
        <button id="askBtn" type="button">唤醒这段记忆</button>
      </div>

      <section class="answer-card" id="answerCard">
        <div class="empty">先选一本书并提一个问题。这里会显示结构化记忆卡片、证据片段和可继续追问的方向。</div>
      </section>

      <section class="section">
        <span class="label">书库总览</span>
        <div class="books" id="booksPanel"></div>
      </section>
    </main>
  </div>

  <script>
    const state = {
      books: [],
      currentBookId: "",
      currentUserId: "default"
    };

    const bookSelect = document.getElementById("bookSelect");
    const userInput = document.getElementById("userInput");
    const progressInput = document.getElementById("progressInput");
    const questionInput = document.getElementById("questionInput");
    const statusBar = document.getElementById("statusBar");
    const bookMeta = document.getElementById("bookMeta");
    const booksPanel = document.getElementById("booksPanel");
    const entitiesPanel = document.getElementById("entitiesPanel");
    const chaptersPanel = document.getElementById("chaptersPanel");
    const answerCard = document.getElementById("answerCard");

    function setStatus(text) {
      statusBar.textContent = text;
    }

    function escapeHtml(value) {
      return String(value)
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
        booksPanel.innerHTML = '<div class="empty">当前还没有已建索引的书。先用 CLI 执行 build。</div>';
        return;
      }
      booksPanel.innerHTML = books.map((book) => `
        <div class="book-item">
          <strong>${escapeHtml(book.title)}</strong>
          <div class="muted mono">${escapeHtml(book.book_id)}</div>
          <div>章节 ${book.chapter_count} · 实体 ${book.entity_count}</div>
          <div class="muted mono">${escapeHtml(book.source_path)}</div>
        </div>
      `).join("");
    }

    function renderEntities(entities) {
      if (!entities.length) {
        entitiesPanel.innerHTML = '<div class="empty">这本书还没有实体索引。</div>';
        return;
      }
      entitiesPanel.innerHTML = entities.slice(0, 12).map((entity) => `
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
        chaptersPanel.innerHTML = '<div class="empty">还没有章节索引。</div>';
        return;
      }
      chaptersPanel.innerHTML = chapters.map((chapter) => `
        <div class="entity-item">
          <strong>第 ${chapter.chapter_number} 章 ${escapeHtml(chapter.title)}</strong>
          <div class="muted">${escapeHtml((chapter.summary || "").slice(0, 60))}${(chapter.summary || "").length > 60 ? "..." : ""}</div>
        </div>
      `).join("");
    }

    function renderAnswer(card) {
      const evidenceHtml = (card.evidence || []).length
        ? `<div class="grid-two evidence">${card.evidence.map((item) => `
            <div class="evidence-item">
              <strong>第 ${item.chapter_number} 章《${escapeHtml(item.chapter_title)}》</strong>
              <div class="muted">${escapeHtml(item.reason)}</div>
              <p>${escapeHtml(item.excerpt)}</p>
            </div>
          `).join("")}</div>`
        : '<div class="empty">这次没有抓到可展示的证据片段。</div>';

      const suggestionsHtml = (card.suggestions || []).length
        ? `<div class="suggestions">${card.suggestions.map((item) => `
            <button class="suggestion-item" type="button" data-question="${escapeHtml(item)}">${escapeHtml(item)}</button>
          `).join("")}</div>`
        : "";

      answerCard.innerHTML = `
        <div class="answer-head">
          <span class="pill">${escapeHtml(card.intent)}</span>
          <span class="pill">已读至第 ${card.progress_chapter} 章</span>
          ${card.entity_name ? `<span class="pill">实体：${escapeHtml(card.entity_name)}</span>` : ""}
          ${card.spoiler_blocked ? `<span class="pill">防剧透已触发</span>` : ""}
        </div>
        <p class="answer-text">${escapeHtml(card.answer)}</p>
        ${card.summary ? `<p class="muted">${escapeHtml(card.summary)}</p>` : ""}
        <div class="section">
          <span class="label">证据片段</span>
          ${evidenceHtml}
        </div>
        <div class="section">
          <span class="label">继续追问</span>
          ${suggestionsHtml || '<div class="empty">当前没有推荐追问。</div>'}
        </div>
      `;

      answerCard.querySelectorAll("[data-question]").forEach((button) => {
        button.addEventListener("click", () => {
          questionInput.value = button.getAttribute("data-question") || "";
          askQuestion();
        });
      });
    }

    async function refreshBooks() {
      const data = await requestJson("/api/books");
      state.books = data.books || [];
      renderBooks(state.books);

      bookSelect.innerHTML = state.books.map((book) =>
        `<option value="${escapeHtml(book.book_id)}">${escapeHtml(book.title)} (${escapeHtml(book.book_id)})</option>`
      ).join("");

      if (state.books.length && !state.currentBookId) {
        state.currentBookId = state.books[0].book_id;
        bookSelect.value = state.currentBookId;
      }
      if (state.currentBookId) {
        await refreshCurrentBook();
      } else {
        bookMeta.textContent = "当前没有可用书籍。先用 CLI 建索引。";
      }
    }

    async function refreshCurrentBook() {
      state.currentBookId = bookSelect.value;
      state.currentUserId = userInput.value.trim() || "default";
      if (!state.currentBookId) {
        return;
      }

      const [progress, entityData, chapterData] = await Promise.all([
        requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/progress?user=${encodeURIComponent(state.currentUserId)}`),
        requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/entities`),
        requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/chapters?limit=30`)
      ]);

      progressInput.value = progress.progress_chapter || "";
      const book = state.books.find((item) => item.book_id === state.currentBookId);
      bookMeta.innerHTML = book
        ? `当前书籍：<strong>${escapeHtml(book.title)}</strong><br>来源：<span class="mono">${escapeHtml(book.source_path)}</span><br>总章节：${book.chapter_count}，当前进度：${progress.progress_chapter || "未设置"}`
        : "未找到当前书籍信息。";
      renderEntities(entityData.entities || []);
      renderChapters(chapterData.chapters || []);
    }

    async function saveProgress() {
      if (!state.currentBookId) {
        setStatus("请先选一本书。");
        return;
      }
      const progressValue = Number(progressInput.value);
      if (!progressValue) {
        setStatus("请输入有效的章节号。");
        return;
      }
      const payload = {
        book_id: state.currentBookId,
        user_id: userInput.value.trim() || "default",
        progress_chapter: progressValue
      };
      await requestJson("/api/progress", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      setStatus(`已保存 ${payload.user_id} 在 ${payload.book_id} 的阅读进度：第 ${progressValue} 章。`);
      await refreshCurrentBook();
    }

    async function askQuestion() {
      if (!state.currentBookId) {
        setStatus("请先选一本书。");
        return;
      }
      const question = questionInput.value.trim();
      if (!question) {
        setStatus("请输入一个问题。");
        return;
      }
      setStatus("正在检索章节、实体索引和已读范围内的证据...");
      const progressValue = progressInput.value ? Number(progressInput.value) : null;
      const payload = {
        book_id: state.currentBookId,
        user_id: userInput.value.trim() || "default",
        progress_chapter: Number.isFinite(progressValue) ? progressValue : null,
        question
      };
      const card = await requestJson("/api/ask", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      renderAnswer(card);
      setStatus("记忆卡片已更新。");
    }

    document.getElementById("askBtn").addEventListener("click", () => {
      askQuestion().catch((error) => setStatus(error.message));
    });
    document.getElementById("saveProgressBtn").addEventListener("click", () => {
      saveProgress().catch((error) => setStatus(error.message));
    });
    bookSelect.addEventListener("change", () => {
      refreshCurrentBook().catch((error) => setStatus(error.message));
    });
    userInput.addEventListener("change", () => {
      refreshCurrentBook().catch((error) => setStatus(error.message));
    });

    refreshBooks().catch((error) => {
      setStatus(error.message);
    });
  </script>
</body>
</html>
"""
