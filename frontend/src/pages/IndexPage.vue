<script setup lang="ts">
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import KnowledgeList from "@/components/KnowledgeList.vue";
import RelationGraph from "@/components/RelationGraph.vue";
import { useBookRecallStore } from "@/stores/bookrecall";

const store = useBookRecallStore();
const { state } = storeToRefs(store);

const selectedEntity = ref("");

const visibleRelations = computed(() => {
  if (!selectedEntity.value) {
    return state.value.relations.slice(0, 40);
  }
  return state.value.relations.filter((relation) => {
    return relation.source_entity === selectedEntity.value || relation.target_entity === selectedEntity.value;
  });
});

const entityItems = computed(() => {
  return state.value.entities.slice(0, 24).map((entity) => ({
    id: entity.name,
    title: entity.name,
    meta: `首次第 ${entity.first_chapter_number} 章 · ${entity.mention_count} 次`
  }));
});

const eventItems = computed(() => {
  return state.value.events.map((event) => ({
    id: `${event.chapter_number}-${event.summary}`,
    title: `第 ${event.chapter_number} 章 · ${event.event_type}`,
    meta: event.summary,
    detail: event.entities?.length ? `关联实体：${event.entities.join("、")}` : ""
  }));
});

const relationItems = computed(() => {
  return visibleRelations.value.map((relation) => ({
    id: `${relation.source_entity}-${relation.target_entity}-${relation.relation_type}`,
    title: `${relation.source_entity} → ${relation.target_entity}`,
    meta: `${relation.relation_type} · 第 ${relation.first_chapter_number} 章`,
    detail: `${relation.mention_count} 次共现`
  }));
});
</script>

<template>
  <section class="content-page page-rise index-workbench">
    <div class="stats-grid index-stats-grid">
      <article v-for="(value, key) in state.stats" :key="key" class="stat-card">
        <strong>{{ value }}</strong>
        <span>{{ key }}</span>
      </article>
    </div>
    <RelationGraph :relations="state.relations" @focus="(entity) => (selectedEntity = entity)" />
    <div class="knowledge-grid index-knowledge-grid">
      <KnowledgeList title="实体索引" :items="entityItems" empty-text="当前书还没有实体索引。" compact />
      <KnowledgeList title="事件链" :items="eventItems" empty-text="当前书还没有事件链。" compact />
      <KnowledgeList title="关系索引" :items="relationItems" empty-text="当前筛选下没有关系。" compact />
      <section class="control-card reader-panel-card">
        <h2>原文阅读器</h2>
        <div class="reader-card chapter-reader">
          <strong>{{ state.reader.title }}</strong>
          <small>{{ state.reader.meta }}</small>
          <article v-html="state.reader.content"></article>
        </div>
      </section>
    </div>
  </section>
</template>
