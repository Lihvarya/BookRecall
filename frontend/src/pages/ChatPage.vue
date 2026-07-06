<script setup lang="ts">
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import AgentTracePanel from "@/components/AgentTracePanel.vue";
import { useBookRecallStore } from "@/stores/bookrecall";
import type { EvidenceItem, SessionTurn } from "@/types";

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
