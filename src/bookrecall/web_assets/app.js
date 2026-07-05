const state = {
  books: [],
  runtime: null,
  providers: {},
  currentBookId: "",
  currentUserId: "default",
  currentSessionId: "default-session",
  currentGroupFilter: "",
  currentAgentPolicy: "auto",
  importedBookText: "",
  importedBookSourceName: "",
  currentTurns: [],
  currentSessions: [],
  entities: [],
  themes: []
};

const PREFERENCES_KEY = "bookrecall.preferences";
const LEGACY_API_SETTINGS_KEY = "bookrecall.apiSettings";

const els = {
  bookSelect: document.getElementById("bookSelect"),
  groupFilterSelect: document.getElementById("groupFilterSelect"),
  userInput: document.getElementById("userInput"),
  sessionInput: document.getElementById("sessionInput"),
  progressInput: document.getElementById("progressInput"),
  questionInput: document.getElementById("questionInput"),
  statusBar: document.getElementById("statusBar"),
  bookMeta: document.getElementById("bookMeta"),
  bookGroupInput: document.getElementById("bookGroupInput"),
  bookTagsInput: document.getElementById("bookTagsInput"),
  bookMetaResultPanel: document.getElementById("bookMetaResultPanel"),
  answerStyleSelect: document.getElementById("answerStyleSelect"),
  preferenceFocusInput: document.getElementById("preferenceFocusInput"),
  preferenceCustomInput: document.getElementById("preferenceCustomInput"),
  preferencesResultPanel: document.getElementById("preferencesResultPanel"),
  booksPanel: document.getElementById("booksPanel"),
  statsPanel: document.getElementById("statsPanel"),
  entitiesPanel: document.getElementById("entitiesPanel"),
  themesPanel: document.getElementById("themesPanel"),
  eventsPanel: document.getElementById("eventsPanel"),
  relationsPanel: document.getElementById("relationsPanel"),
  chaptersPanel: document.getElementById("chaptersPanel"),
  sessionListPanel: document.getElementById("sessionListPanel"),
  compareLeftSessionSelect: document.getElementById("compareLeftSessionSelect"),
  compareRightSessionSelect: document.getElementById("compareRightSessionSelect"),
  sessionComparePanel: document.getElementById("sessionComparePanel"),
  sessionPanel: document.getElementById("sessionPanel"),
  tracePanel: document.getElementById("tracePanel"),
  answerCard: document.getElementById("answerCard"),
  readerTitle: document.getElementById("readerTitle"),
  readerMeta: document.getElementById("readerMeta"),
  chapterReader: document.getElementById("chapterReader"),
  policySelect: document.getElementById("policySelect"),
  policyPill: document.getElementById("policyPill"),
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
  buildFileInput: document.getElementById("buildFileInput"),
  buildTextInput: document.getElementById("buildTextInput"),
  buildEntitiesInput: document.getElementById("buildEntitiesInput"),
  buildThemesInput: document.getElementById("buildThemesInput"),
  buildOverwriteInput: document.getElementById("buildOverwriteInput"),
  buildResultPanel: document.getElementById("buildResultPanel"),
  vectorModelInput: document.getElementById("vectorModelInput"),
  vectorBackendSelect: document.getElementById("vectorBackendSelect"),
  vectorLimitInput: document.getElementById("vectorLimitInput"),
  vectorResultPanel: document.getElementById("vectorResultPanel"),
  searchQueryInput: document.getElementById("searchQueryInput"),
  searchLimitInput: document.getElementById("searchLimitInput"),
  searchResultPanel: document.getElementById("searchResultPanel")
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

function excerptNeedle(excerpt) {
  return String(excerpt || "").replace(/\s+/g, " ").trim().slice(0, 80);
}

function renderHighlightedContent(content, excerpt = "") {
  const raw = String(content || "");
  const needle = excerptNeedle(excerpt);
  if (!needle) {
    return escapeHtml(raw);
  }
  let index = raw.indexOf(needle);
  let matched = needle;
  if (index < 0 && needle.length > 24) {
    matched = needle.slice(0, Math.max(24, Math.floor(needle.length / 2)));
    index = raw.indexOf(matched);
  }
  if (index < 0) {
    return escapeHtml(raw);
  }
  const before = raw.slice(0, index);
  const match = raw.slice(index, index + matched.length);
  const after = raw.slice(index + matched.length);
  return `${escapeHtml(before)}<mark>${escapeHtml(match)}</mark>${escapeHtml(after)}`;
}

async function openChapter(chapterNumber, excerpt = "") {
  if (!state.currentBookId || !chapterNumber) {
    return;
  }
  els.readerTitle.textContent = `正在打开第 ${chapterNumber} 章...`;
  els.readerMeta.textContent = "loading";
  els.chapterReader.innerHTML = '<div class="empty">正在载入章节原文。</div>';
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/chapters/${encodeURIComponent(chapterNumber)}`);
  const chapter = data.chapter || {};
  els.readerTitle.textContent = `第 ${chapter.chapter_number} 章 ${chapter.title || ""}`;
  els.readerMeta.textContent = excerpt ? "已尝试高亮证据片段" : "章节原文";
  els.chapterReader.innerHTML = `<pre>${renderHighlightedContent(chapter.content || "", excerpt)}</pre>`;
  document.getElementById("readerPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderBooks(books) {
  const visibleBooks = state.currentGroupFilter
    ? books.filter((book) => (
        state.currentGroupFilter === "__ungrouped__"
          ? !(book.book_group || "").trim()
          : (book.book_group || "") === state.currentGroupFilter
      ))
    : books;
  if (!visibleBooks.length) {
    els.booksPanel.innerHTML = '<div class="empty">当前还没有已建索引的书。先用 CLI 执行 build。</div>';
    return;
  }
  els.booksPanel.innerHTML = visibleBooks.map((book) => `
    <div class="book-item">
      <strong>${escapeHtml(book.title)}</strong>
      <div class="muted mono">${escapeHtml(book.book_id)}</div>
      <div>章节 ${book.chapter_count} · 实体 ${book.entity_count}</div>
      <div class="pill-row">
        <span class="pill">${escapeHtml(book.book_group || "未分组")}</span>
        ${(book.tags || []).map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`).join("")}
      </div>
      <div class="muted mono">${escapeHtml(book.source_path)}</div>
    </div>
  `).join("");
}

function renderGroupFilter(books) {
  const groups = [];
  for (const book of books) {
    const group = (book.book_group || "").trim();
    if (group && !groups.includes(group)) {
      groups.push(group);
    }
  }
  groups.sort((a, b) => a.localeCompare(b, "zh-CN"));
  els.groupFilterSelect.innerHTML = [
    '<option value="">全部分组</option>',
    '<option value="__ungrouped__">未分组</option>',
    ...groups.map((group) => `<option value="${escapeHtml(group)}">${escapeHtml(group)}</option>`)
  ].join("");
  if (state.currentGroupFilter === "__ungrouped__") {
    els.groupFilterSelect.value = "__ungrouped__";
  } else if (groups.includes(state.currentGroupFilter)) {
    els.groupFilterSelect.value = state.currentGroupFilter;
  } else {
    state.currentGroupFilter = "";
    els.groupFilterSelect.value = "";
  }
}

function renderModels(runtime) {
  const deps = runtime.dependencies || {};
  const depCards = [
    ["numpy", deps.numpy],
    ["sentence-transformers", deps.sentence_transformers],
    ["torch", deps.torch],
    ["faiss", deps.faiss],
    ["langgraph", deps.langgraph]
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

  const policyCards = (runtime.agent_policies || []).map((item) => `
    <div class="mini-card">
      <strong>${escapeHtml(item.name)}</strong>
      <span class="pill ${item.ready ? "ready" : "warn"}">${item.ready ? "可用" : "缺依赖"}</span>
    </div>
  `).join("");

  els.modelPanel.innerHTML = `
    <div class="stack">${depCards}</div>
    <div class="mini-card">
      <strong>推荐 embedding</strong>
      <div class="mono">${escapeHtml(deps.recommended_embedding_model || "BAAI/bge-small-zh-v1.5")}</div>
      <div class="muted mono">${escapeHtml(runtime.vector_dir)}</div>
    </div>
    <div class="stack">${policyCards || '<div class="empty">暂无 Agent 策略状态。</div>'}</div>
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
    <button class="entity-item chapter-link" type="button" data-chapter="${chapter.chapter_number}">
      <strong>第 ${chapter.chapter_number} 章 ${escapeHtml(chapter.title)}</strong>
      <div class="muted">${escapeHtml((chapter.summary || "").slice(0, 70))}${(chapter.summary || "").length > 70 ? "..." : ""}</div>
      <span class="pill">打开原文</span>
    </button>
  `).join("");
  els.chaptersPanel.querySelectorAll("[data-chapter]").forEach((item) => {
    item.addEventListener("click", () => openChapter(Number(item.dataset.chapter)).catch((err) => setStatus(err.message)));
  });
}

function renderSession(turns) {
  if (!turns || !turns.length) {
    state.currentTurns = [];
    els.sessionPanel.innerHTML = '<div class="empty">当前会话还没有历史记录。</div>';
    return;
  }
  state.currentTurns = turns;
  els.sessionPanel.innerHTML = turns.slice().reverse().map((turn) => `
    <div class="turn-item" data-turn-id="${escapeHtml(turn.turn_id || "")}">
      <div class="pill-row">
        <span class="pill">第 ${escapeHtml(turn.turn_index)} 轮</span>
        ${turn.entity_name ? `<span class="pill">实体：${escapeHtml(turn.entity_name)}</span>` : ""}
        <span class="pill">进度：第 ${escapeHtml(turn.progress_chapter)} 章</span>
      </div>
      <p><strong>问：</strong>${escapeHtml(turn.question || "")}</p>
      <p><strong>答：</strong>${escapeHtml(turn.answer || "")}</p>
      ${turn.summary ? `<div class="muted tiny">${escapeHtml(turn.summary)}</div>` : ""}
      <div class="inline-actions">
        <button class="ghost tiny" type="button" data-session-action="reask" data-turn-id="${escapeHtml(turn.turn_id || "")}" data-question="${escapeHtml(turn.question || "")}">重新提问</button>
        <button class="ghost tiny" type="button" data-session-action="rerun" data-turn-id="${escapeHtml(turn.turn_id || "")}" data-question="${escapeHtml(turn.question || "")}">从此重算</button>
        <button class="ghost tiny" type="button" data-session-action="branch" data-turn-id="${escapeHtml(turn.turn_id || "")}" data-question="${escapeHtml(turn.question || "")}">新建分支</button>
        <button class="ghost tiny" type="button" data-session-action="trace" data-turn-id="${escapeHtml(turn.turn_id || "")}">查看轨迹</button>
        <button class="ghost tiny" type="button" data-session-action="edit" data-turn-id="${escapeHtml(turn.turn_id || "")}" data-question="${escapeHtml(turn.question || "")}" data-answer="${escapeHtml(turn.answer || "")}" data-summary="${escapeHtml(turn.summary || "")}">编辑保存</button>
        <button class="ghost tiny danger-action" type="button" data-session-action="delete" data-turn-id="${escapeHtml(turn.turn_id || "")}">删除此轮</button>
      </div>
    </div>
  `).join("");
  els.sessionPanel.querySelectorAll("[data-session-action]").forEach((btn) => {
    btn.addEventListener("click", () => handleSessionAction(btn).catch((err) => setStatus(err.message)));
  });
}

function renderSessionList(sessions) {
  state.currentSessions = sessions || [];
  if (!sessions || !sessions.length) {
    renderSessionCompareOptions([]);
    els.sessionListPanel.innerHTML = '<div class="empty tiny">当前书籍还没有会话历史。提问后会自动出现在这里。</div>';
    return;
  }
  renderSessionCompareOptions(sessions);
  els.sessionListPanel.innerHTML = sessions.map((session) => {
    const active = session.session_id === state.currentSessionId;
    const lastQuestion = session.last_question || "暂无问题";
    const lastAnswer = session.last_summary || session.last_answer || "";
    return `
      <button class="session-item ${active ? "active" : ""}" type="button" data-session-id="${escapeHtml(session.session_id)}">
        <div class="pill-row">
          <span class="pill ${active ? "ready" : ""}">${active ? "当前" : "会话"}</span>
          <span class="pill">${escapeHtml(session.turn_count || 0)} 轮</span>
          <span class="pill">最近第 ${escapeHtml(session.last_turn_index || 0)} 轮</span>
        </div>
        <strong>${escapeHtml(session.session_id)}</strong>
        <div class="muted tiny">问：${escapeHtml(lastQuestion.slice(0, 70))}${lastQuestion.length > 70 ? "..." : ""}</div>
        ${lastAnswer ? `<div class="muted tiny">答：${escapeHtml(lastAnswer.slice(0, 70))}${lastAnswer.length > 70 ? "..." : ""}</div>` : ""}
        <div class="muted mono">${escapeHtml(session.updated_at || "")}</div>
      </button>
    `;
  }).join("");
  els.sessionListPanel.querySelectorAll("[data-session-id]").forEach((item) => {
    item.addEventListener("click", async () => {
      state.currentSessionId = item.dataset.sessionId || "default-session";
      els.sessionInput.value = state.currentSessionId;
      saveLocalPreferences();
      await loadBookDetails();
      setStatus(`已切换到会话：${state.currentSessionId}`);
    });
  });
}

function renderSessionCompareOptions(sessions) {
  const options = (sessions || []).map((session) => (
    `<option value="${escapeHtml(session.session_id)}">${escapeHtml(session.session_id)} · ${escapeHtml(session.turn_count || 0)} 轮</option>`
  )).join("");
  els.compareLeftSessionSelect.innerHTML = options;
  els.compareRightSessionSelect.innerHTML = options;
  if (!sessions || !sessions.length) {
    els.sessionComparePanel.innerHTML = '<div class="empty tiny">暂无可对比会话。</div>';
    return;
  }
  const sessionIds = sessions.map((session) => session.session_id);
  const currentIndex = Math.max(0, sessionIds.indexOf(state.currentSessionId));
  const fallbackRight = sessionIds.find((id) => id !== state.currentSessionId) || sessionIds[0];
  els.compareLeftSessionSelect.value = sessionIds.includes(els.compareLeftSessionSelect.value)
    ? els.compareLeftSessionSelect.value
    : sessionIds[currentIndex];
  els.compareRightSessionSelect.value = sessionIds.includes(els.compareRightSessionSelect.value)
    ? els.compareRightSessionSelect.value
    : fallbackRight;
}

function renderTurnDiffList(turns, label) {
  if (!turns || !turns.length) {
    return `<div class="empty tiny">${escapeHtml(label)}没有独有轮次。</div>`;
  }
  return turns.map((turn) => `
    <div class="turn-diff-item">
      <span class="pill">第 ${escapeHtml(turn.turn_index)} 轮</span>
      ${turn.entity_name ? `<span class="pill">实体：${escapeHtml(turn.entity_name)}</span>` : ""}
      <p><strong>问：</strong>${escapeHtml(turn.question || "")}</p>
      <p><strong>答：</strong>${escapeHtml((turn.answer || "").slice(0, 140))}${(turn.answer || "").length > 140 ? "..." : ""}</p>
    </div>
  `).join("");
}

function renderSessionComparison(comparison) {
  if (!comparison) {
    els.sessionComparePanel.innerHTML = '<div class="empty tiny">暂无会话对比结果。</div>';
    return;
  }
  const leftLabel = comparison.left_session_id || "左侧会话";
  const rightLabel = comparison.right_session_id || "右侧会话";
  els.sessionComparePanel.innerHTML = `
    <div class="compare-summary">
      <strong>${escapeHtml(comparison.summary || "分支对比完成。")}</strong>
      <div class="pill-row">
        <span class="pill ready">共同前缀 ${escapeHtml(comparison.common_prefix_turns || 0)} 轮</span>
        <span class="pill">分歧点：第 ${escapeHtml(comparison.divergence_turn || 1)} 轮</span>
        <span class="pill">${escapeHtml(leftLabel)}：${escapeHtml(comparison.left_turn_count || 0)} 轮</span>
        <span class="pill">${escapeHtml(rightLabel)}：${escapeHtml(comparison.right_turn_count || 0)} 轮</span>
      </div>
      <div class="grid-two">
        <div class="mini-card">
          <strong>${escapeHtml(leftLabel)} 独有</strong>
          <div class="muted tiny">实体：${escapeHtml((comparison.left_entities || []).join("、") || "无")}</div>
          <div class="muted tiny">工具：${escapeHtml((comparison.left_tools || []).join("、") || "无")}</div>
        </div>
        <div class="mini-card">
          <strong>${escapeHtml(rightLabel)} 独有</strong>
          <div class="muted tiny">实体：${escapeHtml((comparison.right_entities || []).join("、") || "无")}</div>
          <div class="muted tiny">工具：${escapeHtml((comparison.right_tools || []).join("、") || "无")}</div>
        </div>
      </div>
      <div class="muted tiny">共同实体：${escapeHtml((comparison.shared_entities || []).join("、") || "无")} · 共同工具：${escapeHtml((comparison.shared_tools || []).join("、") || "无")}</div>
    </div>
    <div class="grid-two">
      <div class="diff-column">
        <span class="label">${escapeHtml(leftLabel)} 的独有轮次</span>
        ${renderTurnDiffList(comparison.left_unique_turns || [], leftLabel)}
      </div>
      <div class="diff-column">
        <span class="label">${escapeHtml(rightLabel)} 的独有轮次</span>
        ${renderTurnDiffList(comparison.right_unique_turns || [], rightLabel)}
      </div>
    </div>
  `;
}

function summarizeTrace(trace) {
  const items = trace || [];
  const toolCounts = {};
  let totalHits = 0;
  let spoilerBlocks = 0;
  for (const item of items) {
    const tool = item.tool_name || "unknown";
    toolCounts[tool] = (toolCounts[tool] || 0) + 1;
    totalHits += Number(item.hit_count || 0);
    if (item.spoiler_blocked) {
      spoilerBlocks += 1;
    }
  }
  return {
    steps: items.length,
    totalHits,
    spoilerBlocks,
    toolCounts,
    path: items.map((item) => item.tool_name || "unknown")
  };
}

function renderTraceSummary(trace) {
  const summary = summarizeTrace(trace);
  const toolBadges = Object.entries(summary.toolCounts).map(([tool, count]) => (
    `<span class="pill">${escapeHtml(tool)} x${escapeHtml(count)}</span>`
  )).join("");
  const path = summary.path.length
    ? summary.path.map((tool, index) => `
        <span class="trace-node">
          <span class="mono">step ${index + 1}</span>
          <strong>${escapeHtml(tool)}</strong>
        </span>
      `).join('<span class="trace-arrow">→</span>')
    : '<span class="muted tiny">暂无工具路径</span>';
  return `
    <div class="trace-summary">
      <div class="pill-row">
        <span class="pill ready">工具步数 ${escapeHtml(summary.steps)}</span>
        <span class="pill">总命中 ${escapeHtml(summary.totalHits)}</span>
        <span class="pill ${summary.spoilerBlocks ? "warn" : ""}">防剧透 ${escapeHtml(summary.spoilerBlocks)}</span>
        <span class="pill">耗时：未采集</span>
      </div>
      <div class="trace-path">${path}</div>
      <div class="pill-row">${toolBadges || '<span class="pill">暂无工具调用</span>'}</div>
    </div>
  `;
}

function renderTrace(trace) {
  if (!trace || !trace.length) {
    els.tracePanel.innerHTML = '<div class="empty">提问后会显示本轮工具调用轨迹。</div>';
    return;
  }
  const detailHtml = trace.map((item) => `
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
  els.tracePanel.innerHTML = `${renderTraceSummary(trace)}${detailHtml}`;
}

function renderAppliedPreferences(preferences) {
  if (!preferences || !Object.keys(preferences).length) {
    return "";
  }
  const parts = [];
  if (preferences.answer_style) {
    parts.push(`风格：${preferences.answer_style}`);
  }
  if (preferences.focus) {
    parts.push(`关注：${preferences.focus}`);
  }
  if (preferences.custom_prompt) {
    parts.push(`自定义：${preferences.custom_prompt}`);
  }
  return parts.length
    ? `<div class="pill-row"><span class="pill ready">已应用长期偏好</span>${parts.map((part) => `<span class="pill">${escapeHtml(part)}</span>`).join("")}</div>`
    : "";
}

function renderAnswer(card) {
  const evidenceHtml = (card.evidence || []).length
    ? `<div class="grid-two evidence">${card.evidence.map((item) => `
        <div class="evidence-item">
          <strong>第 ${item.chapter_number} 章《${escapeHtml(item.chapter_title)}》</strong>
          <p>${escapeHtml(item.excerpt)}</p>
          <div class="muted tiny">${escapeHtml(item.reason || "")}</div>
          <button class="ghost tiny" type="button" data-open-chapter="${item.chapter_number}" data-excerpt="${escapeHtml(item.excerpt)}">查看原文并高亮</button>
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
      ${runtime.effective_policy ? `<span class="pill">策略：${escapeHtml(runtime.effective_policy)}</span>` : ""}
      ${runtime.retriever ? `<span class="pill">检索器：${escapeHtml(runtime.retriever)}</span>` : ""}
      ${runtime.cloud_reasoner_enabled ? `<span class="pill ready">云端：${escapeHtml(runtime.cloud_model)}</span>` : ""}
    </div>
    <p class="answer-text">${escapeHtml(card.answer)}</p>
    ${card.summary ? `<p class="muted">${escapeHtml(card.summary)}</p>` : ""}
    ${renderAppliedPreferences(card.user_preferences || {})}
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
  els.answerCard.querySelectorAll("[data-open-chapter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      openChapter(Number(btn.dataset.openChapter), btn.dataset.excerpt || "").catch((err) => setStatus(err.message));
    });
  });
  renderSession(card.session ? (card.session.turns || []) : []);
  renderTrace(card.trace || []);
}

function updatePills() {
  els.policyPill.textContent = `policy: ${els.policySelect.value}`;
  els.retrieverPill.textContent = `retriever: ${els.retrieverSelect.value}`;
  els.cloudPill.textContent = els.cloudEnabledInput.checked ? `cloud: ${els.apiModelInput.value || "on"}` : "cloud: off";
  els.cloudPill.className = `pill ${els.cloudEnabledInput.checked ? "ready" : ""}`;
}

function selectOptionIfExists(selectEl, value) {
  if (!value) {
    return;
  }
  if (Array.from(selectEl.options).some((option) => option.value === value)) {
    selectEl.value = value;
  }
}

function applyProvider(providerId) {
  const provider = state.providers[providerId];
  if (!provider) return;
  els.apiEndpointInput.value = provider.endpoint || "";
  els.apiModelInput.value = provider.model || "";
  updatePills();
}

function readLocalPreferences() {
  try {
    const raw = localStorage.getItem(PREFERENCES_KEY);
    if (raw) {
      return JSON.parse(raw);
    }
    const legacyRaw = localStorage.getItem(LEGACY_API_SETTINGS_KEY);
    if (!legacyRaw) {
      return {};
    }
    const legacy = JSON.parse(legacyRaw);
    return {
      provider: legacy.provider || "deepseek",
      endpoint: legacy.endpoint || "",
      model: legacy.model || "",
      apiKey: legacy.apiKey || "",
      cloud_enabled: Boolean(legacy.enabled),
      remember_api: Boolean(legacy.remember)
    };
  } catch (_) {
    localStorage.removeItem(PREFERENCES_KEY);
    return {};
  }
}

function applyLocalPreferences(saved) {
  if (!saved || typeof saved !== "object") {
    return;
  }
  state.currentUserId = saved.user_id || state.currentUserId;
  state.currentSessionId = saved.session_id || state.currentSessionId;
  state.currentGroupFilter = saved.group_filter || state.currentGroupFilter;
  state.currentBookId = saved.last_book_id || state.currentBookId;
  state.currentAgentPolicy = saved.agent_policy || state.currentAgentPolicy;
  els.userInput.value = state.currentUserId;
  els.sessionInput.value = state.currentSessionId;
  selectOptionIfExists(els.policySelect, state.currentAgentPolicy);
  selectOptionIfExists(els.retrieverSelect, saved.retriever);

  const providerId = saved.provider || "deepseek";
  selectOptionIfExists(els.providerSelect, providerId);
  if (saved.endpoint || saved.model || saved.provider === "custom") {
    els.apiEndpointInput.value = saved.endpoint || "";
    els.apiModelInput.value = saved.model || "";
  } else {
    applyProvider(providerId);
  }
  els.apiKeyInput.value = saved.apiKey || "";
  els.cloudEnabledInput.checked = Boolean(saved.cloud_enabled);
  els.rememberApiInput.checked = Boolean(saved.remember_api);
  updatePills();
}

function saveLocalPreferences() {
  const payload = {
    user_id: els.userInput.value.trim() || "default",
    session_id: els.sessionInput.value.trim() || "default-session",
    last_book_id: state.currentBookId || "",
    group_filter: els.groupFilterSelect.value || "",
    agent_policy: els.policySelect.value,
    retriever: els.retrieverSelect.value,
    provider: els.providerSelect.value,
    endpoint: els.apiEndpointInput.value.trim(),
    model: els.apiModelInput.value.trim(),
    apiKey: els.rememberApiInput.checked ? els.apiKeyInput.value.trim() : "",
    cloud_enabled: els.cloudEnabledInput.checked,
    remember_api: els.rememberApiInput.checked
  };
  localStorage.setItem(PREFERENCES_KEY, JSON.stringify(payload));
  localStorage.removeItem(LEGACY_API_SETTINGS_KEY);
  setStatus("已保存控制台偏好到当前浏览器 localStorage。");
  updatePills();
}

function saveLocalApiSettings() {
  saveLocalPreferences();
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
  const text = state.importedBookText || els.buildTextInput.value;
  if (!bookId || !text.trim()) {
    els.buildResultPanel.textContent = "请至少填写 book_id，并选择 TXT 文件或粘贴少量正文。";
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
      overwrite: els.buildOverwriteInput.checked,
      source_name: state.importedBookSourceName
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

async function readSelectedBookFile() {
  const file = els.buildFileInput.files?.[0];
  if (!file) {
    return;
  }
  if (!file.name.toLowerCase().endsWith(".txt") && file.type && file.type !== "text/plain") {
    setStatus("建议选择 TXT 纯文本文件。");
  }
  const text = await file.text();
  state.importedBookText = text;
  state.importedBookSourceName = file.name;
  els.buildTextInput.value = "";
  if (!els.buildBookIdInput.value.trim()) {
    els.buildBookIdInput.value = file.name.replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "_");
  }
  if (!els.buildTitleInput.value.trim()) {
    els.buildTitleInput.value = file.name.replace(/\.[^.]+$/, "");
  }
  const preview = text.slice(0, 500).replace(/\s+/g, " ").trim();
  els.buildResultPanel.innerHTML = `
    已读取本地文件：<strong>${escapeHtml(file.name)}</strong><br>
    大小：${escapeHtml(Math.round(file.size / 1024))} KB · 字符数：${escapeHtml(text.length)}<br>
    <span class="muted">仅显示开头预览，不把全文写入页面输入框：</span>
    <div class="file-preview">${escapeHtml(preview || "无可预览文本")}</div>
  `;
  setStatus("TXT 文件已读取，可以开始建索引。");
}

async function rebuildCurrentBookIndex() {
  if (!state.currentBookId) {
    setStatus("请先选择一本书。");
    return;
  }
  if (!window.confirm("重建会清空并重新生成当前书的结构化索引、事件、主题、关系和本书会话记忆。继续吗？")) {
    return;
  }
  els.buildResultPanel.textContent = "正在基于已保存章节重建结构化索引...";
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/rebuild`, {
    method: "POST",
    body: JSON.stringify({
      entities: els.buildEntitiesInput.value,
      themes: els.buildThemesInput.value
    })
  });
  const built = data.book || {};
  els.buildResultPanel.innerHTML = `
    已重建：<strong>${escapeHtml(built.book_id)}</strong><br>
    章节 ${escapeHtml(built.chapter_count)} · 实体 ${escapeHtml(built.entities)}
    · 关系 ${escapeHtml(built.relations)} · 主题 ${escapeHtml(built.themes)}
    · 事件 ${escapeHtml(built.events)}
  `;
  await loadBooks(state.currentBookId);
  setStatus("当前书结构化索引已重建。");
}

async function deleteCurrentBook() {
  if (!state.currentBookId) {
    setStatus("请先选择一本书。");
    return;
  }
  const book = state.books.find((item) => item.book_id === state.currentBookId);
  const label = book ? `${book.title} (${book.book_id})` : state.currentBookId;
  if (!window.confirm(`将删除《${label}》的本地书籍数据、结构化索引、阅读进度、会话记忆和向量索引。此操作不可撤销。继续吗？`)) {
    return;
  }
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/delete`, {
    method: "POST",
    body: JSON.stringify({})
  });
  els.buildResultPanel.textContent = `已删除 ${data.deleted?.book_id || label}。`;
  state.currentBookId = "";
  await loadBooks();
  setStatus("当前书本地数据已删除。");
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

async function deleteCurrentVectorIndex() {
  if (!state.currentBookId) {
    els.vectorResultPanel.textContent = "请先选择一本书。";
    return;
  }
  if (!window.confirm("只删除当前书的本地向量索引文件，不会删除书籍正文和结构化索引。继续吗？")) {
    return;
  }
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/vectors/delete`, {
    method: "POST",
    body: JSON.stringify({})
  });
  const info = data.vector_index || {};
  els.vectorResultPanel.textContent = `已删除 ${info.deleted_count || 0} 个向量索引文件。`;
  await loadBooks(state.currentBookId);
  setStatus("当前书向量索引已删除。");
}

function renderSearchResults(search) {
  const hits = search.hits || [];
  if (!hits.length) {
    els.searchResultPanel.innerHTML = '<div class="empty tiny">没有命中证据片段。可以换个问法，或确认阅读进度没有限制过严。</div>';
    return;
  }
  els.searchResultPanel.innerHTML = `
    <div class="pill-row">
      <span class="pill">retriever: ${escapeHtml(search.retriever)}</span>
      <span class="pill">effective: ${escapeHtml(search.effective_retriever)}</span>
      <span class="pill">hits: ${escapeHtml(hits.length)}</span>
    </div>
    ${hits.map((hit) => `
      <div class="search-hit">
        <div class="pill-row">
          <span class="pill">第 ${escapeHtml(hit.chapter_number)} 章</span>
          <span class="pill">score ${escapeHtml(Number(hit.score || 0).toFixed(4))}</span>
        </div>
        <strong>${escapeHtml(hit.chapter_title || "")}</strong>
        <p>${escapeHtml(hit.child_text || "")}</p>
        <button class="ghost tiny" type="button" data-open-search-hit="${escapeHtml(hit.chapter_number)}" data-excerpt="${escapeHtml(hit.child_text || "")}">打开原文并高亮</button>
      </div>
    `).join("")}
  `;
  els.searchResultPanel.querySelectorAll("[data-open-search-hit]").forEach((btn) => {
    btn.addEventListener("click", () => {
      openChapter(Number(btn.dataset.openSearchHit), btn.dataset.excerpt || "").catch((err) => setStatus(err.message));
    });
  });
}

async function searchEvidenceFromPanel() {
  if (!state.currentBookId) {
    els.searchResultPanel.innerHTML = '<div class="empty tiny">请先选择一本书。</div>';
    return;
  }
  const query = els.searchQueryInput.value.trim();
  if (!query) {
    els.searchResultPanel.innerHTML = '<div class="empty tiny">请输入要检索的问题或关键词。</div>';
    return;
  }
  els.searchResultPanel.innerHTML = '<div class="empty tiny">正在检索证据片段...</div>';
  setStatus("正在测试召回层...");
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/search`, {
    method: "POST",
    body: JSON.stringify({
      query,
      retriever: els.retrieverSelect.value,
      progress_chapter: els.progressInput.value ? Number(els.progressInput.value) : null,
      limit: els.searchLimitInput.value ? Number(els.searchLimitInput.value) : 6
    })
  });
  renderSearchResults(data.search || {});
  setStatus("召回层检索完成。");
}

async function loadSessionList() {
  if (!state.currentBookId) {
    renderSessionList([]);
    return;
  }
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/sessions?user=${encodeURIComponent(state.currentUserId)}&limit=50`);
  renderSessionList(data.sessions || []);
}

async function compareSessionsFromPanel() {
  if (!state.currentBookId) {
    setStatus("请先选择一本书。");
    return;
  }
  const left = els.compareLeftSessionSelect.value;
  const right = els.compareRightSessionSelect.value;
  if (!left || !right || left === right) {
    setStatus("请选择两个不同的会话进行对比。");
    return;
  }
  els.sessionComparePanel.innerHTML = '<div class="empty tiny">正在对比分支差异...</div>';
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/sessions/compare?user=${encodeURIComponent(state.currentUserId)}&left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}&limit=100`);
  renderSessionComparison(data.comparison || {});
  setStatus(`已对比 ${left} 与 ${right}。`);
}

function applyUserPreferences(preferences) {
  const data = preferences || {};
  els.answerStyleSelect.value = data.answer_style || "";
  els.preferenceFocusInput.value = data.focus || "";
  els.preferenceCustomInput.value = data.custom_prompt || "";
  els.preferencesResultPanel.textContent = data.updated_at ? `上次更新：${data.updated_at}` : "尚未保存长期偏好。";
}

async function saveUserPreferences() {
  if (!state.currentBookId) {
    setStatus("请先选择一本书。");
    return;
  }
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/preferences`, {
    method: "POST",
    body: JSON.stringify({
      user_id: state.currentUserId,
      answer_style: els.answerStyleSelect.value,
      focus: els.preferenceFocusInput.value,
      custom_prompt: els.preferenceCustomInput.value
    })
  });
  applyUserPreferences(data.preferences || {});
  setStatus("长期回答偏好已保存。");
}

async function loadBooks(preferredBookId = "") {
  const [booksData, runtime] = await Promise.all([
    requestJson("/api/books"),
    requestJson("/api/runtime")
  ]);
  const preferences = readLocalPreferences();
  state.books = booksData.books || [];
  state.runtime = runtime;
  state.providers = Object.fromEntries((runtime.cloud.providers || []).map((item) => [item.id, item]));
  applyLocalPreferences(preferences);
  renderGroupFilter(state.books);
  renderBooks(state.books);
  renderModels(runtime);
  if (!els.vectorModelInput.value) {
    els.vectorModelInput.value = runtime.dependencies?.recommended_embedding_model || "BAAI/bge-small-zh-v1.5";
  }

  els.bookSelect.innerHTML = state.books.map((book) => `<option value="${escapeHtml(book.book_id)}">${escapeHtml(book.title)}</option>`).join("");
  if (state.books.length) {
    const savedBookId = preferences.last_book_id || state.currentBookId;
    state.currentBookId = state.books.some((book) => book.book_id === preferredBookId)
      ? preferredBookId
      : state.books.some((book) => book.book_id === savedBookId)
        ? savedBookId
        : state.books[0].book_id;
    els.bookSelect.value = state.currentBookId;
    await loadBookDetails();
  } else {
    els.bookMeta.textContent = "暂无书籍。";
  }

  if (!preferences.provider && !els.apiEndpointInput.value && !els.apiModelInput.value) {
    applyProvider("deepseek");
  }
  if (runtime.cloud.env_key_available) {
    els.cloudPill.textContent = `cloud env: ${runtime.cloud.model}`;
  }
  updatePills();
}

async function loadBookDetails() {
  const book = state.books.find((item) => item.book_id === state.currentBookId);
  if (!book) return;
  els.bookMeta.innerHTML = `
      <strong>${escapeHtml(book.title)}</strong>
      <div>章节 ${book.chapter_count} · 实体 ${book.entity_count}</div>
      <div>分组：${escapeHtml(book.book_group || "未分组")}</div>
      <div>标签：${escapeHtml((book.tags || []).join("、") || "无")}</div>
      <div class="muted mono">${escapeHtml(book.book_id)}</div>
    `;
  els.bookGroupInput.value = book.book_group || "";
  els.bookTagsInput.value = (book.tags || []).join(", ");
  const [entitiesData, chaptersData, progressData, preferencesData, sessionData, sessionsData, statsData, themesData, eventsData, relationsData] = await Promise.all([
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/entities`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/chapters?limit=60`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/progress?user=${encodeURIComponent(state.currentUserId)}`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/preferences?user=${encodeURIComponent(state.currentUserId)}`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/session?user=${encodeURIComponent(state.currentUserId)}&session=${encodeURIComponent(state.currentSessionId)}&limit=50`),
    requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/sessions?user=${encodeURIComponent(state.currentUserId)}&limit=50`),
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
  applyUserPreferences(preferencesData.preferences || {});
  renderSession(sessionData.turns || []);
  renderSessionList(sessionsData.sessions || []);
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

async function saveBookMetadata() {
  if (!state.currentBookId) {
    setStatus("请先选择一本书。");
    return;
  }
  const payload = {
    book_group: els.bookGroupInput.value.trim(),
    tags: els.bookTagsInput.value
  };
  const data = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/metadata`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
  const book = data.book || {};
  els.bookMetaResultPanel.innerHTML = `
    已保存：<strong>${escapeHtml(book.book_group || "未分组")}</strong>
    ${book.tags?.length ? ` · ${escapeHtml(book.tags.join("、"))}` : ""}
  `;
  state.currentGroupFilter = book.book_group || "";
  await loadBooks(state.currentBookId);
  setStatus("书籍分组和标签已保存。");
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
    agent_policy: els.policySelect.value,
    retriever: els.retrieverSelect.value,
    cloud_config: buildCloudConfig()
  };
  const card = await requestJson("/api/ask", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  renderAnswer(card);
  await loadSessionList();
  setStatus("完成。");
}

async function handleSessionAction(btn) {
  const action = btn.dataset.sessionAction;
  const turnId = Number(btn.dataset.turnId);
  if (!turnId) {
    setStatus("这条历史记录缺少 turn_id，无法操作。");
    return;
  }
  if (action === "reask") {
    els.questionInput.value = btn.dataset.question || "";
    els.questionInput.focus();
    setStatus("已把历史问题放回输入框，可编辑后继续提问。");
    return;
  }
  if (action === "trace") {
    const turn = (state.currentTurns || []).find((item) => Number(item.turn_id) === turnId);
    if (!turn) {
      setStatus("没有找到这轮对话的 trace。");
      return;
    }
    renderTrace(turn.trace || []);
    setStatus(`已回放第 ${turn.turn_index} 轮工具轨迹。`);
    return;
  }
  if (action === "rerun") {
    const question = window.prompt("从这一轮开始重算。可先修改问题：", btn.dataset.question || "");
    if (question === null) return;
    if (!window.confirm("这会删除该轮及其后续会话历史，然后用这个问题重新提问。继续吗？")) {
      return;
    }
    setStatus("正在从历史轮次重新生成会话分支...");
    const card = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/session/turns/${encodeURIComponent(turnId)}`, {
      method: "POST",
      body: JSON.stringify({
        operation: "rerun",
        user_id: state.currentUserId,
        session_id: state.currentSessionId,
        question,
        progress_chapter: els.progressInput.value ? Number(els.progressInput.value) : null,
        agent_policy: els.policySelect.value,
        retriever: els.retrieverSelect.value,
        cloud_config: buildCloudConfig()
      })
    });
    renderAnswer(card);
    await loadSessionList();
    setStatus(`已从第 ${card.rerun?.from_turn_index || ""} 轮重算，删除后续 ${card.rerun?.deleted_turns || 0} 轮并生成新回答。`);
    return;
  }
  if (action === "branch") {
    const question = window.prompt("创建新会话分支。可先修改这一轮问题：", btn.dataset.question || "");
    if (question === null) return;
    setStatus("正在创建新的会话分支...");
    const card = await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/session/turns/${encodeURIComponent(turnId)}`, {
      method: "POST",
      body: JSON.stringify({
        operation: "branch",
        user_id: state.currentUserId,
        session_id: state.currentSessionId,
        question,
        progress_chapter: els.progressInput.value ? Number(els.progressInput.value) : null,
        agent_policy: els.policySelect.value,
        retriever: els.retrieverSelect.value,
        cloud_config: buildCloudConfig()
      })
    });
    const targetSession = card.branch?.target_session_id;
    if (targetSession) {
      state.currentSessionId = targetSession;
      els.sessionInput.value = targetSession;
    }
    renderAnswer(card);
    await loadSessionList();
    setStatus(`已创建新分支：${targetSession || "未命名分支"}。原会话未修改。`);
    return;
  }
  if (action === "delete") {
    if (!window.confirm("删除这轮对话历史？这不会影响书籍索引。")) {
      return;
    }
    await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/session/turns/${encodeURIComponent(turnId)}`, {
      method: "POST",
      body: JSON.stringify({
        operation: "delete",
        user_id: state.currentUserId,
        session_id: state.currentSessionId
      })
    });
    await loadBookDetails();
    setStatus("已删除该轮对话。");
    return;
  }
  if (action === "edit") {
    const question = window.prompt("编辑这一轮的问题：", btn.dataset.question || "");
    if (question === null) return;
    const answer = window.prompt("编辑这一轮的回答：", btn.dataset.answer || "");
    if (answer === null) return;
    const summary = window.prompt("编辑摘要（可留空）：", btn.dataset.summary || "");
    await requestJson(`/api/books/${encodeURIComponent(state.currentBookId)}/session/turns/${encodeURIComponent(turnId)}`, {
      method: "POST",
      body: JSON.stringify({
        operation: "update",
        user_id: state.currentUserId,
        session_id: state.currentSessionId,
        question,
        answer,
        summary
      })
    });
    await loadBookDetails();
    setStatus("已保存该轮对话修改。");
  }
}

els.bookSelect.addEventListener("change", async () => {
  state.currentBookId = els.bookSelect.value;
  await loadBookDetails();
});
els.groupFilterSelect.addEventListener("change", () => {
  state.currentGroupFilter = els.groupFilterSelect.value;
  renderBooks(state.books);
});
els.userInput.addEventListener("change", async () => {
  state.currentUserId = els.userInput.value.trim() || "default";
  await loadBookDetails();
});
els.sessionInput.addEventListener("change", () => {
  state.currentSessionId = els.sessionInput.value.trim() || "default-session";
  loadBookDetails().catch((err) => setStatus(err.message));
});
els.policySelect.addEventListener("change", updatePills);
els.retrieverSelect.addEventListener("change", updatePills);
els.cloudEnabledInput.addEventListener("change", updatePills);
els.apiModelInput.addEventListener("input", updatePills);
els.providerSelect.addEventListener("change", () => applyProvider(els.providerSelect.value));
els.buildFileInput.addEventListener("change", () => readSelectedBookFile().catch((err) => setStatus(err.message)));
els.buildTextInput.addEventListener("input", () => {
  if (els.buildTextInput.value.trim()) {
    state.importedBookText = "";
    state.importedBookSourceName = "";
  }
});
document.getElementById("saveProgressBtn").addEventListener("click", () => saveProgress().catch((err) => setStatus(err.message)));
document.getElementById("refreshSessionsBtn").addEventListener("click", () => loadSessionList().catch((err) => setStatus(err.message)));
document.getElementById("compareSessionsBtn").addEventListener("click", () => compareSessionsFromPanel().catch((err) => {
  els.sessionComparePanel.innerHTML = `<div class="empty tiny">${escapeHtml(err.message)}</div>`;
  setStatus(err.message);
}));
document.getElementById("savePreferencesBtn").addEventListener("click", () => saveUserPreferences().catch((err) => {
  els.preferencesResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.getElementById("saveBookMetaBtn").addEventListener("click", () => saveBookMetadata().catch((err) => {
  els.bookMetaResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.getElementById("askBtn").addEventListener("click", () => askQuestion().catch((err) => {
  setStatus(err.message);
  els.answerCard.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
}));
document.getElementById("saveApiBtn").addEventListener("click", saveLocalApiSettings);
document.getElementById("clearApiBtn").addEventListener("click", () => {
  localStorage.removeItem(PREFERENCES_KEY);
  localStorage.removeItem(LEGACY_API_SETTINGS_KEY);
  els.apiKeyInput.value = "";
  els.cloudEnabledInput.checked = false;
  els.rememberApiInput.checked = false;
  applyProvider(els.providerSelect.value);
  setStatus("已清除浏览器保存的控制台偏好。");
});
document.getElementById("buildBookBtn").addEventListener("click", () => buildBookFromPanel().catch((err) => {
  els.buildResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.getElementById("rebuildBookBtn").addEventListener("click", () => rebuildCurrentBookIndex().catch((err) => {
  els.buildResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.getElementById("deleteBookBtn").addEventListener("click", () => deleteCurrentBook().catch((err) => {
  els.buildResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.getElementById("buildVectorBtn").addEventListener("click", () => buildVectorIndexFromPanel().catch((err) => {
  els.vectorResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.getElementById("deleteVectorBtn").addEventListener("click", () => deleteCurrentVectorIndex().catch((err) => {
  els.vectorResultPanel.textContent = err.message;
  setStatus(err.message);
}));
document.getElementById("searchEvidenceBtn").addEventListener("click", () => searchEvidenceFromPanel().catch((err) => {
  els.searchResultPanel.innerHTML = `<div class="empty tiny">${escapeHtml(err.message)}</div>`;
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
