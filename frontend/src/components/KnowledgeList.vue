<script setup lang="ts">
export interface KnowledgeListItem {
  id: string;
  title: string;
  meta: string;
  detail?: string;
}

defineProps<{
  title: string;
  items: KnowledgeListItem[];
  emptyText?: string;
  compact?: boolean;
}>();
</script>

<template>
  <section class="control-card knowledge-list-card" :class="{ 'knowledge-list-compact': compact }">
    <div class="knowledge-list-head">
      <h2>{{ title }}</h2>
      <span class="pill">{{ items.length }} 条</span>
    </div>
    <article v-for="item in items" :key="item.id" class="list-row">
      <strong>{{ item.title }}</strong>
      <span>{{ item.meta }}</span>
      <small v-if="item.detail">{{ item.detail }}</small>
    </article>
    <div v-if="!items.length" class="empty-state">{{ emptyText || "暂无数据。" }}</div>
  </section>
</template>
