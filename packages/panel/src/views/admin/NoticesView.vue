<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { Send, RefreshCw } from "lucide-vue-next";
import { api } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { formatDateTime, localInputToOffsetIso } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import SegmentedTabs from "@/components/domain/SegmentedTabs.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import HelpTip from "@/components/ui/HelpTip.vue";
import DateTimePickerInput from "@/components/ui/DateTimePickerInput.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const activeTab = ref("send");

// Composer
const composerAvailable = ref(true);
const recipientCounts = ref<{ all: number; groups: Record<string, number> }>({ all: 0, groups: {} });
const members = ref<[number, string][]>([]);
const groups = ref<[number, string][]>([]);
const audiences = ref<[string, string][]>([]);
const presentations = ref<[string, string][]>([]);

const audience = ref("all");
const selectedMember = ref<number | null>(null);
const selectedGroup = ref<number | null>(null);
const title = ref("");
const content = ref("");
const link = ref("");
const presentation = ref("toast");
const seconds = ref<number | "">("");
const expiresAt = ref("");
const sending = ref(false);
const sendError = ref("");

// Rules
const inappEnabled = ref(false);
const wechatAvailable = ref(false);
const availableEvents = ref<Record<string, string>>({});
const inappEvents = ref<string[]>([]);
const wechatEvents = ref<string[]>([]);
const reminderMinutes = ref(30);
const channels = ref<
  {
    channel: string;
    label: string;
    fields: Record<string, { label: string; required?: boolean; secret?: boolean; help?: string }>;
    values: Record<string, string>;
  }[]
>([]);
const receiverInputs = ref<Record<string, Record<string, string>>>({});
const savingRules = ref(false);

// History
interface HistoryRow {
  id: number;
  title: string;
  content: string;
  manual: boolean;
  state: string;
  created_at: string;
  sender: string | null;
  audience: string;
  channel: string;
  count: number;
  delivered: number;
  read: number;
  show_read: boolean;
  can_cancel: boolean;
  can_revoke: boolean;
}
const historyOrigin = ref("");
const historyRows = ref<HistoryRow[]>([]);
const historyPage = ref(1);
const historyCount = ref(0);
const historyNext = ref<number | null>(null);
const loadingHistory = ref(false);

const tabs = computed(() => [
  { value: "send", label: t("admin.tabSend") },
  { value: "rules", label: t("admin.tabRules") },
  { value: "history", label: t("admin.tabHistory") },
]);

const estimatedRecipients = computed(() => {
  if (audience.value === "member") return selectedMember.value ? 1 : 0;
  if (audience.value === "group") {
    return selectedGroup.value ? (recipientCounts.value.groups[String(selectedGroup.value)] ?? 0) : 0;
  }
  return recipientCounts.value.all;
});

async function loadComposer() {
  try {
    const res = await api.getNoticeComposer();
    composerAvailable.value = res.available;
    recipientCounts.value = res.recipient_counts;
    members.value = res.members;
    groups.value = res.groups;
    audiences.value = res.audiences;
    presentations.value = res.presentations;
    if (!selectedMember.value && res.members.length) selectedMember.value = res.members[0][0];
    if (!selectedGroup.value && res.groups.length) selectedGroup.value = res.groups[0][0];
    if (res.presentations.length && !res.presentations.some(([k]) => k === presentation.value)) {
      presentation.value = res.presentations[0][0];
    }
  } catch {
    composerAvailable.value = false;
  }
}

async function loadRules() {
  try {
    const res = await api.getNotificationSettings();
    inappEnabled.value = res.inapp_enabled;
    wechatAvailable.value = res.wechat_available;
    availableEvents.value = res.available_events;
    inappEvents.value = [...(res.inapp_events || [])];
    wechatEvents.value = [...(res.events || [])];
    reminderMinutes.value = res.reminder_minutes;
    channels.value = res.channels;
    const inputs: Record<string, Record<string, string>> = {};
    for (const ch of res.channels) {
      inputs[ch.channel] = {};
      for (const [k, spec] of Object.entries(ch.fields)) {
        inputs[ch.channel][k] = spec.secret ? "" : (ch.values?.[k] || "");
      }
    }
    receiverInputs.value = inputs;
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
  }
}

async function loadHistory(page = 1, silent = false) {
  if (!silent) loadingHistory.value = true;
  try {
    const res = await api.listNoticeHistory({ origin: historyOrigin.value || undefined, page, page_size: 20 });
    historyRows.value = res.items;
    historyPage.value = res.page;
    historyCount.value = res.count;
    historyNext.value = res.next_page;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loadingHistory.value = false;
  }
}

async function sendAnnouncement() {
  sendError.value = "";
  sending.value = true;
  try {
    const payload: Record<string, unknown> = {
      audience: audience.value,
      title: title.value.trim(),
      content: content.value.trim(),
      presentation: presentation.value,
    };
    if (audience.value === "member") payload.member = selectedMember.value;
    if (audience.value === "group") payload.group = selectedGroup.value;
    if (link.value.trim()) payload.link = link.value.trim();
    if (seconds.value) payload.seconds = Number(seconds.value);
    if (expiresAt.value) payload.expires_at = localInputToOffsetIso(expiresAt.value);

    const res = await api.sendNotice(payload);
    notices.pushToast({ level: "success", title: t("admin.noticeSent", { count: res.recipients }), seconds: 5 });
    title.value = "";
    content.value = "";
    link.value = "";
  } catch (err) {
    sendError.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    sending.value = false;
  }
}

function toggleEvent(list: string[], key: string): string[] {
  return list.includes(key) ? list.filter((x) => x !== key) : [...list, key];
}

async function saveRules() {
  savingRules.value = true;
  try {
    const receivers: Record<string, Record<string, string>> = {};
    for (const [ch, vals] of Object.entries(receiverInputs.value)) {
      const nonEmpty: Record<string, string> = {};
      for (const [k, v] of Object.entries(vals)) {
        if (v.trim() !== "") nonEmpty[k] = v.trim();
      }
      if (Object.keys(nonEmpty).length) receivers[ch] = nonEmpty;
    }

    await api.updateNotificationSettings({
      enabled: wechatEvents.value.length > 0,
      events: wechatEvents.value,
      inapp_events: inappEnabled.value ? inappEvents.value : undefined,
      reminder_minutes: Number(reminderMinutes.value),
      receivers,
    });
    notices.pushToast({ level: "success", title: t("admin.rulesSaved"), seconds: 4 });
    await loadRules();
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    savingRules.value = false;
  }
}

async function stopCampaign(id: number, action: "cancel" | "withdraw") {
  try {
    await api.stopNotice(id, action);
    notices.pushToast({ level: "success", title: t("admin.noticeStopped"), seconds: 4 });
    await loadHistory(historyPage.value);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  }
}

watch(activeTab, (tab) => {
  if (tab === "rules") void loadRules();
  if (tab === "history") void loadHistory(1);
});

watch(historyOrigin, () => void loadHistory(1));

onMounted(() => {
  void loadComposer();
});

useAutoRefresh(() => {
  if (activeTab.value === "history") return loadHistory(historyPage.value, true);
}, 20000);
</script>

<template>
  <div>
    <PageHeader :title="t('admin.noticesTitle')" :subtitle="t('admin.noticesSubtitle')" />

    <SegmentedTabs v-model="activeTab" :tabs="tabs">
      <!-- Send Announcement -->
      <template #send>
        <p v-if="!composerAvailable" class="card p-6 text-sm text-muted">
          {{ t("admin.inappDisabled") }}
        </p>

        <form v-else class="card space-y-4 p-6" @submit.prevent="sendAnnouncement">
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label for="ntc-aud" class="mb-1 block text-xs font-medium text-body">{{ t("admin.audience") }}</label>
              <select id="ntc-aud" v-model="audience" class="field-input">
                <option v-for="[val, label] in audiences" :key="val" :value="val">{{ label }}</option>
              </select>
            </div>
            <div v-if="audience === 'member'">
              <label for="ntc-mem" class="mb-1 block text-xs font-medium text-body">{{ t("admin.selectMember") }}</label>
              <select id="ntc-mem" v-model.number="selectedMember" class="field-input">
                <option v-for="[id, label] in members" :key="id" :value="id">{{ label }}</option>
              </select>
            </div>
            <div v-else-if="audience === 'group'">
              <label for="ntc-grp" class="mb-1 block text-xs font-medium text-body">{{ t("admin.selectGroup") }}</label>
              <select id="ntc-grp" v-model.number="selectedGroup" class="field-input">
                <option v-for="[id, label] in groups" :key="id" :value="id">{{ label }}</option>
              </select>
            </div>
          </div>

          <p class="rounded-lg bg-accent-soft px-3.5 py-2 text-xs font-medium text-ink">
            {{ t("admin.recipientCount", { count: estimatedRecipients }) }}
          </p>

          <div>
            <label for="ntc-title" class="mb-1 block text-xs font-medium text-body">{{ t("admin.noticeTitle") }}</label>
            <input id="ntc-title" v-model="title" type="text" required class="field-input" />
          </div>

          <div>
            <label for="ntc-body" class="mb-1 block text-xs font-medium text-body">{{ t("admin.noticeContent") }}</label>
            <textarea id="ntc-body" v-model="content" rows="4" required class="field-input text-sm" />
          </div>

          <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label for="ntc-pres" class="mb-1 block text-xs font-medium text-body">{{ t("admin.presentation") }}</label>
              <select id="ntc-pres" v-model="presentation" class="field-input">
                <option v-for="[val, label] in presentations" :key="val" :value="val">{{ label }}</option>
              </select>
            </div>
            <div>
              <label for="ntc-sec" class="mb-1 block text-xs font-medium text-body">{{ t("admin.durationSeconds") }}</label>
              <input id="ntc-sec" v-model="seconds" type="number" min="1" class="field-input tabular-nums" />
            </div>
            <div>
              <label for="ntc-exp" class="mb-1 block text-xs font-medium text-body">{{ t("admin.cutoffTime") }}</label>
              <DateTimePickerInput id="ntc-exp" v-model="expiresAt" clearable :aria-label="t('admin.cutoffTime')" />
            </div>
          </div>

          <div>
            <label for="ntc-link" class="mb-1 block text-xs font-medium text-body">{{ t("admin.noticeLink") }}</label>
            <input id="ntc-link" v-model="link" type="text" placeholder="/environments" class="field-input font-mono text-sm" />
          </div>

          <p v-if="sendError" role="alert" class="rounded-lg bg-danger-bg px-3 py-2 text-xs text-danger">{{ sendError }}</p>

          <button type="submit" class="btn btn-primary" :disabled="sending">
            <Send class="size-4" aria-hidden="true" />
            {{ sending ? t("app.saving") : t("admin.sendNow") }}
          </button>
        </form>
      </template>

      <!-- Automatic Rules -->
      <template #rules>
        <form class="card space-y-6 p-6" @submit.prevent="saveRules">
          <fieldset v-if="inappEnabled" class="space-y-2.5">
            <legend class="text-sm font-semibold text-ink">{{ t("admin.inappEventsLegend") }}</legend>
            <div class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              <label
                v-for="(label, key) in availableEvents"
                :key="`inapp-${key}`"
                class="flex cursor-pointer items-center gap-2.5 rounded-lg border border-border-subtle bg-canvas/60 px-3 py-2 text-sm"
              >
                <input
                  type="checkbox"
                  :checked="inappEvents.includes(key)"
                  @change="inappEvents = toggleEvent(inappEvents, key)"
                />
                <span>{{ label }}</span>
              </label>
            </div>
          </fieldset>

          <fieldset v-if="wechatAvailable" class="space-y-3 border-t border-border-subtle pt-5">
            <legend class="flex items-center gap-1.5 text-sm font-semibold text-ink">
              <span>{{ t("admin.wechatLegend") }}</span>
              <HelpTip :text="t('admin.wechatHelp')" :label="t('admin.wechatLegend')" />
            </legend>
            <div class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              <label
                v-for="(label, key) in availableEvents"
                :key="`wc-${key}`"
                class="flex cursor-pointer items-center gap-2.5 rounded-lg border border-border-subtle bg-canvas/60 px-3 py-2 text-sm"
              >
                <input
                  type="checkbox"
                  :checked="wechatEvents.includes(key)"
                  @change="wechatEvents = toggleEvent(wechatEvents, key)"
                />
                <span>{{ label }}</span>
              </label>
            </div>

            <div v-for="ch in channels" :key="ch.channel" class="space-y-3 pt-2">
              <div v-for="(spec, fieldKey) in ch.fields" :key="fieldKey">
                <div class="mb-1 flex items-center gap-1.5">
                  <label :for="`adm-ch-${ch.channel}-${fieldKey}`" class="block text-xs font-medium text-body">
                    {{ ch.label }} · {{ spec.label }}
                  </label>
                  <HelpTip
                    v-if="spec.secret && ch.values?.[fieldKey]"
                    :text="t('admin.tokenConfiguredHint')"
                    :label="spec.label"
                  />
                </div>
                <input
                  :id="`adm-ch-${ch.channel}-${fieldKey}`"
                  v-model="receiverInputs[ch.channel][fieldKey]"
                  :type="spec.secret ? 'password' : 'text'"
                  class="field-input"
                />
              </div>
            </div>
          </fieldset>

          <div class="border-t border-border-subtle pt-5">
            <div class="max-w-sm">
              <div class="mb-1 flex items-center gap-1.5">
                <label for="ntc-lead" class="block text-sm font-medium text-ink">
                  {{ t("admin.reminderLeadMinutes") }}
                </label>
                <HelpTip :text="t('admin.reminderLeadHelp')" :label="t('admin.reminderLeadMinutes')" />
              </div>
              <input
                id="ntc-lead"
                v-model.number="reminderMinutes"
                type="number"
                min="1"
                max="1440"
                required
                class="field-input tabular-nums"
              />
            </div>
          </div>

          <button type="submit" class="btn btn-primary" :disabled="savingRules">
            {{ savingRules ? t("app.saving") : t("admin.saveRules") }}
          </button>
        </form>
      </template>

      <!-- History -->
      <template #history>
        <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div class="flex gap-1.5">
            <button
              v-for="opt in [
                { value: '', label: t('admin.filterAll') },
                { value: 'manual', label: t('admin.filterManual') },
                { value: 'automatic', label: t('admin.filterAutomatic') },
              ]"
              :key="opt.value"
              type="button"
              class="btn btn-sm"
              :class="historyOrigin === opt.value ? 'btn-primary' : 'btn-secondary'"
              @click="historyOrigin = opt.value"
            >
              {{ opt.label }}
            </button>
          </div>
          <button type="button" class="btn btn-ghost btn-sm" :disabled="loadingHistory" @click="loadHistory(historyPage)">
            <RefreshCw class="size-4" :class="{ 'animate-spin': loadingHistory }" aria-hidden="true" />
            {{ t("app.refresh") }}
          </button>
        </div>

        <div class="space-y-3">
          <article v-for="row in historyRows" :key="row.id" class="card flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
            <div class="min-w-0 space-y-1">
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-sm font-semibold text-ink">{{ row.title }}</h3>
                <StatusBadge :status="row.state" />
              </div>
              <p class="text-xs text-body">{{ row.content }}</p>
              <p class="text-xs text-muted tabular-nums">
                {{ row.audience }} · {{ row.channel }} ·
                {{ t("admin.deliveredRead", { delivered: row.delivered, count: row.count, read: row.read }) }} ·
                {{ formatDateTime(row.created_at, locale.current, auth.timeZone) }}
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-2">
              <button
                v-if="row.can_cancel"
                type="button"
                class="btn btn-secondary btn-sm"
                @click="stopCampaign(row.id, 'cancel')"
              >
                {{ t("admin.cancelRemaining") }}
              </button>
              <button
                v-if="row.can_revoke"
                type="button"
                class="btn btn-danger btn-sm"
                @click="stopCampaign(row.id, 'withdraw')"
              >
                {{ t("admin.withdrawNotice") }}
              </button>
            </div>
          </article>
        </div>

        <PaginationBar
          :page="historyPage"
          :page-size="20"
          :count="historyCount"
          :next-page="historyNext"
          @change="loadHistory"
        />
      </template>
    </SegmentedTabs>
  </div>
</template>
