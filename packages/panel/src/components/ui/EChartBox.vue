<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from "vue";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart, CustomChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  LegendComponent,
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsCoreOption, ECharts, ECElementEvent } from "echarts/core";
import { useThemeStore } from "@/stores/theme";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

const props = withDefaults(
  defineProps<{
    option: EChartsCoreOption;
    height?: string;
    ariaLabel?: string;
  }>(),
  { height: "280px" },
);

const emit = defineEmits<{
  chartClick: [params: ECElementEvent];
}>();

const containerRef = ref<HTMLElement | null>(null);
const theme = useThemeStore();
let chart: ECharts | null = null;
let observer: ResizeObserver | null = null;

function render() {
  if (!containerRef.value) return;
  if (!chart) {
    chart = echarts.init(containerRef.value, undefined, { renderer: "canvas" });
    chart.on("click", (params) => {
      emit("chartClick", params as ECElementEvent);
    });
  }
  const style = getComputedStyle(document.documentElement);
  const inkMuted = style.getPropertyValue("--ink-muted").trim() || "#67655f";
  chart.setOption(
    {
      animation: false,
      textStyle: { color: inkMuted, fontFamily: style.getPropertyValue("--font-sans").trim() },
      ...props.option,
    },
    true,
  );
  chart.resize();
}

function dispatchAction(payload: Parameters<ECharts["dispatchAction"]>[0]) {
  chart?.dispatchAction(payload);
}

defineExpose({ dispatchAction });

onMounted(() => {
  render();
  if (containerRef.value) {
    observer = new ResizeObserver(() => {
      if (containerRef.value?.clientWidth && containerRef.value?.clientHeight) {
        chart?.resize();
      }
    });
    observer.observe(containerRef.value);
  }
});

watch([() => props.option, () => props.height, () => theme.resolvedTheme], () => {
  requestAnimationFrame(render);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div
    ref="containerRef"
    role="img"
    :aria-label="ariaLabel"
    class="w-full"
    :style="{ height }"
  />
</template>
