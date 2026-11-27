<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  DialogRoot,
  DialogPortal,
  DialogOverlay,
  DialogContent,
  DialogTitle,
  DialogClose,
} from "reka-ui";
import {
  LayoutDashboard,
  TerminalSquare,
  Cpu,
  Layers,
  Database,
  ArrowLeftRight,
  KeyRound,
  Bell,
  CheckSquare,
  Server,
  Activity,
  BarChart3,
  Boxes,
  HardDrive,
  FolderKanban,
  Users,
  UsersRound,
  Megaphone,
  SlidersHorizontal,
  ScrollText,
  Sun,
  Moon,
  LogOut,
  Menu,
  X,
  PanelLeftClose,
  PanelLeftOpen,
  Globe,
} from "lucide-vue-next";
import { useAuthStore } from "@/stores/auth";
import { useThemeStore } from "@/stores/theme";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import BrandMark from "@/components/ui/BrandMark.vue";
import ToastRegion from "./ToastRegion.vue";

interface NavItem {
  to: string;
  labelKey: string;
  icon: unknown;
  badge?: number;
  visible?: boolean;
}

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const theme = useThemeStore();
const locale = useLocaleStore();
const notifications = useNotificationsStore();

const mobileOpen = ref(false);
const collapsed = ref(false);

watch(
  () => route.fullPath,
  () => {
    mobileOpen.value = false;
  },
);

const workspaceNav = computed<NavItem[]>(() =>
  [
    { to: "/", labelKey: "nav.overview", icon: LayoutDashboard, visible: true },
    { to: "/environments", labelKey: "nav.environments", icon: TerminalSquare, visible: true },
    { to: "/jobs", labelKey: "nav.jobs", icon: Cpu, visible: true },
    { to: "/images", labelKey: "nav.images", icon: Layers, visible: true },
    { to: "/datasets", labelKey: "nav.datasets", icon: Database, visible: auth.features.public_datasets },
    { to: "/syncs", labelKey: "nav.syncs", icon: ArrowLeftRight, visible: auth.features.synchronization },
    { to: "/account", labelKey: "nav.account", icon: KeyRound, visible: true },
    {
      to: "/inbox",
      labelKey: "nav.inbox",
      icon: Bell,
      badge: notifications.unreadCount,
      visible: auth.features.notifications,
    },
  ].filter((item) => item.visible),
);

const adminNav = computed<NavItem[]>(() => {
  if (!auth.isStaff) return [];
  return [
    {
      to: "/admin/approvals",
      labelKey: "nav.approvals",
      icon: CheckSquare,
      badge: auth.pendingApprovals,
    },
    { to: "/admin/servers", labelKey: "nav.servers", icon: Server },
    { to: "/admin/performance", labelKey: "nav.performance", icon: Activity },
    { to: "/admin/usage", labelKey: "nav.usage", icon: BarChart3 },
    { to: "/admin/containers", labelKey: "nav.containers", icon: Boxes },
    { to: "/admin/storage", labelKey: "nav.storage", icon: HardDrive },
    { to: "/admin/images", labelKey: "nav.managedImages", icon: FolderKanban },
    { to: "/admin/members", labelKey: "nav.members", icon: Users },
    { to: "/admin/groups", labelKey: "nav.groups", icon: UsersRound },
    { to: "/admin/notices", labelKey: "nav.notices", icon: Megaphone },
    { to: "/admin/settings", labelKey: "nav.settings", icon: SlidersHorizontal },
    { to: "/admin/audits", labelKey: "nav.audits", icon: ScrollText },
  ];
});

function isActive(to: string): boolean {
  if (to === "/") return route.path === "/";
  return route.path === to || route.path.startsWith(`${to}/`);
}

function toggleLanguage() {
  const next = locale.current === "zh-hans" ? "en" : "zh-hans";
  void locale.switchLocale(next, auth.isAuthenticated);
}

async function handleSignOut() {
  notifications.reset();
  await auth.signOut();
  await router.push("/login");
}

const currentSectionTitle = computed(() => {
  const key = route.meta.titleKey as string | undefined;
  return key ? t(key) : t("app.name");
});

const userInitials = computed(() =>
  (auth.user?.first_name || auth.user?.username || "?").slice(0, 2),
);
</script>

<template>
  <div class="min-h-dvh bg-canvas text-body">
    <a href="#main-content" class="skip-link visually-hidden">{{ t("app.skipToContent") }}</a>

    <div class="flex min-h-dvh items-start">
      <!-- Desktop Sidebar: sticky full-viewport height so user & sign-out stay pinned -->
      <aside
        class="hidden shrink-0 border-r border-border-subtle bg-sidebar transition-[width] duration-200 lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col"
        :class="collapsed ? 'w-[4.25rem]' : 'w-64'"
        aria-label="Primary navigation"
      >
        <div
          class="flex h-14 shrink-0 items-center border-b border-border-subtle"
          :class="collapsed ? 'justify-center px-2' : 'justify-between gap-2 px-3.5'"
        >
          <RouterLink
            v-if="!collapsed"
            to="/"
            class="flex min-w-0 items-center gap-2.5 text-ink no-underline"
          >
            <BrandMark size="md" />
            <span class="truncate font-serif text-[1.0625rem] font-semibold leading-none tracking-tight text-ink">
              {{ t("app.name") }}
            </span>
          </RouterLink>
          <button
            type="button"
            class="btn btn-ghost btn-sm px-2"
            :title="collapsed ? t('app.expandSidebar') : t('app.collapseSidebar')"
            :aria-label="collapsed ? t('app.expandSidebar') : t('app.collapseSidebar')"
            @click="collapsed = !collapsed"
          >
            <PanelLeftOpen v-if="collapsed" class="size-4" aria-hidden="true" />
            <PanelLeftClose v-else class="size-4" aria-hidden="true" />
          </button>
        </div>

        <nav class="flex-1 overflow-y-auto px-2 py-4">
          <p
            v-if="!collapsed"
            class="mb-1.5 px-2.5 text-[11px] font-semibold tracking-wider text-muted uppercase"
          >
            {{ t("nav.workspace") }}
          </p>
          <ul class="space-y-0.5">
            <li v-for="item in workspaceNav" :key="item.to">
              <RouterLink
                :to="item.to"
                :title="collapsed ? t(item.labelKey) : undefined"
                :aria-current="isActive(item.to) ? 'page' : undefined"
                class="group relative flex items-center rounded-lg py-2 text-sm font-medium no-underline transition-colors"
                :class="[
                  collapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5',
                  isActive(item.to)
                    ? 'bg-card text-ink shadow-2xs ring-1 ring-border-subtle'
                    : 'text-body hover:bg-subtle/70 hover:text-ink',
                ]"
              >
                <component
                  :is="item.icon"
                  class="size-4 shrink-0"
                  :class="isActive(item.to) ? 'text-accent' : 'text-muted group-hover:text-ink'"
                  aria-hidden="true"
                />
                <span v-if="!collapsed" class="truncate">{{ t(item.labelKey) }}</span>
                <span
                  v-if="!collapsed && item.badge && item.badge > 0"
                  class="ml-auto rounded-full bg-accent px-1.5 py-0.5 text-[11px] font-semibold leading-none text-accent-ink tabular-nums"
                >
                  {{ item.badge }}
                </span>
                <span
                  v-else-if="collapsed && item.badge && item.badge > 0"
                  class="absolute top-1.5 right-2.5 size-2 rounded-full bg-accent"
                  aria-hidden="true"
                />
              </RouterLink>
            </li>
          </ul>

          <template v-if="adminNav.length">
            <div class="my-4 border-t border-border-subtle" />
            <p
              v-if="!collapsed"
              class="mb-1.5 px-2.5 text-[11px] font-semibold tracking-wider text-muted uppercase"
            >
              {{ t("nav.admin") }}
            </p>
            <ul class="space-y-0.5">
              <li v-for="item in adminNav" :key="item.to">
                <RouterLink
                  :to="item.to"
                  :title="collapsed ? t(item.labelKey) : undefined"
                  :aria-current="isActive(item.to) ? 'page' : undefined"
                  class="group relative flex items-center rounded-lg py-2 text-sm font-medium no-underline transition-colors"
                  :class="[
                    collapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5',
                    isActive(item.to)
                      ? 'bg-card text-ink shadow-2xs ring-1 ring-border-subtle'
                      : 'text-body hover:bg-subtle/70 hover:text-ink',
                  ]"
                >
                  <component
                    :is="item.icon"
                    class="size-4 shrink-0"
                    :class="isActive(item.to) ? 'text-accent' : 'text-muted group-hover:text-ink'"
                    aria-hidden="true"
                  />
                  <span v-if="!collapsed" class="truncate">{{ t(item.labelKey) }}</span>
                  <span
                    v-if="!collapsed && item.badge && item.badge > 0"
                    class="ml-auto rounded-full bg-accent px-1.5 py-0.5 text-[11px] font-semibold leading-none text-accent-ink tabular-nums"
                  >
                    {{ item.badge }}
                  </span>
                  <span
                    v-else-if="collapsed && item.badge && item.badge > 0"
                    class="absolute top-1.5 right-2.5 size-2 rounded-full bg-accent"
                    aria-hidden="true"
                  />
                </RouterLink>
              </li>
            </ul>
          </template>
        </nav>

        <div class="shrink-0 border-t border-border-subtle p-2.5">
          <div v-if="collapsed" class="flex flex-col items-center gap-1.5">
            <RouterLink
              to="/account"
              :title="`${auth.user?.first_name || auth.user?.username} — ${t('nav.account')}`"
              class="flex size-9 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent uppercase no-underline transition-colors hover:bg-accent/20"
            >
              {{ userInitials }}
            </RouterLink>
            <button
              type="button"
              class="btn btn-ghost btn-sm px-2"
              :title="t('app.signOut')"
              :aria-label="t('app.signOut')"
              @click="handleSignOut"
            >
              <LogOut class="size-4" aria-hidden="true" />
            </button>
          </div>
          <div v-else class="flex items-center gap-1.5">
            <RouterLink
              to="/account"
              class="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg p-1.5 text-ink no-underline transition-colors hover:bg-subtle/70"
            >
              <div
                class="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent uppercase"
                aria-hidden="true"
              >
                {{ userInitials }}
              </div>
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm font-medium text-ink">
                  {{ auth.user?.first_name || auth.user?.username }}
                </p>
                <p class="truncate text-xs text-muted">{{ auth.user?.username }}</p>
              </div>
            </RouterLink>
            <button
              type="button"
              class="btn btn-ghost btn-sm shrink-0 px-2"
              :title="t('app.signOut')"
              :aria-label="t('app.signOut')"
              @click="handleSignOut"
            >
              <LogOut class="size-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </aside>

      <!-- Mobile Navigation Drawer -->
      <DialogRoot v-model:open="mobileOpen">
        <DialogPortal>
          <DialogOverlay class="fixed inset-0 z-40 bg-ink/40 backdrop-blur-[2px] lg:hidden" />
          <DialogContent
            class="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r border-border-subtle bg-sidebar shadow-2xl focus:outline-none lg:hidden"
          >
            <div class="flex h-14 items-center justify-between border-b border-border-subtle px-4">
              <div class="flex items-center gap-2.5">
                <BrandMark size="sm" />
                <DialogTitle class="font-serif text-base font-semibold text-ink">
                  {{ t("app.name") }}
                </DialogTitle>
              </div>
              <DialogClose as-child>
                <button type="button" class="btn btn-ghost btn-sm px-2" :aria-label="t('app.close')">
                  <X class="size-4" aria-hidden="true" />
                </button>
              </DialogClose>
            </div>
            <nav class="flex-1 overflow-y-auto px-3 py-4">
              <p class="mb-1.5 px-2.5 text-[11px] font-semibold tracking-wider text-muted uppercase">
                {{ t("nav.workspace") }}
              </p>
              <ul class="space-y-0.5">
                <li v-for="item in workspaceNav" :key="item.to">
                  <RouterLink
                    :to="item.to"
                    :aria-current="isActive(item.to) ? 'page' : undefined"
                    class="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium no-underline"
                    :class="isActive(item.to) ? 'bg-card text-ink ring-1 ring-border-subtle' : 'text-body hover:bg-subtle/70'"
                  >
                    <component :is="item.icon" class="size-4 shrink-0" :class="isActive(item.to) ? 'text-accent' : 'text-muted'" aria-hidden="true" />
                    <span class="truncate">{{ t(item.labelKey) }}</span>
                    <span
                      v-if="item.badge && item.badge > 0"
                      class="ml-auto rounded-full bg-accent px-1.5 py-0.5 text-[11px] font-semibold leading-none text-accent-ink tabular-nums"
                    >
                      {{ item.badge }}
                    </span>
                  </RouterLink>
                </li>
              </ul>

              <template v-if="adminNav.length">
                <div class="my-4 border-t border-border-subtle" />
                <p class="mb-1.5 px-2.5 text-[11px] font-semibold tracking-wider text-muted uppercase">
                  {{ t("nav.admin") }}
                </p>
                <ul class="space-y-0.5">
                  <li v-for="item in adminNav" :key="item.to">
                    <RouterLink
                      :to="item.to"
                      :aria-current="isActive(item.to) ? 'page' : undefined"
                      class="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium no-underline"
                      :class="isActive(item.to) ? 'bg-card text-ink ring-1 ring-border-subtle' : 'text-body hover:bg-subtle/70'"
                    >
                      <component :is="item.icon" class="size-4 shrink-0" :class="isActive(item.to) ? 'text-accent' : 'text-muted'" aria-hidden="true" />
                      <span class="truncate">{{ t(item.labelKey) }}</span>
                      <span
                        v-if="item.badge && item.badge > 0"
                        class="ml-auto rounded-full bg-accent px-1.5 py-0.5 text-[11px] font-semibold leading-none text-accent-ink tabular-nums"
                      >
                        {{ item.badge }}
                      </span>
                    </RouterLink>
                  </li>
                </ul>
              </template>
            </nav>
            <div class="space-y-2 border-t border-border-subtle p-3">
              <RouterLink
                to="/account"
                class="flex items-center gap-2.5 rounded-lg p-1.5 text-ink no-underline hover:bg-subtle/70"
              >
                <div class="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent uppercase">
                  {{ userInitials }}
                </div>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-sm font-medium text-ink">{{ auth.user?.first_name || auth.user?.username }}</p>
                  <p class="truncate text-xs text-muted">{{ auth.user?.username }}</p>
                </div>
              </RouterLink>
              <button type="button" class="btn btn-secondary w-full justify-start" @click="handleSignOut">
                <LogOut class="size-4" aria-hidden="true" />
                {{ t("app.signOut") }}
              </button>
            </div>
          </DialogContent>
        </DialogPortal>
      </DialogRoot>

      <!-- Main Column -->
      <div class="flex min-w-0 flex-1 flex-col">
        <header class="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-border-subtle bg-canvas/90 px-5 backdrop-blur-xs sm:px-8 lg:px-10 xl:px-12">
          <div class="flex min-w-0 items-center gap-2.5">
            <button
              type="button"
              class="btn btn-ghost btn-sm px-2 lg:hidden"
              :aria-label="t('app.menu')"
              @click="mobileOpen = true"
            >
              <Menu class="size-5" aria-hidden="true" />
            </button>
            <span class="truncate text-sm font-medium text-muted">{{ currentSectionTitle }}</span>
          </div>

          <div class="flex items-center gap-1.5">
            <RouterLink
              v-if="auth.features.notifications"
              to="/inbox"
              class="btn btn-ghost btn-sm relative px-2.5"
              :aria-label="t('nav.inbox')"
            >
              <Bell class="size-4" aria-hidden="true" />
              <span
                v-if="notifications.unreadCount > 0"
                class="absolute top-1 right-1.5 size-2 rounded-full bg-accent"
                aria-hidden="true"
              />
            </RouterLink>

            <button
              type="button"
              class="btn btn-ghost btn-sm px-2.5"
              :aria-label="t('app.language')"
              @click="toggleLanguage"
            >
              <Globe class="size-4" aria-hidden="true" />
              <span class="text-xs font-medium">{{ locale.current === "zh-hans" ? "EN" : "中文" }}</span>
            </button>

            <button
              type="button"
              class="btn btn-ghost btn-sm px-2.5"
              :aria-label="t('app.toggleTheme')"
              :aria-pressed="theme.resolvedTheme === 'dark'"
              @click="theme.toggle()"
            >
              <Sun v-if="theme.resolvedTheme === 'dark'" class="size-4" aria-hidden="true" />
              <Moon v-else class="size-4" aria-hidden="true" />
            </button>
          </div>
        </header>

        <!-- Persistent Announcement Banner -->
        <div
          v-if="notifications.banner"
          class="border-b border-accent/25 bg-accent-soft px-5 py-3 sm:px-8 lg:px-10"
          role="region"
          :aria-label="notifications.banner.title"
        >
          <div class="mx-auto flex max-w-[1720px] items-start justify-between gap-4">
            <div class="min-w-0 text-sm">
              <p class="font-semibold text-ink">{{ notifications.banner.title }}</p>
              <p v-if="notifications.banner.content" class="mt-0.5 text-body">
                {{ notifications.banner.content }}
              </p>
              <RouterLink
                v-if="notifications.banner.link"
                :to="notifications.banner.link"
                class="mt-1 inline-block text-xs font-medium text-accent underline underline-offset-2"
              >
                {{ t("inbox.openRelated") }}
              </RouterLink>
            </div>
            <button
              type="button"
              class="btn btn-ghost btn-sm px-2"
              :aria-label="t('app.close')"
              @click="notifications.dismissBanner()"
            >
              <X class="size-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        <main
          id="main-content"
          tabindex="-1"
          class="mx-auto w-full max-w-[1720px] flex-1 px-5 py-6 outline-none sm:px-8 lg:px-10 lg:py-8"
        >
          <slot />
        </main>
      </div>
    </div>

    <ToastRegion />
  </div>
</template>
