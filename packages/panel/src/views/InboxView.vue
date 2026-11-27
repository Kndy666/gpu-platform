<script setup lang="ts">
import { ref, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import { Bell, RefreshCw, ExternalLink, ChevronRight, X } from "lucide-vue-next";
import { api, type InboxItem } from "@/api/endpoints";
import { formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const items = ref<InboxItem[]>([]);
const selected = ref<InboxItem | null>(null);
const page = ref(1);
const count = ref(0);
const nextPage = ref<number | null>(null);
const loading = ref(true);

async function load(targetPage = page.value, silent = false) {
  if (!silent) loading.value = true;
  try {
    const res = await api.listInbox(targetPage);
    items.value = res.items;
    page.value = res.page;
    count.value = res.count;
    nextPage.value = res.next_page;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function openItem(item: InboxItem) {
  if (selected.value?.id === item.id) {
    selected.value = null;
    return;
  }
  selected.value = item;
  if (item.unread) {
    try {
      const updated = await api.markInboxRead(item.id);
      item.unread = false;
      selected.value = updated;
      await notices.pollOnce();
    } catch {
      // Ignore read failure
    }
  }
}

onMounted(() => {
  void load(1);
});

useAutoRefresh(() => load(page.value, true), 20000);
</script>

<template>
  <div>
    <PageHeader :title="t('inbox.title')" :subtitle="t('inbox.subtitle')">
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="load(page)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
      </template>
    </PageHeader>

    <div v-if="loading && !items.length" class="card p-10 text-center text-sm text-muted">
      {{ t("app.loading") }}
    </div>

    <EmptyState v-else-if="!items.length" :icon="Bell" :title="t('inbox.empty')" />

    <div v-else class="inbox-layout" :class="{ 'has-detail': Boolean(selected) }">
      <div class="space-y-3">
        <div class="card divide-y divide-border-subtle overflow-hidden">
          <button
            v-for="item in items"
            :key="item.id"
            type="button"
            class="group flex w-full items-start gap-3.5 px-5 py-4 text-left transition-colors hover:bg-subtle/45"
            :class="[
              selected?.id === item.id
                ? 'bg-accent-soft/45 shadow-[inset_3px_0_0_var(--accent)]'
                : item.unread
                  ? 'bg-accent-soft/18'
                  : '',
            ]"
            :aria-expanded="selected?.id === item.id"
            @click="openItem(item)"
          >
            <span
              class="mt-1.5 size-2 shrink-0 rounded-full"
              :class="item.unread ? 'bg-accent' : 'bg-transparent'"
              :aria-label="item.unread ? t('inbox.unread') : undefined"
            />
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <p
                  class="truncate text-sm"
                  :class="item.unread ? 'font-semibold text-ink' : 'font-medium text-body'"
                >
                  {{ item.title }}
                </p>
                <span class="shrink-0 text-xs text-muted tabular-nums">
                  {{ formatDateTime(item.sent_at, locale.current, auth.timeZone) }}
                </span>
              </div>
              <p class="mt-1 line-clamp-2 text-xs text-muted">{{ item.content }}</p>
            </div>
            <ChevronRight
              class="mt-1 size-4 shrink-0 text-muted/70 transition-transform group-hover:translate-x-0.5 group-hover:text-ink"
              :class="selected?.id === item.id ? 'text-accent' : ''"
              aria-hidden="true"
            />
          </button>
        </div>

        <PaginationBar
          :page="page"
          :page-size="20"
          :count="count"
          :next-page="nextPage"
          @change="load"
        />
      </div>

      <article
        v-if="selected"
        data-inbox-detail
        class="card sticky top-20 h-fit space-y-4 p-6"
      >
        <div class="flex items-start justify-between gap-3 border-b border-border-subtle pb-4">
          <div class="min-w-0 space-y-1">
            <h2 class="text-lg font-semibold text-ink">{{ selected.title }}</h2>
            <p class="text-xs text-muted tabular-nums">
              {{ formatDateTime(selected.sent_at, locale.current, auth.timeZone) }}
            </p>
          </div>
          <button
            type="button"
            class="btn btn-ghost btn-sm shrink-0 px-2"
            :aria-label="t('app.close')"
            @click="selected = null"
          >
            <X class="size-4" aria-hidden="true" />
          </button>
        </div>
        <p v-if="selected.withdrawn" class="rounded-lg bg-subtle px-3 py-2 text-xs text-muted">
          {{ t("inbox.withdrawn") }}
        </p>
        <p class="whitespace-pre-wrap text-sm leading-relaxed text-body">{{ selected.content }}</p>
        <div v-if="selected.link" class="pt-1">
          <RouterLink :to="selected.link" class="btn btn-secondary btn-sm">
            <ExternalLink class="size-3.5" aria-hidden="true" />
            {{ t("inbox.openRelated") }}
          </RouterLink>
        </div>
      </article>
    </div>
  </div>
</template>
