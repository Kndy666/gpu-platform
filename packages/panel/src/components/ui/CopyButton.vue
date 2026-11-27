<script setup lang="ts">
import { ref } from "vue";
import { Check, Copy } from "lucide-vue-next";
import { useI18n } from "vue-i18n";
import { copyToClipboard } from "@/lib/format";
import { useNotificationsStore } from "@/stores/notifications";

const props = withDefaults(
  defineProps<{
    text: string;
    label?: string;
    variant?: "secondary" | "ghost";
    size?: "sm" | "md";
  }>(),
  { variant: "secondary", size: "sm" },
);

const { t } = useI18n();
const notices = useNotificationsStore();
const copied = ref(false);

async function onCopy() {
  const ok = await copyToClipboard(props.text);
  if (ok) {
    copied.value = true;
    notices.pushToast({ level: "success", title: t("app.copied"), seconds: 3 });
    window.setTimeout(() => {
      copied.value = false;
    }, 1800);
  } else {
    notices.pushToast({ level: "error", title: t("app.copyFailed"), seconds: 6 });
  }
}
</script>

<template>
  <button
    type="button"
    class="btn"
    :class="[variant === 'ghost' ? 'btn-ghost' : 'btn-secondary', size === 'sm' ? 'btn-sm' : '']"
    @click="onCopy"
  >
    <Check v-if="copied" class="size-3.5 text-ok" aria-hidden="true" />
    <Copy v-else class="size-3.5" aria-hidden="true" />
    <span>{{ label || t("app.copy") }}</span>
  </button>
</template>
