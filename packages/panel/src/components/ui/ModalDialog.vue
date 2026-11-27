<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from "vue";
import { X } from "lucide-vue-next";
import { useI18n } from "vue-i18n";

const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    size?: "md" | "lg";
    closedBy?: "any" | "closerequest" | "none";
  }>(),
  { size: "md", closedBy: "any" },
);

const emit = defineEmits<{
  "update:open": [value: boolean];
  close: [];
}>();

const { t } = useI18n();
const dialogRef = ref<HTMLDialogElement | null>(null);
const headingId = `dlg-${Math.random().toString(36).slice(2, 9)}`;

function syncOpen(isOpen: boolean) {
  const el = dialogRef.value;
  if (!el) return;
  if (isOpen && !el.open) {
    el.showModal();
  } else if (!isOpen && el.open) {
    el.close();
  }
}

watch(() => props.open, syncOpen, { flush: "post" });

function onNativeClose() {
  if (dialogRef.value) dialogRef.value.returnValue = "";
  if (props.open) {
    emit("update:open", false);
    emit("close");
  }
}

function requestClose() {
  emit("update:open", false);
  emit("close");
}

onBeforeUnmount(() => {
  if (dialogRef.value?.open) dialogRef.value.close();
});
</script>

<template>
  <dialog
    ref="dialogRef"
    class="app-dialog"
    :class="{ 'app-dialog-lg': size === 'lg' }"
    :aria-labelledby="headingId"
    :closedby="closedBy"
    @close="onNativeClose"
  >
    <header class="flex items-start justify-between gap-4 border-b border-border-subtle px-6 py-4">
      <h1 :id="headingId" class="font-serif text-xl font-medium text-ink leading-snug">{{ title }}</h1>
      <button
        type="button"
        class="btn btn-ghost btn-sm -mr-2 -mt-1 px-2"
        :aria-label="t('app.close')"
        @click="requestClose"
      >
        <X class="size-4" aria-hidden="true" />
      </button>
    </header>
    <div class="overflow-y-auto px-6 py-5">
      <slot />
    </div>
    <footer
      v-if="$slots.footer"
      class="flex flex-wrap items-center justify-end gap-2.5 border-t border-border-subtle px-6 py-4"
    >
      <slot name="footer" />
    </footer>
  </dialog>
</template>
