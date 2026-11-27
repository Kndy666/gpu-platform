<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { KeyRound, Eye, EyeOff, Trash2, Download, ExternalLink } from "lucide-vue-next";
import { api, type SshKeyItem, type ApiTokenItem } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { compactNumber, formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useThemeStore, type ThemePreference } from "@/stores/theme";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import PageHeader from "@/components/layout/PageHeader.vue";
import SegmentedTabs from "@/components/domain/SegmentedTabs.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CopyButton from "@/components/ui/CopyButton.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import HelpTip from "@/components/ui/HelpTip.vue";

const { t } = useI18n();
const auth = useAuthStore();
const theme = useThemeStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const activeTab = ref("keys");

// SSH keys
const keys = ref<SshKeyItem[]>([]);
const keyName = ref("");
const publicKey = ref("");
const keyBusy = ref(false);
const keyError = ref("");

// API tokens
const tokens = ref<ApiTokenItem[]>([]);
const revealedTokens = ref<Record<string, string>>({});
const tokenName = ref("");
const tokenExpires = ref<number>(30 * 86400);
const tokenBusy = ref(false);
const newlyCreatedToken = ref<string | null>(null);
const revokeTarget = ref<ApiTokenItem | null>(null);
const revoking = ref(false);

// Password
const oldPassword = ref("");
const newPassword1 = ref("");
const newPassword2 = ref("");
const passwordBusy = ref(false);
const passwordError = ref("");

// Personal notification channels
interface UserChannel {
  channel: string;
  label: string;
  fields: Record<string, { label: string; required?: boolean; secret?: boolean; help?: string }>;
  values: Record<string, string>;
}
const channels = ref<UserChannel[]>([]);
const channelInputs = ref<Record<string, Record<string, string>>>({});
const savingChannel = ref("");

const tabs = computed(() => [
  { value: "keys", label: t("account.tabKeys") },
  { value: "api", label: t("account.tabApi") },
  { value: "password", label: t("account.tabPassword") },
  { value: "preferences", label: t("account.tabPreferences") },
]);

const apiBaseUrl = computed(() => `${window.location.origin}/api/v1`);

const tokenLifetimes = computed(() => [
  { label: t("account.lifetime1d"), seconds: 86400 },
  { label: t("account.lifetime7d"), seconds: 7 * 86400 },
  { label: t("account.lifetime30d"), seconds: 30 * 86400 },
  { label: t("account.lifetime90d"), seconds: 90 * 86400 },
  { label: t("account.lifetime1y"), seconds: 365 * 86400 },
  { label: t("account.lifetimeNever"), seconds: 0 },
]);

async function loadKeys() {
  const res = await api.listKeys();
  keys.value = res.items;
}

async function loadTokens() {
  const res = await api.listTokens();
  tokens.value = res.items;
}

async function loadChannels() {
  try {
    const res = await api.getUserNotificationChannels();
    channels.value = (res.channels || []) as unknown as UserChannel[];
    const init: Record<string, Record<string, string>> = {};
    for (const ch of channels.value) {
      init[ch.channel] = { ...(ch.values || {}) };
    }
    channelInputs.value = init;
  } catch {
    channels.value = [];
  }
}

async function addKey() {
  keyError.value = "";
  keyBusy.value = true;
  try {
    await api.addKey({ name: keyName.value.trim(), public_key: publicKey.value.trim() });
    keyName.value = "";
    publicKey.value = "";
    notices.pushToast({ level: "success", title: t("account.keyAdded"), seconds: 5 });
    await loadKeys();
  } catch (err) {
    keyError.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    keyBusy.value = false;
  }
}

async function toggleKey(item: SshKeyItem) {
  try {
    await api.updateKey(item.id, { enabled: !item.enabled });
    notices.pushToast({
      level: "success",
      title: item.enabled ? t("account.keyDisabled") : t("account.keyEnabled"),
      seconds: 5,
    });
    await loadKeys();
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  }
}

async function createToken() {
  tokenBusy.value = true;
  try {
    const created = await api.createToken({
      name: tokenName.value.trim() || undefined,
      expires_in: tokenExpires.value,
    });
    newlyCreatedToken.value = created.token;
    revealedTokens.value[created.id] = created.token;
    tokenName.value = "";
    notices.pushToast({ level: "success", title: t("account.tokenCreated"), seconds: 6 });
    await loadTokens();
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    tokenBusy.value = false;
  }
}

async function toggleRevealToken(id: string) {
  if (revealedTokens.value[id]) {
    delete revealedTokens.value[id];
    return;
  }
  try {
    const detail = await api.revealToken(id);
    if (detail.token) revealedTokens.value[id] = detail.token;
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  }
}

async function confirmRevoke() {
  if (!revokeTarget.value) return;
  revoking.value = true;
  try {
    await api.revokeToken(revokeTarget.value.id);
    delete revealedTokens.value[revokeTarget.value.id];
    revokeTarget.value = null;
    notices.pushToast({ level: "success", title: t("account.tokenRevoked"), seconds: 5 });
    await loadTokens();
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    revoking.value = false;
  }
}

async function submitPassword() {
  passwordError.value = "";
  passwordBusy.value = true;
  try {
    await api.changePassword({
      old_password: oldPassword.value,
      new_password1: newPassword1.value,
      new_password2: newPassword2.value,
    });
    oldPassword.value = "";
    newPassword1.value = "";
    newPassword2.value = "";
    notices.pushToast({ level: "success", title: t("account.passwordUpdated"), seconds: 6 });
    await loadTokens();
  } catch (err) {
    passwordError.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    passwordBusy.value = false;
  }
}

async function saveChannel(channelName: string) {
  savingChannel.value = channelName;
  try {
    await api.saveUserNotificationChannel(channelName, channelInputs.value[channelName] || {});
    notices.pushToast({ level: "success", title: t("app.actionSuccess"), seconds: 4 });
    await loadChannels();
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    savingChannel.value = "";
  }
}

onMounted(() => {
  void Promise.all([loadKeys(), loadTokens(), loadChannels()]);
});
</script>

<template>
  <div>
    <PageHeader :title="t('account.title')" :subtitle="t('account.subtitle')" />

    <SegmentedTabs v-model="activeTab" :tabs="tabs">
      <!-- SSH Keys -->
      <template #keys>
        <div class="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <form class="card space-y-4 p-5 lg:col-span-5" @submit.prevent="addKey">
            <p class="text-sm text-muted">{{ t("account.keysIntro") }}</p>
            <div>
              <label for="ssh-name" class="mb-1 block text-sm font-medium text-ink">{{ t("account.keyName") }}</label>
              <input id="ssh-name" v-model="keyName" type="text" required class="field-input" :placeholder="t('account.keyNamePlaceholder')" />
            </div>
            <div>
              <label for="ssh-pub" class="mb-1 block text-sm font-medium text-ink">{{ t("account.publicKey") }}</label>
              <textarea id="ssh-pub" v-model="publicKey" rows="4" required class="field-input font-mono text-xs" :placeholder="t('account.publicKeyPlaceholder')" />
            </div>
            <p v-if="keyError" role="alert" class="rounded-lg bg-danger-bg px-3 py-2 text-xs text-danger">{{ keyError }}</p>
            <button type="submit" class="btn btn-primary" :disabled="keyBusy">
              {{ keyBusy ? t("app.saving") : t("account.addKey") }}
            </button>
          </form>

          <div class="space-y-3 lg:col-span-7">
            <EmptyState v-if="!keys.length" :icon="KeyRound" :title="t('account.noKeys')" />
            <article v-for="item in keys" :key="item.id" class="card flex flex-wrap items-center justify-between gap-3 p-4">
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <span class="font-medium text-ink">{{ item.name }}</span>
                  <StatusBadge :status="item.enabled ? 'active' : 'inactive'" />
                </div>
                <p class="mt-1 truncate font-mono text-xs text-muted">{{ item.fingerprint }}</p>
              </div>
              <button
                type="button"
                class="btn btn-sm"
                :class="item.enabled ? 'btn-danger' : 'btn-secondary'"
                @click="toggleKey(item)"
              >
                {{ item.enabled ? t("account.disableKey") : t("account.enableKey") }}
              </button>
            </article>
          </div>
        </div>
      </template>

      <!-- API & Agent skill -->
      <template #api>
        <div class="space-y-6">
          <section class="card flex flex-wrap items-center justify-between gap-4 p-5">
            <div class="max-w-2xl space-y-1">
              <p class="text-sm text-body">{{ t("account.apiIntro") }}</p>
              <p class="font-mono text-xs text-muted">{{ t("account.apiEndpoint") }}: {{ apiBaseUrl }}</p>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <CopyButton :text="apiBaseUrl" />
              <a href="/api/v1/docs" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm">
                <ExternalLink class="size-3.5" aria-hidden="true" />
                {{ t("account.openApiDocs") }}
              </a>
              <a href="/api/v1/skill.zip" class="btn btn-primary btn-sm">
                <Download class="size-3.5" aria-hidden="true" />
                {{ t("account.downloadSkill") }}
              </a>
            </div>
          </section>

          <form class="card flex flex-wrap items-end gap-3 p-5" @submit.prevent="createToken">
            <div class="min-w-52 flex-1">
              <label for="tok-name" class="mb-1 block text-xs font-medium text-body">{{ t("account.tokenName") }}</label>
              <input id="tok-name" v-model="tokenName" type="text" class="field-input" :placeholder="t('account.tokenNamePlaceholder')" />
            </div>
            <div class="w-44">
              <label for="tok-exp" class="mb-1 block text-xs font-medium text-body">{{ t("account.tokenLifetime") }}</label>
              <select id="tok-exp" v-model.number="tokenExpires" class="field-input">
                <option v-for="opt in tokenLifetimes" :key="opt.seconds" :value="opt.seconds">{{ opt.label }}</option>
              </select>
            </div>
            <button type="submit" class="btn btn-primary" :disabled="tokenBusy">
              {{ t("account.createToken") }}
            </button>
          </form>

          <div v-if="newlyCreatedToken" class="card space-y-2 border-accent/40 bg-accent-soft p-4">
            <div class="flex items-center justify-between gap-2">
              <p class="text-sm font-medium text-ink">{{ t("account.tokenCreated") }}</p>
              <CopyButton :text="newlyCreatedToken" />
            </div>
            <pre class="code-block text-xs">{{ newlyCreatedToken }}</pre>
          </div>

          <EmptyState v-if="!tokens.length" :title="t('account.noTokens')" />
          <div v-else class="table-shell">
            <table class="data-table">
              <thead>
                <tr>
                  <th>{{ t("account.tokenName") }}</th>
                  <th>Token</th>
                  <th>{{ t("account.tokenLifetime") }}</th>
                  <th>{{ t("tasks.status") }}</th>
                  <th class="text-end">{{ t("app.actions") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="tok in tokens" :key="tok.id">
                  <td class="font-medium text-ink">{{ tok.name || tok.id }}</td>
                  <td class="font-mono text-xs">
                    {{ revealedTokens[tok.id] || tok.masked }}
                  </td>
                  <td class="tabular-nums text-xs text-muted">
                    {{ tok.expires_at ? formatDateTime(tok.expires_at, locale.current, auth.timeZone) : t("account.neverExpires") }}
                  </td>
                  <td><StatusBadge :status="tok.status" /></td>
                  <td class="text-end whitespace-nowrap">
                    <div class="inline-flex items-center gap-1.5">
                      <button
                        v-if="tok.status === 'active'"
                        type="button"
                        class="btn btn-ghost btn-sm"
                        @click="toggleRevealToken(tok.id)"
                      >
                        <EyeOff v-if="revealedTokens[tok.id]" class="size-3.5" aria-hidden="true" />
                        <Eye v-else class="size-3.5" aria-hidden="true" />
                        {{ revealedTokens[tok.id] ? t("account.hideToken") : t("account.showToken") }}
                      </button>
                      <CopyButton v-if="revealedTokens[tok.id]" :text="revealedTokens[tok.id]" variant="ghost" />
                      <button
                        v-if="tok.status === 'active'"
                        type="button"
                        class="btn btn-danger btn-sm"
                        @click="revokeTarget = tok"
                      >
                        <Trash2 class="size-3.5" aria-hidden="true" />
                        {{ t("account.revokeToken") }}
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>

      <!-- Password -->
      <template #password>
        <form class="card space-y-4 p-6" @submit.prevent="submitPassword">
          <div>
            <label for="pw-old" class="mb-1 block text-sm font-medium text-ink">{{ t("account.currentPassword") }}</label>
            <input id="pw-old" v-model="oldPassword" type="password" autocomplete="current-password" required class="field-input" />
          </div>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label for="pw-new1" class="mb-1 block text-sm font-medium text-ink">{{ t("account.newPassword") }}</label>
              <input id="pw-new1" v-model="newPassword1" type="password" autocomplete="new-password" required class="field-input" />
            </div>
            <div>
              <label for="pw-new2" class="mb-1 block text-sm font-medium text-ink">{{ t("account.confirmPassword") }}</label>
              <input id="pw-new2" v-model="newPassword2" type="password" autocomplete="new-password" required class="field-input" />
            </div>
          </div>
          <p v-if="passwordError" role="alert" class="rounded-lg bg-danger-bg px-3 py-2 text-xs text-danger">{{ passwordError }}</p>
          <button type="submit" class="btn btn-primary" :disabled="passwordBusy">
            {{ passwordBusy ? t("app.saving") : t("account.updatePassword") }}
          </button>
        </form>
      </template>

      <!-- Preferences & Quota Summary -->
      <template #preferences>
        <div class="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div class="space-y-6">
            <section class="card space-y-4 p-5">
              <div class="flex items-center gap-1.5">
                <h2>{{ t("account.themeLabel") }}</h2>
                <HelpTip :text="t('account.themeHelp')" :label="t('account.themeLabel')" />
              </div>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="opt in (['system', 'light', 'dark'] as ThemePreference[])"
                  :key="opt"
                  type="button"
                  class="btn btn-sm"
                  :class="theme.preference === opt ? 'btn-primary' : 'btn-secondary'"
                  @click="theme.setPreference(opt)"
                >
                  {{ opt === "system" ? t("app.themeSystem") : opt === "light" ? t("app.themeLight") : t("app.themeDark") }}
                </button>
              </div>
            </section>

            <section class="card space-y-4 p-5">
              <div class="flex items-center gap-1.5">
                <h2>{{ t("account.languageLabel") }}</h2>
                <HelpTip :text="t('account.languageHelp')" :label="t('account.languageLabel')" />
              </div>
              <div class="flex flex-wrap gap-2">
                <button
                  type="button"
                  class="btn btn-sm"
                  :class="locale.current === 'en' ? 'btn-primary' : 'btn-secondary'"
                  @click="locale.switchLocale('en', true)"
                >
                  English
                </button>
                <button
                  type="button"
                  class="btn btn-sm"
                  :class="locale.current === 'zh-hans' ? 'btn-primary' : 'btn-secondary'"
                  @click="locale.switchLocale('zh-hans', true)"
                >
                  简体中文
                </button>
              </div>
            </section>

            <section v-for="ch in channels" :key="ch.channel" class="card space-y-3 p-5">
              <h2>{{ ch.label }}</h2>
              <div v-for="(spec, fieldKey) in ch.fields" :key="fieldKey">
                <div class="mb-1 flex items-center gap-1.5">
                  <label :for="`ch-${ch.channel}-${fieldKey}`" class="block text-xs font-medium text-body">
                    {{ spec.label }}
                  </label>
                  <HelpTip v-if="spec.help" :text="spec.help" :label="spec.label" />
                </div>
                <input
                  :id="`ch-${ch.channel}-${fieldKey}`"
                  v-model="channelInputs[ch.channel][fieldKey]"
                  :type="spec.secret ? 'password' : 'text'"
                  class="field-input"
                />
              </div>
              <button
                type="button"
                class="btn btn-primary btn-sm"
                :disabled="savingChannel === ch.channel"
                @click="saveChannel(ch.channel)"
              >
                {{ t("app.save") }}
              </button>
            </section>
          </div>

          <section v-if="auth.user" class="card h-fit space-y-3.5 p-5">
            <h2>{{ t("account.quotaSummary") }}</h2>
            <dl class="space-y-2.5 text-sm">
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("account.groupLabel") }}</dt>
                <dd class="font-medium text-ink">{{ auth.user.group?.name || "—" }}</dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("account.approvalLabel") }}</dt>
                <dd class="font-medium text-ink">
                  {{ auth.user.effective_limits.approval_mode === "automatic" ? t("account.approvalAuto") : t("account.approvalManual") }}
                </dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("admin.homeQuota") }}</dt>
                <dd class="font-medium text-ink tabular-nums">{{ compactNumber(auth.user.home_quota_gib) }} GiB</dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("admin.concurrentTasks") }}</dt>
                <dd class="font-medium text-ink tabular-nums">
                  {{ auth.user.effective_limits.unlimited ? t("app.unlimited") : (auth.user.effective_limits.concurrent_tasks ?? "—") }}
                </dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("admin.maxEnvHours") }}</dt>
                <dd class="font-medium text-ink tabular-nums">
                  {{ auth.user.effective_limits.unlimited ? t("app.unlimited") : `${auth.user.effective_limits.interactive_hours ?? "—"} ${t("app.hours")}` }}
                </dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("admin.extensionLimit") }}</dt>
                <dd class="font-medium text-ink tabular-nums">{{ auth.user.extension_limit }}</dd>
              </div>
            </dl>
          </section>
        </div>
      </template>
    </SegmentedTabs>

    <ConfirmDialog
      :open="revokeTarget !== null"
      :title="t('account.revokeConfirmTitle')"
      :message="t('account.revokeConfirmBody')"
      :confirm-label="t('account.revokeToken')"
      danger
      :busy="revoking"
      @update:open="!$event && (revokeTarget = null)"
      @confirm="confirmRevoke"
    />
  </div>
</template>
