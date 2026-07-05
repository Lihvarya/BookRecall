<script setup lang="ts">
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import AgentTracePanel from "@/components/AgentTracePanel.vue";
import { useBookRecallStore } from "@/stores/bookrecall";
import type { EvidenceItem, SessionTurn, SessionTurnDiff } from "@/types";

const store = useBookRecallStore();
const { state, traceSummary } = storeToRefs(store);

const showTrace = ref(false);

const orderedTurns = computed(() => [...state.value.currentTurns].sort((a, b) => a.turn_index - b.turn_index));

const activeSession = computed(() => {
  return state.value.sessions.find((session) => session.session_id === state.value.currentSessionId);
});

const sessionHint = computed(() => {
  const turns = activeSession.value?.turn_count || orderedTurns.value.length;
  return `${turns} 轮对话`;
});

const latestCurrentQuestion = computed(() => {
  return orderedTurns.value[orderedTurns.value.length - 1]?.question || activeSession.value?.last_question || "";
});

const questionTemplates = [
  { name: "首次出现", tpl: "{entity}第一次出现在哪一章？" },
  { name: "后续轨迹", tpl: "{entity}后来还有出现过吗？" },
  { name: "关系回忆", tpl: "{entity}还和谁有关？" },
  { name: "主题变化", tpl: "{theme}前后有什么变化？" },
  { name: "关键事件", tpl: "{entity}涉及哪些关键事件？" }
];

function evidenceText(item: EvidenceItem) {
  return item.excerpt || item.child_text || "";
}

function listText(items: string[] | undefined) {
  return items?.length ? items.join("、") : "无";
}

function turnTools(turn: SessionTurn) {
  const names = (turn.trace || []).map((item) => item.tool_name).filter(Boolean) as string[];
  return names.length ? [...new Set(names)].join(" → ") : "无工具轨迹";
}

function diffStatusLabel(status: string) {
  const labels: Record<string, string> = {
    left_only: "仅左侧存在",
    right_only: "仅右侧存在",
    same_question: "问题相同",
    different_question: "问题不同"
  };
  return labels[status] || status;
}

function diffTools(items: string[] | undefined) {
  return items?.length ? items.join(" → ") : "无工具轨迹";
}

function answerExcerpt(turn: SessionTurn, side: "left" | "right") {
  const diff = state.value.sessionComparison?.turn_diffs?.find((item: SessionTurnDiff) => {
    return side === "left" ? item.left_turn_index === turn.turn_index : item.right_turn_index === turn.turn_index;
  });
  const excerpt = side === "left" ? diff?.left_answer_excerpt : diff?.right_answer_excerpt;
  return excerpt || turn.answer;
}

function run(action: () => Promise<void>) {
  action().catch((error: Error) => store.reportError(error, "对话操作失败"));
}

function sessionTitle(session: { session_id: string; last_question?: string }) {
  const source =
    session.session_id === state.value.currentSessionId
      ? latestCurrentQuestion.value || session.last_question || session.session_id
      : session.last_question || session.session_id || "新会话";
  return source.trim().slice(0, 10) || "新会话";
}

function askQuestion() {
  showTrace.value = true;
  run(() => store.askQuestion());
}

function turnAction(action: string, turn: SessionTurn) {
  store.handleSessionAction(action, turn).catch((error: Error) => store.reportError(error, "会话操作失败"));
}

function askWithSuggestion(suggestion: string) {
  state.value.form.question = suggestion;
  askQuestion();
}

</script>

<template>
  <section class="chat-page page-rise">
    <aside class="chat-session-rail">
      <div class="side-head">
        <div>
          <p class="eyebrow">Sessions</p>
          <strong>历史会话</strong>
        </div>
        <button class="primary-button compact-button" type="button" @click="store.startNewSession()">新会话</button>
      </div>
      <div class="session-current">
        <span class="pill">当前</span>
        <strong>{{ latestCurrentQuestion ? latestCurrentQuestion.slice(0, 10) : state.currentSessionId }}</strong>
        <small>{{ state.currentSessionId }}</small>
      </div>
      <button class="secondary-button w-full" type="button" @click="run(() => store.loadSessionList())">刷新会话</button>
      <div class="session-list">
        <button
          v-for="session in state.sessions"
          :key="session.session_id"
          :class="['session-item', session.session_id === state.currentSessionId ? 'session-item-active' : '']"
          type="button"
          @click="run(() => store.switchSession(session.session_id))"
        >
          <strong>{{ sessionTitle(session) }}</strong>
          <span>{{ session.turn_count || 0 }} 轮</span>
          <small>{{ session.last_question || session.session_id }}</small>
        </button>
      </div>
    </aside>

    <section class="chat-workspace">
      <div class="chat-hero">
        <div>
          <p class="eyebrow">BookRecall Agent</p>
          <h1>阅读记忆对话</h1>
          <p>默认延续当前会话继续追问；只有你点击“新会话”时，BookRecall 才会开启一条新的记忆线。</p>
        </div>
        <div class="chat-hero-actions">
          <span class="pill">{{ state.currentBookId || "未选择书籍" }}</span>
          <span class="pill">{{ sessionHint }}</span>
        </div>
      </div>

      <div class="chat-toolbar">
        <button class="ghost-button px-3 py-2 text-xs" type="button" @click="showTrace = !showTrace">
          {{ showTrace ? "收起轨迹" : `工具轨迹 ${traceSummary.count}` }}
        </button>
        <button class="ghost-button px-3 py-2 text-xs" type="button" @click="run(() => store.loadCurrentSessionDigest())">
          会话摘要
        </button>
        <button class="danger-button px-3 py-2 text-xs" type="button" @click="run(() => store.deleteCurrentSession())">
          清空当前会话
        </button>
      </div>

      <details class="session-popover">
        <summary>分支对比与合并</summary>
        <div class="side-head">
          <strong>选择两个会话分支</strong>
          <span class="pill">不会覆盖原会话</span>
        </div>
        <div class="branch-merge-card">
          <div class="compare-grid">
            <label>
              <span>左分支</span>
              <select v-model="state.form.compareLeft" class="field">
                <option v-for="session in state.sessions" :key="`left-${session.session_id}`" :value="session.session_id">
                  {{ session.session_id }}
                </option>
              </select>
            </label>
            <label>
              <span>右分支</span>
              <select v-model="state.form.compareRight" class="field">
                <option v-for="session in state.sessions" :key="`right-${session.session_id}`" :value="session.session_id">
                  {{ session.session_id }}
                </option>
              </select>
            </label>
          </div>
          <div class="branch-actions">
            <button class="secondary-button px-3 py-2 text-xs" type="button" @click="run(() => store.compareSessions())">
              对比分支
            </button>
            <button class="primary-button compact-button" type="button" @click="run(() => store.mergeComparedSessions())">
              合并为新会话
            </button>
          </div>
          <p v-if="state.sessionComparison?.summary" class="branch-summary">{{ state.sessionComparison.summary }}</p>
          <p v-if="state.sessionMerge?.summary" class="branch-summary">{{ state.sessionMerge.summary }}</p>
          <div v-if="state.sessionComparison" class="branch-compare-view">
            <div class="compare-metrics">
              <article>
                <strong>{{ state.sessionComparison.common_prefix_turns || 0 }}</strong>
                <span>共同前缀</span>
              </article>
              <article>
                <strong>{{ state.sessionComparison.divergence_turn || 1 }}</strong>
                <span>分歧轮次</span>
              </article>
              <article>
                <strong>{{ state.sessionComparison.left_unique_turns?.length || 0 }}</strong>
                <span>左侧独有</span>
              </article>
              <article>
                <strong>{{ state.sessionComparison.right_unique_turns?.length || 0 }}</strong>
                <span>右侧独有</span>
              </article>
            </div>
            <div class="compare-shared">
              <span>共同实体：{{ listText(state.sessionComparison.shared_entities) }}</span>
              <span>共同工具：{{ listText(state.sessionComparison.shared_tools) }}</span>
            </div>
            <div v-if="state.sessionComparison.diff_insights?.length" class="diff-insight-grid">
              <article v-for="insight in state.sessionComparison.diff_insights" :key="`${insight.kind}-${insight.title}`">
                <span>{{ insight.kind }}</span>
                <strong>{{ insight.title }}</strong>
                <p>{{ insight.detail }}</p>
              </article>
            </div>
            <div class="delta-grid">
              <article>
                <strong>实体差异</strong>
                <span>左侧独有：{{ listText(state.sessionComparison.entity_delta?.left_only) }}</span>
                <span>右侧独有：{{ listText(state.sessionComparison.entity_delta?.right_only) }}</span>
              </article>
              <article>
                <strong>工具差异</strong>
                <span>左侧独有：{{ listText(state.sessionComparison.tool_delta?.left_only) }}</span>
                <span>右侧独有：{{ listText(state.sessionComparison.tool_delta?.right_only) }}</span>
              </article>
            </div>
            <div v-if="state.sessionComparison.turn_diffs?.length" class="turn-diff-strip">
              <article v-for="diff in state.sessionComparison.turn_diffs" :key="`diff-${diff.offset}`">
                <span>差异 {{ diff.offset }} · {{ diffStatusLabel(diff.status) }}</span>
                <strong>{{ diff.left_question || "左侧无对应问题" }}</strong>
                <strong>{{ diff.right_question || "右侧无对应问题" }}</strong>
                <small>左侧工具：{{ diffTools(diff.left_tools) }}</small>
                <small>右侧工具：{{ diffTools(diff.right_tools) }}</small>
              </article>
            </div>
            <div class="branch-columns">
              <article class="branch-column">
                <header>
                  <strong>{{ state.sessionComparison.left_session_id || state.form.compareLeft }}</strong>
                  <small>{{ state.sessionComparison.left_turn_count || 0 }} 轮 · 实体 {{ listText(state.sessionComparison.left_entities) }}</small>
                  <small>工具 {{ listText(state.sessionComparison.left_tools) }}</small>
                </header>
                <div v-if="state.sessionComparison.left_unique_turns?.length" class="branch-turn-list">
                  <section v-for="turn in state.sessionComparison.left_unique_turns" :key="`left-turn-${turn.turn_id}`">
                    <span>第 {{ turn.turn_index }} 轮</span>
                    <h4>{{ turn.question }}</h4>
                    <p>{{ answerExcerpt(turn, "left") }}</p>
                    <small>{{ turn.summary || "无摘要" }}</small>
                    <em>{{ turnTools(turn) }}</em>
                  </section>
                </div>
                <div v-else class="empty-state">左分支没有独有轮次。</div>
              </article>
              <article class="branch-column">
                <header>
                  <strong>{{ state.sessionComparison.right_session_id || state.form.compareRight }}</strong>
                  <small>{{ state.sessionComparison.right_turn_count || 0 }} 轮 · 实体 {{ listText(state.sessionComparison.right_entities) }}</small>
                  <small>工具 {{ listText(state.sessionComparison.right_tools) }}</small>
                </header>
                <div v-if="state.sessionComparison.right_unique_turns?.length" class="branch-turn-list">
                  <section v-for="turn in state.sessionComparison.right_unique_turns" :key="`right-turn-${turn.turn_id}`">
                    <span>第 {{ turn.turn_index }} 轮</span>
                    <h4>{{ turn.question }}</h4>
                    <p>{{ answerExcerpt(turn, "right") }}</p>
                    <small>{{ turn.summary || "无摘要" }}</small>
                    <em>{{ turnTools(turn) }}</em>
                  </section>
                </div>
                <div v-else class="empty-state">右分支没有独有轮次。</div>
              </article>
            </div>
          </div>
        </div>
      </details>

      <div class="conversation">
        <section v-if="!orderedTurns.length && !state.answerCard" class="welcome-card">
          <p class="eyebrow">Hybrid Recall</p>
          <h2>像和读书搭子聊天一样，找回书里的某一刻</h2>
          <p>选择一本书和阅读进度后直接提问。BookRecall 会优先走结构化索引，再补证据片段，必要时调用云端模型总结。</p>
          <div class="template-row">
            <button
              v-for="item in questionTemplates"
              :key="item.name"
              class="secondary-button px-3 py-2 text-xs"
              type="button"
              @click="store.applyQuestionTemplate(item.tpl)"
            >
              {{ item.name }}
            </button>
          </div>
        </section>

        <article v-for="turn in orderedTurns" :key="turn.turn_id" class="turn-block">
          <div class="message user-message">
            <div class="avatar">你</div>
            <div class="bubble">
              <p>{{ turn.question }}</p>
              <small>第 {{ turn.turn_index }} 轮 · 进度 {{ turn.progress_chapter || state.form.progress || "?" }}</small>
            </div>
          </div>
          <div class="message assistant-message">
            <div class="avatar">B</div>
            <div class="bubble">
              <p>{{ turn.answer }}</p>
              <small>{{ turn.summary || "已写入当前会话记忆" }}</small>
              <div class="turn-actions">
                <button type="button" @click="turnAction('reask', turn)">重新提问</button>
                <button type="button" @click="turnAction('trace', turn)">查看轨迹</button>
                <button type="button" @click="turnAction('edit', turn)">编辑</button>
                <button type="button" @click="turnAction('branch', turn)">从此分支</button>
                <button type="button" @click="turnAction('rerun', turn)">重算</button>
                <button class="danger-link" type="button" @click="turnAction('delete', turn)">删除</button>
              </div>
            </div>
          </div>
        </article>

        <section v-if="state.answerCard?.evidence?.length" class="evidence-strip">
          <strong>本轮证据</strong>
          <div class="evidence-grid">
            <article v-for="item in state.answerCard.evidence" :key="`${item.chapter_number}-${evidenceText(item).slice(0, 20)}`">
              <span>第 {{ item.chapter_number }} 章</span>
              <p>{{ evidenceText(item) }}</p>
              <button type="button" @click="run(() => store.openChapter(item.chapter_number, evidenceText(item)))">打开原文</button>
            </article>
          </div>
        </section>

        <section v-if="state.answerCard?.suggestions?.length" class="suggestion-row">
          <button v-for="suggestion in state.answerCard.suggestions" :key="suggestion" type="button" @click="askWithSuggestion(suggestion)">
            {{ suggestion }}
          </button>
        </section>
      </div>

      <div v-if="state.sessionDigest" class="digest-card">
        <strong>会话摘要</strong>
        <p>{{ state.sessionDigest.synopsis }}</p>
        <small>轮次 {{ state.sessionDigest.turn_count }} · 实体 {{ (state.sessionDigest.entities || []).join("、") || "无" }}</small>
      </div>

      <AgentTracePanel v-show="showTrace" :trace="state.currentTrace" :summary="traceSummary" />

      <form class="composer" @submit.prevent="askQuestion">
        <textarea
          v-model="state.form.question"
          placeholder="问问这本书：某个人第一次出现在哪？后来还有没有出现？这条线索怎么发展？"
          :disabled="state.isAsking"
          @keydown.ctrl.enter.prevent="askQuestion"
          @keydown.meta.enter.prevent="askQuestion"
        ></textarea>
        <div class="composer-footer">
          <div class="composer-meta">
            <span>会话：{{ state.currentSessionId }}</span>
            <span>进度：第 {{ state.form.progress || "?" }} 章</span>
            <span>{{ state.status }}</span>
          </div>
          <button class="primary-button" type="submit" :disabled="state.isAsking">
            {{ state.isAsking ? "思考中..." : "发送" }}
          </button>
        </div>
      </form>
    </section>
  </section>
</template>
