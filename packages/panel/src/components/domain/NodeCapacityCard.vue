<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { ChevronDown, Server } from "lucide-vue-next";
import type { NodeResourceCard, DisplayResource } from "@/api/endpoints";
import { compactNumber, formatQuantity, formatGpuMemory } from "@/lib/format";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import MetricBar from "@/components/ui/MetricBar.vue";

const props = defineProps<{
  node: NodeResourceCard;
}>();

const { t } = useI18n();

const hostResources = computed(() => {
  const accelKeys = new Set(props.node.accelerator_resource_keys || []);
  return (props.node.display_resources || []).filter((res) => !accelKeys.has(res.key));
});

const readyGpuCount = computed(() => props.node.gpus.filter((g) => g.ready).length);

function freePercent(res: DisplayResource): number {
  if (!res.total) return 0;
  return Math.max(0, Math.min(100, (res.available / res.total) * 100));
}

function memFreePercent(available: number, total: number): number {
  if (!total) return 0;
  return Math.max(0, Math.min(100, (available / total) * 100));
}

function remainingTone(percent: number, defaultTone: "ok" | "accent" | "info" = "ok"): "ok" | "accent" | "info" | "warn" | "danger" {
  if (percent <= 15) return "danger";
  if (percent <= 35) return "warn";
  return defaultTone;
}
</script>

<template>
  <details class="node-disclosure node-card-wrap card">
    <summary class="flex flex-wrap items-center justify-between gap-3 px-5 py-4 select-none hover:bg-subtle/35">
      <div class="flex min-w-0 items-center gap-2.5">
        <ChevronDown class="disclosure-chevron size-4 shrink-0 text-muted" aria-hidden="true" />
        <Server class="size-4 shrink-0 text-accent" aria-hidden="true" />
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <span class="truncate text-base font-semibold text-ink">{{ node.name }}</span>
            <StatusBadge :status="node.state" :label="node.state_label" />
          </div>
          <p v-if="node.model" class="mt-0.5 truncate text-xs text-muted">{{ node.model }}</p>
        </div>
      </div>

      <!-- Compact remaining-capacity summary visible while collapsed -->
      <div class="flex flex-wrap items-center gap-2 text-xs tabular-nums">
        <span
          v-if="node.gpus.length"
          class="rounded-full border border-border-subtle bg-canvas px-2.5 py-0.5 font-medium text-ink"
        >
          {{ node.device_label }} {{ compactNumber(node.gpu_available, 0) }} / {{ node.gpus.length }} {{ t("overview.available") }}
        </span>
        <span
          v-for="res in hostResources.slice(0, 2)"
          :key="res.key"
          class="rounded-full border border-border-subtle bg-canvas px-2.5 py-0.5 text-muted"
        >
          {{ res.label }} {{ formatQuantity(res.available, res.unit, t("app.cores")) }} / {{ formatQuantity(res.total, res.unit, t("app.cores")) }}
        </span>
      </div>
    </summary>

    <div class="cursor-default select-none space-y-4 border-t border-border-subtle px-5 py-4">
      <!-- Host resource remaining bars (0% available = empty, 100% available = full) -->
      <div v-if="hostResources.length" class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div
          v-for="res in hostResources"
          :key="res.key"
          class="rounded-lg border border-border-subtle bg-canvas/60 px-3.5 py-2.5"
        >
          <div class="flex items-center justify-between gap-2 text-xs text-muted">
            <span>{{ res.label }}</span>
            <span class="tabular-nums">{{ Math.round(freePercent(res)) }}% {{ t("overview.available") }}</span>
          </div>
          <p class="mt-1 text-sm font-semibold text-ink tabular-nums">
            {{ formatQuantity(res.available, res.unit, t("app.cores")) }}
            <span class="font-normal text-muted">
              / {{ formatQuantity(res.total, res.unit, t("app.cores")) }}
            </span>
          </p>
          <MetricBar
            class="mt-2"
            compact
            :percent="freePercent(res)"
            :tone="remainingTone(freePercent(res), 'ok')"
          />
        </div>
      </div>

      <!-- Per-GPU/NPU remaining bars -->
      <div v-if="node.gpus.length" class="space-y-2.5">
        <div class="flex items-center justify-between gap-2 text-xs text-muted">
          <span class="font-medium">{{ node.device_details_label || t("overview.gpuDetails") }}</span>
          <span class="tabular-nums">{{ t("overview.deviceSummary", { ready: readyGpuCount, total: node.gpus.length }) }}</span>
        </div>
        <div class="grid grid-cols-1 gap-2.5 @[460px]:grid-cols-2">
          <div
            v-for="gpu in node.gpus"
            :key="gpu.uuid || gpu.index"
            class="rounded-lg border border-border-subtle bg-elevated/70 px-3.5 py-3"
          >
            <div class="flex items-center justify-between gap-2 text-xs">
              <span class="font-semibold text-ink tabular-nums">
                {{ gpu.device_label || node.device_label }} #{{ gpu.index }}
              </span>
              <span v-if="!gpu.ready" class="font-medium text-danger">{{ t("taskDetail.unavailable") }}</span>
              <span v-else class="font-medium text-muted tabular-nums">
                {{ compactNumber(gpu.available_compute_percent, 0) }}% {{ t("overview.available") }}
              </span>
            </div>
            <div class="mt-2.5 space-y-2">
              <div>
                <div class="mb-1 flex justify-between text-xs text-muted tabular-nums">
                  <span>{{ t("overview.compute") }}</span>
                  <span>{{ compactNumber(gpu.available_compute_percent, 0) }}% / 100%</span>
                </div>
                <MetricBar
                  compact
                  :percent="gpu.ready ? gpu.available_compute_percent : 0"
                  :tone="remainingTone(gpu.ready ? gpu.available_compute_percent : 0, 'accent')"
                />
              </div>
              <div>
                <div class="mb-1 flex justify-between text-xs text-muted tabular-nums">
                  <span>{{ gpu.device_memory_label || node.device_memory_label }}</span>
                  <span>{{ formatGpuMemory(gpu.available_memory_gib) }} / {{ formatGpuMemory(gpu.memory_total_gib) }}</span>
                </div>
                <MetricBar
                  compact
                  :percent="gpu.ready ? memFreePercent(gpu.available_memory_gib, gpu.memory_total_gib) : 0"
                  :tone="remainingTone(gpu.ready ? memFreePercent(gpu.available_memory_gib, gpu.memory_total_gib) : 0, 'info')"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
      <p v-else class="text-xs text-muted">{{ t("overview.noDevices") }}</p>
    </div>
  </details>
</template>
