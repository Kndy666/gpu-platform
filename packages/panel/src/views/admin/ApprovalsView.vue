<script setup lang="ts">
import { ref, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import { CheckSquare, RefreshCw, Check, X } from "lucide-vue-next";
import { api, type TaskItem, type ExtensionItem } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { compactNumber, formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const pendingTasks = ref<TaskItem[]>([]);
const pendingExtensions = ref<ExtensionItem[]>([]);
const loading = ref(true);

const priorities = ref<Record<string, 0 | 100>>({});
const notes = ref<Record<string, string>>({});
const busyId = ref<string | null>(null);

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    const [tasksRes, extRes] = await Promise.all([
      api.listApprovals("tasks", 1),
      api.listApprovals("extensions", 1),
    ]);
    pendingTasks.value = tasksRes.items as TaskItem[];
    pendingExtensions.value = extRes.items as ExtensionItem[];
    for (const item of pendingTasks.value) {
      if (!(item.id in priorities.value)) priorities.value[item.id] = (item.priority === 100 ? 100 : 0);
    }
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function decideTask(task: TaskItem, action: "approve" | "reject") {
  const note = (notes.value[task.id] || "").trim();
  if (action === "reject" && !note) return;
  busyId.value = task.id;
  try {
    await api.reviewTask(task.id, {
      action,
      priority: action === "approve" ? priorities.value[task.id] ?? 0 : undefined,
      reason: note,
    });
    notices.pushToast({ level: "success", title: t("admin.decisionSaved"), seconds: 4 });
    await Promise.all([load(), auth.refresh()]);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    busyId.value = null;
  }
}

async function decideExtension(ext: ExtensionItem, action: "approve" | "reject") {
  const note = (notes.value[`ext-${ext.id}`] || "").trim();
  if (action === "reject" && !note) return;
  busyId.value = `ext-${ext.id}`;
  try {
    await api.reviewExtension(ext.id, { action, reason: note });
    notices.pushToast({ level: "success", title: t("admin.decisionSaved"), seconds: 4 });
    await Promise.all([load(), auth.refresh()]);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    busyId.value = null;
  }
}

onMounted(() => {
  void load(false);
});

useAutoRefresh(() => load(true), 10000);
</script>

<template>
  <div class="space-y-8">
    <PageHeader :title="t('admin.approvalsTitle')" :subtitle="t('admin.approvalsSubtitle')">
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="load(false)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
      </template>
    </PageHeader>

    <div v-if="loading && !pendingTasks.length && !pendingExtensions.length" class="card p-10 text-center text-sm text-muted">
      {{ t("app.loading") }}
    </div>

    <EmptyState
      v-else-if="!pendingTasks.length && !pendingExtensions.length"
      :icon="CheckSquare"
      :title="t('admin.noPending')"
    />

    <template v-else>
      <section v-if="pendingTasks.length" class="space-y-3">
        <h2>{{ t("admin.pendingTasks") }} ({{ pendingTasks.length }})</h2>
        <article v-for="task in pendingTasks" :key="task.id" class="card space-y-3 p-5">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <RouterLink :to="`/tasks/${task.id}`" class="text-base font-semibold text-ink no-underline hover:text-accent">
                {{ task.purpose }}
              </RouterLink>
              <p class="mt-0.5 text-xs text-muted tabular-nums">
                {{ task.username }} · {{ task.node_name }} ·
                {{ task.mode === "interactive" ? t("tasks.modeInteractive") : t("tasks.modeBatch") }} ·
                {{ task.gpu > 0 ? `${compactNumber(task.gpu, 0)} GPU · ` : "" }}{{ compactNumber(task.cpu) }} {{ t("app.cores") }} · {{ compactNumber(task.memory_gib) }} GiB
              </p>
              <p v-if="task.ends_at" class="mt-0.5 text-xs text-muted tabular-nums">
                {{ formatDateTime(task.starts_at || task.created_at, locale.current, auth.timeZone) }} –
                {{ formatDateTime(task.ends_at, locale.current, auth.timeZone) }}
              </p>
            </div>
            <RouterLink :to="`/tasks/${task.id}`" class="btn btn-ghost btn-sm">{{ t("tasks.openDetail") }}</RouterLink>
          </div>

          <div class="grid grid-cols-1 gap-2.5 sm:grid-cols-12">
            <select v-model.number="priorities[task.id]" class="field-input sm:col-span-3" :aria-label="t('taskDetail.priority')">
              <option :value="0">{{ t("tasks.priorityNormal") }}</option>
              <option :value="100">{{ t("tasks.priorityHigh") }}</option>
            </select>
            <input
              v-model="notes[task.id]"
              type="text"
              class="field-input sm:col-span-5"
              :placeholder="t('admin.notePlaceholder')"
            />
            <div class="flex items-center justify-end gap-2 sm:col-span-4">
              <button
                type="button"
                class="btn btn-primary btn-sm"
                :disabled="busyId === task.id"
                @click="decideTask(task, 'approve')"
              >
                <Check class="size-3.5" aria-hidden="true" />
                {{ t("admin.approve") }}
              </button>
              <button
                type="button"
                class="btn btn-danger btn-sm"
                :disabled="busyId === task.id || !(notes[task.id] || '').trim()"
                @click="decideTask(task, 'reject')"
              >
                <X class="size-3.5" aria-hidden="true" />
                {{ t("admin.reject") }}
              </button>
            </div>
          </div>
        </article>
      </section>

      <section v-if="pendingExtensions.length" class="space-y-3">
        <h2>{{ t("admin.pendingExtensions") }} ({{ pendingExtensions.length }})</h2>
        <article v-for="ext in pendingExtensions" :key="ext.id" class="card space-y-3 p-5">
          <div>
            <RouterLink :to="`/tasks/${ext.request_id}`" class="text-base font-semibold text-ink no-underline hover:text-accent">
              {{ ext.request_purpose || ext.request_id }}
            </RouterLink>
            <p class="mt-0.5 text-xs text-muted tabular-nums">
              {{ ext.username }} · {{ ext.node_name }} ·
              {{
                t("admin.extensionChange", {
                  from: formatDateTime(ext.previous_ends_at, locale.current, auth.timeZone),
                  to: formatDateTime(ext.ends_at, locale.current, auth.timeZone),
                })
              }}
            </p>
            <p v-if="ext.reason" class="mt-1 text-sm text-body">{{ ext.reason }}</p>
          </div>

          <div class="flex flex-wrap items-center gap-2.5">
            <input
              v-model="notes[`ext-${ext.id}`]"
              type="text"
              class="field-input min-w-56 flex-1"
              :placeholder="t('admin.notePlaceholder')"
            />
            <button
              type="button"
              class="btn btn-primary btn-sm"
              :disabled="busyId === `ext-${ext.id}`"
              @click="decideExtension(ext, 'approve')"
            >
              <Check class="size-3.5" aria-hidden="true" />
              {{ t("admin.approve") }}
            </button>
            <button
              type="button"
              class="btn btn-danger btn-sm"
              :disabled="busyId === `ext-${ext.id}` || !(notes[`ext-${ext.id}`] || '').trim()"
              @click="decideExtension(ext, 'reject')"
            >
              <X class="size-3.5" aria-hidden="true" />
              {{ t("admin.reject") }}
            </button>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>
