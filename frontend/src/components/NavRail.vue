<script setup lang="ts">
type NavItem = {
  id: string;
  icon: string;
  title: string;
};

defineProps<{
  navItems: NavItem[];
  activeNav: string;
}>();

defineEmits<{
  navigate: [id: string];
}>();
</script>

<template>
  <nav class="nav-rail">
    <div class="brand-mark">B</div>
    <button
      v-for="nav in navItems"
      :key="nav.id"
      :class="['nav-button', activeNav === nav.id ? 'nav-button-active' : '']"
      :title="nav.title"
      type="button"
      @click="$emit('navigate', nav.id)"
    >
      <span>{{ nav.icon }}</span>
      <small>{{ nav.title }}</small>
    </button>
    <div class="flex-1"></div>
    <span class="status-dot" title="本地优先"></span>
  </nav>
</template>
