<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { RefreshCw, ScrollText, ChevronDown, ChevronUp } from "lucide-vue-next";
import { api, type AuditEntry } from "@/api/endpoints";
import { formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

const AUDIT_ACTION_KEYS = [
  "submit",
  "approve",
  "reject",
  "running",
  "stop_requested",
  "finished",
  "resources_released",
  "task_deleted",
  "extension_submitted",
  "extension_approved",
  "extension_rejected",
  "extension_superseded",
  "extend",
  "adjust_time",
  "ending_soon",
  "long_batch",
  "save_environment",
  "environment_saved",
  "image_build_submit",
  "image_build_succeeded",
  "image_build_failed",
  "image_delete_requested",
  "image_recycle_requested",
  "image_recycled",
  "base_image_updated",
  "sync_requested",
  "sync_completed",
  "workspace_ssh_connected",
  "workspace_ssh_rejected",
  "ssh_key_add",
  "ssh_key_enable",
  "ssh_key_disable",
  "ssh_access_updated",
  "api_token_issued",
  "api_token_revoked",
  "password_changed",
  "member_created",
  "member_updated",
  "group_created",
  "group_updated",
  "group_deleted",
  "policy_updated",
  "approval_rule_updated",
  "node_created",
  "node_updated",
  "node_offline",
  "notice_sent",
  "notice_canceled",
  "notice_revoked",
  "push_settings_updated",
  "recycle_settings_updated",
  "home_resize",
  "workspace_copied",
  "volume_delete_requested",
  "volume_deleted",
  "volume_deletion_notice",
  "cleanup",
  "user_test_cleanup",
] as const;

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const items = ref<AuditEntry[]>([]);
const page = ref(1);
const count = ref(0);
const nextPage = ref<number | null>(null);
const loading = ref(true);
const actionFilter = ref("");
const expanded = ref<Record<number, boolean>>({});

const actionOptions = computed(() =>
  AUDIT_ACTION_KEYS.map((key) => ({
    value: key,
    label: t(`auditActions.${key}`),
  })),
);

async function load(targetPage = 1, silent = false) {
  if (!silent) loading.value = true;
  try {
    const res = await api.listAudits({ action: actionFilter.value || undefined, page: targetPage, page_size: 30 });
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

watch(actionFilter, () => {
  void load(1);
});

onMounted(() => {
  void load(1);
});

useAutoRefresh(() => load(page.value, true), 20000);
</script>

<template>
  <div>
    <PageHeader :title="t('admin.auditsTitle')" :subtitle="t('admin.auditsSubtitle')">
      <template #actions>
        <select
          v-model="actionFilter"
          class="field-input min-h-8 w-auto min-w-44 py-1 text-xs"
          :aria-label="t('admin.filterActionLabel')"
        >
          <option value="">{{ t("admin.allActions") }}</option>
          <option v-for="opt in actionOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="load(page)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
      </template>
    </PageHeader>

    <EmptyState v-if="!loading && !items.length" :icon="ScrollText" :title="t('admin.noAudits')" />

    <template v-else>
      <div class="table-shell">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t("admin.time") }}</th>
              <th>{{ t("admin.actor") }}</th>
              <th>{{ t("admin.event") }}</th>
              <th>{{ t("admin.object") }}</th>
              <th class="text-end">{{ t("app.details") }}</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="entry in items" :key="entry.id">
              <tr>
                <td class="whitespace-nowrap tabular-nums text-xs text-muted">
                  {{ formatDateTime(entry.created_at, locale.current, auth.timeZone) }}
                </td>
                <td class="text-xs font-medium text-ink">{{ entry.actor_name || t("admin.systemActor") }}</td>
                <td class="text-xs text-ink">{{ entry.label || entry.action }}</td>
                <td class="text-xs text-muted">{{ entry.target_label || entry.target }}</td>
                <td class="text-end">
                  <button
                    v-if="entry.details?.length"
                    type="button"
                    class="btn btn-ghost btn-sm"
                    :aria-expanded="Boolean(expanded[entry.id])"
                    @click="expanded[entry.id] = !expanded[entry.id]"
                  >
                    <ChevronUp v-if="expanded[entry.id]" class="size-3.5" aria-hidden="true" />
                    <ChevronDown v-else class="size-3.5" aria-hidden="true" />
                    {{ t("app.details") }}
                  </button>
                </td>
              </tr>
              <tr v-if="expanded[entry.id] && entry.details?.length" class="bg-canvas/60">
                <td colspan="5">
                  <dl class="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
                    <div v-for="(d, idx) in entry.details" :key="idx" class="rounded-md border border-border-subtle bg-card px-2.5 py-1.5">
                      <dt class="text-muted">{{ d.label }}</dt>
                      <dd
                        class="mt-0.5 text-ink"
                        :class="d.structured ? 'font-mono whitespace-pre-wrap break-all' : 'break-words'"
                      >{{ d.value }}</dd>
                    </div>
                  </dl>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <PaginationBar
        :page="page"
        :page-size="30"
        :count="count"
        :next-page="nextPage"
        @change="load"
      />
    </template>
  </div>
</template>
