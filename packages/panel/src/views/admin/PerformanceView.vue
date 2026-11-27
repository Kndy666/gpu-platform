<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useI18n } from "vue-i18n";
import { RefreshCw, Pause, Play, RotateCcw, ArrowDown, ArrowUp, ChevronDown } from "lucide-vue-next";
import type { EChartsCoreOption } from "echarts/core";
import { api, type PerformanceData, type PerformanceHistoryData } from "@/api/endpoints";
import { compactNumber, formatByteRate, formatDateTime, formatQuantity, formatShortDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useThemeStore } from "@/stores/theme";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import MetricBar from "@/components/ui/MetricBar.vue";
import EChartBox from "@/components/ui/EChartBox.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const theme = useThemeStore();

const live = ref<PerformanceData | null>(null);
const liveLoading = ref(true);
const liveFailed = ref(false);
const refreshSeconds = ref(5);
const paused = ref(false);

// History state
const history = ref<PerformanceHistoryData | null>(null);
const historyMetric = ref("cpu");
const historyDays = ref(1);
const selectedNodeIds = ref<string[]>([]);
const chartRef = ref<InstanceType<typeof EChartBox> | null>(null);

let timer: number | null = null;
const palette = ["#b85332", "#3e6790", "#296b4f", "#955b14", "#7b528c", "#507b82"];

async function loadLive() {
  liveLoading.value = true;
  try {
    live.value = await api.getPerformance();
    liveFailed.value = false;
  } catch {
    liveFailed.value = true;
  } finally {
    liveLoading.value = false;
  }
}

async function loadHistory() {
  try {
    const res = await api.getPerformanceHistory({
      metric: historyMetric.value,
      days: historyDays.value,
      nodes: selectedNodeIds.value.length ? selectedNodeIds.value : undefined,
    });
    history.value = res;
    if (!selectedNodeIds.value.length) {
      selectedNodeIds.value = res.history_nodes.filter((n) => n.selected).map((n) => String(n.id));
    }
  } catch {
    // Retain existing history view
  }
}

function restartTimer() {
  if (timer !== null) window.clearInterval(timer);
  timer = null;
  if (!paused.value) {
    timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadLive();
    }, refreshSeconds.value * 1000);
  }
}

function toggleHistoryNode(id: string) {
  if (selectedNodeIds.value.includes(id)) {
    selectedNodeIds.value = selectedNodeIds.value.filter((item) => item !== id);
  } else {
    selectedNodeIds.value = [...selectedNodeIds.value, id];
  }
  void loadHistory();
}

function resetZoom() {
  chartRef.value?.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
}

function timestampToIso(ts: number): string {
  // Backend sends Unix milliseconds; support seconds defensively too
  const ms = ts > 1e11 ? ts : ts * 1000;
  return new Date(ms).toISOString();
}

const historyChartOption = computed<EChartsCoreOption>(() => {
  const h = history.value?.history;
  if (!h || !h.has_data) return {};
  // Depend on resolvedTheme so CSS tokens re-read cleanly on light/dark switches
  const isDark = theme.resolvedTheme === "dark";
  const accent = isDark ? "#d97757" : "#b85332";
  const accentFill = isDark ? "rgba(217, 119, 87, 0.22)" : "rgba(184, 83, 50, 0.16)";
  const trackBg = isDark ? "#2c2b28" : "#ebe7dc";
  const borderCol = isDark ? "#3f3d38" : "#d8d2c5";
  const splitCol = isDark ? "#33322e" : "#e7e3d8";
  const handleFill = isDark ? "#302f2c" : "#ffffff";
  const inkBody = isDark ? "#c8c5bc" : "#3d3d3a";
  const inkMuted = isDark ? "#96938a" : "#67655f";
  const tooltipBg = isDark ? "#262523" : "#ffffff";

  const zone = h.zone || auth.timeZone;
  const xShort = h.timestamps.map((ts) => formatShortDateTime(timestampToIso(ts), locale.current, zone));
  const xFull = h.timestamps.map((ts) => formatDateTime(timestampToIso(ts), locale.current, zone));

  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: tooltipBg,
      borderColor: borderCol,
      textStyle: { color: inkBody, fontSize: 12 },
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [];
        if (!list.length) return "";
        const idx = Number((list[0] as { dataIndex?: number }).dataIndex ?? 0);
        const header = `<div style="font-weight:600;margin-bottom:4px">${xFull[idx] ?? ""}</div>`;
        const rows = list
          .map((item) => {
            const p = item as { marker?: string; seriesName?: string; value?: number | null };
            const val = p.value == null ? "—" : `${compactNumber(p.value)}${h.unit}`;
            return `<div style="display:flex;justify-content:space-between;gap:14px"><span>${p.marker ?? ""}${p.seriesName ?? ""}</span><strong>${val}</strong></div>`;
          })
          .join("");
        return header + rows;
      },
    },
    legend: {
      bottom: 0,
      itemGap: 14,
      textStyle: { color: inkBody, fontSize: 12 },
    },
    grid: { top: 20, left: 54, right: 24, bottom: 102 },
    dataZoom: [
      { type: "inside" },
      {
        type: "slider",
        height: 22,
        bottom: 36,
        backgroundColor: trackBg,
        borderColor: borderCol,
        fillerColor: accentFill,
        dataBackground: {
          lineStyle: { color: borderCol, width: 1 },
          areaStyle: { color: borderCol, opacity: 0.3 },
        },
        selectedDataBackground: {
          lineStyle: { color: accent, width: 1.5 },
          areaStyle: { color: accent, opacity: 0.18 },
        },
        handleStyle: {
          color: handleFill,
          borderColor: accent,
          borderWidth: 1.5,
        },
        moveHandleStyle: {
          color: accent,
          opacity: 0.75,
        },
        emphasis: {
          handleStyle: { borderColor: accent },
          moveHandleStyle: { color: accent },
        },
        textStyle: { color: inkMuted, fontSize: 11 },
      },
    ],
    xAxis: {
      type: "category",
      data: xShort,
      boundaryGap: false,
      axisLine: { lineStyle: { color: borderCol } },
      axisTick: { lineStyle: { color: borderCol } },
      axisLabel: { color: inkMuted, margin: 10, hideOverlap: true, fontSize: 11 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: inkMuted, formatter: `{value}${h.unit}` },
      splitLine: { lineStyle: { color: splitCol } },
    },
    series: h.nodes.map((node, i) => ({
      name: node.name,
      type: "line",
      smooth: 0.2,
      showSymbol: false,
      lineStyle: { width: 2 },
      itemStyle: { color: palette[i % palette.length] },
      data: node.points.map((pt) => pt[0]),
    })),
  };
});

watch([refreshSeconds, paused], restartTimer);
watch([historyMetric, historyDays], () => void loadHistory());

onMounted(async () => {
  await Promise.all([loadLive(), loadHistory()]);
  restartTimer();
});

onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer);
});
</script>

<template>
  <div class="space-y-8">
    <PageHeader :title="t('admin.performanceTitle')" :subtitle="t('admin.performanceSubtitle')">
      <template #actions>
        <select v-model.number="refreshSeconds" class="field-input min-h-8 w-auto py-1 text-xs" :aria-label="t('admin.refreshInterval')">
          <option :value="5">5 {{ t("app.seconds") }}</option>
          <option :value="15">15 {{ t("app.seconds") }}</option>
          <option :value="30">30 {{ t("app.seconds") }}</option>
        </select>
        <button type="button" class="btn btn-secondary btn-sm" @click="paused = !paused">
          <Play v-if="paused" class="size-3.5" aria-hidden="true" />
          <Pause v-else class="size-3.5" aria-hidden="true" />
          {{ paused ? t("admin.resume") : t("admin.pause") }}
        </button>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="liveLoading" @click="loadLive">
          <RefreshCw class="size-4" :class="{ 'animate-spin': liveLoading }" aria-hidden="true" />
          {{ t("app.refreshNow") }}
        </button>
      </template>
    </PageHeader>

    <p v-if="liveFailed" role="alert" class="rounded-lg border border-warn/30 bg-warn-bg px-4 py-2.5 text-sm text-warn">
      {{ t("admin.refreshFailed") }}
    </p>

    <!-- Archive storage info -->
    <div v-if="live?.archive" class="card flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-xs text-muted">
      <span>
        {{ t("admin.historyStorage") }}:
        <strong class="font-medium text-ink tabular-nums">{{ compactNumber(live.archive.used_mib) }} / {{ compactNumber(live.archive.max_mib) }} MiB</strong>
      </span>
      <span v-if="live.archive.last_at" class="tabular-nums">
        {{ t("admin.lastWrite", { time: formatDateTime(live.archive.last_at, locale.current, auth.timeZone) }) }}
      </span>
      <span v-if="live.archive_notice" class="text-warn">{{ live.archive_notice }}</span>
    </div>

    <!-- Live per-server telemetry -->
    <section class="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <article v-for="node in live?.nodes || []" :key="node.id" class="card space-y-4 p-5">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 class="text-base font-semibold text-ink">{{ node.name }}</h2>
            <p class="text-xs text-muted tabular-nums">
              {{ node.architecture }} · {{ formatDateTime(node.sampled_at, locale.current, auth.timeZone) }}
            </p>
          </div>
          <StatusBadge :status="node.fresh ? 'online' : 'offline'" :label="node.status_label" />
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div class="rounded-lg border border-border-subtle bg-canvas/60 p-3">
            <div class="mb-1 flex justify-between text-xs">
              <span class="text-muted">CPU</span>
              <span class="font-semibold text-ink tabular-nums">{{ compactNumber(node.metrics.cpu?.percent) }}%</span>
            </div>
            <MetricBar compact :percent="node.metrics.cpu?.percent" />
            <p v-if="node.metrics.cpu?.cores" class="mt-1 text-[11px] text-muted tabular-nums">
              {{ compactNumber(node.metrics.cpu.cores, 0) }} {{ t("app.cores") }}
              <template v-if="node.metrics.cpu.temperature_c != null"> · {{ compactNumber(node.metrics.cpu.temperature_c) }}°C</template>
            </p>
          </div>

          <div class="rounded-lg border border-border-subtle bg-canvas/60 p-3">
            <div class="mb-1 flex justify-between text-xs">
              <span class="text-muted">{{ t("overview.memory") }}</span>
              <span class="font-semibold text-ink tabular-nums">{{ compactNumber(node.metrics.memory?.percent) }}%</span>
            </div>
            <MetricBar compact :percent="node.metrics.memory?.percent" tone="info" />
            <p class="mt-1 text-[11px] text-muted tabular-nums">
              {{ compactNumber(node.metrics.memory?.used_gib) }} / {{ compactNumber(node.metrics.memory?.total_gib) }} GiB
            </p>
          </div>
        </div>

        <!-- Per-GPU live metrics -->
        <div v-if="node.metrics.gpus?.length" class="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <div
            v-for="gpu in node.metrics.gpus"
            :key="gpu.uuid || gpu.index"
            class="rounded-lg border border-border-subtle bg-elevated/60 p-3 text-xs"
          >
            <div class="flex items-center justify-between gap-2 font-medium text-ink">
              <span>{{ node.device_label }} #{{ gpu.index }}</span>
              <span class="text-muted tabular-nums">
                <template v-if="gpu.temperature_c != null">{{ compactNumber(gpu.temperature_c, 0) }}°C</template>
                <template v-if="gpu.power_w != null"> · {{ compactNumber(gpu.power_w, 0) }}W</template>
              </span>
            </div>
            <div class="mt-2 space-y-1.5">
              <div>
                <div class="mb-0.5 flex justify-between text-[11px] text-muted tabular-nums">
                  <span>{{ t("overview.compute") }}</span>
                  <span>{{ compactNumber(gpu.utilization_percent, 0) }}%</span>
                </div>
                <MetricBar compact :percent="gpu.utilization_percent" />
              </div>
              <div>
                <div class="mb-0.5 flex justify-between text-[11px] text-muted tabular-nums">
                  <span>{{ node.device_memory_label }}</span>
                  <span>{{ compactNumber((gpu.memory_used_mib ?? 0) / 1024) }} / {{ compactNumber((gpu.memory_total_mib ?? 0) / 1024) }} GiB</span>
                </div>
                <MetricBar compact :percent="gpu.memory_percent" tone="info" />
              </div>
            </div>
          </div>
        </div>

        <!-- Disks & Network compact row -->
        <div v-if="node.metrics.disks?.length || node.metrics.network?.length" class="grid grid-cols-1 gap-4 border-t border-border-subtle pt-3.5 text-xs sm:grid-cols-2">
          <div v-if="node.metrics.disks?.length" class="space-y-2">
            <p class="font-medium text-muted">{{ t("admin.diskUsage") }}</p>
            <div v-for="disk in node.metrics.disks" :key="disk.name" class="space-y-1">
              <div class="flex items-center justify-between gap-2 tabular-nums">
                <span class="truncate font-medium text-body">{{ disk.name }}</span>
                <span class="shrink-0 text-ink">
                  <template v-if="disk.used_gib != null && disk.total_gib != null">
                    {{ formatQuantity(disk.used_gib, "GiB") }} / {{ formatQuantity(disk.total_gib, "GiB") }}
                    <span class="ml-1 text-muted">({{ compactNumber(disk.percent, 0) }}%)</span>
                  </template>
                  <template v-else>{{ compactNumber(disk.percent, 0) }}%</template>
                </span>
              </div>
              <MetricBar compact :percent="disk.percent" :tone="(disk.percent ?? 0) > 85 ? 'warn' : 'accent'" />
            </div>
          </div>
          <div v-if="node.metrics.network?.length" class="space-y-1.5">
            <p class="font-medium text-muted">{{ t("admin.networkIo") }}</p>
            <div
              v-for="net in node.metrics.network"
              :key="net.name"
              class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-md bg-canvas/55 px-2.5 py-1.5 tabular-nums"
            >
              <span class="truncate font-medium text-body">{{ net.name }}</span>
              <div class="flex shrink-0 items-center gap-3 text-ink">
                <span class="inline-flex min-w-22 items-center justify-end gap-1">
                  <ArrowDown class="size-3.5 shrink-0 text-ok" aria-hidden="true" />
                  <span>{{ formatByteRate(net.rx_mib_s) }}</span>
                </span>
                <span class="inline-flex min-w-22 items-center justify-end gap-1">
                  <ArrowUp class="size-3.5 shrink-0 text-info" aria-hidden="true" />
                  <span>{{ formatByteRate(net.tx_mib_s) }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </article>
    </section>

    <!-- Historical Trends -->
    <section v-if="history" class="card space-y-4 p-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2>{{ t("admin.historyTitle") }}</h2>
        <div class="flex flex-wrap items-center gap-2">
          <select v-model="historyMetric" class="field-input min-h-8 w-auto py-1 text-xs" :aria-label="t('admin.metric')">
            <option v-for="(label, key) in history.history_metrics" :key="key" :value="key">{{ label }}</option>
          </select>
          <select v-model.number="historyDays" class="field-input min-h-8 w-auto py-1 text-xs" :aria-label="t('admin.timeRange')">
            <option v-for="days in history.history_ranges" :key="days" :value="days">
              {{ days === 1 ? t("admin.range24h") : t("admin.rangeDays", { count: days }) }}
            </option>
          </select>
          <button type="button" class="btn btn-secondary btn-sm" @click="resetZoom">
            <RotateCcw class="size-3.5" aria-hidden="true" />
            {{ t("admin.resetZoom") }}
          </button>
        </div>
      </div>

      <!-- Server selector chips -->
      <div class="flex flex-wrap gap-2">
        <button
          v-for="node in history.history_nodes"
          :key="node.id"
          type="button"
          class="btn btn-sm"
          :class="selectedNodeIds.includes(String(node.id)) ? 'btn-primary' : 'btn-secondary'"
          @click="toggleHistoryNode(String(node.id))"
        >
          {{ node.name }}
        </button>
      </div>

      <p v-if="history.history.coverage_note" class="text-xs text-muted">{{ history.history.coverage_note }}</p>

      <EChartBox
        v-if="history.history.has_data"
        ref="chartRef"
        :option="historyChartOption"
        height="360px"
        :aria-label="history.history.label"
      />
      <p v-else class="py-10 text-center text-sm text-muted">{{ t("admin.noSamples") }}</p>

      <details v-if="history.history.has_data" class="app-disclosure border-t border-border-subtle pt-3">
        <summary class="btn btn-ghost btn-sm inline-flex select-none items-center gap-1.5">
          <ChevronDown class="disclosure-chevron size-4 shrink-0 text-muted" aria-hidden="true" />
          <span>{{ t("admin.showDataTable") }}</span>
        </summary>
        <div class="table-shell mt-3">
          <table class="data-table">
            <thead>
              <tr>
                <th>{{ t("tasks.server") }}</th>
                <th>{{ t("admin.mean") }}</th>
                <th>{{ t("admin.peak") }}</th>
                <th>{{ t("admin.samples") }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="node in history.history.nodes" :key="node.id">
                <td class="font-medium text-ink">{{ node.name }}</td>
                <td class="tabular-nums">{{ compactNumber(node.mean) }}{{ history.history.unit }}</td>
                <td class="tabular-nums">{{ compactNumber(node.peak) }}{{ history.history.unit }}</td>
                <td class="tabular-nums">{{ node.samples }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </section>
  </div>
</template>
