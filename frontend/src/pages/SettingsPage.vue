<script setup lang="ts">
import { computed } from "vue";
import { storeToRefs } from "pinia";
import { useBookRecallStore } from "@/stores/bookrecall";

const store = useBookRecallStore();
const { state, frontendModeLabel } = storeToRefs(store);

const selectedToolDescription = computed(() => {
  return (
    state.value.agentTools.find((tool) => tool.name === state.value.selectedToolName)?.description ||
    "选择工具后可查看说明。"
  );
});

function run(action: () => Promise<void>) {
  action().catch((error: Error) => store.reportError(error, "设置操作失败"));
}

function onToolSelect(event: Event) {
  const target = event.target as HTMLSelectElement;
  store.selectAgentTool(target.value);
}
</script>

<template>
  <section class="content-page page-rise">
    <div class="settings-grid">
      <section class="control-card">
        <h2>基础上下文</h2>
        <label>用户 ID<input v-model="state.currentUserId" class="field" @change="run(() => store.loadBookDetails())" /></label>
        <label>阅读进度<input v-model="state.form.progress" type="number" min="1" class="field" /></label>
        <button class="secondary-button" type="button" @click="run(() => store.saveProgress())">保存阅读进度</button>
      </section>

      <section class="control-card">
        <h2>Agent 策略</h2>
        <select v-model="state.form.policy" class="field">
          <option value="auto">Auto：规则优先，云端开启时 ReAct</option>
          <option value="rule_based">RuleBased：本地确定性 ReAct</option>
          <option value="llm_react">LLM ReAct：云端模型规划</option>
          <option value="langgraph">LangGraph：图编排策略</option>
        </select>
        <select v-model="state.form.retriever" class="field mt-3">
          <option value="lexical">Lexical：倒排检索</option>
          <option value="embedding">Embedding：语义召回</option>
          <option value="auto">Auto：有向量库即优先</option>
        </select>
      </section>

      <section class="control-card">
        <h2>云端 API</h2>
        <select v-model="state.form.provider" class="field" @change="store.applyProvider(state.form.provider)">
          <option value="deepseek">DeepSeek</option>
          <option value="openai">OpenAI</option>
          <option value="custom">Custom</option>
        </select>
        <input v-model="state.form.apiEndpoint" class="field mt-3" placeholder="Endpoint API URL" />
        <input v-model="state.form.apiModel" class="field mt-3" placeholder="Model Name" />
        <input v-model="state.form.apiKey" type="password" class="field mt-3" placeholder="API Key" />
        <label class="check-line"><input v-model="state.form.cloudEnabled" type="checkbox" />启用云端 ReAct</label>
        <label class="check-line"><input v-model="state.form.rememberApi" type="checkbox" />保存到浏览器 LocalStorage</label>
        <div class="mt-3 flex gap-3">
          <button class="secondary-button" type="button" @click="store.saveLocalPreferences()">保存偏好</button>
          <button class="ghost-button" type="button" @click="store.clearLocalPreferences()">清除偏好</button>
        </div>
      </section>

      <section class="control-card">
        <h2>回答偏好</h2>
        <select v-model="state.form.answerStyle" class="field">
          <option value="">默认</option>
          <option value="brief">简洁</option>
          <option value="detailed">详细</option>
        </select>
        <input v-model="state.form.preferenceFocus" class="field mt-3" placeholder="关注重点，例如人物关系 / 伏笔" />
        <textarea v-model="state.form.preferenceCustom" class="field mt-3 min-h-[90px]" placeholder="自定义长期偏好"></textarea>
        <button class="secondary-button mt-3" type="button" @click="run(() => store.saveUserPreferences())">保存长期偏好</button>
      </section>

      <section class="control-card wide-card">
        <h2>Agent 工具箱</h2>
        <select class="field" :value="state.selectedToolName" @change="onToolSelect">
          <option v-for="tool in state.agentTools" :key="tool.name" :value="tool.name">{{ tool.name }}</option>
        </select>
        <p class="mt-2 text-muted">{{ selectedToolDescription }}</p>
        <textarea v-model="state.toolArgumentsText" class="field mt-3 min-h-[150px] font-mono text-xs" spellcheck="false"></textarea>
        <div class="mt-3 flex gap-3">
          <button class="secondary-button" type="button" @click="run(() => store.loadAgentTools())">刷新工具清单</button>
          <button class="primary-button" type="button" @click="run(() => store.runSelectedAgentTool())">执行工具</button>
        </div>
        <pre v-if="state.toolRunResult" class="result-pre">{{ JSON.stringify(state.toolRunResult.result, null, 2) }}</pre>
      </section>

      <section class="control-card wide-card">
        <h2>系统诊断</h2>
        <div class="stats-grid">
          <article class="stat-card"><strong>{{ frontendModeLabel }}</strong><span>前端模式</span></article>
          <article class="stat-card"><strong>{{ state.diagnostics?.database?.exists ? "可用" : "缺失" }}</strong><span>数据库</span></article>
          <article class="stat-card"><strong>{{ state.diagnostics?.stats?.books ?? state.books.length }}</strong><span>书籍</span></article>
        </div>
        <button class="secondary-button mt-4" type="button" @click="run(() => store.loadDiagnostics())">刷新诊断</button>
      </section>
    </div>
  </section>
</template>
