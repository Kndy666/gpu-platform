<script setup lang="ts">
import { ref, watch, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import { Search, RefreshCw, Boxes } from "lucide-vue-next";
import { api, type TaskItem, type ServerNode } from "@/api/endpoints";
import { compactNumber, formatShortDateTime } from "@/lib/format";
import { isImeComposing } from "@/lib/ime";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const items = ref<TaskItem[]>([]);
const nodes = ref<ServerNode[]>([]);
const count = ref(0);
const page = ref(1);
const nextPage = ref<number | null>(null);
const loading = ref(true);

const searchInput = ref("");
const activeQuery = ref("");
const filterNode = ref("");
const filterMode = ref("");
const filterStatus = ref("");

async function load(targetPage = 1, silent = false) {
  if (!silent) loading.value = true;
  try {
    const res = await api.listContainers({
      node: filterNode.value || undefined,
      mode: filterMode.value || undefined,
      status: filterStatus.value || undefined,
      q: activeQuery.value || undefined,
      page: targetPage,
      page_size: 25,
    });
    items.value = res.items;
    count.value = res.count;
    page.value = res.page;
    nextPage.value = res.next_page;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

function runSearch() {
  activeQuery.value = searchInput.value.trim();
  void load(1);
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !isImeComposing(event)) {
    event.preventDefault();
    runSearch();
  }
}

watch([filterNode, filterMode, filterStatus], () => void load(1));

onMounted(async () => {
  const n = await api.listNodes().catch(() => null);
  if (n) nodes.value = n.items;
  void load(1);
});

useAutoRefresh(() => load(page.value, true), 15000);
</script>

<template>
  <div>
    <PageHeader :title="t('admin.containersTitle')" :subtitle="t('admin.containersSubtitle')">
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="load(page)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
      </template>
    </PageHeader>

    <div class="mb-4 flex flex-wrap items-center gap-3">
      <div class="relative min-w-56 flex-1 max-w-sm">
        <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden="true" />
        <input
          v-model="searchInput"
          type="search"
          class="field-input pl-9"
          :placeholder="t('admin.searchContainers')"
          @keydown="onKeydown"
        />
      </div>
      <select v-model="filterNode" class="field-input w-auto" :aria-label="t('tasks.server')">
        <option value="">{{ t("tasks.allServers") }}</option>
        <option v-for="n in nodes" :key="n.id" :value="String(n.id)">{{ n.name }}</option>
      </select>
      <select v-model="filterMode" class="field-input w-auto">
        <option value="">{{ t("tasks.allModes") }}</option>
        <option value="interactive">{{ t("tasks.modeInteractive") }}</option>
        <option value="batch">{{ t("tasks.modeBatch") }}</option>
      </select>
      <select v-model="filterStatus" class="field-input w-auto" :aria-label="t('tasks.status')">
        <option value="">{{ t("tasks.allStatuses") }}</option>
        <option
          v-for="st in ['running', 'submitted', 'queued', 'scheduled', 'starting', 'succeeded', 'stopped', 'failed', 'rejected']"
          :key="st"
          :value="st"
        >
          {{ t(`status.${st}`) }}
        </option>
      </select>
      <button type="button" class="btn btn-secondary" @click="runSearch">{{ t("app.search") }}</button>
    </div>

    <EmptyState v-if="!loading && !items.length" :icon="Boxes" :title="t('overview.idleDay')" />

    <template v-else>
      <div class="table-shell">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t("tasks.purpose") }}</th>
              <th>{{ t("admin.owner") }}</th>
              <th>{{ t("tasks.server") }}</th>
              <th>{{ t("tasks.resources") }}</th>
              <th>{{ t("tasks.schedule") }}</th>
              <th>{{ t("tasks.status") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in items" :key="task.id">
              <td>
                <RouterLink :to="`/tasks/${task.id}`" class="font-medium text-ink no-underline hover:text-accent">
                  {{ task.purpose }}
                </RouterLink>
                <p class="text-xs text-muted">
                  {{ task.mode === "interactive" ? t("tasks.modeInteractive") : t("tasks.modeBatch") }}
                </p>
              </td>
              <td>{{ task.username }}</td>
              <td>{{ task.node_name }}</td>
              <td class="whitespace-nowrap text-xs text-muted tabular-nums">
                {{ task.gpu > 0 ? `${compactNumber(task.gpu, 0)} GPU · ` : "" }}{{ compactNumber(task.cpu) }} {{ t("app.cores") }} · {{ compactNumber(task.memory_gib) }} GiB
              </td>
              <td class="whitespace-nowrap text-xs text-muted tabular-nums">
                {{ formatShortDateTime(task.created_at, locale.current, auth.timeZone) }}
              </td>
              <td><StatusBadge :status="task.status" :label="task.status_label" /></td>
            </tr>
          </tbody>
        </table>
      </div>

      <PaginationBar
        :page="page"
        :page-size="25"
        :count="count"
        :next-page="nextPage"
        @change="load"
      />
    </template>
  </div>
</template>
