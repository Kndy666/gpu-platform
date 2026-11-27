<script setup lang="ts">
import { computed } from "vue";
import { ChevronLeft, ChevronRight } from "lucide-vue-next";
import { useI18n } from "vue-i18n";

const props = defineProps<{
  page: number;
  pageSize: number;
  count: number;
  nextPage: number | null;
}>();

const emit = defineEmits<{
  change: [page: number];
}>();

const { t } = useI18n();
const totalPages = computed(() => Math.max(1, Math.ceil(props.count / Math.max(1, props.pageSize))));
</script>

<template>
  <nav
    v-if="totalPages > 1"
    class="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-muted"
    :aria-label="t('app.pageSummary', { page, total: totalPages, count })"
  >
    <span class="tabular-nums">{{ t("app.pageSummary", { page, total: totalPages, count }) }}</span>
    <div class="flex items-center gap-2">
      <button
        type="button"
        class="btn btn-secondary btn-sm"
        :disabled="page <= 1"
        @click="emit('change', page - 1)"
      >
        <ChevronLeft class="size-4" aria-hidden="true" />
        {{ t("app.previous") }}
      </button>
      <button
        type="button"
        class="btn btn-secondary btn-sm"
        :disabled="!nextPage"
        @click="emit('change', nextPage || page + 1)"
      >
        {{ t("app.next") }}
        <ChevronRight class="size-4" aria-hidden="true" />
      </button>
    </div>
  </nav>
</template>
