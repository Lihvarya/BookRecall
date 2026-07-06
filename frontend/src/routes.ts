import type { Component } from "vue";
import ChatPage from "@/pages/ChatPage.vue";
import IndexPage from "@/pages/IndexPage.vue";
import LibraryPage from "@/pages/LibraryPage.vue";
import SettingsPage from "@/pages/SettingsPage.vue";

export type RouteId = "chat" | "library" | "index" | "settings";

export interface AppRoute {
  id: RouteId;
  icon: string;
  title: string;
  component: Component;
  showHeader: boolean;
  meta: {
    eyebrow: string;
    heading: string;
    summary: string;
  };
}

export const appRoutes: AppRoute[] = [
  {
    id: "chat",
    icon: "问",
    title: "对话",
    component: ChatPage,
    showHeader: false,
    meta: {
      eyebrow: "BookRecall Agent",
      heading: "阅读记忆对话",
      summary: "默认持续使用当前会话；只有点击“新会话”才会开启新的记忆线。"
    }
  },
  {
    id: "library",
    icon: "书",
    title: "书库",
    component: LibraryPage,
    showHeader: true,
    meta: {
      eyebrow: "Library Lab",
      heading: "书库工作台",
      summary: "集中管理书籍、导入重建、向量索引和召回测试。"
    }
  },
  {
    id: "index",
    icon: "索",
    title: "索引",
    component: IndexPage,
    showHeader: true,
    meta: {
      eyebrow: "Knowledge Index",
      heading: "索引与原文",
      summary: "查看实体、主题、事件、关系和章节证据。"
    }
  },
  {
    id: "settings",
    icon: "设",
    title: "设置",
    component: SettingsPage,
    showHeader: true,
    meta: {
      eyebrow: "Runtime",
      heading: "设置与诊断",
      summary: "配置 Agent 策略、云端模型、偏好、工具箱和系统状态。"
    }
  }
];

export const defaultRouteId: RouteId = "chat";

export function normalizeRouteId(raw: string): RouteId {
  const normalized = raw.replace(/^#\/?/, "");
  const routeId = (normalized === "import" || normalized === "model" ? "library" : normalized) as RouteId;
  return appRoutes.some((route) => route.id === routeId) ? routeId : defaultRouteId;
}
