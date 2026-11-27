import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { i18n } from "@/i18n";

declare module "vue-router" {
  interface RouteMeta {
    public?: boolean;
    staff?: boolean;
    feature?: "public_datasets" | "synchronization" | "notifications";
    titleKey?: string;
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/LoginView.vue"),
    meta: { public: true, titleKey: "auth.title" },
  },
  {
    path: "/",
    name: "overview",
    component: () => import("@/views/OverviewView.vue"),
    meta: { titleKey: "overview.title" },
  },
  {
    path: "/environments",
    name: "environments",
    component: () => import("@/views/TasksView.vue"),
    props: { mode: "interactive" },
    meta: { titleKey: "tasks.envTitle" },
  },
  {
    path: "/jobs",
    name: "jobs",
    component: () => import("@/views/TasksView.vue"),
    props: { mode: "batch" },
    meta: { titleKey: "tasks.jobTitle" },
  },
  {
    path: "/environments/new",
    name: "new-environment",
    component: () => import("@/views/NewRequestView.vue"),
    props: { mode: "interactive" },
    meta: { titleKey: "request.newEnvTitle" },
  },
  {
    path: "/jobs/new",
    name: "new-job",
    component: () => import("@/views/NewRequestView.vue"),
    props: { mode: "batch" },
    meta: { titleKey: "request.newJobTitle" },
  },
  {
    path: "/tasks/:id",
    name: "task-detail",
    component: () => import("@/views/TaskDetailView.vue"),
    meta: { titleKey: "tasks.openDetail" },
  },
  {
    path: "/images",
    name: "images",
    component: () => import("@/views/ImagesView.vue"),
    meta: { titleKey: "images.title" },
  },
  {
    path: "/datasets",
    name: "datasets",
    component: () => import("@/views/DatasetsView.vue"),
    meta: { feature: "public_datasets", titleKey: "datasets.title" },
  },
  {
    path: "/syncs",
    name: "syncs",
    component: () => import("@/views/SyncsView.vue"),
    meta: { feature: "synchronization", titleKey: "syncs.title" },
  },
  {
    path: "/account",
    name: "account",
    component: () => import("@/views/AccountView.vue"),
    meta: { titleKey: "account.title" },
  },
  {
    path: "/inbox",
    name: "inbox",
    component: () => import("@/views/InboxView.vue"),
    meta: { feature: "notifications", titleKey: "inbox.title" },
  },
  {
    path: "/admin/approvals",
    name: "admin-approvals",
    component: () => import("@/views/admin/ApprovalsView.vue"),
    meta: { staff: true, titleKey: "admin.approvalsTitle" },
  },
  {
    path: "/admin/servers",
    name: "admin-servers",
    component: () => import("@/views/admin/ServersView.vue"),
    meta: { staff: true, titleKey: "admin.serversTitle" },
  },
  {
    path: "/admin/performance",
    name: "admin-performance",
    component: () => import("@/views/admin/PerformanceView.vue"),
    meta: { staff: true, titleKey: "admin.performanceTitle" },
  },
  {
    path: "/admin/usage",
    name: "admin-usage",
    component: () => import("@/views/admin/UsageView.vue"),
    meta: { staff: true, titleKey: "admin.usageTitle" },
  },
  {
    path: "/admin/containers",
    name: "admin-containers",
    component: () => import("@/views/admin/ContainersView.vue"),
    meta: { staff: true, titleKey: "admin.containersTitle" },
  },
  {
    path: "/admin/storage",
    name: "admin-storage",
    component: () => import("@/views/admin/StorageView.vue"),
    meta: { staff: true, titleKey: "admin.storageTitle" },
  },
  {
    path: "/admin/images",
    name: "admin-images",
    component: () => import("@/views/admin/ManagedImagesView.vue"),
    meta: { staff: true, titleKey: "admin.managedImagesTitle" },
  },
  {
    path: "/admin/members",
    name: "admin-members",
    component: () => import("@/views/admin/MembersView.vue"),
    meta: { staff: true, titleKey: "admin.membersTitle" },
  },
  {
    path: "/admin/groups",
    name: "admin-groups",
    component: () => import("@/views/admin/GroupsView.vue"),
    meta: { staff: true, titleKey: "admin.groupsTitle" },
  },
  {
    path: "/admin/notices",
    name: "admin-notices",
    component: () => import("@/views/admin/NoticesView.vue"),
    meta: { staff: true, titleKey: "admin.noticesTitle" },
  },
  {
    path: "/admin/settings",
    name: "admin-settings",
    component: () => import("@/views/admin/SettingsView.vue"),
    meta: { staff: true, titleKey: "admin.settingsTitle" },
  },
  {
    path: "/admin/audits",
    name: "admin-audits",
    component: () => import("@/views/admin/AuditsView.vue"),
    meta: { staff: true, titleKey: "admin.auditsTitle" },
  },
  {
    path: "/:pathMatch(.*)*",
    redirect: "/",
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 };
  },
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  const locale = useLocaleStore();

  if (!auth.initialized) {
    const profile = await auth.bootstrap();
    if (profile) {
      locale.syncFromUser(profile.language, profile.default_language);
    }
  }

  if (to.meta.public) {
    if (auth.isAuthenticated) {
      const next = typeof to.query.next === "string" && to.query.next.startsWith("/") ? to.query.next : "/";
      return next;
    }
    return true;
  }

  if (!auth.isAuthenticated) {
    return { path: "/login", query: to.fullPath !== "/" ? { next: to.fullPath } : {} };
  }

  if (to.meta.staff && !auth.isStaff) {
    return "/";
  }

  if (to.meta.feature && !auth.features[to.meta.feature]) {
    return "/";
  }

  return true;
});

router.afterEach((to) => {
  const appName = i18n.global.t("app.name");
  const titleKey = to.meta.titleKey;
  document.title = titleKey ? `${i18n.global.t(titleKey)} — ${appName}` : appName;
});

export default router;
