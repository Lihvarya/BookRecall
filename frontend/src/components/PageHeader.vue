<script setup lang="ts">
defineProps<{
  eyebrow: string;
  heading: string;
  summary: string;
  currentBookTitle: string;
  policy: string;
  retriever: string;
}>();
</script>

<template>
  <header class="topbar">
    <div>
      <p class="eyebrow">{{ eyebrow }}</p>
      <h1>{{ heading }}</h1>
      <p>{{ summary }}</p>
    </div>
    <div class="topbar-actions">
      <span class="pill">{{ currentBookTitle }}</span>
      <span class="pill">policy: {{ policy }}</span>
      <span class="pill">retriever: {{ retriever }}</span>
    </div>
  </header>
</template>
