<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { storeToRefs } from "pinia";
import ErrorBanner from "@/components/ErrorBanner.vue";
import NavRail from "@/components/NavRail.vue";
import PageHeader from "@/components/PageHeader.vue";
import { appRoutes, defaultRouteId, normalizeRouteId, type RouteId } from "@/routes";
import { useBookRecallStore } from "@/stores/bookrecall";

const store = useBookRecallStore();
const { state, currentBook } = storeToRefs(store);

const activeNav = ref<RouteId>(defaultRouteId);

const navItems = computed(() => appRoutes.map(({ id, icon, title }) => ({ id, icon, title })));
const activeRoute = computed(() => appRoutes.find((route) => route.id === activeNav.value) || appRoutes[0]);
const activePageMeta = computed(() => activeRoute.value.meta);
const currentBookTitle = computed(() => currentBook.value?.title || "未选择书籍");

function navigate(id: RouteId) {
  activeNav.value = id;
  const nextHash = `#${id}`;
  if (window.location.hash !== nextHash) {
    window.location.hash = nextHash;
  }
}

function syncPageFromHash() {
  activeNav.value = normalizeRouteId(window.location.hash);
}

function run(action: () => Promise<void>) {
  action().catch((error: Error) => store.reportError(error, "初始化控制台失败"));
}

onMounted(() => {
  syncPageFromHash();
  window.addEventListener("hashchange", syncPageFromHash);
  run(async () => {
    await Promise.all([store.loadBooks(), store.loadDiagnostics(), store.loadAgentTools()]);
  });
});

onBeforeUnmount(() => {
  window.removeEventListener("hashchange", syncPageFromHash);
});
</script>

<template>
  <div class="app-shell text-ink antialiased">
    <NavRail :nav-items="navItems" :active-nav="activeNav" @navigate="(id) => navigate(id as RouteId)" />

    <main class="main-stage">
      <ErrorBanner :error="state.lastError" @dismiss="store.clearError()" />

      <PageHeader
        v-if="activeRoute.showHeader"
        :eyebrow="activePageMeta.eyebrow"
        :heading="activePageMeta.heading"
        :summary="activePageMeta.summary"
        :current-book-title="currentBookTitle"
        :policy="state.form.policy"
        :retriever="state.form.retriever"
      />

      <component
        :is="route.component"
        v-for="route in appRoutes"
        :key="route.id"
        v-show="activeNav === route.id"
      />
    </main>
  </div>
</template>
