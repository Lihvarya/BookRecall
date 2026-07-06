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
          <option value="auto">Auto：本地 Qwen 可用时 Planner，否则规则/云端回退</option>
          <option value="rule_based">RuleBased：本地确定性 ReAct</option>
          <option value="local_planner">Local Planner：本地 Qwen 规划工具</option>
          <option value="llm_react">LLM ReAct：云端模型规划</option>
          <option value="langgraph">LangGraph：图编排策略</option>
        </select>
        <select v-model="state.form.retriever" class="field mt-3">
          <option value="lexical">Lexical：倒排检索</option>
          <option value="embedding">Embedding：Qwen 向量召回 + Qwen 重排</option>
          <option value="auto">Auto：优先 Qwen embedding，缺索引时 lexical + Qwen 重排</option>
        </select>
        <p class="mt-2 text-xs leading-6 text-muted">
          本地 Qwen 的 endpoint、模型名和 GGUF 路径在下方“本地 Qwen 模型”卡片配置；Auto 策略会在本地模型可用时优先使用 Local Planner。
        </p>
      </section>

      <section class="control-card local-model-card wide-card">
        <div class="local-model-head">
          <div>
            <span class="eyebrow">Local Reasoner</span>
            <h2>本地 Qwen 模型</h2>
            <p>这里控制问答期的 Local Planner 和按需结构化索引；推荐优先接 LM Studio。</p>
          </div>
          <label class="switch-line">
            <input v-model="state.form.localQwenEnabled" type="checkbox" />
            <span>启用</span>
          </label>
        </div>
        <div class="form-grid mt-4">
          <label>
            LM Studio / llama.cpp endpoint
            <input
              v-model="state.form.smartIndexEndpoint"
              class="field"
              placeholder="推荐：http://127.0.0.1:1234"
            />
          </label>
          <label>
            本地服务模型名
            <input v-model="state.form.localQwenModelName" class="field" placeholder="例如 qwen3.5-4b" />
          </label>
          <label class="md:col-span-2">
            GGUF 模型路径（不填 endpoint 时才直接加载）
            <input
              v-model="state.form.smartIndexModelPath"
              class="field"
              placeholder="例如 D:\BookRecall\models\llm\qwen3-4b-instruct-2507-q4_k_m.gguf"
            />
          </label>
        </div>
        <div class="local-model-tips">
          <span>Endpoint 优先：填了 endpoint 就调用本地服务，不会在 BookRecall 进程里直接加载 GGUF。</span>
          <span>导入期智能索引由“书库”页的 Qwen 智能索引开关控制；问答期默认走这里的本地 Qwen。</span>
          <span>如果使用 LM Studio，请保持服务已启动，并确认模型名与 LM Studio 中加载的模型 ID 一致。</span>
        </div>
        <button class="secondary-button mt-4" type="button" @click="store.saveLocalPreferences()">保存本地模型配置</button>
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
