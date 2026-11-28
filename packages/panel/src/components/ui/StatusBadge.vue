<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { CheckCircle2, Clock, AlertTriangle, XCircle, CircleDashed, Wrench } from "lucide-vue-next";

const props = defineProps<{
  status: string;
  label?: string;
}>();

const { t, te } = useI18n();

const tone = computed(() => {
  const s = props.status.toLowerCase();
  if (["running", "succeeded", "online", "ready", "approved", "active", "healthy", "completed", "sent"].includes(s)) {
    return "ok";
  }
  if (["submitted", "scheduled", "queued", "starting", "preparing", "pending", "working", "building", "saving", "maintenance", "sending", "deleting", "canceling", "stopping"].includes(s)) {
    return "warn";
  }
  if (["failed", "rejected", "offline", "error", "invalidated", "revoked", "unhealthy"].includes(s)) {
    return "danger";
  }
  return "neutral";
});

const displayLabel = computed(() => {
  if (props.label) return props.label;
  const key = `status.${props.status.toLowerCase()}`;
  return te(key) ? t(key) : props.status;
});

const icon = computed(() => {
  const s = props.status.toLowerCase();
  if (s === "maintenance") return Wrench;
  if (tone.value === "ok") return CheckCircle2;
  if (tone.value === "warn") return Clock;
  if (tone.value === "danger") return s === "offline" || s === "unhealthy" ? AlertTriangle : XCircle;
  return CircleDashed;
});

const classes = computed(() => {
  switch (tone.value) {
    case "ok":
      return "bg-ok-bg text-ok border-ok/25";
    case "warn":
      return "bg-warn-bg text-warn border-warn/25";
    case "danger":
      return "bg-danger-bg text-danger border-danger/25";
    default:
      return "bg-subtle text-muted border-border-subtle";
  }
});
</script>

<template>
  <span
    class="inline-flex cursor-default select-none items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium leading-5 whitespace-nowrap"
    :class="classes"
  >
    <component :is="icon" class="size-3.5 shrink-0" aria-hidden="true" />
    <span>{{ displayLabel }}</span>
  </span>
</template>
