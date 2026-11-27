<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue";
import { RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import { Plus, Play, RefreshCw, TerminalSquare } from "lucide-vue-next";
import { api, type ResourcesOverview, type TimelineData, type TaskItem } from "@/api/endpoints";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { compactNumber, formatShortDateTime, todayInZone } from "@/lib/format";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import NodeCapacityCard from "@/components/domain/NodeCapacityCard.vue";
import TimelineBoard from "@/components/domain/TimelineBoard.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CopyButton from "@/components/ui/CopyButton.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const overview = ref<ResourcesOverview | null>(null);
const timeline = ref<TimelineData | null>(null);
const tasks = ref<TaskItem[]>([]);
const selectedDate = ref(todayInZone(auth.timeZone));
const loading = ref(true);
const timelineLoading = ref(false);

const summaryText = computed(() => {
  if (!overview.value) return "";
  return t("overview.summary", {
    servers: overview.value.nodes.length,
    running: overview.value.running_count,
    queued: overview.value.queued_count,
  });
});

async function loadAll(silent = false) {
  if (!silent) loading.value = true;
  try {
    const [resOverview, resTimeline, resTasks] = await Promise.all([
      api.getResources(),
      api.getTimeline(selectedDate.value),
      api.listTasks({ scope: "own", page: 1, page_size: 8 }),
    ]);
    overview.value = resOverview;
    timeline.value = resTimeline;
    tasks.value = resTasks.items;
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

async function loadTimeline(date: string) {
  timelineLoading.value = true;
  try {
    timeline.value = await api.getTimeline(date);
  } catch {
    // Keep last visible timeline on transient failure
  } finally {
    timelineLoading.value = false;
  }
}

watch(selectedDate, (date) => {
  if (date) void loadTimeline(date);
});

onMounted(() => {
  void loadAll(false);
});

useAutoRefresh(() => loadAll(true), 10000);

function formatTaskResources(task: TaskItem): string {
  const parts: string[] = [];
  if (task.gpu > 0) parts.push(`${compactNumber(task.gpu, 0)} GPU`);
  parts.push(`${compactNumber(task.cpu)} ${t("app.cores")}`);
  parts.push(`${compactNumber(task.memory_gib)} GiB`);
  return parts.join(" · ");
}
</script>

<template>
  <div class="space-y-8">
    <PageHeader :title="t('overview.title')" :subtitle="summaryText">
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="loadAll(false)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
        <RouterLink to="/jobs/new" class="btn btn-secondary">
          <Play class="size-4" aria-hidden="true" />
          {{ t("overview.newJob") }}
        </RouterLink>
        <RouterLink to="/environments/new" class="btn btn-primary">
          <Plus class="size-4" aria-hidden="true" />
          {{ t("overview.newEnv") }}
        </RouterLink>
      </template>
    </PageHeader>

    <!-- Server availability grid -->
    <section aria-labelledby="servers-heading">
      <div class="mb-3.5 flex items-center justify-between gap-3">
        <h2 id="servers-heading">{{ t("overview.serverCapacity") }}</h2>
        <div v-if="overview?.accelerator_counts?.length" class="flex flex-wrap gap-2 text-xs text-muted">
          <span
            v-for="item in overview.accelerator_counts"
            :key="item.label"
            class="rounded-full border border-border-subtle bg-card px-2.5 py-0.5 tabular-nums"
          >
            {{ item.count }} {{ item.label }}
          </span>
        </div>
      </div>

      <div v-if="loading && !overview" class="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div class="card h-52 animate-pulse bg-subtle/40" />
        <div class="card h-52 animate-pulse bg-subtle/40" />
      </div>

      <EmptyState
        v-else-if="!overview || !overview.nodes.length"
        :title="t('overview.waitingReport')"
      />

      <div v-else class="grid grid-cols-1 items-start gap-3.5 xl:grid-cols-2">
        <NodeCapacityCard v-for="node in overview.nodes" :key="node.id" :node="node" />
      </div>
    </section>

    <!-- Daily schedule timeline -->
    <TimelineBoard
      :data="timeline"
      :date="selectedDate"
      :loading="timelineLoading"
      @update:date="selectedDate = $event"
    />

    <!-- Recent tasks -->
    <section aria-labelledby="recent-tasks-heading">
      <div class="mb-3.5 flex items-center justify-between gap-3">
        <h2 id="recent-tasks-heading">{{ t("overview.recentTasks") }}</h2>
        <div class="flex items-center gap-3 text-sm">
          <RouterLink to="/environments" class="text-accent no-underline hover:underline">
            {{ t("nav.environments") }}
          </RouterLink>
          <RouterLink to="/jobs" class="text-accent no-underline hover:underline">
            {{ t("nav.jobs") }}
          </RouterLink>
        </div>
      </div>

      <EmptyState
        v-if="!loading && !tasks.length"
        :icon="TerminalSquare"
        :title="t('overview.noRecentTasks')"
        :hint="t('overview.noRecentHint')"
      >
        <template #action>
          <RouterLink to="/environments/new" class="btn btn-primary">
            {{ t("overview.newEnv") }}
          </RouterLink>
        </template>
      </EmptyState>

      <div v-else class="table-shell">
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
            <tr v-for="task in tasks" :key="task.id">
              <td>
                <RouterLink :to="`/tasks/${task.id}`" class="font-medium text-ink no-underline hover:text-accent">
                  {{ task.purpose }}
                </RouterLink>
                <p class="mt-0.5 text-xs text-muted">
                  {{ task.mode === "interactive" ? t("tasks.modeInteractive") : t("tasks.modeBatch") }}
                </p>
              </td>
              <td class="whitespace-nowrap">{{ task.node_name }}</td>
              <td class="whitespace-nowrap tabular-nums text-xs text-muted">
                {{ formatTaskResources(task) }}
              </td>
              <td class="whitespace-nowrap tabular-nums text-xs text-muted">
                <span v-if="task.ends_at">
                  {{ formatShortDateTime(task.starts_at || task.created_at, locale.current, auth.timeZone) }} –
                  {{ formatShortDateTime(task.ends_at, locale.current, auth.timeZone) }}
                </span>
                <span v-else>{{ formatShortDateTime(task.created_at, locale.current, auth.timeZone) }}</span>
              </td>
              <td>
                <StatusBadge :status="task.status" :label="task.status_label" />
              </td>
              <td class="text-end whitespace-nowrap">
                <div class="inline-flex items-center gap-1.5">
                  <CopyButton
                    v-if="task.mode === 'interactive' && task.status === 'running' && task.connection?.command"
                    :text="task.connection.command"
                    :label="t('overview.quickSsh')"
                  />
                  <RouterLink :to="`/tasks/${task.id}`" class="btn btn-ghost btn-sm">
                    {{ t("tasks.openDetail") }}
                  </RouterLink>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
