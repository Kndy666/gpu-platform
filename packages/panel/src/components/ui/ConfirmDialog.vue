<script setup lang="ts">
import { useI18n } from "vue-i18n";
import ModalDialog from "./ModalDialog.vue";

withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    message: string;
    confirmLabel?: string;
    danger?: boolean;
    busy?: boolean;
  }>(),
  { danger: false, busy: false },
);

const emit = defineEmits<{
  "update:open": [value: boolean];
  confirm: [];
}>();

const { t } = useI18n();
</script>

<template>
  <ModalDialog :open="open" :title="title" closed-by="closerequest" @update:open="emit('update:open', $event)">
    <p class="text-sm text-body leading-relaxed">{{ message }}</p>
    <slot />
    <template #footer>
      <button
        type="button"
        class="btn btn-secondary"
        autofocus
        :disabled="busy"
        @click="emit('update:open', false)"
      >
        {{ t("app.cancel") }}
      </button>
      <button
        type="button"
        class="btn"
        :class="danger ? 'btn-danger-solid' : 'btn-primary'"
        :disabled="busy"
        @click="emit('confirm')"
      >
        {{ busy ? t("app.saving") : (confirmLabel || t("app.confirm")) }}
      </button>
    </template>
  </ModalDialog>
</template>
