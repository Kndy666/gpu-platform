<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from "vue";
import { RouterLink } from "vue-router";
import { X, CheckCircle2, AlertCircle, Info } from "lucide-vue-next";
import { useI18n } from "vue-i18n";
import { useNotificationsStore, type ToastNotice } from "@/stores/notifications";

const { t } = useI18n();
const store = useNotificationsStore();
const stackRef = ref<HTMLElement | null>(null);
const timers = new Map<string, number>();
const paused = ref(false);

function syncPopover(hasItems: boolean) {
  const el = stackRef.value as (HTMLElement & { showPopover?: () => void; hidePopover?: () => void; matches?: (s: string) => boolean }) | null;
  if (!el || typeof el.showPopover !== "function" || typeof el.hidePopover !== "function") return;
  try {
    const isOpen = el.matches?.(":popover-open") ?? false;
    if (hasItems && !isOpen) {
      el.showPopover();
    } else if (!hasItems && isOpen) {
      el.hidePopover();
    }
  } catch {
    // Fallback gracefully in environments without popover API
  }
}

function scheduleAutoDismiss(notice: ToastNotice) {
  if (timers.has(notice.id) || notice.seconds <= 0 || paused.value) return;
  const ms = Math.max(2500, notice.seconds * 1000);
  const timerId = window.setTimeout(() => {
    timers.delete(notice.id);
    store.dismissToast(notice.id);
  }, ms);
  timers.set(notice.id, timerId);
}

function pauseAll() {
  paused.value = true;
  for (const timerId of timers.values()) {
    window.clearTimeout(timerId);
  }
  timers.clear();
}

function resumeAll() {
  paused.value = false;
  for (const notice of store.toasts) {
    scheduleAutoDismiss(notice);
  }
}

watch(
  () => store.toasts.map((item) => item.id),
  (ids) => {
    syncPopover(ids.length > 0);
    const active = new Set(ids);
    for (const [id, timerId] of timers.entries()) {
      if (!active.has(id)) {
        window.clearTimeout(timerId);
        timers.delete(id);
      }
    }
    for (const notice of store.toasts) {
      scheduleAutoDismiss(notice);
    }
  },
  { flush: "post", immediate: true },
);

onBeforeUnmount(() => {
  for (const timerId of timers.values()) {
    window.clearTimeout(timerId);
  }
  timers.clear();
});

function iconFor(level: ToastNotice["level"]) {
  if (level === "success") return CheckCircle2;
  if (level === "error") return AlertCircle;
  return Info;
}

function toneFor(level: ToastNotice["level"]) {
  if (level === "success") return "text-ok";
  if (level === "error") return "text-danger";
  return "text-accent";
}
</script>

<template>
  <div class="visually-hidden" aria-live="polite" aria-atomic="true">
    {{ store.liveAnnouncement }}
  </div>
  <div
    ref="stackRef"
    popover="manual"
    role="region"
    :aria-label="t('nav.inbox')"
    class="toast-stack"
    @pointerenter="pauseAll"
    @pointerleave="resumeAll"
    @focusin="pauseAll"
    @focusout="resumeAll"
  >
    <article
      v-for="item in store.toasts"
      :key="item.id"
      class="card flex gap-3 px-4 py-3 shadow-lg"
      :class="item.content || item.link ? 'items-start' : 'items-center'"
    >
      <component
        :is="iconFor(item.level)"
        class="size-4 shrink-0"
        :class="[toneFor(item.level), item.content || item.link ? 'mt-0.5' : '']"
        aria-hidden="true"
      />
      <div class="min-w-0 flex-1">
        <p class="text-sm font-medium text-ink leading-snug">{{ item.title }}</p>
        <p v-if="item.content" class="mt-0.5 text-xs text-muted leading-relaxed">{{ item.content }}</p>
        <RouterLink
          v-if="item.link"
          :to="item.link"
          class="mt-1.5 inline-block text-xs font-medium text-accent underline-offset-2 hover:underline"
          @click="store.dismissToast(item.id)"
        >
          {{ t("inbox.openRelated") }}
        </RouterLink>
      </div>
      <button
        type="button"
        class="-mr-1 inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted transition-colors hover:bg-subtle hover:text-ink"
        :class="item.content || item.link ? '-mt-0.5' : ''"
        :aria-label="t('app.close')"
        @click="store.dismissToast(item.id)"
      >
        <X class="size-4" aria-hidden="true" />
      </button>
    </article>
  </div>
</template>
