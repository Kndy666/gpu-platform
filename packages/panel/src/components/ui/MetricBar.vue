<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    percent: number | null | undefined;
    tone?: "accent" | "ok" | "warn" | "danger" | "info";
    compact?: boolean;
  }>(),
  { tone: "accent", compact: false },
);

const clamped = computed(() => {
  const n = Number(props.percent ?? 0);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, n));
});

const fillClass = computed(() => {
  switch (props.tone) {
    case "ok":
      return "bg-ok";
    case "warn":
      return "bg-warn";
    case "danger":
      return "bg-danger";
    case "info":
      return "bg-info";
    default:
      return "bg-accent";
  }
});
</script>

<template>
  <div
    class="w-full overflow-hidden rounded-full bg-subtle"
    :class="compact ? 'h-1.5' : 'h-2'"
    role="progressbar"
    :aria-valuenow="Math.round(clamped)"
    aria-valuemin="0"
    aria-valuemax="100"
  >
    <div
      class="h-full rounded-full transition-[width] duration-300 ease-out"
      :class="fillClass"
      :style="{ width: `${clamped}%` }"
    />
  </div>
</template>
