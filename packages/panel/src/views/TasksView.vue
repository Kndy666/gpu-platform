<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import { Plus, Play, RefreshCw, Square, TerminalSquare, Cpu } from "lucide-vue-next";
import { api, type TaskItem, type ServerNode } from "@/api/endpoints";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { compactNumber, formatShortDateTime } from "@/lib/format";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CopyButton from "@/components/ui/CopyButton.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";

const props = defineProps<{
  mode: "interactive" | "batch";
}>();

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const items = ref<TaskItem[]>([]);
const nodes = ref<ServerNode[]>([]);
const count = ref(0);
const page = ref(1);
const pageSize = ref(20);
const nextPage = ref<number | null>(null);
const filterNode = ref("");
const filterStatus = ref("");
const loading = ref(true);

const stopTarget = ref<TaskItem | null>(null);
const stopping = ref(false);

const statuses = [
  "submitted",
  "scheduled",
  "queued",
  "starting",
  "running",
  "succeeded",
  "failed",
  "stopped",
  "rejected",
];

const isEnv = computed(() => props.mode === "interactive");

async function loadTasks(targetPage = page.value, silent = false) {
  if (!silent) loading.value = true;
  try {
    const res = await api.listTasks({
      scope: "own",
      mode: props.mode,
      node: filterNode.value || undefined,
      status: filterStatus.value || undefined,
      page: targetPage,
      page_size: pageSize.value,
    });
    items.value = res.items;
    count.value = res.count;
    page.value = res.page;
    nextPage.value = res.next_page;
  } catch (err) {
    if (!silent) {
      notices.pushToast({
        level: "error",
        title: err instanceof Error ? err.message : t("app.loadError"),
        seconds: 6,
      });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function loadNodes() {
  try {
    const res = await api.listNodes();
    nodes.value = res.items;
  } catch {
    // Non-critical filter list
  }
}

watch(
  () => props.mode,
  () => {
    page.value = 1;
    void loadTasks(1);
  },
);

watch([filterNode, filterStatus], () => {
  page.value = 1;
  void loadTasks(1);
});

onMounted(() => {
  void loadNodes();
  void loadTasks(1);
});

useAutoRefresh(() => loadTasks(page.value, true), 10000);

function canStopTask(task: TaskItem): boolean {
  return !task.stop_requested && ["submitted", "scheduled", "queued", "starting", "running"].includes(task.status);
}

async function confirmStop() {
  if (!stopTarget.value) return;
  stopping.value = true;
  try {
    await api.stopTask(stopTarget.value.id);
    notices.pushToast({ level: "success", title: t("tasks.stopRequested"), seconds: 5 });
    stopTarget.value = null;
    await loadTasks(page.value);
  } catch (err) {
    notices.pushToast({
      level: "error",
      title: err instanceof Error ? err.message : t("app.loadError"),
      seconds: 6,
    });
  } finally {
    stopping.value = false;
  }
}

function formatResources(task: TaskItem): string {
  const parts: string[] = [];
  if (task.gpu > 0) parts.push(`${compactNumber(task.gpu, 0)} GPU (${compactNumber(task.gpu_memory_total_gib ?? 0)} GiB)`);
  parts.push(`${compactNumber(task.cpu)} ${t("app.cores")}`);
  parts.push(`${compactNumber(task.memory_gib)} GiB`);
  return parts.join(" · ");
}
</script>

<template>
  <div>
    <PageHeader
      :title="isEnv ? t('tasks.envTitle') : t('tasks.jobTitle')"
      :subtitle="isEnv ? t('tasks.envSubtitle') : t('tasks.jobSubtitle')"
    >
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="loadTasks(page)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
        <RouterLink :to="isEnv ? '/environments/new' : '/jobs/new'" class="btn btn-primary">
          <Plus v-if="isEnv" class="size-4" aria-hidden="true" />
          <Play v-else class="size-4" aria-hidden="true" />
          {{ isEnv ? t("tasks.newEnv") : t("tasks.newJob") }}
        </RouterLink>
      </template>
    </PageHeader>

    <!-- Filters -->
    <div class="mb-4 flex flex-wrap items-center gap-3">
      <select v-model="filterNode" class="field-input w-auto min-w-44" :aria-label="t('tasks.server')">
        <option value="">{{ t("tasks.allServers") }}</option>
        <option v-for="node in nodes" :key="node.id" :value="String(node.id)">
          {{ node.name }}
        </option>
      </select>

      <select v-model="filterStatus" class="field-input w-auto min-w-44" :aria-label="t('tasks.status')">
        <option value="">{{ t("tasks.allStatuses") }}</option>
        <option v-for="st in statuses" :key="st" :value="st">{{ t(`status.${st}`) }}</option>
      </select>
    </div>

    <div v-if="loading && !items.length" class="card p-10 text-center text-sm text-muted">
      {{ t("app.loading") }}
    </div>

    <EmptyState
      v-else-if="!items.length"
      :icon="isEnv ? TerminalSquare : Cpu"
      :title="isEnv ? t('tasks.emptyEnv') : t('tasks.emptyJob')"
      :hint="isEnv ? t('tasks.emptyEnvHint') : t('tasks.emptyJobHint')"
    >
      <template #action>
        <RouterLink :to="isEnv ? '/environments/new' : '/jobs/new'" class="btn btn-primary">
          {{ isEnv ? t("tasks.newEnv") : t("tasks.newJob") }}
        </RouterLink>
      </template>
    </EmptyState>

    <template v-else>
      <div class="table-shell">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t("tasks.purpose") }}</th>
              <th>{{ t("tasks.server") }}</th>
              <th>{{ t("tasks.resources") }}</th>
              <th>{{ t("tasks.schedule") }}</th>
              <th>{{ t("tasks.status") }}</th>
              <th class="text-end">{{ t("app.actions") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in items" :key="task.id">
              <td>
                <RouterLink :to="`/tasks/${task.id}`" class="font-medium text-ink no-underline hover:text-accent">
                  {{ task.purpose }}
                </RouterLink>
                <p v-if="task.image_name" class="mt-0.5 text-xs text-muted">{{ task.image_name }}</p>
              </td>
              <td class="whitespace-nowrap">{{ task.node_name }}</td>
              <td class="whitespace-nowrap text-xs text-muted tabular-nums">
                {{ formatResources(task) }}
              </td>
              <td class="whitespace-nowrap text-xs text-muted tabular-nums">
                <template v-if="task.ends_at">
                  {{ formatShortDateTime(task.starts_at || task.created_at, locale.current, auth.timeZone) }} –
                  {{ formatShortDateTime(task.ends_at, locale.current, auth.timeZone) }}
                </template>
                <template v-else>
                  {{ formatShortDateTime(task.created_at, locale.current, auth.timeZone) }}
                </template>
              </td>
              <td>
                <StatusBadge :status="task.status" :label="task.status_label" />
              </td>
              <td class="text-end whitespace-nowrap">
                <div class="inline-flex items-center gap-1.5">
                  <CopyButton
                    v-if="isEnv && task.status === 'running' && task.connection?.command"
                    :text="task.connection.command"
                    :label="t('overview.quickSsh')"
                  />
                  <button
                    v-if="canStopTask(task)"
                    type="button"
                    class="btn btn-danger btn-sm"
                    @click="stopTarget = task"
                  >
                    <Square class="size-3.5" aria-hidden="true" />
                    {{ task.status === "running" ? t("tasks.stop") : t("tasks.cancelTask") }}
                  </button>
                  <RouterLink :to="`/tasks/${task.id}`" class="btn btn-ghost btn-sm">
                    {{ t("tasks.openDetail") }}
                  </RouterLink>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <PaginationBar
        :page="page"
        :page-size="pageSize"
        :count="count"
        :next-page="nextPage"
        @change="loadTasks"
      />
    </template>

    <ConfirmDialog
      :open="stopTarget !== null"
      :title="t('tasks.stopConfirmTitle')"
      :message="t('tasks.stopConfirmBody')"
      :confirm-label="t('tasks.stopConfirmAction')"
      danger
      :busy="stopping"
      @update:open="!$event && (stopTarget = null)"
      @confirm="confirmStop"
    />
  </div>
</template>
