const state = {
  books: [],
  runtime: null,
  providers: {},
  currentBookId: "",
  currentUserId: "default",
  currentSessionId: "default-session",
  entities: [],
  themes: []
};

const els = {
  bookSelect: document.getElementById("bookSelect"),
  userInput: document.getElementById("userInput"),
  sessionInput: document.getElementById("sessionInput"),
  progressInput: document.getElementById("progressInput"),
  questionInput: document.getElementById("questionInput"),
  statusBar: document.getElementById("statusBar"),
  bookMeta: document.getElementById("bookMeta"),
  booksPanel: document.getElementById("booksPanel"),
  statsPanel: document.getElementById("statsPanel"),
  entitiesPanel: document.getElementById("entitiesPanel"),
  themesPanel: document.getElementById("themesPanel"),
  eventsPanel: document.getElementById("eventsPanel"),
  relationsPanel: document.getElementById("relationsPanel"),
  chaptersPanel: document.getElementById("chaptersPanel"),
  sessionPanel: document.getElementById("sessionPanel"),
  tracePanel: document.getElementById("tracePanel"),
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
  rememberApiInput: document.getElementById("rememberApiInput"),
  buildBookIdInput: document.getElementById("buildBookIdInput"),
  buildTitleInput: document.getElementById("buildTitleInput"),
  buildTextInput: document.getElementById("buildTextInput"),
  buildEntitiesInput: document.getElementById("buildEntitiesInput"),
  buildThemesInput: document.getElementById("buildThemesInput"),
  buildOverwriteInput: document.getElementById("buildOverwriteInput"),
  buildResultPanel: document.getElementById("buildResultPanel"),
  vectorModelInput: document.getElementById("vectorModelInput"),
  vectorBackendSelect: document.getElementById("vectorBackendSelect"),
  vectorLimitInput: document.getElementById("vectorLimitInput"),
  vectorResultPanel: document.getElementById("vectorResultPanel")
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

function renderStats(stats) {
  const items = [
    ["章节", stats.chapters || 0],
    ["实体", stats.entities || 0],
    ["实体提及", stats.entity_mentions || 0],
    ["关系", stats.relations || 0],
    ["主题", stats.themes || 0],
    ["事件", stats.events || 0]
  ];
  els.statsPanel.innerHTML = items.map(([label, value]) => `
    <div class="stat-cell">
      <strong>${escapeHtml(value)}</strong>
      <span class="muted tiny">${escapeHtml(label)}</span>
    </div>
  `).join("");
}

function renderThemes(themes) {
  if (!themes.length) {
    els.themesPanel.innerHTML = '<div class="empty">这本书还没有主题索引。可在 build 时传入 --themes。</div>';
    return;
  }
  els.themesPanel.innerHTML = themes.slice(0, 12).map((theme) => `
    <div class="entity-item">
      <strong>${escapeHtml(theme.name)}</strong>
      <div>首次出现：第 ${theme.first_chapter_number} 章</div>
      <div>线索数：${theme.mention_count}</div>
      <div class="muted">${theme.aliases.length ? "别名：" + escapeHtml(theme.aliases.join("、")) : "无别名"}</div>
    </div>
  `).join("");
}

function renderEvents(events) {
  if (!events.length) {
    els.eventsPanel.innerHTML = '<div class="empty">这本书还没有事件链索引。</div>';
    return;
  }
  els.eventsPanel.innerHTML = events.slice(0, 12).map((event) => `
    <div class="entity-item">
      <div class="pill-row">
        <span class="pill">第 ${event.chapter_number} 章</span>
        <span class="pill">${escapeHtml(event.event_type)}</span>
      </div>
      <strong>${escapeHtml(event.summary)}</strong>
      <div class="muted tiny">${escapeHtml((event.entities || []).join("、"))}</div>
    </div>
  `).join("");
}

function renderRelations(relations) {
  if (!relations.length) {
    els.relationsPanel.innerHTML = '<div class="empty">这本书还没有关系索引。</div>';
    return;
  }
  els.relationsPanel.innerHTML = relations.slice(0, 12).map((relation) => `
    <div class="entity-item">
      <strong>${escapeHtml(relation.source_entity)} ↔ ${escapeHtml(relation.target_entity)}</strong>
      <div class="pill-row">
        <span class="pill">${escapeHtml(relation.relation_type)}</span>
        <span class="pill">第 ${relation.first_chapter_number} 章起</span>
        <span class="pill">${relation.mention_count} 条证据</span>
      </div>
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

function renderSession(turns) {
  if (!turns || !turns.length) {
    els.sessionPanel.innerHTML = '<div class="empty">当前会话还没有历史记录。</div>';
    return;
  }
  els.sessionPanel.innerHTML = turns.slice().reverse().map((turn) => `
    <div class="turn-item">
      <div class="pill-row">
        <span class="pill">第 ${escapeHtml(turn.turn_index)} 轮</span>
        ${turn.entity_name ? `<span class="pill">实体：${escapeHtml(turn.entity_name)}</span>` : ""}
        <span class="pill">进度：第 ${escapeHtml(turn.progress_chapter)} 章</span>
      </div>
      <p><strong>问：</strong>${escapeHtml(turn.question || "")}</p>
      <p><strong>答：</strong>${escapeHtml(turn.answer || "")}</p>
      ${turn.summary ? `<div class="muted tiny">${escapeHtml(turn.summary)}</div>` : ""}
    </div>
  `).join("");
}

function renderTrace(trace) {
  if (!trace || !trace.length) {
    els.tracePanel.innerHTML = '<div class="empty">提问后会显示本轮工具调用轨迹。</div>';
    return;
  }
  els.tracePanel.innerHTML = trace.map((item) => `
    <div class="trace-item">
      <div class="pill-row">
        <span class="pill">step ${escapeHtml(item.step)}</span>
        <span class="pill">${escapeHtml(item.tool_name || "unknown")}</span>
        ${item.spoiler_blocked ? '<span class="pill warn">触发防剧透</span>' : ""}
        <span class="pill">hits: ${escapeHtml(item.hit_count ?? 0)}</span>
      </div>
      <div class="tiny"><strong>thought</strong></div>
      <div class="muted">${escapeHtml(item.thought || "")}</div>
      <div class="tiny"><strong>arguments</strong></div>
      <code>${escapeHtml(JSON.stringify(item.arguments || {}, null, 2))}</code>
      <div class="tiny"><strong>observation</strong></div>
      <div class="muted">${escapeHtml(item.observation_summary || "")}</div>
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
  renderSession(card.session ? (card.session.turns || []) : []);
  renderTrace(card.trace || []);
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

function applyQuestionTemplate(template) {
  const entity = state.entities[0]?.name || "黑衣人";
  const theme = state.themes[0]?.name || "自由意志";
  els.questionInput.value = template
    .replaceAll("{entity}", entity)
    .replaceAll("{theme}", theme);
  els.questionInput.focus();
  setStatus("已填入快捷问题，可以直接提问。");
}

async function buildBookFromPanel() {
  const bookId = els.buildBookIdInput.value.trim();
  const text = els.buildTextInput.value;
  if (!bookId || !text.trim()) {
    els.buildResultPanel.textContent = "请至少填写 book_id 和书籍正文。";
    return;
  }
  els.buildResultPanel.textContent = "正在解析章节并构建本地结构化索引...";
  setStatus("正在建索引...");
  const data = await requestJson("/api/books/build", {
    method: "POST",
    body: JSON.stringify({
      book_id: bookId,
      title: els.buildTitleInput.value.trim(),
      text,
      entities: els.buildEntitiesInput.value,
      themes: els.buildThemesInput.value,
      overwrite: els.buildOverwriteInput.checked
    })
  });
  const built = data.book || {};
  els.buildResultPanel.innerHTML = `
    已创建：<strong>${escapeHtml(built.book_id)}</strong><br>
    章节 ${escapeHtml(built.chapter_count)} · 实体 ${escapeHtml(built.entities)}
    · 关系 ${escapeHtml(built.relations)} · 主题 ${escapeHtml(built.themes)}
    · 事件 ${escapeHtml(built.events)}
  `;
  await loadBooks(bookId);
  setStatus(`已完成《${built.title || bookId}》索引构建。`);
}

async function buildVectorIndexFromPanel() {
  if (!state.currentBookId) {
    els.vectorResultPanel.textContent = "请先选择一本书。";
    return;
  }
  els.vectorResultPanel.textContent = "正在加载 embedding 模型并构建向量索引...";
  setStatus("正在构建向量索引...");
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/vectors`, {
    method: "POST",
    body: JSON.stringify({
      model: els.vectorModelInput.value.trim(),
      backend: els.vectorBackendSelect.value,
      limit_chunks: els.vectorLimitInput.value ? Number(els.vectorLimitInput.value) : null
    })
  });
  const info = data.vector_index || {};
  els.vectorResultPanel.innerHTML = `
    已构建：<strong>${escapeHtml(info.book_id)}</strong><br>
    backend ${escapeHtml(info.backend)} · chunks ${escapeHtml(info.chunk_count)}
    · dim ${escapeHtml(info.dimension)}<br>
    <span class="mono">${escapeHtml(info.path || "")}</span>
  `;
  await loadBooks(state.currentBookId);
  setStatus("向量索引构建完成，可以切换到 embedding 或 auto 检索。");
}

async function loadBooks(preferredBookId = "") {
  const [booksData, runtime] = await Promise.all([
    requestJson("/api/books"),
    requestJson("/api/runtime")
  ]);
  state.books = booksData.books || [];
  state.runtime = runtime;
  state.providers = Object.fromEntries((runtime.cloud.providers || []).map((item) => [item.id, item]));
  renderBooks(state.books);
  renderModels(runtime);
  if (!els.vectorModelInput.value) {
    els.vectorModelInput.value = runtime.dependencies?.recommended_embedding_model || "BAAI/bge-small-zh-v1.5";
  }

  els.bookSelect.innerHTML = state.books.map((book) => `<option value="${escapeHtml(book.book_id)}">${escapeHtml(book.title)}</option>`).join("");
  if (state.books.length) {
    state.currentBookId = state.books.some((book) => book.book_id === preferredBookId)
      ? preferredBookId
      : state.books[0].book_id;
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
  const [entitiesData, chaptersData, progressData, sessionData, statsData, themesData, eventsData, relationsData] = await Promise.all([
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/entities`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/chapters?limit=60`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/progress?user=${encodeURIComponent(state.currentUserId)}`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/session?user=${encodeURIComponent(state.currentUserId)}&session=${encodeURIComponent(state.currentSessionId)}&limit=10`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/stats`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/themes`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/events?limit=20`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/relations?limit=40`)
  ]);
  state.entities = entitiesData.entities || [];
  state.themes = themesData.themes || [];
  renderEntities(entitiesData.entities || []);
  renderStats(statsData.stats || {});
  renderThemes(themesData.themes || []);
  renderEvents(eventsData.events || []);
  renderRelations(relationsData.relations || []);
  renderChapters(chaptersData.chapters || []);
  renderSession(sessionData.turns || []);
  renderTrace([]);
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
    session_id: state.currentSessionId,
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
els.sessionInput.addEventListener("change", () => {
  state.currentSessionId = els.sessionInput.value.trim() || "default-session";
  loadBookDetails().catch((err) => setStatus(err.message));
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
document.getElementById("buildBookBtn").addEventListener("click", () => buildBookFromPanel().catch((err) => {
  els.buildResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.getElementById("buildVectorBtn").addEventListener("click", () => buildVectorIndexFromPanel().catch((err) => {
  els.vectorResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.querySelectorAll("[data-template]").forEach((btn) => {
  btn.addEventListener("click", () => applyQuestionTemplate(btn.dataset.template || ""));
});

loadBooks().catch((err) => {
  console.error(err);
  setStatus(err.message);
  els.bookMeta.textContent = "加载失败。";
});
