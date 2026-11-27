<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { Plus, Pencil, Trash2 } from "lucide-vue-next";
import { api, type GroupItem } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { cleanDecimal, compactNumber } from "@/lib/format";
import { useNotificationsStore } from "@/stores/notifications";
import PageHeader from "@/components/layout/PageHeader.vue";
import ModalDialog from "@/components/ui/ModalDialog.vue";
import SwitchToggle from "@/components/ui/SwitchToggle.vue";
import QuotaFields, { type QuotaModel, type QuotaResourceDef } from "@/components/domain/QuotaFields.vue";

const { t } = useI18n();
const notices = useNotificationsStore();

const groups = ref<GroupItem[]>([]);
const loading = ref(true);

// Create/Edit modal
const dialogOpen = ref(false);
const editingId = ref<number | null>(null);
const quotaResources = ref<QuotaResourceDef[]>([]);
const formName = ref("");
const formDefault = ref(false);
const formQuota = ref<QuotaModel>({
  unlimited: false,
  concurrent_tasks: 1,
  interactive_hours: 120,
  extension_limit: 1,
  approval_mode: "manual",
  home_size_gib: 50,
  resource_limits: {},
  per_allocation_limits: {},
});
const formError = ref("");
const saving = ref(false);

// Delete modal
const deleteTarget = ref<GroupItem | null>(null);
const moveDestination = ref<number | null>(null);
const deleting = ref(false);

const destinationChoices = computed(() =>
  groups.value.filter((g) => g.id !== deleteTarget.value?.id),
);

async function load() {
  loading.value = true;
  try {
    const res = await api.listGroups(1, 100);
    groups.value = res.items;
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    loading.value = false;
  }
}

function normalizeLimitMap(raw: Record<string, number | string | null> | undefined): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (const [k, v] of Object.entries(raw ?? {})) {
    const cleaned = cleanDecimal(v);
    out[k] = cleaned === "" ? null : cleaned;
  }
  return out;
}

async function openDialog(group: GroupItem | null) {
  formError.value = "";
  editingId.value = group?.id ?? null;
  try {
    const opts = (await api.getGroupOptions(group?.id ?? null)) as {
      initial: Record<string, unknown>;
      resources: QuotaResourceDef[];
    };
    quotaResources.value = opts.resources;
    const init = opts.initial;
    formName.value = String(init.name ?? "");
    formDefault.value = Boolean(init.make_default);
    formQuota.value = {
      unlimited: Boolean(init.unlimited),
      concurrent_tasks: (init.concurrent_tasks as number | null) ?? 1,
      interactive_hours: (init.interactive_hours as number | null) ?? 120,
      extension_limit: Number(init.extension_limit ?? 1),
      approval_mode: init.approval_mode === "automatic" ? "automatic" : "manual",
      home_size_gib: Number(init.home_size_gib ?? 50),
      resource_limits: normalizeLimitMap(init.resource_limits as Record<string, number | string | null> | undefined),
      per_allocation_limits: normalizeLimitMap(
        init.per_allocation_limits as Record<string, number | string | null> | undefined,
      ),
    };
    dialogOpen.value = true;
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  }
}

async function saveGroup() {
  formError.value = "";
  saving.value = true;
  try {
    const cleanResources: Record<string, number> = {};
    for (const [k, v] of Object.entries(formQuota.value.resource_limits)) {
      if (v !== null && v !== "" && v !== undefined) cleanResources[k] = Number(v);
    }
    const cleanPerAlloc: Record<string, number> = {};
    for (const [k, v] of Object.entries(formQuota.value.per_allocation_limits)) {
      if (v !== null && v !== "" && v !== undefined) cleanPerAlloc[k] = Number(v);
    }

    const payload: Record<string, unknown> = {
      name: formName.value.trim(),
      make_default: formDefault.value,
      unlimited: formQuota.value.unlimited,
      extension_limit: Number(formQuota.value.extension_limit),
      approval_mode: formQuota.value.approval_mode,
      home_quota_gib: Number(formQuota.value.home_size_gib),
      extensions: {
        resources: cleanResources,
        per_allocation: cleanPerAlloc,
      },
    };
    if (!formQuota.value.unlimited) {
      payload.concurrent_tasks = Number(formQuota.value.concurrent_tasks ?? 1);
      payload.interactive_hours = Number(formQuota.value.interactive_hours ?? 120);
    }

    if (editingId.value) {
      await api.updateGroup(editingId.value, payload);
    } else {
      await api.createGroup(payload);
    }
    dialogOpen.value = false;
    notices.pushToast({ level: "success", title: t("admin.groupSaved"), seconds: 4 });
    await load();
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    saving.value = false;
  }
}

function openDelete(group: GroupItem) {
  deleteTarget.value = group;
  moveDestination.value = groups.value.find((g) => g.id !== group.id)?.id ?? null;
}

async function confirmDelete() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  try {
    await api.deleteGroup(deleteTarget.value.id, moveDestination.value);
    deleteTarget.value = null;
    notices.pushToast({ level: "success", title: t("admin.groupDeleted"), seconds: 4 });
    await load();
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    deleting.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div>
    <PageHeader :title="t('admin.groupsTitle')" :subtitle="t('admin.groupsSubtitle')">
      <template #actions>
        <button type="button" class="btn btn-primary" @click="openDialog(null)">
          <Plus class="size-4" aria-hidden="true" />
          {{ t("admin.addGroup") }}
        </button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
      <article v-for="group in groups" :key="group.id" class="card flex flex-col justify-between gap-4 p-5">
        <div class="space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2">
              <h2 class="text-base font-semibold text-ink">{{ group.name }}</h2>
              <span v-if="group.is_default" class="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent">
                {{ t("admin.defaultGroup") }}
              </span>
            </div>
            <span class="text-xs text-muted tabular-nums">
              {{ t("admin.memberCount", { count: group.member_count ?? 0 }) }}
            </span>
          </div>

          <dl class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
            <div>
              <dt class="text-muted">{{ t("admin.approvalPolicy") }}</dt>
              <dd class="font-medium text-ink">
                {{ group.approval_mode === "automatic" ? t("account.approvalAuto") : t("account.approvalManual") }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">{{ t("admin.homeQuota") }}</dt>
              <dd class="font-medium text-ink tabular-nums">{{ compactNumber(group.home_size_gib ?? 50) }} GiB</dd>
            </div>
            <div>
              <dt class="text-muted">{{ t("admin.concurrentTasks") }}</dt>
              <dd class="font-medium text-ink tabular-nums">
                {{ group.unlimited ? t("app.unlimited") : (group.concurrent_tasks ?? "—") }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">{{ t("admin.maxEnvHours") }}</dt>
              <dd class="font-medium text-ink tabular-nums">
                {{ group.unlimited ? t("app.unlimited") : `${group.interactive_hours ?? "—"} ${t("app.hours")}` }}
              </dd>
            </div>
          </dl>
        </div>

        <div class="flex items-center justify-end gap-2 border-t border-border-subtle pt-3">
          <button type="button" class="btn btn-secondary btn-sm" @click="openDialog(group)">
            <Pencil class="size-3.5" aria-hidden="true" />
            {{ t("app.edit") }}
          </button>
          <button
            v-if="!group.is_default && groups.length > 1"
            type="button"
            class="btn btn-danger btn-sm"
            @click="openDelete(group)"
          >
            <Trash2 class="size-3.5" aria-hidden="true" />
            {{ t("admin.deleteGroup") }}
          </button>
        </div>
      </article>
    </div>

    <!-- Edit / Create Group Modal -->
    <ModalDialog
      v-model:open="dialogOpen"
      :title="editingId ? t('admin.editGroup') : t('admin.addGroup')"
      size="lg"
    >
      <form id="group-form" class="space-y-5" @submit.prevent="saveGroup">
        <div>
          <label for="grp-name" class="mb-1 block text-xs font-medium text-body">{{ t("admin.groupName") }}</label>
          <input id="grp-name" v-model="formName" type="text" required class="field-input" />
        </div>

        <SwitchToggle id="grp-default" v-model="formDefault" :label="t('admin.setAsDefault')" />

        <div class="border-t border-border-subtle pt-4">
          <QuotaFields v-model="formQuota" :resources="quotaResources" id-prefix="group" />
        </div>

        <p v-if="formError" role="alert" class="rounded-lg bg-danger-bg px-3 py-2 text-xs text-danger">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn btn-secondary" :disabled="saving" @click="dialogOpen = false">
          {{ t("app.cancel") }}
        </button>
        <button type="submit" form="group-form" class="btn btn-primary" :disabled="saving">
          {{ saving ? t("app.saving") : t("app.save") }}
        </button>
      </template>
    </ModalDialog>

    <!-- Delete Group Modal -->
    <ModalDialog
      :open="deleteTarget !== null"
      :title="t('admin.deleteGroupTitle', { name: deleteTarget?.name || '' })"
      closed-by="closerequest"
      @update:open="!$event && (deleteTarget = null)"
    >
      <div class="space-y-4">
        <p class="text-sm text-body">{{ t("admin.deleteGroupBody") }}</p>
        <div>
          <label for="grp-dest" class="mb-1 block text-xs font-medium text-body">{{ t("admin.moveMembersTo") }}</label>
          <select id="grp-dest" v-model.number="moveDestination" required class="field-input">
            <option v-for="g in destinationChoices" :key="g.id" :value="g.id">{{ g.name }}</option>
          </select>
        </div>
      </div>
      <template #footer>
        <button type="button" class="btn btn-secondary" :disabled="deleting" @click="deleteTarget = null">
          {{ t("app.cancel") }}
        </button>
        <button type="button" class="btn btn-danger-solid" :disabled="deleting || !moveDestination" @click="confirmDelete">
          {{ deleting ? t("app.saving") : t("admin.deleteGroup") }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>
