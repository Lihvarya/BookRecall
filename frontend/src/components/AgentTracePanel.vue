<script setup lang="ts">
import type { TraceItem } from "@/types";

defineProps<{
  trace: TraceItem[];
  summary: {
    count: number;
    tools: string[];
    blockedCount: number;
    totalElapsedMs: number;
    slowestTool: string;
    slowestElapsedMs: number | null | undefined;
  };
}>();

function tracePreview(item: TraceItem) {
  return JSON.stringify(item.observation ?? item.arguments ?? {}, null, 2);
}

function formatMs(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "未记录";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}s`;
  }
  return `${Number(value).toFixed(2)}ms`;
}
</script>

<template>
  <div class="trace-drawer">
    <div class="side-head">
      <strong>工具轨迹</strong>
      <span class="pill">{{ summary.count }} 步 · {{ formatMs(summary.totalElapsedMs) }}</span>
    </div>
    <div v-if="trace.length" class="trace-overview">
      <span>路径：{{ summary.tools.join(" → ") || "无" }}</span>
      <span>防剧透：{{ summary.blockedCount }} 次</span>
      <span>最慢：{{ summary.slowestTool || "无" }} {{ formatMs(summary.slowestElapsedMs) }}</span>
    </div>
    <div v-if="trace.length" class="trace-list">
      <article v-for="(item, index) in trace" :key="index">
        <strong>{{ index + 1 }}. {{ item.tool_name || "unknown_tool" }}</strong>
        <div class="trace-meta">
          <span>{{ item.status || "ok" }}</span>
          <span>耗时 {{ formatMs(item.elapsed_ms) }}</span>
          <span>命中 {{ item.hit_count ?? 0 }}</span>
          <span v-if="item.blocked_by_spoiler || item.spoiler_blocked">防剧透拦截</span>
        </div>
        <p v-if="item.observation_summary">{{ item.observation_summary }}</p>
        <pre>{{ tracePreview(item) }}</pre>
      </article>
    </div>
    <div v-else class="empty-state">提问后会显示 Agent 的工具调用链路。</div>
  </div>
</template>
