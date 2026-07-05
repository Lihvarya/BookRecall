<script setup lang="ts">
import { storeToRefs } from "pinia";
import { useBookRecallStore } from "@/stores/bookrecall";

const store = useBookRecallStore();
const { state, groups, visibleBooks } = storeToRefs(store);

function run(action: () => Promise<void>) {
  action().catch((error: Error) => store.reportError(error, "书库操作失败"));
}

function openBook(bookId: string) {
  state.value.currentBookId = bookId;
  run(() => store.loadBookDetails());
}
</script>

<template>
  <section class="content-page page-rise">
    <div class="control-card">
      <h2>当前书籍</h2>
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
    </div>

    <div class="book-grid">
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
  </section>
</template>
