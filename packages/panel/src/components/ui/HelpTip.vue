<script setup lang="ts">
import { ref, onMounted } from "vue";
import { HelpCircle } from "lucide-vue-next";

defineProps<{
  text: string;
  label?: string;
}>();

const tipId = `tip-${Math.random().toString(36).slice(2, 10)}`;
const expanded = ref(false);
const popoverRef = ref<HTMLElement | null>(null);

function onToggle(event: Event) {
  const toggleEvent = event as Event & { newState?: string };
  expanded.value = toggleEvent.newState === "open";
}

onMounted(() => {
  // Progressive enhancement fallback where popover="hint" degrades to "manual" (e.g. Safari)
  const el = popoverRef.value as (HTMLElement & { popover?: string }) | null;
  if (!el || typeof el.popover !== "string") return;
  const probe = document.createElement("div") as HTMLElement & { popover?: string };
  probe.popover = "hint";
  if (probe.popover !== "hint") {
    el.addEventListener("beforetoggle", (event) => {
      const e = event as Event & { newState?: string };
      if (e.newState === "open" && el.getAttribute("popover") === "hint") {
        el.popover = "auto";
      }
    });
  }
});
</script>

<template>
  <span class="help-tip-wrap inline-flex items-center align-middle">
    <button
      type="button"
      class="inline-flex size-4.5 items-center justify-center rounded-full text-muted transition-colors hover:bg-subtle hover:text-ink focus-visible:text-ink"
      :aria-label="label || text"
      :aria-expanded="expanded"
      :popovertarget="tipId"
      popovertargetaction="show"
      :interestfor="tipId"
    >
      <HelpCircle class="size-3.5" aria-hidden="true" />
    </button>
    <div
      :id="tipId"
      ref="popoverRef"
      popover="hint"
      role="tooltip"
      class="help-tip-popover"
      @toggle="onToggle"
    >
      {{ text }}
    </div>
  </span>
</template>
