<script setup lang="ts">
import { storeToRefs } from "pinia";
import { useBookRecallStore } from "@/stores/bookrecall";
import type { EvidenceItem } from "@/types";

const store = useBookRecallStore();
const { state, currentVectorIndex, dependencyCards } = storeToRefs(store);

function evidenceText(item: EvidenceItem) {
  return item.excerpt || item.child_text || "";
}

function run(action: () => Promise<void>) {
  action().catch((error: Error) => store.reportError(error, "模型操作失败"));
}
</script>

<template>
  <section class="content-page page-rise">
    <div class="control-card wide-card">
      <h2>本地模型与召回测试</h2>
      <div class="dependency-grid">
        <article v-for="[name, ready] in dependencyCards" :key="name">
          <strong>{{ name }}</strong>
          <span :class="ready ? 'ok' : 'bad'">{{ ready ? "可用" : "缺失" }}</span>
        </article>
      </div>
      <div class="form-grid mt-5">
        <input v-model="state.form.vectorModel" class="field" placeholder="BAAI/bge-small-zh-v1.5" />
        <select v-model="state.form.vectorBackend" class="field">
          <option value="auto">Auto</option>
          <option value="numpy">Numpy</option>
          <option value="faiss">FAISS</option>
        </select>
        <input v-model="state.form.vectorLimit" type="number" min="1" class="field" placeholder="Max Chunks，留空为全部" />
        <button class="secondary-button" type="button" @click="run(() => store.buildVectorIndex())">构建向量索引</button>
      </div>
      <div class="mt-3 flex flex-wrap gap-3">
        <span class="pill">{{ currentVectorIndex?.built ? "当前书向量索引已构建" : "当前书向量索引未构建" }}</span>
        <button class="danger-button" type="button" @click="run(() => store.deleteCurrentVectorIndex())">删除当前书向量索引</button>
      </div>
      <div class="empty-state mt-4 text-left">{{ state.vectorResult }}</div>
    </div>

    <div class="control-card wide-card">
      <h2>测试召回层</h2>
      <div class="form-grid">
        <input v-model="state.form.searchQuery" class="field" placeholder="输入关键词或自然语言问题" />
        <input v-model="state.form.searchLimit" type="number" min="1" max="20" class="field" />
        <button class="secondary-button" type="button" @click="run(() => store.searchEvidence())">检索证据</button>
      </div>
      <div v-if="state.searchResult?.hits?.length" class="evidence-grid mt-4">
        <article v-for="hit in state.searchResult.hits" :key="`${hit.chapter_number}-${evidenceText(hit).slice(0, 12)}`">
          <span>第 {{ hit.chapter_number }} 章</span>
          <p>{{ evidenceText(hit) }}</p>
          <button type="button" @click="run(() => store.openChapter(hit.chapter_number, evidenceText(hit)))">打开原文</button>
        </article>
      </div>
    </div>
  </section>
</template>
