import { defineStore } from "pinia";
import { computed, reactive } from "vue";
import { escapeHtml, postJson, requestJson } from "@/api";
import type {
  AnswerCard,
  AgentToolRun,
  AgentToolSchema,
  BookSummary,
  ChapterSummary,
  CloudProvider,
  DiagnosticsStatus,
  DynamicAuditRecord,
  DynamicAuditReviewResult,
  DynamicAuditStats,
  EntitySummary,
  EventSummary,
  IndexJob,
  ModelCheckResult,
  RelationSummary,
  RuntimeStatus,
  SearchResult,
  SessionComparison,
  SessionDigest,
  SessionMerge,
  SessionSummary,
  SessionTurn,
  ThemeSummary,
  TraceItem,
  VectorIndexSummary
} from "@/types";

const PREFS_KEY = "bookrecall.preferences";
const LEGACY_KEY = "bookrecall.apiSettings";

interface FormState {
  progress: string | number;
  question: string;
  bookGroup: string;
  bookTags: string;
  answerStyle: string;
  preferenceFocus: string;
  preferenceCustom: string;
  compareLeft: string;
  compareRight: string;
  buildBookId: string;
  buildTitle: string;
  buildText: string;
  buildEntities: string;
  buildThemes: string;
  buildOverwrite: boolean;
  smartIndexEnabled: boolean;
  localQwenEnabled: boolean;
  localQwenModelName: string;
  smartIndexModelPath: string;
  smartIndexEndpoint: string;
  smartIndexProfile: string;
  smartIndexMaxChapters: string | number;
  smartIndexBatchChapters: string | number;
  autoBuildVectorIndex: boolean;
  vectorModel: string;
  vectorBackend: string;
  vectorLimit: string | number;
  vectorBatchSize: string | number;
  rerankEnabled: boolean;
  rerankModel: string;
  rerankCandidates: string | number;
  searchQuery: string;
  searchLimit: string | number;
  policy: string;
  retriever: string;
  forceExactSearch: boolean;
  provider: string;
  apiEndpoint: string;
  apiModel: string;
  apiKey: string;
  cloudEnabled: boolean;
  rememberApi: boolean;
}

interface ReaderState {
  title: string;
  meta: string;
  content: string;
}

interface BookRecallState {
  books: BookSummary[];
  runtime: RuntimeStatus;
  diagnostics: DiagnosticsStatus | null;
  providers: Record<string, CloudProvider>;
  entities: EntitySummary[];
  themes: ThemeSummary[];
  events: EventSummary[];
  relations: RelationSummary[];
  dynamicAuditRecords: DynamicAuditRecord[];
  dynamicAuditStats: DynamicAuditStats;
  chapters: ChapterSummary[];
  stats: Record<string, number>;
  sessions: SessionSummary[];
  currentTurns: SessionTurn[];
  currentTrace: TraceItem[];
  agentTools: AgentToolSchema[];
  selectedToolName: string;
  toolArgumentsText: string;
  toolRunResult: AgentToolRun | null;
  currentBookId: string;
  currentUserId: string;
  currentSessionId: string;
  currentGroupFilter: string;
  importedBookText: string;
  importedBookSourceName: string;
  status: string;
  isAsking: boolean;
  isSearching: boolean;
  isIndexing: boolean;
  indexJob: IndexJob | null;
  modelChecks: Record<string, ModelCheckResult | null>;
  checkingModel: string;
  lastError: {
    message: string;
    context?: string;
    occurredAt: string;
    suggestions: string[];
  } | null;
  buildResult: string;
  vectorResult: string;
  searchResult: SearchResult | null;
  searchError: string;
  searchElapsedMs: number | null;
  sessionComparison: SessionComparison | null;
  sessionMerge: SessionMerge | null;
  sessionDigest: SessionDigest | null;
  answerCard: AnswerCard | null;
  reader: ReaderState;
  form: FormState;
}

interface ToolParameterMeta {
  required?: boolean;
}

interface BuildBookResult {
  book_id?: string;
  title?: string;
  chapter_count?: number;
  entities?: number;
  relations?: number;
  themes?: number;
  events?: number;
  vector_index?: {
    ok?: boolean;
    backend?: string;
    chunk_count?: number;
    error?: string;
  };
}

export const useBookRecallStore = defineStore("bookrecall", () => {
  const state = reactive<BookRecallState>({
    books: [] as BookSummary[],
    runtime: {} as RuntimeStatus,
    diagnostics: null as DiagnosticsStatus | null,
    providers: {} as Record<string, CloudProvider>,
    entities: [] as EntitySummary[],
    themes: [] as ThemeSummary[],
    events: [] as EventSummary[],
    relations: [] as RelationSummary[],
    dynamicAuditRecords: [] as DynamicAuditRecord[],
    dynamicAuditStats: {
      tracked: {},
      by_status: {},
      dynamic_totals: {},
      legacy_untracked: {},
      tracked_total: 0,
      legacy_untracked_total: 0,
      pending_total: 0,
      confirmed_total: 0,
      rejected_total: 0
    } as DynamicAuditStats,
    chapters: [] as ChapterSummary[],
    stats: {} as Record<string, number>,
    sessions: [] as SessionSummary[],
    currentTurns: [] as SessionTurn[],
    currentTrace: [] as TraceItem[],
    agentTools: [] as AgentToolSchema[],
    selectedToolName: "",
    toolArgumentsText: "{}",
    toolRunResult: null as AgentToolRun | null,
    currentBookId: "",
    currentUserId: "default",
    currentSessionId: "default-session",
    currentGroupFilter: "",
    importedBookText: "",
    importedBookSourceName: "",
    status: "系统准备就绪。",
    isAsking: false,
    isSearching: false,
    isIndexing: false,
    indexJob: null,
    modelChecks: {} as Record<string, ModelCheckResult | null>,
    checkingModel: "",
    lastError: null,
    buildResult: "推荐选择本地 TXT 文件导入；页面只显示文件摘要，不预览全文。",
    vectorResult: "默认架构：Qwen3-Embedding-0.6B 粗召回 + Qwen3-Reranker-0.6B 精排；旧索引需重建后才会切到 Qwen embedding。",
    searchResult: null as SearchResult | null,
    searchError: "",
    searchElapsedMs: null,
    sessionComparison: null as SessionComparison | null,
    sessionMerge: null as SessionMerge | null,
    sessionDigest: null as SessionDigest | null,
    answerCard: null as AnswerCard | null,
    reader: {
      title: "还没有打开章节",
      meta: "点击章节或证据定位",
      content: '<div class="empty-state">从章节浏览或回答证据卡片打开原文，匹配的证据片段会被高亮。</div>'
    },
    form: {
      progress: "",
      question: "",
      bookGroup: "",
      bookTags: "",
      answerStyle: "",
      preferenceFocus: "",
      preferenceCustom: "",
      compareLeft: "",
      compareRight: "",
      buildBookId: "",
      buildTitle: "",
      buildText: "",
      buildEntities: "",
      buildThemes: "",
      buildOverwrite: false,
      smartIndexEnabled: false,
      localQwenEnabled: true,
      localQwenModelName: "qwen3.5-4b",
      smartIndexModelPath: "D:\\BookRecall\\models\\llm\\qwen3-4b-instruct-2507-q4_k_m.gguf",
      smartIndexEndpoint: "",
      smartIndexProfile: "fast",
      smartIndexMaxChapters: "",
      smartIndexBatchChapters: 6,
      autoBuildVectorIndex: true,
      vectorModel: "Qwen/Qwen3-Embedding-0.6B",
      vectorBackend: "auto",
      vectorLimit: "",
      vectorBatchSize: 64,
      rerankEnabled: true,
      rerankModel: "Qwen/Qwen3-Reranker-0.6B",
      rerankCandidates: 6,
      searchQuery: "",
      searchLimit: 6,
      policy: "auto",
      retriever: "auto",
      forceExactSearch: false,
      provider: "deepseek",
      apiEndpoint: "",
      apiModel: "",
      apiKey: "",
      cloudEnabled: false,
      rememberApi: false
    } as FormState
  });

  const groups = computed(() => {
    const groupNames = state.books
      .map((book: BookSummary) => (book.book_group || "").trim())
      .filter((group): group is string => Boolean(group));
    return [...new Set<string>(groupNames)].sort((a, b) => a.localeCompare(b, "zh-CN"));
  });

  const visibleBooks = computed(() => {
    if (!state.currentGroupFilter) {
      return state.books;
    }
    if (state.currentGroupFilter === "__ungrouped__") {
      return state.books.filter((book: BookSummary) => !(book.book_group || "").trim());
    }
    return state.books.filter((book: BookSummary) => (book.book_group || "") === state.currentGroupFilter);
  });

  const currentBook = computed(() => state.books.find((book: BookSummary) => book.book_id === state.currentBookId));

  const currentVectorIndex = computed(() => {
    return (state.runtime.vector_indexes || []).find((item: VectorIndexSummary) => item.book_id === state.currentBookId);
  });

  const dependencyCards = computed(() => {
    const deps = state.runtime.dependencies || {};
    return [
      ["numpy", deps.numpy],
      ["sentence-transformers", deps.sentence_transformers],
      ["torch", deps.torch],
      ["faiss", deps.faiss],
      ["langgraph", deps.langgraph],
      ["llama-cpp", deps.llama_cpp]
    ] as Array<[string, unknown]>;
  });

  const traceSummary = computed(() => summarizeTrace(state.currentTrace));

  const frontendModeLabel = computed(() => {
    const mode = state.diagnostics?.frontend?.mode;
    if (mode === "vue_dist") {
      return "Vue 构建版";
    }
    if (mode === "legacy_static") {
      return "旧版静态回退";
    }
    return "未知";
  });

  function setStatus(message: string) {
    state.status = message;
  }

  function reportError(error: unknown, context = "操作失败") {
    const message = error instanceof Error ? error.message : String(error || "未知错误");
    state.lastError = {
      message,
      context,
      occurredAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      suggestions: recoverySuggestions(message, context)
    };
    setStatus(`${context}：${message}`);
  }

  function clearError() {
    state.lastError = null;
  }

  function recoverySuggestions(message: string, context = "") {
    const text = `${context} ${message}`.toLowerCase();
    const suggestions: string[] = [];
    if (/book|书|索引|没有找到/.test(text)) {
      suggestions.push("先在书库选择一本书；如果书籍不存在，请到“导入”页重新导入并构建索引。");
    }
    if (/faiss|numpy|sentence|transformer|langgraph|依赖|module|install/.test(text)) {
      suggestions.push("打开“设置与诊断”页查看依赖状态；缺失依赖需要在本地虚拟环境中安装后重启服务。");
    }
    if (/api|key|unauthorized|401|403|cloud|model|endpoint|deepseek|openai/.test(text)) {
      suggestions.push("检查云端模型开关、Endpoint、Model 和 API Key；如果只想本地检索，可先关闭云端推理。");
    }
    if (/json|参数|arguments|合法/.test(text)) {
      suggestions.push("检查输入参数是否为合法 JSON；工具箱参数可先恢复默认模板再重试。");
    }
    if (/session|会话|分支|merge|turn/.test(text)) {
      suggestions.push("刷新会话列表，确认左右分支不同且目标会话未被占用；必要时新建会话后再操作。");
    }
    if (/vector|向量|embedding|retriever/.test(text)) {
      suggestions.push("切换召回器为 lexical/auto，或在“模型与召回”页重建当前书的向量索引。");
    }
    if (/network|fetch|failed|请求失败|timeout|连接/.test(text)) {
      suggestions.push("确认 BookRecall 服务仍在运行，并刷新页面后重试当前操作。");
    }
    if (!suggestions.length) {
      suggestions.push("保留当前页面状态，检查输入后重试；如果重复出现，可到“设置与诊断”页查看系统状态。");
    }
    return suggestions.slice(0, 3);
  }

  function applyLocalPreferences(saved: Record<string, unknown>) {
    if (!saved) {
      return;
    }
    state.currentUserId = String(saved.user_id || state.currentUserId);
    state.currentSessionId = String(saved.session_id || state.currentSessionId);
    state.currentGroupFilter = String(saved.group_filter || state.currentGroupFilter);
    state.form.policy = String(saved.agent_policy || state.form.policy);
    state.form.retriever = String(saved.retriever || state.form.retriever);
    state.form.forceExactSearch = Boolean(saved.force_exact_search);
    state.form.provider = String(saved.provider || state.form.provider);
    state.form.apiEndpoint = String(saved.endpoint || state.form.apiEndpoint);
    state.form.apiModel = String(saved.model || state.form.apiModel);
    state.form.apiKey = String(saved.apiKey || "");
    state.form.cloudEnabled = Boolean(saved.cloud_enabled);
    state.form.rememberApi = Boolean(saved.remember_api);
    state.form.localQwenEnabled = saved.local_qwen_enabled === undefined ? state.form.localQwenEnabled : Boolean(saved.local_qwen_enabled);
    state.form.localQwenModelName = String(saved.local_qwen_model || state.form.localQwenModelName);
    state.form.smartIndexEnabled = Boolean(saved.smart_index_enabled);
    state.form.smartIndexModelPath = String(saved.smart_index_model_path || state.form.smartIndexModelPath);
    state.form.smartIndexEndpoint = String(saved.smart_index_endpoint || state.form.smartIndexEndpoint);
    state.form.smartIndexProfile = String(saved.smart_index_profile || state.form.smartIndexProfile);
    state.form.smartIndexMaxChapters = String(saved.smart_index_max_chapters || state.form.smartIndexMaxChapters);
    state.form.smartIndexBatchChapters = String(saved.smart_index_batch_chapters || state.form.smartIndexBatchChapters);
    state.form.autoBuildVectorIndex = saved.auto_build_vector_index === undefined ? state.form.autoBuildVectorIndex : Boolean(saved.auto_build_vector_index);
    state.form.vectorModel = String(saved.vector_model || state.form.vectorModel);
    state.form.vectorBackend = String(saved.vector_backend || state.form.vectorBackend);
    state.form.vectorBatchSize = String(saved.vector_batch_size || state.form.vectorBatchSize);
    state.form.rerankEnabled = saved.rerank_enabled === undefined ? state.form.rerankEnabled : Boolean(saved.rerank_enabled);
    state.form.rerankModel = String(saved.rerank_model || state.form.rerankModel);
    state.form.rerankCandidates = String(saved.rerank_candidates || state.form.rerankCandidates);
  }

  function saveLocalPreferences() {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({
        user_id: state.currentUserId,
        session_id: state.currentSessionId,
        last_book_id: state.currentBookId,
        group_filter: state.currentGroupFilter,
        agent_policy: state.form.policy,
        retriever: state.form.retriever,
        force_exact_search: state.form.forceExactSearch,
        provider: state.form.provider,
        endpoint: state.form.apiEndpoint,
        model: state.form.apiModel,
        apiKey: state.form.rememberApi ? state.form.apiKey : "",
        cloud_enabled: state.form.cloudEnabled,
        remember_api: state.form.rememberApi,
        local_qwen_enabled: state.form.localQwenEnabled,
        local_qwen_model: state.form.localQwenModelName,
        smart_index_enabled: state.form.smartIndexEnabled,
        smart_index_model_path: state.form.smartIndexModelPath,
        smart_index_endpoint: state.form.smartIndexEndpoint,
        smart_index_profile: state.form.smartIndexProfile,
        smart_index_max_chapters: state.form.smartIndexMaxChapters,
        smart_index_batch_chapters: state.form.smartIndexBatchChapters,
        auto_build_vector_index: state.form.autoBuildVectorIndex,
        vector_model: state.form.vectorModel,
        vector_backend: state.form.vectorBackend,
        vector_batch_size: state.form.vectorBatchSize,
        rerank_enabled: state.form.rerankEnabled,
        rerank_model: state.form.rerankModel,
        rerank_candidates: state.form.rerankCandidates
      })
    );
    localStorage.removeItem(LEGACY_KEY);
    setStatus("已保存控制台偏好到浏览器本地。");
  }

  function clearLocalPreferences() {
    localStorage.removeItem(PREFS_KEY);
    localStorage.removeItem(LEGACY_KEY);
    state.form.apiKey = "";
    state.form.cloudEnabled = false;
    state.form.rememberApi = false;
    applyProvider(state.form.provider);
    setStatus("已清除浏览器保存的控制台偏好。");
  }

  function applyProvider(providerId: string) {
    const provider = state.providers[providerId];
    if (!provider) {
      return;
    }
    state.form.apiEndpoint = provider.endpoint || "";
    state.form.apiModel = provider.model || "";
  }

  async function loadBooks(preferredId = "") {
    const [booksData, runtimeData] = await Promise.all([
      requestJson<{ books: BookSummary[] }>("/api/books"),
      requestJson<RuntimeStatus>("/api/runtime")
    ]);
    state.books = booksData.books || [];
    state.runtime = runtimeData || {};
    state.providers = Object.fromEntries(
      (runtimeData.cloud?.providers || []).map((item: CloudProvider) => [item.id, item])
    );

    let prefs: Record<string, unknown> = {};
    try {
      prefs = JSON.parse(localStorage.getItem(PREFS_KEY) || localStorage.getItem(LEGACY_KEY) || "{}");
    } catch {
      prefs = {};
    }
    applyLocalPreferences(prefs);
    if (!state.form.apiEndpoint && !state.form.apiModel) {
      applyProvider(state.form.provider);
    }

    const savedBookId = String(prefs.last_book_id || state.currentBookId || "");
    if (state.books.length) {
      state.currentBookId = state.books.some((book: BookSummary) => book.book_id === preferredId)
        ? preferredId
        : state.books.some((book: BookSummary) => book.book_id === savedBookId)
          ? savedBookId
          : state.books[0].book_id;
      await loadBookDetails();
    } else {
      setStatus("当前还没有书籍索引。");
    }
  }

  async function loadDiagnostics() {
    state.diagnostics = await requestJson<DiagnosticsStatus>("/api/diagnostics");
  }

  async function loadAgentTools() {
    const data = await requestJson<{ tools: AgentToolSchema[]; count: number }>("/api/agent/tools");
    state.agentTools = data.tools || [];
    if (!state.selectedToolName && state.agentTools.length) {
      state.selectedToolName = state.agentTools[0].name;
      state.toolArgumentsText = defaultArgumentsForTool(state.agentTools[0]);
    }
  }

  function selectAgentTool(toolName: string) {
    state.selectedToolName = toolName;
    const tool = state.agentTools.find((item: AgentToolSchema) => item.name === toolName);
    if (tool) {
      state.toolArgumentsText = defaultArgumentsForTool(tool);
    }
  }

  async function runSelectedAgentTool() {
    if (!state.currentBookId) {
      setStatus("请先选择一本书。");
      return;
    }
    if (!state.selectedToolName) {
      setStatus("请先选择一个 Agent 工具。");
      return;
    }
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(state.toolArgumentsText || "{}") as Record<string, unknown>;
    } catch {
      throw new Error("工具参数不是合法 JSON。");
    }
    const data = await postJson<{ tool_run: AgentToolRun }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/agent/tools/run`,
      {
        user_id: state.currentUserId,
        session_id: state.currentSessionId,
        tool_name: state.selectedToolName,
        arguments: args,
        question: state.form.question,
        progress_chapter: state.form.progress ? Number(state.form.progress) : null,
        retriever: state.form.retriever
      }
    );
    state.toolRunResult = data.tool_run;
    setStatus(`工具 ${state.selectedToolName} 执行完成。`);
  }

  async function loadBookDetails() {
    const book = currentBook.value;
    if (!book) {
      return;
    }
    state.form.bookGroup = book.book_group || "";
    state.form.bookTags = (book.tags || []).join(", ");
    const bookPath = `/api/books/${encodeURIComponent(state.currentBookId)}`;
    const [entities, chapters, progress, preferences, session, sessions, stats, themes, events, relations, dynamicAudit] =
      await Promise.all([
        requestJson<{ entities: EntitySummary[] }>(`${bookPath}/entities`),
        requestJson<{ chapters: ChapterSummary[] }>(`${bookPath}/chapters?limit=80`),
        requestJson<{ progress_chapter?: number; max_chapter?: number }>(
          `${bookPath}/progress?user=${encodeURIComponent(state.currentUserId)}`
        ),
        requestJson<{ preferences: Record<string, string> }>(
          `${bookPath}/preferences?user=${encodeURIComponent(state.currentUserId)}`
        ),
        requestJson<{ turns: SessionTurn[] }>(
          `${bookPath}/session?user=${encodeURIComponent(state.currentUserId)}&session=${encodeURIComponent(
            state.currentSessionId
          )}&limit=50`
        ),
        requestJson<{ sessions: SessionSummary[] }>(
          `${bookPath}/sessions?user=${encodeURIComponent(state.currentUserId)}&limit=50`
        ),
        requestJson<{ stats: Record<string, number> }>(`${bookPath}/stats`),
        requestJson<{ themes: ThemeSummary[] }>(`${bookPath}/themes`),
        requestJson<{ events: EventSummary[] }>(`${bookPath}/events?limit=20`),
        requestJson<{ relations: RelationSummary[] }>(`${bookPath}/relations?limit=40`),
        requestJson<{ records: DynamicAuditRecord[]; stats: DynamicAuditStats }>(
          `${bookPath}/dynamic-audit?limit=100`
        )
      ]);

    state.entities = entities.entities || [];
    state.chapters = chapters.chapters || [];
    state.form.progress = progress.progress_chapter || progress.max_chapter || "";
    state.form.answerStyle = preferences.preferences?.answer_style || "";
    state.form.preferenceFocus = preferences.preferences?.focus || "";
    state.form.preferenceCustom = preferences.preferences?.custom_prompt || "";
    state.currentTurns = session.turns || [];
    state.currentTrace = latestTurnTrace(state.currentTurns);
    state.sessions = sessions.sessions || [];
    state.stats = stats.stats || {};
    state.themes = themes.themes || [];
    state.events = events.events || [];
    state.relations = relations.relations || [];
    state.dynamicAuditRecords = dynamicAudit.records || [];
    state.dynamicAuditStats = dynamicAudit.stats || {
      tracked: {},
      by_status: {},
      dynamic_totals: {},
      legacy_untracked: {},
      tracked_total: 0,
      legacy_untracked_total: 0,
      pending_total: 0,
      confirmed_total: 0,
      rejected_total: 0
    };
    updateCompareOptions();
    setStatus(`已加载《${book.title}》。`);
  }

  async function reviewDynamicAudit(
    record: DynamicAuditRecord,
    action: "confirm" | "correct" | "reject",
    changes: { evidence?: string; summary?: string; confidence?: number; note?: string } = {}
  ) {
    if (!state.currentBookId) {
      throw new Error("请先选择一本书。");
    }
    const data = await postJson<{ review: DynamicAuditReviewResult }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/dynamic-audit/review`,
      {
        audit_id: record.audit_id,
        action,
        expected_review_version: record.review_version || 0,
        ...changes
      }
    );
    await loadBookDetails();
    const labels = { confirm: "已确认", correct: "已修正并退回待审核", reject: "已拒绝并安全清理" };
    setStatus(`动态索引记录${labels[action]}。`);
    return data.review;
  }

  function updateCompareOptions() {
    const ids = state.sessions.map((item: SessionSummary) => item.session_id);
    if (!ids.length) {
      state.form.compareLeft = "";
      state.form.compareRight = "";
      return;
    }
    if (!ids.includes(state.form.compareLeft)) {
      state.form.compareLeft = ids.includes(state.currentSessionId) ? state.currentSessionId : ids[0];
    }
    if (!ids.includes(state.form.compareRight) || state.form.compareRight === state.form.compareLeft) {
      state.form.compareRight = ids.find((id: string) => id !== state.form.compareLeft) || ids[0];
    }
  }

  async function loadSessionList() {
    if (!state.currentBookId) {
      return;
    }
    const data = await requestJson<{ sessions: SessionSummary[] }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/sessions?user=${encodeURIComponent(
        state.currentUserId
      )}&limit=50`
    );
    state.sessions = data.sessions || [];
    updateCompareOptions();
    setStatus("会话列表已刷新。");
  }

  async function loadCurrentSessionDigest() {
    if (!state.currentBookId || !state.currentSessionId) {
      return;
    }
    const data = await requestJson<{ digest: SessionDigest }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/session/digest?user=${encodeURIComponent(
        state.currentUserId
      )}&session=${encodeURIComponent(state.currentSessionId)}&limit=200`
    );
    state.sessionDigest = data.digest;
    setStatus("当前会话记忆摘要已刷新。");
  }

  async function deleteCurrentSession() {
    if (!state.currentBookId || !state.currentSessionId) {
      return;
    }
    if (!confirm(`清空当前会话「${state.currentSessionId}」的全部 Agent 记忆？`)) {
      return;
    }
    const data = await postJson<{ session: { deleted_turns: number } }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/session/delete`,
      {
        user_id: state.currentUserId,
        session_id: state.currentSessionId
      }
    );
    state.currentTurns = [];
    state.currentTrace = [];
    state.answerCard = null;
    state.sessionDigest = null;
    await loadSessionList();
    setStatus(`已清空当前会话记忆，共删除 ${data.session.deleted_turns} 轮。`);
  }

  async function switchSession(sessionId: string) {
    state.currentSessionId = sessionId;
    state.answerCard = null;
    state.sessionDigest = null;
    state.currentTrace = [];
    saveLocalPreferences();
    await loadBookDetails();
  }

  function startNewSession() {
    const stamp = new Date()
      .toISOString()
      .replace(/[-:]/g, "")
      .replace(/\..+$/, "")
      .replace("T", "-");
    state.currentSessionId = `session-${stamp}`;
    state.currentTurns = [];
    state.currentTrace = [];
    state.answerCard = null;
    state.sessionDigest = null;
    state.form.question = "";
    saveLocalPreferences();
    setStatus("已开启新会话。后续提问会写入这个新会话。");
  }

  async function saveProgress() {
    if (!state.currentBookId || !state.form.progress) {
      setStatus("请先选择书籍并填写阅读进度。");
      return;
    }
    const data = await postJson<{ progress_chapter: number }>("/api/progress", {
      book_id: state.currentBookId,
      user_id: state.currentUserId,
      progress_chapter: Number(state.form.progress)
    });
    state.form.progress = data.progress_chapter;
    setStatus(`进度保存到第 ${data.progress_chapter} 章。`);
  }

  async function askQuestion() {
    if (!state.currentBookId) {
      setStatus("请先选择一本书。");
      return;
    }
    const question = String(state.form.question).trim();
    if (!question) {
      setStatus("先写一个问题吧。");
      return;
    }
    if (state.isAsking) {
      setStatus("Agent 正在处理上一条问题，请稍等。");
      return;
    }
    setStatus("Agent 正在检索和规划...");
    state.answerCard = null;
    state.isAsking = true;
    state.form.question = "";
    const optimisticTurnId = -Date.now();
    const optimisticTurn: SessionTurn = {
      turn_id: optimisticTurnId,
      turn_index: nextTurnIndex(),
      question,
      answer: "正在思考...",
      summary: "BookRecall 正在规划工具调用、检索索引并组织回答。",
      progress_chapter: state.form.progress ? Number(state.form.progress) : undefined,
      matched_entities: [],
      trace: buildThinkingTrace("正在分析问题并选择工具")
    };
    state.currentTurns = [...state.currentTurns.filter((turn) => turn.turn_id >= 0), optimisticTurn];
    state.currentTrace = optimisticTurn.trace || [];
    try {
      const card = await postJson<AnswerCard>("/api/ask", {
        book_id: state.currentBookId,
        user_id: state.currentUserId,
        session_id: state.currentSessionId,
        question,
        progress_chapter: state.form.progress ? Number(state.form.progress) : null,
        agent_policy: state.form.policy,
        retriever: state.form.retriever,
        force_exact_search: state.form.forceExactSearch,
        cloud_config: {
          enabled: state.form.cloudEnabled,
          endpoint: state.form.apiEndpoint.trim(),
          model: state.form.apiModel.trim(),
          api_key: state.form.apiKey.trim()
        },
        local_llm_config: localQwenPayload(),
        rerank_config: rerankPayload()
      });
      state.answerCard = card;
      state.currentTrace = card.trace || [];
      if (card.session?.turns) {
        state.currentTurns = card.session.turns;
      } else {
        state.currentTurns = state.currentTurns.filter((turn) => turn.turn_id !== optimisticTurnId);
      }
      await loadSessionList();
      setStatus("回答完成。");
    } catch (error) {
      state.currentTurns = state.currentTurns.map((turn) =>
        turn.turn_id === optimisticTurnId
          ? {
              ...turn,
              answer: "这次请求没有成功完成。",
              summary: "请查看页面顶部错误提示，按恢复建议处理后重试。",
              trace: buildThinkingTrace("请求失败，等待用户处理")
            }
          : turn
      );
      state.currentTrace = buildThinkingTrace("请求失败，等待用户处理");
      throw error;
    } finally {
      state.isAsking = false;
    }
  }

  async function handleSessionAction(action: string, turn: SessionTurn) {
    if (!turn.turn_id) {
      return;
    }
    if (action === "reask") {
      state.form.question = turn.question || "";
      setStatus("问题已回填到输入框。");
      return;
    }
    if (action === "trace") {
      state.currentTrace = turn.trace || [];
      setStatus("已回放该轮工具轨迹。");
      return;
    }
    if (action === "delete") {
      if (!confirm("删除这轮对话？")) {
        return;
      }
      await postJson(`/api/books/${encodeURIComponent(state.currentBookId)}/session/turns/${turn.turn_id}`, {
        operation: "delete",
        user_id: state.currentUserId,
        session_id: state.currentSessionId
      });
      await loadBookDetails();
      setStatus("已删除该轮对话。");
      return;
    }
    if (action === "edit") {
      const question = prompt("问题：", turn.question || "");
      if (question === null) {
        return;
      }
      const answer = prompt("回答：", turn.answer || "");
      if (answer === null) {
        return;
      }
      const summary = prompt("摘要：", turn.summary || "");
      await postJson(`/api/books/${encodeURIComponent(state.currentBookId)}/session/turns/${turn.turn_id}`, {
        operation: "update",
        user_id: state.currentUserId,
        session_id: state.currentSessionId,
        question,
        answer,
        summary
      });
      await loadBookDetails();
      setStatus("该轮对话已保存。");
      return;
    }
    if (action === "rerun" || action === "branch") {
      const question = prompt(action === "rerun" ? "从此重算，可修改问题：" : "新建分支，可修改问题：", turn.question || "");
      if (question === null) {
        return;
      }
      if (action === "rerun" && !confirm("这会删除此轮及后续轮次并重算，继续吗？")) {
        return;
      }
      const payload = await postJson<AnswerCard & { branch?: { target_session_id?: string } }>(
        `/api/books/${encodeURIComponent(state.currentBookId)}/session/turns/${turn.turn_id}`,
        {
          operation: action,
          user_id: state.currentUserId,
          session_id: state.currentSessionId,
          question,
          progress_chapter: state.form.progress ? Number(state.form.progress) : null,
          agent_policy: state.form.policy,
          retriever: state.form.retriever,
          force_exact_search: state.form.forceExactSearch,
          cloud_config: {
            enabled: state.form.cloudEnabled,
            endpoint: state.form.apiEndpoint.trim(),
            model: state.form.apiModel.trim(),
            api_key: state.form.apiKey.trim()
          },
          local_llm_config: localQwenPayload(),
          rerank_config: rerankPayload()
        }
      );
      if (action === "branch" && payload.branch?.target_session_id) {
        state.currentSessionId = payload.branch.target_session_id;
      }
      state.answerCard = payload;
      await loadSessionList();
      await loadBookDetails();
      setStatus(action === "branch" ? "新分支已生成。" : "已从该轮重算。");
    }
  }

  function renderHighlightedContent(content: string, excerpt = "") {
    const raw = String(content || "");
    const needle = String(excerpt || "").replace(/\s+/g, " ").trim().slice(0, 80);
    if (!needle) {
      return `<pre>${escapeHtml(raw)}</pre>`;
    }
    let index = raw.indexOf(needle);
    let matched = needle;
    if (index < 0 && needle.length > 24) {
      matched = needle.slice(0, Math.max(24, Math.floor(needle.length / 2)));
      index = raw.indexOf(matched);
    }
    if (index < 0) {
      return `<pre>${escapeHtml(raw)}</pre>`;
    }
    return `<pre>${escapeHtml(raw.slice(0, index))}<mark>${escapeHtml(matched)}</mark>${escapeHtml(
      raw.slice(index + matched.length)
    )}</pre>`;
  }

  function focusReaderPanel() {
    if (window.location.hash !== "#index") {
      window.location.hash = "#index";
    }
    window.setTimeout(() => {
      document.getElementById("reader-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
  }

  async function openChapter(chapterNumber: number, excerpt = "") {
    if (!state.currentBookId || !chapterNumber) {
      return;
    }
    state.reader.title = `第 ${chapterNumber} 章加载中...`;
    state.reader.meta = "loading";
    state.reader.content = '<div class="empty-state">正在载入章节原文。</div>';
    focusReaderPanel();
    const data = await requestJson<{
      chapter: { chapter_number: number; title?: string; content: string };
    }>(`/api/books/${encodeURIComponent(state.currentBookId)}/chapters/${chapterNumber}`);
    state.reader.title = `第 ${data.chapter.chapter_number} 章 ${data.chapter.title || ""}`;
    state.reader.meta = excerpt ? "已尝试高亮证据片段" : "章节原文";
    state.reader.content = renderHighlightedContent(data.chapter.content || "", excerpt);
    focusReaderPanel();
    setStatus(`已打开第 ${data.chapter.chapter_number} 章原文。`);
  }

  async function readSelectedBookFile(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) {
      return;
    }
    const text = await file.text();
    state.importedBookText = text;
    state.importedBookSourceName = file.name;
    state.form.buildText = "";
    if (!state.form.buildBookId) {
      state.form.buildBookId = file.name.replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "_");
    }
    if (!state.form.buildTitle) {
      state.form.buildTitle = file.name.replace(/\.[^.]+$/, "");
    }
    state.buildResult = `已读取本地文件：${escapeHtml(file.name)} (${Math.round(
      file.size / 1024
    )} KB)。页面不预览全文，只保留导入内容用于建库。`;
    setStatus("TXT 文件已准备好。");
  }

  async function buildBookFromPanel() {
    setStatus("正在启动后台索引任务...");
    state.isIndexing = true;
    state.indexJob = null;
    try {
      const data = await postJson<{ job: IndexJob }>("/api/books/build-job", {
        book_id: state.form.buildBookId,
        title: state.form.buildTitle,
        text: state.importedBookText || state.form.buildText,
        entities: state.form.buildEntities,
        themes: state.form.buildThemes,
        overwrite: state.form.buildOverwrite,
        source_name: state.importedBookSourceName,
        smart_index: smartIndexPayload(),
        vector_index: vectorIndexPayload()
      });
      const job = await waitForIndexJob(data.job.job_id);
      const book = (job.result || {}) as BuildBookResult;
      state.buildResult = `创建成功：${book.book_id || state.form.buildBookId}，章节 ${book.chapter_count || 0}，实体 ${
        book.entities || 0
      }，主题 ${book.themes || 0}，关系 ${book.relations || 0}，事件 ${book.events || 0}。`;
      if (book.vector_index && typeof book.vector_index === "object") {
        const vector = book.vector_index as { ok?: boolean; backend?: string; chunk_count?: number; error?: string };
        state.buildResult += vector.ok
          ? ` 向量索引已构建：${vector.backend || "auto"}，${vector.chunk_count || 0} 个 chunk。`
          : ` 向量索引未完成：${vector.error || "未知错误"}。`;
      }
      await loadBooks(String(book.book_id || state.form.buildBookId));
      setStatus("书籍索引构建完成。");
    } catch (error) {
      state.isIndexing = false;
      throw error;
    }
  }

  async function rebuildCurrentBookIndex() {
    if (!state.currentBookId || !confirm("重建会覆盖当前书的结构化索引。继续？")) {
      return;
    }
    setStatus("正在启动结构化索引重建任务...");
    state.isIndexing = true;
    state.indexJob = null;
    try {
      const data = await postJson<{ job: IndexJob }>(`/api/books/${encodeURIComponent(state.currentBookId)}/rebuild-job`, {
        entities: state.form.buildEntities,
        themes: state.form.buildThemes,
        smart_index: smartIndexPayload()
      });
      const job = await waitForIndexJob(data.job.job_id);
      const book = job.result || {};
      state.buildResult = `重建完成：${book.book_id || state.currentBookId}，章节 ${book.chapter_count || 0}，实体 ${
        book.entities || 0
      }，关系 ${book.relations || 0}，事件 ${book.events || 0}。`;
      await loadBooks(state.currentBookId);
      setStatus("结构化索引已重建。");
    } catch (error) {
      state.isIndexing = false;
      throw error;
    }
  }

  async function waitForIndexJob(jobId: string) {
    try {
      while (true) {
        const data = await requestJson<{ job: IndexJob }>(`/api/jobs/${encodeURIComponent(jobId)}`);
        state.indexJob = data.job;
        const percent = Math.max(0, Math.min(100, Number(data.job.percent || 0)));
        setStatus(`${data.job.stage || "索引任务"}：${data.job.message || ""} ${percent}%`);
        if (data.job.status === "succeeded") {
          return data.job;
        }
        if (data.job.status === "failed") {
          throw new Error(data.job.error || data.job.message || "索引任务失败。");
        }
        await sleep(900);
      }
    } finally {
      state.isIndexing = false;
    }
  }

  function sleep(ms: number) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function smartIndexPayload() {
    const endpoint = state.form.smartIndexEndpoint.trim();
    return {
      enabled: state.form.smartIndexEnabled,
      model: state.form.localQwenModelName.trim(),
      model_path: state.form.smartIndexModelPath.trim(),
      endpoint,
      profile: state.form.smartIndexProfile,
      max_chapters: state.form.smartIndexMaxChapters ? Number(state.form.smartIndexMaxChapters) : 0,
      batch_chapters: state.form.smartIndexBatchChapters ? Number(state.form.smartIndexBatchChapters) : 0,
      n_ctx: 4096,
      max_tokens: state.form.smartIndexProfile === "fast" ? 1024 : 2048
    };
  }

  function localQwenPayload() {
    const endpoint = state.form.smartIndexEndpoint.trim();
    const modelPath = state.form.smartIndexModelPath.trim();
    return {
      ...smartIndexPayload(),
      enabled: state.form.localQwenEnabled && Boolean(endpoint || modelPath),
      model: state.form.localQwenModelName.trim(),
      model_path: endpoint ? "" : modelPath,
      endpoint,
      profile: "ondemand",
      max_tokens: 1536
    };
  }

  function vectorIndexPayload() {
    return {
      enabled: state.form.autoBuildVectorIndex,
      model: state.form.vectorModel,
      backend: state.form.vectorBackend,
      limit_chunks: state.form.vectorLimit ? Number(state.form.vectorLimit) : null,
      batch_size: state.form.vectorBatchSize ? Number(state.form.vectorBatchSize) : null
    };
  }

  function rerankPayload() {
    return {
      enabled: state.form.rerankEnabled,
      model: state.form.rerankModel.trim(),
      candidates: state.form.rerankCandidates ? Number(state.form.rerankCandidates) : 6,
      batch_size: 1,
      max_chars: 384,
      max_length: 512
    };
  }

  async function checkModel(component: "embedding" | "reranker" | "local_llm") {
    state.checkingModel = component;
    state.modelChecks[component] = null;
    setStatus(`正在自检 ${component}...`);
    const config =
      component === "embedding"
        ? { model: state.form.vectorModel.trim() }
        : component === "reranker"
          ? rerankPayload()
          : localQwenPayload();
    try {
      const data = await postJson<{ check: ModelCheckResult }>("/api/models/check", { component, config });
      state.modelChecks[component] = data.check;
      setStatus(data.check.ok ? `${component} 自检通过。` : `${component} 自检失败。`);
    } finally {
      state.checkingModel = "";
    }
  }

  async function deleteCurrentBook() {
    if (!state.currentBookId || !confirm("彻底删除当前书数据，操作不可逆。继续？")) {
      return;
    }
    await postJson(`/api/books/${encodeURIComponent(state.currentBookId)}/delete`, {});
    state.currentBookId = "";
    state.buildResult = "当前书已删除。";
    await loadBooks();
    setStatus("当前书数据已删除。");
  }

  async function buildVectorIndex() {
    if (!state.currentBookId) {
      return;
    }
    setStatus("正在构建向量索引...");
    const data = await postJson<{ vector_index: { chunk_count: number; backend: string } }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/vectors`,
      {
        model: state.form.vectorModel,
        backend: state.form.vectorBackend,
        limit_chunks: state.form.vectorLimit ? Number(state.form.vectorLimit) : null,
        batch_size: state.form.vectorBatchSize ? Number(state.form.vectorBatchSize) : null
      }
    );
    state.vectorResult = `成功构建 ${data.vector_index.chunk_count} chunks，后端 ${data.vector_index.backend}。`;
    await loadBooks(state.currentBookId);
    setStatus("向量索引已就绪。");
  }

  async function deleteCurrentVectorIndex() {
    if (!state.currentBookId || !confirm("删除当前书向量索引？结构化索引不会受影响。")) {
      return;
    }
    const data = await postJson<{ vector_index: { deleted_count?: number } }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/vectors/delete`,
      {}
    );
    state.vectorResult = `已删除向量索引文件 ${data.vector_index.deleted_count || 0} 个。`;
    await loadBooks(state.currentBookId);
    setStatus("当前书向量索引已删除。");
  }

  async function searchEvidence() {
    const query = state.form.searchQuery.trim();
    if (!state.currentBookId) {
      state.searchResult = { hits: [] };
      state.searchError = "请先选择一本书。";
      return;
    }
    if (!query) {
      state.searchResult = { hits: [] };
      state.searchError = "请输入需要检索的关键词或问题。";
      return;
    }
    state.isSearching = true;
    state.searchError = "";
    state.searchElapsedMs = null;
    state.searchResult = null;
    const startedAt = performance.now();
    setStatus("正在执行召回测试...");
    try {
      const data = await postJson<{ search: SearchResult }>(`/api/books/${encodeURIComponent(state.currentBookId)}/search`, {
        query,
        retriever: state.form.retriever,
        rerank_config: rerankPayload(),
        progress_chapter: state.form.progress ? Number(state.form.progress) : null,
        limit: state.form.searchLimit
      });
      state.searchResult = data.search || { hits: [] };
      const hitCount = state.searchResult.hits?.length || 0;
      setStatus(`召回测试完成，命中 ${hitCount} 条证据。`);
    } catch (error) {
      state.searchError = error instanceof Error ? error.message : String(error);
      throw error;
    } finally {
      state.searchElapsedMs = Math.round(performance.now() - startedAt);
      state.isSearching = false;
    }
  }

  async function saveUserPreferences() {
    if (!state.currentBookId) {
      return;
    }
    await postJson(`/api/books/${encodeURIComponent(state.currentBookId)}/preferences`, {
      user_id: state.currentUserId,
      answer_style: state.form.answerStyle,
      focus: state.form.preferenceFocus,
      custom_prompt: state.form.preferenceCustom
    });
    setStatus("长期回答偏好已保存。");
  }

  async function saveBookMetadata() {
    if (!state.currentBookId) {
      return;
    }
    await postJson(`/api/books/${encodeURIComponent(state.currentBookId)}/metadata`, {
      book_group: state.form.bookGroup,
      tags: state.form.bookTags
    });
    await loadBooks(state.currentBookId);
    setStatus("书籍分组与标签已保存。");
  }

  async function compareSessions() {
    if (!state.currentBookId || !state.form.compareLeft || !state.form.compareRight) {
      setStatus("请选择两个会话。");
      return;
    }
    if (state.form.compareLeft === state.form.compareRight) {
      setStatus("请选择两个不同会话进行对比。");
      return;
    }
    const data = await requestJson<{ comparison: SessionComparison }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/sessions/compare?user=${encodeURIComponent(
        state.currentUserId
      )}&left=${encodeURIComponent(state.form.compareLeft)}&right=${encodeURIComponent(state.form.compareRight)}&limit=100`
    );
    state.sessionComparison = data.comparison || {};
    setStatus("分支差异对比完成。");
  }

  async function mergeComparedSessions() {
    if (!state.currentBookId || !state.form.compareLeft || !state.form.compareRight) {
      setStatus("请选择两个要合并的会话。");
      return;
    }
    if (state.form.compareLeft === state.form.compareRight) {
      setStatus("请选择两个不同会话进行合并。");
      return;
    }
    const targetSessionId = `merged-${new Date()
      .toISOString()
      .replace(/[-:]/g, "")
      .replace(/\..+$/, "")
      .replace("T", "-")}`;
    const data = await postJson<{ merge: SessionMerge }>(
      `/api/books/${encodeURIComponent(state.currentBookId)}/sessions/merge`,
      {
        user_id: state.currentUserId,
        left_session_id: state.form.compareLeft,
        right_session_id: state.form.compareRight,
        target_session_id: targetSessionId,
        limit: 100
      }
    );
    state.sessionMerge = data.merge;
    state.currentSessionId = data.merge.target_session_id;
    state.currentTurns = data.merge.session?.turns || [];
    state.currentTrace = [];
    state.answerCard = null;
    state.sessionDigest = null;
    saveLocalPreferences();
    await loadSessionList();
    updateCompareOptions();
    setStatus(data.merge.summary || "分支已合并为新会话。");
  }

  function applyQuestionTemplate(template: string) {
    const entity = state.entities[0]?.name || "黑衣人";
    const theme = state.themes[0]?.name || "自由意志";
    state.form.question = template.replace("{entity}", entity).replace("{theme}", theme);
    setStatus("快捷问题已填入输入框。");
  }

  function summarizeTrace(trace: TraceItem[]) {
    const tools = trace.map((item) => item.tool_name).filter(Boolean) as string[];
    const blockedCount = trace.filter((item) => item.blocked_by_spoiler || item.spoiler_blocked).length;
    const totalElapsedMs = trace.reduce((total, item) => total + Number(item.elapsed_ms || 0), 0);
    const slowest = trace.reduce<TraceItem | null>((current, item) => {
      if (!current) {
        return item;
      }
      return Number(item.elapsed_ms || 0) > Number(current.elapsed_ms || 0) ? item : current;
    }, null);
    return {
      count: trace.length,
      tools,
      blockedCount,
      totalElapsedMs: Math.round(totalElapsedMs * 100) / 100,
      slowestTool: slowest?.tool_name || "",
      slowestElapsedMs: slowest?.elapsed_ms || 0
    };
  }

  function nextTurnIndex() {
    const indexes = state.currentTurns.map((turn: SessionTurn) => Number(turn.turn_index || 0));
    return Math.max(0, ...indexes) + 1;
  }

  function latestTurnTrace(turns: SessionTurn[]): TraceItem[] {
    const sorted = [...turns].sort((a, b) => Number(a.turn_index || 0) - Number(b.turn_index || 0));
    const latest = sorted[sorted.length - 1];
    return latest?.trace || [];
  }

  function buildThinkingTrace(summary: string): TraceItem[] {
    return [
      {
        step: 1,
        tool_name: "agent_planning",
        status: "running",
        observation_summary: summary,
        hit_count: 0,
        elapsed_ms: null
      },
      {
        step: 2,
        tool_name: "local_index_lookup",
        status: "pending",
        observation_summary: "准备查询实体索引、章节索引和召回层。",
        hit_count: 0,
        elapsed_ms: null
      },
      {
        step: 3,
        tool_name: "answer_synthesis",
        status: "pending",
        observation_summary: "等待证据返回后整理回答。",
        hit_count: 0,
        elapsed_ms: null
      }
    ];
  }

  function defaultArgumentsForTool(tool: AgentToolSchema) {
    const args: Record<string, unknown> = {};
    const parameters = (tool.parameters || {}) as Record<string, ToolParameterMeta>;
    for (const [name, meta] of Object.entries(parameters)) {
      if (name === "entity" || name === "source_entity") {
        args[name] = state.entities[0]?.name || "";
      } else if (name === "target_entity") {
        args[name] = state.entities[1]?.name || "";
      } else if (name === "theme") {
        args[name] = state.themes[0]?.name || "";
      } else if (name === "query") {
        args[name] = state.form.question || "";
      } else if (name === "chapter") {
        args[name] = Number(state.form.progress || 1);
      } else if (name === "max_chapter") {
        args[name] = Number(state.form.progress || 0) || undefined;
      } else if (meta.required) {
        args[name] = "";
      }
    }
    return JSON.stringify(args, null, 2);
  }

  return {
    state,
    groups,
    visibleBooks,
    currentBook,
    currentVectorIndex,
    dependencyCards,
    traceSummary,
    frontendModeLabel,
    setStatus,
    reportError,
    clearError,
    loadBooks,
    loadDiagnostics,
    loadAgentTools,
    selectAgentTool,
    runSelectedAgentTool,
    loadBookDetails,
    reviewDynamicAudit,
    loadSessionList,
    loadCurrentSessionDigest,
    deleteCurrentSession,
    switchSession,
    startNewSession,
    saveProgress,
    askQuestion,
    handleSessionAction,
    openChapter,
    readSelectedBookFile,
    buildBookFromPanel,
    rebuildCurrentBookIndex,
    deleteCurrentBook,
    buildVectorIndex,
    deleteCurrentVectorIndex,
    checkModel,
    searchEvidence,
    saveUserPreferences,
    saveBookMetadata,
    compareSessions,
    mergeComparedSessions,
    applyQuestionTemplate,
    saveLocalPreferences,
    clearLocalPreferences,
    applyProvider
  };
});
