<script setup lang="ts">
import { ref } from "vue";

export interface KnowledgeListItem {
  id: string;
  title: string;
  meta: string;
  detail?: string;
}

const props = withDefaults(defineProps<{
  title: string;
  items: KnowledgeListItem[];
  emptyText?: string;
  compact?: boolean;
  defaultOpen?: boolean;
}>(), {
  emptyText: "暂无数据。",
  compact: false,
  defaultOpen: true
});

const isOpen = ref(props.defaultOpen);

function updateOpen(event: Event) {
  isOpen.value = (event.currentTarget as HTMLDetailsElement).open;
}
</script>

<template>
  <details
    class="control-card knowledge-list-card"
    :class="{ 'knowledge-list-compact': compact }"
    :open="isOpen"
    @toggle="updateOpen"
  >
    <summary class="knowledge-list-head">
      <div>
        <h2>{{ title }}</h2>
        <small>{{ isOpen ? "点击收起" : "点击展开" }}</small>
      </div>
      <span class="pill">{{ items.length }} 条</span>
    </summary>
    <div class="knowledge-list-body">
    <article v-for="item in items" :key="item.id" class="list-row">
      <strong>{{ item.title }}</strong>
      <span>{{ item.meta }}</span>
      <small v-if="item.detail">{{ item.detail }}</small>
    </article>
      <div v-if="!items.length" class="empty-state">{{ emptyText }}</div>
    </div>
  </details>
</template>
