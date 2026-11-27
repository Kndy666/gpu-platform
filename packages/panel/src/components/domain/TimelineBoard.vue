<script setup lang="ts">
import { ref, computed } from "vue";
import { RouterLink, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ChevronLeft, ChevronRight, ChevronDown, ChevronUp, RotateCcw } from "lucide-vue-next";
import type { EChartsCoreOption, ECElementEvent } from "echarts/core";
import type { TimelineData, TimelineBlock } from "@/api/endpoints";
import { compactNumber, formatTimeOnly, todayInZone } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useThemeStore } from "@/stores/theme";
import EChartBox from "@/components/ui/EChartBox.vue";
import DatePickerInput from "@/components/ui/DatePickerInput.vue";

interface PackedBlock {
  block: TimelineBlock;
  lane: number;
  left: number;
  width: number;
}

interface ChartRow {
  key: string;
  kind: "node" | "detail";
  nodeId: number;
  label: string;
  sublabel?: string;
  yStart: number;
  yEnd: number;
  laneHeight: number;
  padTop: number;
  blocks: PackedBlock[];
  emptyText?: string;
}

const props = defineProps<{
  data: TimelineData | null;
  date: string;
  loading?: boolean;
}>();

const emit = defineEmits<{
  "update:date": [date: string];
}>();

const { t } = useI18n();
const router = useRouter();
const auth = useAuthStore();
const locale = useLocaleStore();
const theme = useThemeStore();

const expandedGroups = ref<Record<number, boolean>>({});
const showHostRows = ref(true);
const chartRef = ref<InstanceType<typeof EChartBox> | null>(null);

function shiftDate(days: number) {
  const current = new Date(`${props.date}T12:00:00Z`);
  if (Number.isNaN(current.getTime())) return;
  current.setUTCDate(current.getUTCDate() + days);
  emit("update:date", current.toISOString().slice(0, 10));
}

function goToday() {
  emit("update:date", todayInZone(auth.timeZone));
}

function toggleGroup(id: number) {
  expandedGroups.value = { ...expandedGroups.value, [id]: !expandedGroups.value[id] };
}

function resetZoom() {
  chartRef.value?.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
}

function packLanes(blocks: TimelineBlock[]): { items: PackedBlock[]; laneCount: number } {
  const sorted = [...blocks].sort((a, b) => a.left - b.left || b.width - a.width);
  const laneEnds: number[] = [];
  const items: PackedBlock[] = [];
  for (const block of sorted) {
    const left = Math.max(0, Math.min(100, Number(block.left) || 0));
    const width = Math.max(1.2, Math.min(100 - left, Number(block.width) || 0));
    let lane = laneEnds.findIndex((end) => end <= left + 0.05);
    if (lane < 0) lane = laneEnds.length;
    laneEnds[lane] = left + width;
    items.push({ block, lane, left, width });
  }
  return { items, laneCount: Math.max(laneEnds.length, 1) };
}

function isHostScalarRow(rowId: string): boolean {
  const low = rowId.toLowerCase();
  return low.includes(":cpu") || low.includes(":memory") || low.endsWith("cpu") || low.endsWith("memory");
}

const layoutRows = computed<{ rows: ChartRow[]; totalHeight: number }>(() => {
  const groups = props.data?.groups ?? [];
  const rows: ChartRow[] = [];
  let cursor = 0;

  for (const group of groups) {
    const packedSummary = packLanes(group.entries);
    const laneHeight = 32;
    const padTop = 9;
    const padBottom = 9;
    const rowHeight = Math.max(50, packedSummary.laneCount * laneHeight + padTop + padBottom);
    const isExpanded = Boolean(expandedGroups.value[group.id]);

    rows.push({
      key: `node-${group.id}`,
      kind: "node",
      nodeId: group.id,
      label: `${isExpanded ? "▾" : "▸"} ${group.name}`,
      yStart: cursor,
      yEnd: cursor + rowHeight,
      laneHeight,
      padTop,
      blocks: packedSummary.items,
      emptyText: !group.entries.length
        ? group.queued.length
          ? t("overview.waitingShort")
          : t("overview.idle")
        : undefined,
    });
    cursor += rowHeight;

    if (isExpanded) {
      for (const detailRow of group.rows) {
        const rawBlocks = group.blocks.filter((b) => b.row === detailRow.id);
        if (detailRow.id.startsWith("reservations") && !rawBlocks.length) continue;
        if (!showHostRows.value && isHostScalarRow(detailRow.id)) continue;

        const packed = packLanes(rawBlocks);
        const dLaneHeight = 28;
        const dPad = 7;
        const dHeight = Math.max(40, packed.laneCount * dLaneHeight + dPad * 2);

        rows.push({
          key: `row-${group.id}-${detailRow.id}`,
          kind: "detail",
          nodeId: group.id,
          label: detailRow.name,
          sublabel: detailRow.detail,
          yStart: cursor,
          yEnd: cursor + dHeight,
          laneHeight: dLaneHeight,
          padTop: dPad,
          blocks: packed.items,
        });
        cursor += dHeight;
      }
    }
    cursor += 6; // subtle breathing gap between node groups
  }

  return { rows, totalHeight: Math.max(cursor, 80) };
});

const chartHeightPx = computed(() => `${layoutRows.value.totalHeight + 56}px`);

const allQueued = computed(() =>
  (props.data?.groups ?? [])
    .filter((g) => g.queued.length > 0)
    .map((g) => ({ nodeId: g.id, nodeName: g.name, items: g.queued })),
);

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTooltipHtml(block: TimelineBlock): string {
  const start = block.start ? formatTimeOnly(block.start, locale.current, auth.timeZone) : "—";
  const end = block.end ? formatTimeOnly(block.end, locale.current, auth.timeZone) : t("overview.endPending");
  const deviceLabel = block.device_label || "GPU";
  const gpuSpecs = (block.gpu_requests || [])
    .map(
      (spec, idx) =>
        `${deviceLabel} ${idx + 1}: ${compactNumber(spec.compute_percent)}% · ${compactNumber(spec.memory_gib)} GiB`,
    )
    .join("<br/>");
  const accelHtml =
    gpuSpecs || (block.gpu > 0 ? `${compactNumber(block.gpu)} ${escapeHtml(deviceLabel)}` : "");
  const hostLine = `${compactNumber(block.cpu)} ${escapeHtml(t("app.cores"))} · ${compactNumber(block.memory)} GiB`;

  return [
    `<div style="max-width:340px;white-space:normal;line-height:1.5">`,
    `<div style="font-weight:600;margin-bottom:2px">${escapeHtml(block.title)}</div>`,
    block.owner
      ? `<div style="font-size:12px;opacity:0.78;margin-bottom:4px">${escapeHtml(block.owner)}</div>`
      : "",
    `<div style="font-variant-numeric:tabular-nums;font-size:12px;margin-bottom:4px">${escapeHtml(start)} → ${escapeHtml(end)}</div>`,
    accelHtml ? `<div style="font-variant-numeric:tabular-nums;font-size:12px">${accelHtml}</div>` : "",
    `<div style="font-variant-numeric:tabular-nums;font-size:12px;opacity:0.85">${hostLine}</div>`,
    `</div>`,
  ].join("");
}

function formatNowLabel(pct: number): string {
  const totalMinutes = Math.round((pct / 100) * 24 * 60);
  const hh = Math.floor(totalMinutes / 60) % 24;
  const mm = totalMinutes % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

const chartOption = computed<EChartsCoreOption>(() => {
  const data = props.data;
  const { rows, totalHeight } = layoutRows.value;
  if (!data || !rows.length) return {};

  const isDark = theme.resolvedTheme === "dark";
  const fontFamily = getComputedStyle(document.documentElement).getPropertyValue("--font-sans").trim() || "sans-serif";

  const colors = {
    ink: isDark ? "#f2f0e9" : "#141413",
    body: isDark ? "#c8c5bc" : "#3d3d3a",
    muted: isDark ? "#96938a" : "#67655f",
    nodeRowBg: isDark ? "rgba(44, 43, 40, 0.72)" : "rgba(239, 236, 228, 0.58)",
    detailRowBg: isDark ? "rgba(38, 37, 35, 0.55)" : "rgba(250, 249, 245, 0.85)",
    border: isDark ? "#383733" : "#e4e0d5",
    split: isDark ? "rgba(255, 255, 255, 0.06)" : "rgba(20, 20, 19, 0.06)",
    tooltipBg: isDark ? "#262523" : "#ffffff",
    accent: isDark ? "#d97757" : "#b85332",
    bar: {
      running: {
        fill: isDark ? "rgba(217, 119, 87, 0.24)" : "rgba(184, 83, 50, 0.14)",
        border: isDark ? "rgba(217, 119, 87, 0.55)" : "rgba(184, 83, 50, 0.36)",
        stripe: isDark ? "#d97757" : "#b85332",
        text: isDark ? "#f7ebe5" : "#4a1f12",
        sub: isDark ? "#e6bfb0" : "#8b3f27",
      },
      reserved: {
        fill: isDark ? "rgba(127, 169, 212, 0.20)" : "rgba(62, 103, 144, 0.12)",
        border: isDark ? "rgba(127, 169, 212, 0.50)" : "rgba(62, 103, 144, 0.35)",
        stripe: isDark ? "#7fa9d4" : "#3e6790",
        text: isDark ? "#e3effa" : "#1f3a54",
        sub: isDark ? "#a9c7e6" : "#3e6790",
      },
      finished: {
        fill: isDark ? "rgba(150, 147, 138, 0.15)" : "rgba(103, 101, 95, 0.10)",
        border: isDark ? "rgba(150, 147, 138, 0.32)" : "rgba(103, 101, 95, 0.24)",
        stripe: isDark ? "#8b887e" : "#8c8980",
        text: isDark ? "#c8c5bc" : "#4a4945",
        sub: isDark ? "#96938a" : "#6f6d67",
      },
    },
  };

  // Row backgrounds + interactive left labels
  const rowItems = rows.map((row) => ({
    value: [0, row.yStart, row.yEnd, row.kind === "node" ? 1 : 0, row.nodeId],
    rowMeta: row,
  }));

  // Task bars flattened with exact vertical coordinates
  interface BarDatum {
    value: [number, number, number, number];
    block: TimelineBlock;
    isDetail: boolean;
  }
  const barItems: BarDatum[] = [];
  for (const row of rows) {
    const barH = row.kind === "node" ? 26 : 22;
    for (const packed of row.blocks) {
      const yTop = row.yStart + row.padTop + packed.lane * row.laneHeight;
      barItems.push({
        value: [packed.left, packed.left + packed.width, yTop, yTop + barH],
        block: packed.block,
        isDetail: row.kind === "detail",
      });
    }
  }

  const leftGutter = 152;

  return {
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: colors.tooltipBg,
      borderColor: colors.border,
      borderWidth: 1,
      padding: [10, 12],
      textStyle: { color: colors.ink, fontFamily, fontSize: 13 },
      formatter: (params: unknown) => {
        const p = params as { data?: { block?: TimelineBlock } };
        if (!p.data?.block) return "";
        return formatTooltipHtml(p.data.block);
      },
    },
    grid: {
      top: 34,
      left: leftGutter,
      right: 18,
      bottom: 14,
      containLabel: false,
    },
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: 0,
        filterMode: "none",
        zoomOnMouseWheel: "shift",
        moveOnMouseMove: true,
      },
    ],
    xAxis: {
      type: "value",
      position: "top",
      min: 0,
      max: 100,
      interval: 100 / 6,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { show: true, lineStyle: { color: colors.split, type: "dashed" } },
      axisLabel: {
        color: colors.muted,
        fontSize: 12,
        fontFamily,
        margin: 10,
        formatter: (val: number) => {
          const hour = Math.round((val / 100) * 24);
          return `${String(hour).padStart(2, "0")}:00`;
        },
      },
    },
    yAxis: {
      type: "value",
      inverse: true,
      min: 0,
      max: totalHeight,
      show: false,
    },
    series: [
      // Series 0: Row bands & clickable node labels
      {
        type: "custom",
        name: "rows",
        z: 1,
        tooltip: { show: false },
        renderItem: (params: unknown, api: unknown) => {
          const p = params as { dataIndex: number; coordSys: { x: number; width: number } };
          const a = api as { coord: (pt: [number, number]) => [number, number] };
          const meta = rowItems[p.dataIndex]?.rowMeta;
          if (!meta) return null;

          const topPt = a.coord([0, meta.yStart]);
          const botPt = a.coord([100, meta.yEnd]);
          const y = topPt[1];
          const h = Math.max(1, botPt[1] - topPt[1]);
          const trackX = p.coordSys.x;
          const trackW = p.coordSys.width;
          const isNode = meta.kind === "node";

          const children: Record<string, unknown>[] = [
            // Full row background including left gutter
            {
              type: "rect",
              shape: {
                x: 4,
                y,
                width: trackX + trackW - 4,
                height: h,
                r: isNode ? 8 : 4,
              },
              style: {
                fill: isNode ? colors.nodeRowBg : colors.detailRowBg,
                stroke: colors.border,
                lineWidth: 1,
              },
            },
            // Left gutter label
            {
              type: "text",
              style: {
                x: isNode ? 16 : 30,
                y: y + h / 2,
                text: meta.label,
                fill: isNode ? colors.ink : colors.body,
                font: `${isNode ? "600 14px" : "500 13px"} ${fontFamily}`,
                textVerticalAlign: "middle",
                textAlign: "left",
                width: leftGutter - (isNode ? 24 : 38),
                overflow: "truncate",
              },
            },
          ];

          if (meta.emptyText) {
            children.push({
              type: "text",
              silent: true,
              style: {
                x: trackX + 14,
                y: y + h / 2,
                text: meta.emptyText,
                fill: colors.muted,
                font: `400 13px ${fontFamily}`,
                textVerticalAlign: "middle",
              },
            });
          }

          return {
            type: "group",
            silent: !isNode,
            children,
          };
        },
        data: rowItems,
      },

      // Series 1: Task allocation bars
      {
        type: "custom",
        name: "tasks",
        z: 3,
        clip: true,
        renderItem: (params: unknown, api: unknown) => {
          const p = params as {
            dataIndex: number;
            coordSys: { x: number; y: number; width: number; height: number };
          };
          const a = api as { coord: (pt: [number, number]) => [number, number] };
          const datum = barItems[p.dataIndex];
          if (!datum) return null;

          const { block, isDetail } = datum;
          const [startPct, endPct, yTopVal, yBotVal] = datum.value;
          const startPt = a.coord([startPct, yTopVal]);
          const endPt = a.coord([endPct, yBotVal]);
          const rightEdge = a.coord([100, yTopVal])[0];

          const x = startPt[0];
          const y = startPt[1];
          const w = Math.max(8, endPt[0] - startPt[0]);
          const h = Math.max(16, endPt[1] - startPt[1]);

          const styleSet = colors.bar[block.state] ?? colors.bar.finished;
          const children: Record<string, unknown>[] = [];

          // Open-ended continuation dashed line
          if (block.open && data.now !== null && x + w < rightEdge - 2) {
            children.push({
              type: "line",
              silent: true,
              shape: {
                x1: x + w,
                y1: y + h / 2,
                x2: rightEdge,
                y2: y + h / 2,
              },
              style: {
                stroke: styleSet.stripe,
                lineWidth: 1.5,
                lineDash: [4, 4],
                opacity: 0.55,
              },
            });
          }

          // Pill body
          children.push({
            type: "rect",
            shape: { x, y, width: w, height: h, r: 6 },
            style: {
              fill: styleSet.fill,
              stroke: styleSet.border,
              lineWidth: 1,
            },
          });

          // Left status indicator stripe
          children.push({
            type: "rect",
            shape: { x, y, width: Math.min(4, w), height: h, r: [6, 0, 0, 6] },
            style: {
              fill: styleSet.stripe,
            },
          });

          // Label inside pill when wide enough
          const amountText = isDetail && block.amount ? block.amount : "";
          const showAmount = Boolean(amountText && w > 125);
          const reservedRight = showAmount ? Math.min(108, amountText.length * 7 + 16) : 8;
          const labelMaxW = w - 12 - reservedRight;

          if (labelMaxW > 18) {
            const titleText = !isDetail && block.owner ? `${block.title} · ${block.owner}` : block.title;
            children.push({
              type: "text",
              silent: true,
              style: {
                x: x + 10,
                y: y + h / 2,
                text: titleText,
                fill: styleSet.text,
                font: `500 ${isDetail ? "12px" : "13px"} ${fontFamily}`,
                textVerticalAlign: "middle",
                width: labelMaxW,
                overflow: "truncate",
              },
            });
          }

          if (showAmount) {
            children.push({
              type: "text",
              silent: true,
              style: {
                x: x + w - 8,
                y: y + h / 2,
                text: amountText,
                fill: styleSet.sub,
                font: `600 11.5px ${fontFamily}`,
                textAlign: "right",
                textVerticalAlign: "middle",
              },
            });
          }

          return {
            type: "group",
            children,
          };
        },
        data: barItems,
        markLine:
          data.now !== null
            ? {
                symbol: ["none", "none"],
                silent: true,
                z: 10,
                lineStyle: { color: colors.accent, width: 1.5, type: "solid" },
                label: {
                  show: true,
                  position: "start",
                  formatter: formatNowLabel(data.now),
                  backgroundColor: colors.accent,
                  color: "#ffffff",
                  padding: [2, 6],
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 600,
                  distance: 4,
                },
                data: [{ xAxis: data.now }],
              }
            : undefined,
      },
    ],
  };
});

function handleChartClick(params: ECElementEvent) {
  const datum = params.data as { block?: TimelineBlock; rowMeta?: ChartRow } | undefined;
  if (datum?.block?.id) {
    void router.push(`/tasks/${datum.block.id}`);
    return;
  }
  if (datum?.rowMeta?.kind === "node") {
    toggleGroup(datum.rowMeta.nodeId);
  }
}

function queuedTitle(item: TimelineData["groups"][number]["queued"][number]): string {
  const parts = [item.title, item.status_label];
  if (item.gpu > 0) parts.push(`${compactNumber(item.gpu)} ${item.device_label || "GPU"}`);
  parts.push(`${compactNumber(item.cpu)} ${t("app.cores")}`);
  parts.push(`${compactNumber(item.memory_gib)} GiB`);
  return parts.join(" · ");
}

const isToday = computed(() => props.date === todayInZone(auth.timeZone));
</script>

<template>
  <section class="card p-5" :aria-busy="loading ? 'true' : 'false'">
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2>{{ t("overview.timelineTitle") }}</h2>
        <div class="mt-2 flex flex-wrap items-center gap-4 text-xs text-muted">
          <span class="inline-flex items-center gap-1.5">
            <span class="size-2.5 rounded-xs bg-accent" aria-hidden="true" />
            {{ t("overview.legendRunning") }}
          </span>
          <span class="inline-flex items-center gap-1.5">
            <span class="size-2.5 rounded-xs bg-info" aria-hidden="true" />
            {{ t("overview.legendReserved") }}
          </span>
          <span class="inline-flex items-center gap-1.5">
            <span class="size-2.5 rounded-xs bg-muted/60" aria-hidden="true" />
            {{ t("overview.legendFinished") }}
          </span>
          <span class="inline-flex items-center gap-1.5">
            <span class="size-2.5 rounded-xs bg-warn" aria-hidden="true" />
            {{ t("overview.legendQueued") }}
          </span>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <label class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border-subtle bg-canvas/60 px-2.5 py-1.5 text-xs text-body">
          <input v-model="showHostRows" type="checkbox" class="size-3.5" />
          <span>{{ t("overview.showHostRows") }}</span>
        </label>

        <div class="flex items-center gap-1.5">
          <button
            type="button"
            class="btn btn-secondary btn-sm px-2"
            :aria-label="t('overview.prevDay')"
            @click="shiftDate(-1)"
          >
            <ChevronLeft class="size-4" aria-hidden="true" />
          </button>
          <DatePickerInput
            :model-value="date"
            compact
            :aria-label="t('overview.timelineTitle')"
            @update:model-value="emit('update:date', $event)"
          />
          <button
            type="button"
            class="btn btn-secondary btn-sm px-2"
            :aria-label="t('overview.nextDay')"
            @click="shiftDate(1)"
          >
            <ChevronRight class="size-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            :disabled="isToday"
            @click="goToday"
          >
            {{ t("overview.today") }}
          </button>
          <button
            type="button"
            class="btn btn-ghost btn-sm px-2"
            :title="t('admin.resetZoom')"
            :aria-label="t('admin.resetZoom')"
            @click="resetZoom"
          >
            <RotateCcw class="size-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>

    <!-- Node quick expand/collapse bar -->
    <div v-if="data?.groups?.length" class="mb-3 flex flex-wrap items-center gap-1.5">
      <button
        v-for="group in data.groups"
        :key="group.id"
        type="button"
        class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors"
        :class="
          expandedGroups[group.id]
            ? 'border-accent/50 bg-accent-soft text-accent'
            : 'border-border-subtle bg-canvas/70 text-body hover:bg-subtle'
        "
        :aria-expanded="Boolean(expandedGroups[group.id])"
        @click="toggleGroup(group.id)"
      >
        <ChevronUp v-if="expandedGroups[group.id]" class="size-3.5" aria-hidden="true" />
        <ChevronDown v-else class="size-3.5" aria-hidden="true" />
        <span>{{ group.name }}</span>
        <span v-if="group.queued.length" class="ml-0.5 rounded-full bg-warn-bg px-1.5 text-[11px] text-warn tabular-nums">
          {{ group.queued.length }}
        </span>
      </button>
    </div>

    <div v-if="loading && !data" class="py-10 text-center text-sm text-muted">
      {{ t("app.loading") }}
    </div>

    <div v-else-if="!data || !data.groups.length" class="py-10 text-center text-sm text-muted">
      {{ t("overview.idleDay") }}
    </div>

    <div v-else class="-mx-1 overflow-x-auto px-1">
      <div class="min-w-[680px]">
        <EChartBox
          ref="chartRef"
          :option="chartOption"
          :height="chartHeightPx"
          :aria-label="t('overview.timelineTitle')"
          @chart-click="handleChartClick"
        />
      </div>
    </div>

    <!-- Queued tasks strip (only when present) -->
    <div v-if="allQueued.length" class="mt-4 space-y-2 border-t border-border-subtle pt-3.5">
      <div
        v-for="entry in allQueued"
        :key="entry.nodeId"
        class="flex flex-wrap items-center gap-2 text-xs"
      >
        <span class="font-semibold text-warn">
          {{ entry.nodeName }} · {{ t("overview.queuedLabel", { count: entry.items.length }) }}
        </span>
        <component
          :is="item.id ? RouterLink : 'span'"
          v-for="(item, idx) in entry.items"
          :key="item.id || idx"
          :to="item.id ? `/tasks/${item.id}` : undefined"
          :title="queuedTitle(item)"
          class="inline-flex items-center gap-1.5 rounded-full border border-warn/35 bg-warn-bg px-2.5 py-0.5 text-warn no-underline hover:opacity-90"
        >
          <span class="font-medium">{{ item.title }}</span>
          <span class="opacity-80">· {{ item.status_label }}</span>
        </component>
      </div>
    </div>
  </section>
</template>
