<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  ArrowLeft,
  Square,
  Save,
  Play,
  AlertTriangle,
  Check,
  X,
} from "lucide-vue-next";
import { api, type TaskItem, type TaskConnectionData } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { compactNumber, formatDateTime, toLocalDateTimeInput, localInputToOffsetIso } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CopyButton from "@/components/ui/CopyButton.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import HelpTip from "@/components/ui/HelpTip.vue";
import DateTimePickerInput from "@/components/ui/DateTimePickerInput.vue";

const { t, te } = useI18n();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const taskId = computed(() => String(route.params.id));
const task = ref<TaskItem | null>(null);
const conn = ref<TaskConnectionData | null>(null);
const loading = ref(true);

// Stop confirmation & env save
const stopDialogOpen = ref(false);
const stopping = ref(false);
const savingEnv = ref(false);

// Inline time-change state (no modal popup)
type TimeMode = "extend" | "shorten" | "adjust";
const activeTimeMode = ref<TimeMode | null>(null);
const timeEndsAt = ref("");
const timeReason = ref("");
const timeBusy = ref(false);

// Staff review state
const reviewPriority = ref<0 | 100>(0);
const reviewNote = ref("");
const reviewBusy = ref(false);

let pollTimer: number | null = null;

const isActiveStatus = computed(() =>
  Boolean(task.value && ["submitted", "scheduled", "queued", "starting", "preparing", "running"].includes(task.value.status)),
);

const isOwner = computed(() => Boolean(task.value && auth.user && task.value.user_id === auth.user.id));

const availableTimeModes = computed<TimeMode[]>(() => {
  const list: TimeMode[] = [];
  if (task.value?.can_extend) list.push("extend");
  if (task.value?.can_shorten_time) list.push("shorten");
  if (task.value?.can_adjust_time) list.push("adjust");
  return list;
});

const lifecycleSteps = computed(() => {
  const status = task.value?.status ?? "";
  const idxMap: Record<string, number> = {
    submitted: 0,
    scheduled: 1,
    queued: 1,
    starting: 2,
    preparing: 2,
    running: 3,
    succeeded: 4,
    stopped: 4,
    failed: 4,
    rejected: 1,
  };
  const currentIdx = idxMap[status] ?? 0;
  return [
    { key: "submitted", label: t("taskDetail.stepSubmitted"), state: currentIdx > 0 ? "done" : "current" },
    {
      key: "approved",
      label: t("taskDetail.stepApproved"),
      state: status === "rejected" ? "error" : currentIdx > 1 ? "done" : currentIdx === 1 ? "current" : "pending",
    },
    {
      key: "preparing",
      label: t("taskDetail.stepPreparing"),
      state: status === "rejected" ? "pending" : currentIdx > 2 ? "done" : currentIdx === 2 ? "current" : "pending",
    },
    {
      key: "running",
      label: t("taskDetail.stepRunning"),
      state: status === "rejected" ? "pending" : currentIdx > 3 ? "done" : currentIdx === 3 ? "current" : "pending",
    },
    {
      key: "ended",
      label: t("taskDetail.stepEnded"),
      state: status === "failed" ? "error" : currentIdx >= 4 ? "done" : "pending",
    },
  ];
});

function syncInlineTimeDefaults() {
  const modes = availableTimeModes.value;
  if (!modes.length) {
    activeTimeMode.value = null;
    return;
  }
  if (!activeTimeMode.value || !modes.includes(activeTimeMode.value)) {
    activeTimeMode.value = modes[0];
  }
  if (!timeEndsAt.value && task.value?.ends_at) {
    timeEndsAt.value = toLocalDateTimeInput(task.value.ends_at, auth.timeZone);
  }
}

function selectTimeMode(mode: TimeMode) {
  activeTimeMode.value = mode;
  timeEndsAt.value = toLocalDateTimeInput(task.value?.ends_at, auth.timeZone);
  timeReason.value = "";
}

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    const [taskRes, connRes] = await Promise.all([
      api.getTask(taskId.value),
      api.getTaskConnection(taskId.value).catch(() => null),
    ]);
    task.value = taskRes;
    conn.value = connRes;
    syncInlineTimeDefaults();
  } catch (err) {
    if (!silent) {
      notices.pushToast({
        level: "error",
        title: err instanceof Error ? err.message : t("app.loadError"),
        seconds: 6,
      });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function handleStop() {
  stopping.value = true;
  try {
    task.value = await api.stopTask(taskId.value);
    stopDialogOpen.value = false;
    notices.pushToast({ level: "success", title: t("tasks.stopRequested"), seconds: 5 });
    void load(true);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    stopping.value = false;
  }
}

async function handleSaveEnvironment() {
  savingEnv.value = true;
  try {
    await api.saveEnvironment(taskId.value);
    notices.pushToast({ level: "success", title: t("taskDetail.saveStarted"), seconds: 6 });
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    savingEnv.value = false;
  }
}

async function submitTimeChange() {
  if (!activeTimeMode.value || !timeEndsAt.value) return;
  timeBusy.value = true;
  try {
    const iso = localInputToOffsetIso(timeEndsAt.value);
    if (activeTimeMode.value === "extend") {
      await api.requestExtension(taskId.value, { ends_at: iso, reason: timeReason.value.trim() });
      notices.pushToast({ level: "success", title: t("taskDetail.extensionSubmitted"), seconds: 5 });
    } else if (activeTimeMode.value === "shorten") {
      await api.shortenTask(taskId.value, iso);
      notices.pushToast({ level: "success", title: t("taskDetail.timeUpdated"), seconds: 5 });
    } else {
      await api.reviewTask(taskId.value, { action: "adjust_time", ends_at: iso, reason: timeReason.value.trim() });
      notices.pushToast({ level: "success", title: t("taskDetail.timeUpdated"), seconds: 5 });
    }
    timeReason.value = "";
    await load(true);
    if (task.value?.ends_at) {
      timeEndsAt.value = toLocalDateTimeInput(task.value.ends_at, auth.timeZone);
    }
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    timeBusy.value = false;
  }
}

async function submitReview(action: "approve" | "reject") {
  reviewBusy.value = true;
  try {
    task.value = await api.reviewTask(taskId.value, {
      action,
      priority: action === "approve" ? reviewPriority.value : undefined,
      reason: reviewNote.value.trim(),
    });
    notices.pushToast({ level: "success", title: t("taskDetail.reviewSaved"), seconds: 5 });
    await auth.refresh();
    await load(true);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    reviewBusy.value = false;
  }
}

function formatReviewReason(reason: string | undefined): string {
  if (!reason) return "";
  const key = `taskDetail.reviewReasons.${reason}`;
  return te(key) ? t(key) : reason;
}

function formatAllocatedDevice(
  alloc: NonNullable<TaskItem["gpu_allocations"]>[number],
): string {
  const id =
    alloc.device_id ||
    (alloc.index !== undefined && alloc.index !== null
      ? `#${alloc.index}`
      : alloc.slot !== undefined && alloc.slot !== null
        ? `#${alloc.slot}`
        : "");
  return id || "—";
}

function modeLabel(mode: TimeMode): string {
  if (mode === "extend") return t("taskDetail.requestExtension");
  if (mode === "shorten") return t("taskDetail.shortenTime");
  return t("taskDetail.adjustTime");
}

onMounted(() => {
  void load(false);
  pollTimer = window.setInterval(() => {
    if (document.visibilityState === "visible" && isActiveStatus.value) {
      void load(true);
    }
  }, 5000);
});

onBeforeUnmount(() => {
  if (pollTimer !== null) window.clearInterval(pollTimer);
});

const backRoute = computed(() =>
  task.value?.mode === "batch" ? "/jobs" : "/environments",
);
</script>

<template>
  <div>
    <div class="mb-4">
      <RouterLink :to="backRoute" class="inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-ink">
        <ArrowLeft class="size-4" aria-hidden="true" />
        {{ t("taskDetail.backToList") }}
      </RouterLink>
    </div>

    <div v-if="loading && !task" class="card p-10 text-center text-sm text-muted">
      {{ t("app.loading") }}
    </div>

    <template v-else-if="task">
      <PageHeader
        :title="task.purpose"
        :subtitle="`${task.node_name} · ${task.mode === 'interactive' ? t('tasks.modeInteractive') : t('tasks.modeBatch')} · ${task.username}`"
      >
        <template #actions>
          <StatusBadge :status="task.status" :label="task.status_label" />
          <button
            v-if="task.can_stop"
            type="button"
            class="btn btn-danger"
            @click="stopDialogOpen = true"
          >
            <Square class="size-4" aria-hidden="true" />
            {{ task.status === "running" ? t("tasks.stop") : t("tasks.cancelTask") }}
          </button>
        </template>
      </PageHeader>

      <!-- Lifecycle Stepper -->
      <ol class="card mb-6 grid grid-cols-5 gap-2 p-4">
        <li
          v-for="step in lifecycleSteps"
          :key="step.key"
          class="flex flex-col items-center gap-1.5 text-center text-xs"
        >
          <span
            class="flex size-6 items-center justify-center rounded-full border text-[11px] font-semibold"
            :class="{
              'border-ok bg-ok-bg text-ok': step.state === 'done',
              'border-accent bg-accent text-accent-ink': step.state === 'current',
              'border-danger bg-danger-bg text-danger': step.state === 'error',
              'border-border-strong bg-canvas text-muted': step.state === 'pending',
            }"
          >
            <Check v-if="step.state === 'done'" class="size-3.5" aria-hidden="true" />
            <X v-else-if="step.state === 'error'" class="size-3.5" aria-hidden="true" />
            <span v-else>•</span>
          </span>
          <span :class="step.state === 'pending' ? 'text-muted' : 'font-medium text-ink'">
            {{ step.label }}
          </span>
        </li>
      </ol>

      <!-- Errors or review note -->
      <div v-if="task.error" role="alert" class="mb-6 flex items-start gap-3 rounded-xl border border-danger/30 bg-danger-bg px-4 py-3.5 text-sm text-danger">
        <AlertTriangle class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <p>{{ task.error }}</p>
      </div>

      <div v-if="task.review_reason" class="mb-6 rounded-xl border border-border-subtle bg-card px-4 py-3 text-sm">
        <span class="font-medium text-ink">{{ t("taskDetail.reviewReason") }}:</span>
        <span class="ml-1.5 text-body">{{ formatReviewReason(task.review_reason) }}</span>
      </div>

      <!-- Administrator Review Panel -->
      <section v-if="auth.isStaff && task.status === 'submitted'" class="card mb-6 space-y-4 border-accent/35 p-5">
        <h2>{{ t("taskDetail.adminReviewTitle") }}</h2>
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <label for="rev-priority" class="mb-1 block text-xs font-medium text-body">
              {{ t("taskDetail.priority") }}
            </label>
            <select id="rev-priority" v-model.number="reviewPriority" class="field-input">
              <option :value="0">{{ t("tasks.priorityNormal") }}</option>
              <option :value="100">{{ t("tasks.priorityHigh") }}</option>
            </select>
          </div>
          <div class="sm:col-span-2">
            <label for="rev-note" class="mb-1 block text-xs font-medium text-body">
              {{ t("admin.notePlaceholder") }}
            </label>
            <input id="rev-note" v-model="reviewNote" type="text" class="field-input" />
          </div>
        </div>
        <div class="flex flex-wrap gap-2.5">
          <button type="button" class="btn btn-primary" :disabled="reviewBusy" @click="submitReview('approve')">
            {{ t("taskDetail.approve") }}
          </button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="reviewBusy || !reviewNote.trim()"
            @click="submitReview('reject')"
          >
            {{ t("taskDetail.reject") }}
          </button>
        </div>
      </section>

      <div class="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <!-- Left Column -->
        <div class="space-y-6 lg:col-span-7">
          <!-- Connection Panel (Interactive) -->
          <section v-if="task.mode === 'interactive'" class="card space-y-4 p-5">
            <h2>{{ !isOwner && auth.isStaff ? t("taskDetail.adminConnectionTitle") : t("taskDetail.connectionTitle") }}</h2>

            <div
              v-if="conn?.administrator_access && !conn.administrator_access.ready && !isOwner"
              class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warn/30 bg-warn-bg px-3.5 py-2.5 text-sm text-warn"
            >
              <span>{{ t("taskDetail.noAdminKeyWarning") }}</span>
              <RouterLink to="/account" class="btn btn-secondary btn-sm">
                {{ t("taskDetail.addKeyNow") }}
              </RouterLink>
            </div>

            <template v-if="conn?.connection?.command">
              <div>
                <div class="mb-1.5 flex items-center justify-between gap-2">
                  <span class="text-xs font-medium text-muted">SSH</span>
                  <CopyButton :text="conn.connection.command" :label="t('taskDetail.copySshCommand')" />
                </div>
                <pre class="code-block">{{ conn.connection.command }}</pre>
              </div>

              <div v-if="conn.connection.config">
                <div class="mb-1.5 flex items-center justify-between gap-2">
                  <span class="text-xs font-medium text-muted">{{ t("taskDetail.vscodeConfig") }}</span>
                  <CopyButton :text="conn.connection.config" :label="t('taskDetail.copySshConfig')" />
                </div>
                <pre class="code-block">{{ conn.connection.config }}</pre>
              </div>

              <div v-if="conn.internal_ssh" class="border-t border-border-subtle pt-4">
                <div class="mb-2 flex items-center justify-between gap-2">
                  <div class="flex items-center gap-1.5">
                    <h3 class="text-sm font-medium text-ink">{{ t("taskDetail.peerSsh") }}</h3>
                    <HelpTip :text="t('taskDetail.peerSshHint')" :label="t('taskDetail.peerSsh')" />
                  </div>
                  <CopyButton :text="conn.internal_ssh.command" />
                </div>
                <pre class="code-block">{{ conn.internal_ssh.command }}</pre>

                <div v-if="conn.internal_ssh_peers?.length" class="mt-3 space-y-1.5">
                  <p class="text-xs font-medium text-muted">{{ t("taskDetail.peerPeers") }}</p>
                  <div
                    v-for="peer in conn.internal_ssh_peers"
                    :key="peer.name"
                    class="flex items-center justify-between gap-2 rounded-lg border border-border-subtle bg-canvas/60 px-3 py-1.5 text-xs"
                  >
                    <span class="font-medium text-ink">{{ peer.name }}</span>
                    <CopyButton v-if="peer.reachable" :text="peer.command" />
                    <span v-else class="text-muted">{{ t("taskDetail.unavailable") }}</span>
                  </div>
                </div>
              </div>
            </template>

            <p v-else class="text-sm text-muted">{{ t("taskDetail.waitingConnection") }}</p>
          </section>

          <!-- Reuse Environment -->
          <section
            v-if="task.mode === 'interactive' && task.status === 'running' && isOwner && !task.stop_requested"
            class="card space-y-3 p-5"
          >
            <div class="flex items-center gap-1.5">
              <h2>{{ t("taskDetail.reuseTitle") }}</h2>
              <HelpTip :text="t('taskDetail.reuseHint')" :label="t('taskDetail.reuseTitle')" />
            </div>
            <div class="flex flex-wrap gap-2.5">
              <button
                v-if="task.can_save_environment"
                type="button"
                class="btn btn-secondary"
                :disabled="savingEnv"
                @click="handleSaveEnvironment"
              >
                <Save class="size-4" aria-hidden="true" />
                {{ t("taskDetail.saveImage") }}
              </button>
              <button
                type="button"
                class="btn btn-primary"
                @click="router.push({ path: '/jobs/new', query: { source: task.id } })"
              >
                <Play class="size-4" aria-hidden="true" />
                {{ t("taskDetail.turnIntoJob") }}
              </button>
            </div>
          </section>

          <!-- Volumes & Retention -->
          <section class="card space-y-2.5 p-5">
            <h2>{{ t("taskDetail.storageTitle") }}</h2>
            <dl class="space-y-2 text-sm">
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("taskDetail.homePath") }}</dt>
                <dd class="font-mono text-xs text-ink">{{ task.home_dir || `/home/${task.login_user || task.username}` }}</dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("taskDetail.workPath") }}</dt>
                <dd class="font-mono text-xs text-ink">/workspace ({{ compactNumber(task.workspace_gib) }} GiB)</dd>
              </div>
              <div v-if="task.volume?.retain_until" class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("taskDetail.workRetainUntil") }}</dt>
                <dd class="tabular-nums text-xs text-ink">
                  {{ formatDateTime(task.volume.retain_until, locale.current, auth.timeZone) }}
                </dd>
              </div>
            </dl>
          </section>
        </div>

        <!-- Right Column -->
        <div class="space-y-6 lg:col-span-5">
          <!-- Specs -->
          <section class="card space-y-3.5 p-5">
            <h2>{{ t("taskDetail.specsTitle") }}</h2>
            <dl class="space-y-2.5 text-sm">
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("tasks.server") }}</dt>
                <dd class="font-medium text-ink">{{ task.node_name }}</dd>
              </div>
              <div v-if="task.image_name" class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("taskDetail.image") }}</dt>
                <dd class="text-end font-medium text-ink">{{ task.image_name }}</dd>
              </div>
              <div v-if="task.source_environment_name" class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("taskDetail.sourceEnv") }}</dt>
                <dd class="text-end font-medium text-ink">{{ task.source_environment_name }}</dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("tasks.resources") }}</dt>
                <dd class="text-end font-medium text-ink tabular-nums">
                  {{ compactNumber(task.cpu) }} {{ t("app.cores") }} · {{ compactNumber(task.memory_gib) }} GiB
                  <template v-if="task.gpu > 0">
                    · {{ compactNumber(task.gpu, 0) }} GPU ({{ compactNumber(task.gpu_compute_total, 0) }}% / {{ compactNumber(task.gpu_memory_total_gib) }} GiB)
                  </template>
                </dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="shrink-0 text-muted">{{ t("taskDetail.assignedDevices") }}</dt>
                <dd class="text-end font-mono text-xs text-ink break-all">
                  <template v-if="task.gpu_allocations?.length">
                    <div v-for="(g, idx) in task.gpu_allocations" :key="idx">
                      {{ formatAllocatedDevice(g) }}
                    </div>
                  </template>
                  <template v-else-if="task.gpu > 0">{{ t("taskDetail.pendingAllocation") }}</template>
                  <template v-else>{{ t("app.none") }}</template>
                </dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("taskDetail.startsAt") }}</dt>
                <dd class="tabular-nums text-ink">{{ formatDateTime(task.starts_at || task.started_at || task.created_at, locale.current, auth.timeZone) }}</dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-muted">{{ task.mode === "interactive" ? t("taskDetail.endsAt") : t("taskDetail.runUntil") }}</dt>
                <dd class="tabular-nums text-ink">
                  {{ task.ends_at ? formatDateTime(task.ends_at, locale.current, auth.timeZone) : t("taskDetail.commandCompletion") }}
                </dd>
              </div>
              <div v-if="task.exit_code !== null && task.exit_code !== undefined" class="flex justify-between gap-2">
                <dt class="text-muted">{{ t("taskDetail.exitCode") }}</dt>
                <dd class="font-mono text-ink">{{ task.exit_code }}</dd>
              </div>
            </dl>

            <div v-if="task.command" class="border-t border-border-subtle pt-3">
              <p class="mb-1 text-xs font-medium text-muted">{{ t("taskDetail.command") }}</p>
              <pre class="code-block text-xs">{{ task.command }}</pre>
            </div>
          </section>

          <!-- Schedule & Extensions (Interactive, inline forms without popup modals) -->
          <section v-if="task.mode === 'interactive'" class="card space-y-4 p-5">
            <div class="flex items-center justify-between gap-2">
              <h2>{{ t("taskDetail.timeTitle") }}</h2>
              <span class="text-xs text-muted tabular-nums">
                {{ t("taskDetail.extensionsUsed", { used: task.extension_count ?? 0, limit: task.extension_limit ?? 0 }) }}
              </span>
            </div>

            <template v-if="availableTimeModes.length && activeTimeMode">
              <!-- Inline action switcher when more than one time action is available -->
              <div v-if="availableTimeModes.length > 1" class="flex flex-wrap gap-1.5">
                <button
                  v-for="mode in availableTimeModes"
                  :key="mode"
                  type="button"
                  class="btn btn-sm"
                  :class="activeTimeMode === mode ? 'btn-primary' : 'btn-secondary'"
                  @click="selectTimeMode(mode)"
                >
                  {{ modeLabel(mode) }}
                </button>
              </div>

              <form class="space-y-3 rounded-xl border border-border-subtle bg-canvas/55 p-3.5" @submit.prevent="submitTimeChange">
                <div class="flex items-center gap-1.5">
                  <span class="text-sm font-medium text-ink">{{ modeLabel(activeTimeMode) }}</span>
                  <HelpTip
                    v-if="activeTimeMode === 'extend' && task.extension_hours"
                    :text="t('taskDetail.extensionMaxHours', { hours: task.extension_hours })"
                    :label="modeLabel(activeTimeMode)"
                  />
                  <HelpTip
                    v-else-if="activeTimeMode === 'shorten'"
                    :text="t('taskDetail.shortenHint')"
                    :label="modeLabel(activeTimeMode)"
                  />
                </div>

                <p
                  v-if="activeTimeMode === 'shorten' && task.pending_extension"
                  class="rounded-lg bg-warn-bg px-3 py-2 text-xs text-warn"
                >
                  {{ t("taskDetail.shortenPendingWarning") }}
                </p>

                <div>
                  <label for="time-ends-at" class="mb-1 block text-xs font-medium text-body">
                    {{ t("taskDetail.newEndTime") }}
                  </label>
                  <DateTimePickerInput
                    id="time-ends-at"
                    v-model="timeEndsAt"
                    :aria-label="t('taskDetail.newEndTime')"
                  />
                </div>

                <div v-if="activeTimeMode !== 'shorten'">
                  <label for="time-reason" class="mb-1 block text-xs font-medium text-body">
                    {{ t("taskDetail.reason") }}
                  </label>
                  <textarea
                    id="time-reason"
                    v-model="timeReason"
                    rows="2"
                    :required="activeTimeMode === 'extend'"
                    class="field-input text-sm"
                  />
                </div>

                <button type="submit" class="btn btn-primary btn-sm" :disabled="timeBusy">
                  {{ timeBusy ? t("app.saving") : modeLabel(activeTimeMode) }}
                </button>
              </form>
            </template>

            <div v-if="task.extension_history?.length" class="space-y-2 border-t border-border-subtle pt-3">
              <p class="text-xs font-medium text-muted">{{ t("taskDetail.extensionHistory") }}</p>
              <div
                v-for="ext in task.extension_history"
                :key="ext.id"
                class="rounded-lg border border-border-subtle bg-canvas/60 px-3 py-2 text-xs"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="tabular-nums font-medium text-ink">
                    {{ formatDateTime(ext.previous_ends_at, locale.current, auth.timeZone) }} →
                    {{ formatDateTime(ext.ends_at, locale.current, auth.timeZone) }}
                  </span>
                  <StatusBadge :status="ext.status" :label="ext.status_label" />
                </div>
                <p v-if="ext.reason" class="mt-1 text-muted">{{ ext.reason }}</p>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- Stop Confirmation -->
      <ConfirmDialog
        v-model:open="stopDialogOpen"
        :title="t('tasks.stopConfirmTitle')"
        :message="t('tasks.stopConfirmBody')"
        :confirm-label="t('tasks.stopConfirmAction')"
        danger
        :busy="stopping"
        @confirm="handleStop"
      />
    </template>
  </div>
</template>
