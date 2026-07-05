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
      <div class="mt-4 flex flex-wrap gap-3">
        <button class="primary-button" type="button" @click="run(() => store.buildBookFromPanel())">开始建库</button>
        <button class="ghost-button" type="button" @click="run(() => store.rebuildCurrentBookIndex())">重建当前书结构化索引</button>
        <button class="danger-button" type="button" @click="run(() => store.deleteCurrentBook())">删除当前书数据</button>
      </div>
      <div class="empty-state mt-4 text-left" v-html="state.buildResult"></div>
    </div>
  </section>
</template>
