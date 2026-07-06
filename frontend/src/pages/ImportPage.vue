<script setup lang="ts">
import { storeToRefs } from "pinia";
import { useBookRecallStore } from "@/stores/bookrecall";

const store = useBookRecallStore();
const { state } = storeToRefs(store);

function run(action: () => Promise<void>) {
  action().catch((error: Error) => store.reportError(error, "导入操作失败"));
}
</script>

<template>
  <section class="content-page page-rise">
    <div class="control-card wide-card">
      <h2>导入本地 TXT</h2>
      <p>不会预览整本 TXT，避免大文件卡住页面。导入内容仅用于构建本地索引。</p>
      <div class="form-grid">
        <input v-model="state.form.buildBookId" class="field" placeholder="Book ID，例如 sample_book" />
        <input v-model="state.form.buildTitle" class="field" placeholder="书名，可选" />
        <input class="field" type="file" accept=".txt,text/plain" @change="store.readSelectedBookFile" />
        <label class="check-line"><input v-model="state.form.buildOverwrite" type="checkbox" />允许覆盖同名 Book ID</label>
      </div>
      <textarea v-model="state.form.buildText" class="field mt-4 min-h-[90px]" placeholder="少量文本试跑；选择 TXT 后可留空。"></textarea>
      <textarea v-model="state.form.buildEntities" class="field mt-3 min-h-[80px]" placeholder="实体词表，可选：标准名|别名1,别名2"></textarea>
      <textarea v-model="state.form.buildThemes" class="field mt-3 min-h-[80px]" placeholder="主题词表，可选：主题名|别名1,别名2"></textarea>
      <div class="smart-index-card mt-4">
        <label class="check-line">
          <input v-model="state.form.smartIndexEnabled" type="checkbox" />
          启用 Qwen3 智能结构化索引
        </label>
        <p>
          用本地 Qwen3-4B-Instruct-2507 4bit 模型审稿实体、关系和事件。速度会慢很多，但能避免把“就是、没有、他的、时间”这类虚词编进图谱。
        </p>
        <div class="form-grid mt-3">
          <input
            v-model="state.form.smartIndexModelPath"
            class="field"
            placeholder="GGUF 模型路径，例如 D:\BookRecall\models\llm\qwen3-4b-instruct-2507-q4_k_m.gguf"
          />
          <input
            v-model="state.form.smartIndexEndpoint"
            class="field"
            placeholder="可选：本地 OpenAI-compatible endpoint，例如 http://127.0.0.1:8080"
          />
          <input
            v-model="state.form.smartIndexMaxChapters"
            type="number"
            min="0"
            class="field"
            placeholder="最多智能处理章节数；0 为全部"
          />
          <input
            v-model="state.form.smartIndexBatchChapters"
            type="number"
            min="1"
            max="6"
            class="field"
            placeholder="实体抽取合批章节数；推荐 2"
          />
        </div>
      </div>
      <div class="mt-4 flex flex-wrap gap-3">
        <button class="primary-button" type="button" :disabled="state.isIndexing" @click="run(() => store.buildBookFromPanel())">
          {{ state.isIndexing ? "索引中..." : "开始建库" }}
        </button>
        <button class="ghost-button" type="button" :disabled="state.isIndexing" @click="run(() => store.rebuildCurrentBookIndex())">
          重建当前书结构化索引
        </button>
        <button class="danger-button" type="button" :disabled="state.isIndexing" @click="run(() => store.deleteCurrentBook())">
          删除当前书数据
        </button>
      </div>
      <div v-if="state.indexJob" class="index-progress-card mt-4">
        <div class="side-head">
          <strong>{{ state.indexJob.stage || "索引任务" }}</strong>
          <span class="pill">{{ Math.round(Number(state.indexJob.percent || 0)) }}%</span>
        </div>
        <div class="progress-track mt-3">
          <span :style="{ width: `${Math.max(0, Math.min(100, Number(state.indexJob.percent || 0)))}%` }"></span>
        </div>
        <p>{{ state.indexJob.message || "正在处理索引任务..." }}</p>
        <small v-if="state.indexJob.total">
          {{ state.indexJob.current || 0 }} / {{ state.indexJob.total }} · {{ state.indexJob.status }}
        </small>
        <small v-else>{{ state.indexJob.status }}</small>
      </div>
      <div class="empty-state mt-4 text-left" v-html="state.buildResult"></div>
    </div>
  </section>
</template>
