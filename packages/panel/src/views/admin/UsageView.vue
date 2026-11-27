<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { ArrowUpDown, RefreshCw } from "lucide-vue-next";
import type { EChartsCoreOption } from "echarts/core";
import { api, type UsageLiveData, type UsageHistoryData, type UsageRow } from "@/api/endpoints";
import { compactNumber } from "@/lib/format";
import { useNotificationsStore } from "@/stores/notifications";
import { useThemeStore } from "@/stores/theme";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import SegmentedTabs from "@/components/domain/SegmentedTabs.vue";
import EChartBox from "@/components/ui/EChartBox.vue";
import CopyButton from "@/components/ui/CopyButton.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import DatePickerInput from "@/components/ui/DatePickerInput.vue";

const { t } = useI18n();
const notices = useNotificationsStore();
const theme = useThemeStore();

const live = ref<UsageLiveData | null>(null);
const history = ref<UsageHistoryData | null>(null);
const loading = ref(true);
const initialized = ref(false);

// Filters (auto-applied on change)
const fromDate = ref("");
const toDate = ref("");
const account = ref("");
const node = ref("");
const model = ref("");

// Breakdown tab & sorting
const breakdownTab = ref("member");
const sortKey = ref<keyof UsageRow>("gpu_hours");
const sortDir = ref<"asc" | "desc">("desc");

const breakdownTabs = computed(() => [
  { value: "member", label: t("admin.byMember") },
  { value: "model", label: t("admin.byModel") },
  { value: "server", label: t("admin.byServer") },
]);

const usageColumns = computed<{ key: keyof UsageRow; label: string }[]>(() => [
  { key: "label", label: t("tasks.purpose") },
  { key: "applications", label: t("admin.applications") },
  { key: "tasks", label: t("admin.tasksWithRuntime") },
  { key: "hours", label: t("admin.taskHours") },
  { key: "gpu_hours", label: t("admin.gpuHours") },
  { key: "npu_hours", label: t("admin.npuHours") },
]);

async function loadAll(silent = false) {
  if (!silent) loading.value = true;
  try {
    const [liveRes, histRes] = await Promise.all([
      api.getUsageLive(),
      api.getUsageHistory({
        from: fromDate.value || undefined,
        to: toDate.value || undefined,
        account: account.value || undefined,
        node: node.value || undefined,
        model: model.value || undefined,
      }),
    ]);
    live.value = liveRes;
    history.value = histRes;
    if (!initialized.value) {
      if (!fromDate.value && histRes.filters?.from) fromDate.value = histRes.filters.from;
      if (!toDate.value && histRes.filters?.to) toDate.value = histRes.filters.to;
      initialized.value = true;
    }
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function applyFilters() {
  try {
    history.value = await api.getUsageHistory({
      from: fromDate.value || undefined,
      to: toDate.value || undefined,
      account: account.value || undefined,
      node: node.value || undefined,
      model: model.value || undefined,
    });
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
  }
}

watch([fromDate, toDate, account, node, model], () => {
  if (initialized.value) {
    void applyFilters();
  }
});

function toggleSort(key: keyof UsageRow) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === "desc" ? "asc" : "desc";
  } else {
    sortKey.value = key;
    sortDir.value = key === "label" ? "asc" : "desc";
  }
}

function sortRows(rows: UsageRow[]): UsageRow[] {
  const dir = sortDir.value === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = a[sortKey.value];
    const vb = b[sortKey.value];
    if (typeof va === "string" || typeof vb === "string") {
      return String(va ?? "").localeCompare(String(vb ?? "")) * dir;
    }
    return (Number(va ?? 0) - Number(vb ?? 0)) * dir;
  });
}

function ringOption(allocated: number, available: number): EChartsCoreOption {
  const free = Math.max(0, available - allocated);
  const isDark = theme.resolvedTheme === "dark";
  return {
    tooltip: { trigger: "item" },
    series: [
      {
        type: "pie",
        radius: ["62%", "86%"],
        label: { show: false },
        data: [
          { value: allocated, name: t("admin.allocated"), itemStyle: { color: isDark ? "#d97757" : "#b85332" } },
          { value: free, name: t("admin.unallocated"), itemStyle: { color: isDark ? "#383733" : "#dcd7ca" } },
        ],
      },
    ],
  };
}

const trendOption = computed<EChartsCoreOption>(() => {
  const tr = history.value?.trend;
  if (!tr || !tr.dates.length) return {};
  const isDark = theme.resolvedTheme === "dark";
  const inkBody = isDark ? "#c8c5bc" : "#3d3d3a";
  const inkMuted = isDark ? "#96938a" : "#67655f";
  const splitCol = isDark ? "#33322e" : "#e7e3d8";
  const borderCol = isDark ? "#3f3d38" : "#d8d2c5";
  const tooltipBg = isDark ? "#262523" : "#ffffff";
  const colors = isDark
    ? ["#d97757", "#7fa9d4", "#6dc09c", "#e5a956", "#b18cc4"]
    : ["#b85332", "#3e6790", "#296b4f", "#955b14", "#7b528c"];

  return {
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: tooltipBg,
      borderColor: borderCol,
      textStyle: { color: inkBody, fontSize: 13 },
    },
    legend: {
      bottom: 4,
      itemGap: 16,
      itemWidth: 14,
      itemHeight: 10,
      textStyle: { color: inkBody, fontSize: 12 },
    },
    grid: { top: 24, left: 56, right: 24, bottom: 76 },
    xAxis: {
      type: "category",
      data: tr.dates,
      axisLine: { lineStyle: { color: borderCol } },
      axisTick: { show: false },
      axisLabel: { color: inkMuted, margin: 12, fontSize: 12, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: inkMuted, margin: 10, fontSize: 12 },
      splitLine: { lineStyle: { color: splitCol } },
    },
    series: tr.series.map((s, idx) => ({
      name: s.name,
      type: "bar",
      stack: "total",
      barMaxWidth: 30,
      itemStyle: {
        color: colors[idx % colors.length],
        borderRadius: idx === tr.series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0],
      },
      data: s.values,
    })),
  };
});

const rankingOption = computed<EChartsCoreOption>(() => {
  const rank = history.value?.ranking ?? [];
  if (!rank.length) return {};
  const isDark = theme.resolvedTheme === "dark";
  const inkBody = isDark ? "#c8c5bc" : "#3d3d3a";
  const inkMuted = isDark ? "#96938a" : "#67655f";
  const splitCol = isDark ? "#33322e" : "#e7e3d8";
  const borderCol = isDark ? "#3f3d38" : "#d8d2c5";
  const tooltipBg = isDark ? "#262523" : "#ffffff";

  return {
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: tooltipBg,
      borderColor: borderCol,
      textStyle: { color: inkBody, fontSize: 13 },
    },
    legend: {
      bottom: 4,
      itemGap: 16,
      itemWidth: 14,
      itemHeight: 10,
      textStyle: { color: inkBody, fontSize: 12 },
    },
    grid: { top: 20, left: 108, right: 28, bottom: 72 },
    xAxis: {
      type: "value",
      axisLabel: { color: inkMuted, margin: 12, fontSize: 12 },
      splitLine: { lineStyle: { color: splitCol } },
    },
    yAxis: {
      type: "category",
      data: rank.map((r) => r.name),
      inverse: true,
      axisLine: { lineStyle: { color: borderCol } },
      axisTick: { show: false },
      axisLabel: { color: inkBody, margin: 12, fontSize: 13 },
    },
    series: [
      {
        name: t("admin.gpuHours"),
        type: "bar",
        stack: "hours",
        barMaxWidth: 22,
        itemStyle: { color: isDark ? "#d97757" : "#b85332", borderRadius: [0, 4, 4, 0] },
        data: rank.map((r) => r.gpu),
      },
      {
        name: t("admin.npuHours"),
        type: "bar",
        stack: "hours",
        barMaxWidth: 22,
        itemStyle: { color: isDark ? "#7fa9d4" : "#3e6790", borderRadius: [0, 4, 4, 0] },
        data: rank.map((r) => r.npu),
      },
    ],
  };
});

const textReport = computed(() => {
  if (!history.value) return "";
  const h = history.value;
  const lines = [
    `Usage Report (${fromDate.value || h.first} to ${toDate.value || h.as_of})`,
    `Totals: ${h.totals.applications} requests, ${h.totals.tasks} tasks, ${compactNumber(h.totals.gpu_hours)} GPU-h, ${compactNumber(h.totals.npu_hours)} NPU-h`,
    "",
    ...h.people.slice(0, 15).map((p) => `- ${p.label}: ${compactNumber(p.gpu_hours)} GPU-h, ${compactNumber(p.npu_hours)} NPU-h (${p.tasks} tasks)`),
  ];
  return lines.join("\n");
});

onMounted(() => {
  void loadAll(false);
});

useAutoRefresh(() => loadAll(true), 30000);
</script>

<template>
  <div class="space-y-8">
    <PageHeader :title="t('admin.usageTitle')" :subtitle="t('admin.usageSubtitle')">
      <template #actions>
        <CopyButton v-if="textReport" :text="textReport" :label="t('admin.copyReport')" />
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="loadAll(false)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
      </template>
    </PageHeader>

    <!-- Live Allocation Summary -->
    <section v-if="live" class="space-y-4">
      <h2>{{ t("admin.liveSection") }}</h2>
      <div class="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <div class="card p-4">
          <p class="text-xs text-muted">{{ t("admin.onlineServers") }}</p>
          <p class="mt-1 font-serif text-2xl text-ink tabular-nums">{{ live.online }} / {{ live.node_count }}</p>
        </div>
        <div class="card p-4">
          <p class="text-xs text-muted">{{ t("admin.runningTasks") }}</p>
          <p class="mt-1 font-serif text-2xl text-ink tabular-nums">{{ live.running_count }}</p>
        </div>
        <div v-for="(cap, kind) in live.capacity" :key="kind" class="card flex items-center justify-between gap-3 p-4">
          <div>
            <p class="text-xs text-muted uppercase">{{ kind }}</p>
            <p class="mt-1 text-lg font-semibold text-ink tabular-nums">{{ compactNumber(cap.allocated) }} / {{ compactNumber(cap.available) }}</p>
            <p v-if="cap.actual != null" class="text-xs text-muted tabular-nums">{{ t("admin.actualLoad") }}: {{ compactNumber(cap.actual, 0) }}%</p>
          </div>
          <div class="w-16 shrink-0">
            <EChartBox :option="ringOption(cap.allocated, cap.available)" height="64px" :aria-label="kind" />
          </div>
        </div>
      </div>

      <div class="table-shell">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t("tasks.server") }}</th>
              <th>{{ t("tasks.status") }}</th>
              <th>{{ t("admin.allocated") }}</th>
              <th>CPU</th>
              <th>{{ t("overview.memory") }}</th>
              <th>{{ t("admin.runningTasks") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="n in live.nodes" :key="n.id">
              <td class="font-medium text-ink">{{ n.name }}</td>
              <td><StatusBadge :status="n.state" /></td>
              <td class="tabular-nums">{{ compactNumber(n.allocated) }} / {{ compactNumber(n.available) }} {{ n.kind }}</td>
              <td class="tabular-nums text-sm text-muted">{{ n.cpu != null ? `${compactNumber(n.cpu, 0)}%` : "—" }}</td>
              <td class="tabular-nums text-sm text-muted">{{ n.memory != null ? `${compactNumber(n.memory, 0)}%` : "—" }}</td>
              <td class="tabular-nums text-sm">{{ n.running.length }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Historical Usage -->
    <section v-if="history" class="space-y-5">
      <h2>{{ t("admin.historySection") }}</h2>

      <!-- Auto-applying filters (no Apply button needed) -->
      <div class="card flex flex-wrap items-end gap-3.5 p-4">
        <div>
          <label for="u-from" class="mb-1.5 block text-xs font-medium text-muted">{{ t("admin.fromDate") }}</label>
          <DatePickerInput id="u-from" v-model="fromDate" clearable compact :aria-label="t('admin.fromDate')" />
        </div>
        <div>
          <label for="u-to" class="mb-1.5 block text-xs font-medium text-muted">{{ t("admin.toDate") }}</label>
          <DatePickerInput id="u-to" v-model="toDate" clearable compact :aria-label="t('admin.toDate')" />
        </div>
        <div>
          <label for="u-acc" class="mb-1.5 block text-xs font-medium text-muted">{{ t("admin.accountFilter") }}</label>
          <select id="u-acc" v-model="account" class="field-input min-h-9 w-auto py-1.5 text-sm">
            <option value="">{{ t("admin.allAccounts") }}</option>
            <option v-for="opt in history.account_options" :key="opt.id" :value="opt.id">{{ opt.label }}</option>
          </select>
        </div>
        <div>
          <label for="u-node" class="mb-1.5 block text-xs font-medium text-muted">{{ t("admin.serverFilter") }}</label>
          <select id="u-node" v-model="node" class="field-input min-h-9 w-auto py-1.5 text-sm">
            <option value="">{{ t("tasks.allServers") }}</option>
            <option v-for="opt in history.node_options" :key="opt.id" :value="opt.id">{{ opt.label }}</option>
          </select>
        </div>
        <div>
          <label for="u-model" class="mb-1.5 block text-xs font-medium text-muted">{{ t("admin.modelFilter") }}</label>
          <select id="u-model" v-model="model" class="field-input min-h-9 w-auto py-1.5 text-sm">
            <option value="">{{ t("admin.allModels") }}</option>
            <option v-for="opt in history.model_options" :key="opt.id" :value="opt.id">{{ opt.label }}</option>
          </select>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <div class="card p-5">
          <h3 class="mb-3">{{ t("admin.trendTitle") }}</h3>
          <EChartBox v-if="history.trend.dates.length" :option="trendOption" height="340px" :aria-label="t('admin.trendTitle')" />
          <p v-else class="py-12 text-center text-sm text-muted">{{ t("admin.noUsageRecords") }}</p>
        </div>
        <div class="card p-5">
          <h3 class="mb-3">{{ t("admin.rankingTitle") }}</h3>
          <EChartBox v-if="history.ranking.length" :option="rankingOption" height="340px" :aria-label="t('admin.rankingTitle')" />
          <p v-else class="py-12 text-center text-sm text-muted">{{ t("admin.noUsageRecords") }}</p>
        </div>
      </div>

      <SegmentedTabs v-model="breakdownTab" :tabs="breakdownTabs">
        <template
          v-for="tab in [
            { key: 'member', rows: history.people },
            { key: 'model', rows: history.by_model },
            { key: 'server', rows: history.by_node },
          ]"
          :key="tab.key"
          #[tab.key]
        >
          <div class="table-shell">
            <table class="data-table">
              <thead>
                <tr>
                  <th
                    v-for="col in usageColumns"
                    :key="col.key"
                    scope="col"
                    :aria-sort="sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined"
                  >
                    <button
                      type="button"
                      class="flex w-full items-center gap-1 px-4 py-3 text-left text-sm font-medium text-muted hover:text-ink"
                      @click="toggleSort(col.key)"
                    >
                      <span>{{ col.label }}</span>
                      <ArrowUpDown class="size-3.5" aria-hidden="true" />
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in sortRows(tab.rows)" :key="row.label">
                  <td class="font-medium text-ink">{{ row.label }}</td>
                  <td class="tabular-nums">{{ row.applications }}</td>
                  <td class="tabular-nums">{{ row.tasks }}</td>
                  <td class="tabular-nums">{{ compactNumber(row.hours) }}</td>
                  <td class="tabular-nums">{{ compactNumber(row.gpu_hours) }}</td>
                  <td class="tabular-nums">{{ compactNumber(row.npu_hours) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </SegmentedTabs>
    </section>
  </div>
</template>
