<script setup lang="ts">
import { storeToRefs } from "pinia";
import { useBookRecallStore } from "@/stores/bookrecall";
import type { EvidenceItem } from "@/types";

const store = useBookRecallStore();
const { state, groups, visibleBooks, currentVectorIndex, dependencyCards } = storeToRefs(store);

function run(action: () => Promise<void>) {
  action().catch((error: Error) => store.reportError(error, "书库工作台操作失败"));
}

function openBook(bookId: string) {
  state.value.currentBookId = bookId;
  run(() => store.loadBookDetails());
}

function evidenceText(item: EvidenceItem) {
  return item.excerpt || item.child_text || "";
}
</script>

<template>
  <section class="content-page page-rise library-workbench">
    <div class="library-hero">
      <article>
        <span class="pill">Library</span>
        <strong>{{ state.books.length }}</strong>
        <small>本地书籍</small>
      </article>
      <article>
        <span class="pill">Current</span>
        <strong>{{ state.currentBookId || "未选择" }}</strong>
        <small>当前 Book ID</small>
      </article>
      <article>
        <span class="pill">Vector</span>
        <strong>{{ currentVectorIndex?.built ? "已构建" : "未构建" }}</strong>
        <small>{{ currentVectorIndex?.backend || "等待索引" }}</small>
      </article>
    </div>

    <div class="workbench-grid">
      <div class="control-card">
        <h2>书库与当前书</h2>
        <div class="form-grid">
          <label>
            书籍
            <select v-model="state.currentBookId" class="field" @change="run(() => store.loadBookDetails())">
              <option v-for="book in state.books" :key="book.book_id" :value="book.book_id">{{ book.title }}</option>
            </select>
          </label>
          <label>
            分组筛选
            <select v-model="state.currentGroupFilter" class="field">
              <option value="">全部分组</option>
              <option value="__ungrouped__">未分组</option>
              <option v-for="group in groups" :key="group" :value="group">{{ group }}</option>
            </select>
          </label>
          <label>
            书籍分组
            <input v-model="state.form.bookGroup" class="field" placeholder="小说 / 学术 / 待读" />
          </label>
          <label>
            标签
            <input v-model="state.form.bookTags" class="field" placeholder="用逗号分隔" />
          </label>
        </div>
        <button class="secondary-button mt-4" type="button" @click="run(() => store.saveBookMetadata())">保存书籍元数据</button>

        <div class="book-grid compact-books mt-5">
          <article v-for="book in visibleBooks" :key="book.book_id" class="book-card">
            <div>
              <span class="pill">{{ book.book_group || "未分组" }}</span>
              <h3>{{ book.title }}</h3>
              <p>{{ book.chapter_count }} 章 · {{ book.entity_count }} 个实体</p>
              <small>{{ book.book_id }}</small>
            </div>
            <button class="ghost-button" type="button" @click="openBook(book.book_id)">打开</button>
          </article>
        </div>
      </div>

      <div class="control-card wide-card">
        <h2>导入与重建</h2>
        <p>导入本地 TXT 或粘贴少量文本试跑。页面不会预览整本 TXT，避免大文件卡住浏览器。</p>
        <div class="form-grid">
          <input v-model="state.form.buildBookId" class="field" placeholder="Book ID，例如 sample_book" />
          <input v-model="state.form.buildTitle" class="field" placeholder="书名，可选" />
          <input class="field" type="file" accept=".txt,text/plain" @change="store.readSelectedBookFile" />
          <label class="check-line"><input v-model="state.form.buildOverwrite" type="checkbox" />允许覆盖同名 Book ID</label>
          <label class="check-line"><input v-model="state.form.autoBuildVectorIndex" type="checkbox" />导入后自动构建 embedding 向量索引</label>
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
            用本地 Qwen 审稿实体、关系和事件。速度会慢很多，但能减少虚词、弱共现和无意义图谱节点。
          </p>
          <div class="form-grid mt-3">
            <label>
              智能索引速度档
              <select v-model="state.form.smartIndexProfile" class="field">
                <option value="fast">快速：跳过逐章摘要，关系/事件抽样审稿</option>
                <option value="balanced">均衡：部分摘要，关系/事件间隔审稿</option>
                <option value="deep">深度：逐章摘要与关系/事件审稿</option>
              </select>
            </label>
            <input
              v-model="state.form.smartIndexModelPath"
              class="field"
              placeholder="GGUF 模型路径，例如 D:\BookRecall\models\llm\qwen3-4b-instruct-2507-q4_k_m.gguf"
            />
            <input
              v-model="state.form.smartIndexEndpoint"
              class="field"
              placeholder="本地 OpenAI-compatible endpoint，例如 http://127.0.0.1:8080"
            />
            <input
              v-model="state.form.localQwenModelName"
              class="field"
              placeholder="本地服务模型名，例如 qwen3.5-4b"
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
    </div>

    <div class="control-card wide-card">
      <h2>模型与召回</h2>
      <p>
        当前推荐链路：Qwen3-Embedding-0.6B 负责粗召回，Qwen3-Reranker-0.6B 负责精排，Qwen3.5-4B 只基于证据回答。
        如果当前书仍是旧 BGE 向量索引，请重建一次向量索引。
      </p>
      <div class="dependency-grid">
        <article v-for="[name, ready] in dependencyCards" :key="name">
          <strong>{{ name }}</strong>
          <span :class="ready ? 'ok' : 'bad'">{{ ready ? "可用" : "缺失" }}</span>
        </article>
      </div>
      <div class="form-grid mt-5">
        <label>
          Embedding 模型
          <input v-model="state.form.vectorModel" class="field" placeholder="Qwen/Qwen3-Embedding-0.6B" />
        </label>
        <label>
          向量后端
          <select v-model="state.form.vectorBackend" class="field">
            <option value="auto">Auto</option>
            <option value="numpy">Numpy</option>
            <option value="faiss">FAISS</option>
          </select>
        </label>
        <label>
          构建 chunk 上限
          <input v-model="state.form.vectorLimit" type="number" min="1" class="field" placeholder="Max Chunks，留空为全部" />
        </label>
        <label>
          Reranker 模型
          <input v-model="state.form.rerankModel" class="field" placeholder="Qwen/Qwen3-Reranker-0.6B" />
        </label>
        <label>
          重排候选数
          <input v-model="state.form.rerankCandidates" type="number" min="4" max="100" class="field" placeholder="推荐 50" />
        </label>
        <label class="check-line">
          <input v-model="state.form.rerankEnabled" type="checkbox" />
          启用 Qwen 本地重排
        </label>
        <button class="secondary-button" type="button" @click="run(() => store.buildVectorIndex())">构建 / 重建 Qwen 向量索引</button>
      </div>
      <div class="mt-3 flex flex-wrap gap-3">
        <span class="pill">{{ currentVectorIndex?.built ? "当前书向量索引已构建" : "当前书向量索引未构建" }}</span>
        <button class="danger-button" type="button" @click="run(() => store.deleteCurrentVectorIndex())">删除当前书向量索引</button>
      </div>
      <div class="empty-state mt-4 text-left">{{ state.vectorResult }}</div>
    </div>

    <div class="control-card wide-card">
      <h2>召回测试</h2>
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
