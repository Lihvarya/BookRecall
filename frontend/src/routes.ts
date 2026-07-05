import type { Component } from "vue";
import ChatPage from "@/pages/ChatPage.vue";
import ImportPage from "@/pages/ImportPage.vue";
import IndexPage from "@/pages/IndexPage.vue";
import LibraryPage from "@/pages/LibraryPage.vue";
import ModelPage from "@/pages/ModelPage.vue";
import SettingsPage from "@/pages/SettingsPage.vue";

export type RouteId = "chat" | "library" | "index" | "import" | "model" | "settings";

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
      eyebrow: "Library",
      heading: "书库",
      summary: "管理书籍、分组和标签，选择当前阅读对象。"
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
    id: "import",
    icon: "入",
    title: "导入",
    component: ImportPage,
    showHeader: true,
    meta: {
      eyebrow: "Import",
      heading: "导入与重建",
      summary: "导入本地 TXT，构建或重建结构化索引。"
    }
  },
  {
    id: "model",
    icon: "模",
    title: "模型",
    component: ModelPage,
    showHeader: true,
    meta: {
      eyebrow: "Retrieval Lab",
      heading: "模型与召回",
      summary: "构建向量索引，测试 lexical / embedding / auto 召回效果。"
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
  const routeId = raw.replace(/^#\/?/, "") as RouteId;
  return appRoutes.some((route) => route.id === routeId) ? routeId : defaultRouteId;
}
