<script setup lang="ts">
defineProps<{
  error: {
    message: string;
    context?: string;
    occurredAt: string;
    suggestions: string[];
  } | null;
}>();

defineEmits<{
  dismiss: [];
}>();
</script>

<template>
  <aside v-if="error" class="error-banner" role="alert">
    <div>
      <span>{{ error.occurredAt }}</span>
      <strong>{{ error.context || "操作失败" }}</strong>
      <p>{{ error.message }}</p>
      <ul v-if="error.suggestions?.length">
        <li v-for="suggestion in error.suggestions" :key="suggestion">{{ suggestion }}</li>
      </ul>
    </div>
    <button type="button" @click="$emit('dismiss')">关闭</button>
  </aside>
</template>
