<script setup lang="ts">
import { computed, ref } from "vue";
import type { RelationSummary } from "@/types";

const props = defineProps<{
  relations: RelationSummary[];
}>();

const emit = defineEmits<{
  focus: [entity: string];
}>();

const selectedEntity = ref("");

const graphRelations = computed(() => props.relations.slice(0, 40));

const relationTypes = computed(() => {
  return [...new Set(graphRelations.value.map((relation) => relation.relation_type || "关联"))].sort((a, b) =>
    a.localeCompare(b, "zh-CN")
  );
});

const relationGraphNodes = computed(() => {
  const names = [...new Set(graphRelations.value.flatMap((relation) => [relation.source_entity, relation.target_entity]))];
  const weights = new Map<string, number>();
  for (const relation of graphRelations.value) {
    weights.set(relation.source_entity, (weights.get(relation.source_entity) || 0) + relation.mention_count);
    weights.set(relation.target_entity, (weights.get(relation.target_entity) || 0) + relation.mention_count);
  }
  const centerX = 360;
  const centerY = 210;
  const radiusX = 280;
  const radiusY = 145;
  return names.map((name, index) => {
    const angle = names.length <= 1 ? 0 : (Math.PI * 2 * index) / names.length - Math.PI / 2;
    const weight = weights.get(name) || 1;
    return {
      name,
      x: centerX + Math.cos(angle) * radiusX,
      y: centerY + Math.sin(angle) * radiusY,
      size: Math.min(34, 18 + weight * 2),
      weight
    };
  });
});

const relationNodeMap = computed(() => {
  return new Map(relationGraphNodes.value.map((node) => [node.name, node]));
});

const relationGraphEdges = computed(() => {
  return graphRelations.value
    .map((relation) => {
      const source = relationNodeMap.value.get(relation.source_entity);
      const target = relationNodeMap.value.get(relation.target_entity);
      if (!source || !target) {
        return null;
      }
      return {
        relation,
        source,
        target,
        width: Math.min(7, 1.5 + relation.mention_count)
      };
    })
    .filter(Boolean) as Array<{
    relation: RelationSummary;
    source: { name: string; x: number; y: number; size: number; weight: number };
    target: { name: string; x: number; y: number; size: number; weight: number };
    width: number;
  }>;
});

const visibleRelations = computed(() => {
  if (!selectedEntity.value) {
    return graphRelations.value;
  }
  return graphRelations.value.filter((relation) => {
    return relation.source_entity === selectedEntity.value || relation.target_entity === selectedEntity.value;
  });
});

const selectedEntitySummary = computed(() => {
  if (!selectedEntity.value) {
    return "点击图谱中的节点，可聚焦查看某个实体的关系网络。";
  }
  const count = visibleRelations.value.length;
  const mentions = visibleRelations.value.reduce((total, relation) => total + relation.mention_count, 0);
  return `${selectedEntity.value} 关联 ${count} 条关系，共 ${mentions} 次共现。`;
});

function relationColor(type: string) {
  const palette = ["#267352", "#cc842f", "#416f9f", "#9b5c3f", "#6b7d37", "#8b6f47"];
  let hash = 0;
  for (const char of type || "关联") {
    hash = (hash + char.charCodeAt(0)) % palette.length;
  }
  return palette[hash];
}

function chooseEntity(name: string) {
  selectedEntity.value = selectedEntity.value === name ? "" : name;
  emit("focus", selectedEntity.value);
}

function clearFocus() {
  selectedEntity.value = "";
  emit("focus", "");
}
</script>

<template>
  <section class="control-card relation-graph-card">
    <div class="relation-graph-head">
      <div>
        <p class="eyebrow">Knowledge Graph</p>
        <h2>关系图谱</h2>
        <p>{{ selectedEntitySummary }}</p>
      </div>
      <button v-if="selectedEntity" class="ghost-button px-3 py-2 text-xs" type="button" @click="clearFocus">
        取消聚焦
      </button>
    </div>
    <div v-if="graphRelations.length" class="relation-graph-layout">
      <div class="relation-graph-canvas">
        <svg viewBox="0 0 720 420" role="img" aria-label="实体关系图谱">
          <line
            v-for="edge in relationGraphEdges"
            :key="`${edge.relation.source_entity}-${edge.relation.target_entity}-${edge.relation.relation_type}`"
            :x1="edge.source.x"
            :y1="edge.source.y"
            :x2="edge.target.x"
            :y2="edge.target.y"
            :stroke="relationColor(edge.relation.relation_type)"
            :stroke-width="edge.width"
            :class="[
              'relation-edge',
              selectedEntity && edge.relation.source_entity !== selectedEntity && edge.relation.target_entity !== selectedEntity
                ? 'relation-edge-muted'
                : ''
            ]"
          />
          <g
            v-for="node in relationGraphNodes"
            :key="node.name"
            :class="['relation-node', selectedEntity === node.name ? 'relation-node-active' : '']"
            :transform="`translate(${node.x}, ${node.y})`"
            role="button"
            tabindex="0"
            @click="chooseEntity(node.name)"
            @keydown.enter.prevent="chooseEntity(node.name)"
          >
            <circle :r="node.size" />
            <text y="4" text-anchor="middle">{{ node.name.slice(0, 5) }}</text>
          </g>
        </svg>
      </div>
      <aside class="relation-graph-panel">
        <strong>关系类型</strong>
        <div class="relation-type-pills">
          <span
            v-for="type in relationTypes"
            :key="type"
            :style="{ borderColor: relationColor(type), color: relationColor(type) }"
          >
            {{ type }}
          </span>
        </div>
        <strong>当前关系</strong>
        <div class="relation-focus-list">
          <article
            v-for="relation in visibleRelations"
            :key="`${relation.source_entity}-${relation.target_entity}-${relation.relation_type}`"
          >
            <span>{{ relation.source_entity }} → {{ relation.target_entity }}</span>
            <small>{{ relation.relation_type }} · 首次第 {{ relation.first_chapter_number }} 章 · {{ relation.mention_count }} 次</small>
          </article>
        </div>
      </aside>
    </div>
    <div v-else class="empty-state">当前书还没有可视化的关系索引。</div>
  </section>
</template>
