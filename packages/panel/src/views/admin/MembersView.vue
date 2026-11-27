<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { Plus, Pencil, Search } from "lucide-vue-next";
import { api, type UserProfile } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { cleanDecimal, compactNumber } from "@/lib/format";
import { isImeComposing } from "@/lib/ime";
import { useNotificationsStore } from "@/stores/notifications";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import ModalDialog from "@/components/ui/ModalDialog.vue";
import SwitchToggle from "@/components/ui/SwitchToggle.vue";
import HelpTip from "@/components/ui/HelpTip.vue";
import QuotaFields, { type QuotaModel, type QuotaResourceDef } from "@/components/domain/QuotaFields.vue";

interface GroupDefaultData {
  unlimited: boolean;
  concurrent_tasks: number | null;
  interactive_hours: number | null;
  extension_limit: number;
  approval_mode: "manual" | "automatic";
  home_size_gib: number;
  resources: Record<string, number | string | null>;
  per_allocation: Record<string, number | string | null>;
}

const { t } = useI18n();
const notices = useNotificationsStore();

const members = ref<UserProfile[]>([]);
const page = ref(1);
const count = ref(0);
const nextPage = ref<number | null>(null);
const loading = ref(true);
const searchInput = ref("");
const activeQuery = ref("");

// Modal state
const dialogOpen = ref(false);
const editingId = ref<number | null>(null);
const canGrantAdmin = ref(false);
const groups = ref<{ id: number; name: string }[]>([]);
const groupDefaults = ref<Record<string, GroupDefaultData>>({});
const quotaResources = ref<QuotaResourceDef[]>([]);

const formUsername = ref("");
const formFirstName = ref("");
const formEmail = ref("");
const formPassword = ref("");
const formGroupId = ref<number | null>(null);
const formActive = ref(true);
const formStaff = ref(false);
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

async function load(targetPage = 1) {
  loading.value = true;
  try {
    const res = await api.listMembers({ q: activeQuery.value || undefined, page: targetPage, page_size: 25 });
    members.value = res.items;
    page.value = res.page;
    count.value = res.count;
    nextPage.value = res.next_page;
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    loading.value = false;
  }
}

function runSearch() {
  activeQuery.value = searchInput.value.trim();
  void load(1);
}

function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !isImeComposing(event)) {
    event.preventDefault();
    runSearch();
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

async function openDialog(member: UserProfile | null) {
  formError.value = "";
  editingId.value = member?.id ?? null;
  formPassword.value = "";
  try {
    const opts = (await api.getMemberOptions(member?.id ?? null)) as {
      initial: Record<string, unknown>;
      resources: QuotaResourceDef[];
      groups: { id: number; name: string }[];
      group_defaults: Record<string, GroupDefaultData>;
      can_grant_admin: boolean;
    };
    groups.value = opts.groups;
    groupDefaults.value = opts.group_defaults;
    quotaResources.value = opts.resources;
    canGrantAdmin.value = opts.can_grant_admin;
    const init = opts.initial;
    formUsername.value = String(init.username ?? "");
    formFirstName.value = String(init.first_name ?? "");
    formEmail.value = String(init.email ?? "");
    formGroupId.value = (init.group as number | null) ?? opts.groups[0]?.id ?? null;
    formActive.value = Boolean(init.is_active ?? true);
    formStaff.value = Boolean(init.is_staff ?? false);
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

function onGroupChange(nextGroupId: number) {
  formGroupId.value = nextGroupId;
  if (editingId.value !== null) return;
  const preset = groupDefaults.value[String(nextGroupId)];
  if (!preset) return;
  formQuota.value = {
    unlimited: preset.unlimited,
    concurrent_tasks: preset.concurrent_tasks,
    interactive_hours: preset.interactive_hours,
    extension_limit: preset.extension_limit,
    approval_mode: preset.approval_mode,
    home_size_gib: preset.home_size_gib,
    resource_limits: normalizeLimitMap(preset.resources),
    per_allocation_limits: normalizeLimitMap(preset.per_allocation),
  };
}

async function saveMember() {
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
      username: formUsername.value.trim(),
      first_name: formFirstName.value.trim(),
      email: formEmail.value.trim(),
      group: formGroupId.value,
      is_active: formActive.value,
      home_quota_gib: Number(formQuota.value.home_size_gib),
      unlimited: formQuota.value.unlimited,
      extension_limit: Number(formQuota.value.extension_limit),
      approval_mode: formQuota.value.approval_mode,
      extensions: {
        resources: cleanResources,
        per_allocation: cleanPerAlloc,
      },
    };
    if (!formQuota.value.unlimited) {
      payload.concurrent_tasks = Number(formQuota.value.concurrent_tasks ?? 1);
      payload.interactive_hours = Number(formQuota.value.interactive_hours ?? 120);
    }
    if (canGrantAdmin.value) {
      payload.is_staff = formStaff.value;
    }
    if (formPassword.value) {
      payload.password = formPassword.value;
    }

    if (editingId.value) {
      await api.updateMember(editingId.value, payload);
    } else {
      await api.createMember(payload);
    }
    dialogOpen.value = false;
    notices.pushToast({ level: "success", title: t("admin.memberSaved"), seconds: 4 });
    await load(page.value);
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  void load(1);
});
</script>

<template>
  <div>
    <PageHeader :title="t('admin.membersTitle')" :subtitle="t('admin.membersSubtitle')">
      <template #actions>
        <button type="button" class="btn btn-primary" @click="openDialog(null)">
          <Plus class="size-4" aria-hidden="true" />
          {{ t("admin.addMember") }}
        </button>
      </template>
    </PageHeader>

    <div class="mb-4 flex flex-wrap items-center gap-3">
      <div class="relative min-w-56 flex-1 max-w-sm">
        <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden="true" />
        <input
          v-model="searchInput"
          type="search"
          class="field-input pl-9"
          :placeholder="t('admin.searchMembers')"
          @keydown="onSearchKeydown"
        />
      </div>
      <button type="button" class="btn btn-secondary" @click="runSearch">{{ t("app.search") }}</button>
    </div>

    <div class="table-shell">
      <table class="data-table">
        <thead>
          <tr>
            <th>{{ t("auth.username") }}</th>
            <th>{{ t("admin.group") }}</th>
            <th>{{ t("admin.approvalPolicy") }}</th>
            <th>{{ t("admin.homeQuota") }}</th>
            <th>{{ t("tasks.status") }}</th>
            <th class="text-end">{{ t("app.actions") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in members" :key="m.id">
            <td>
              <p class="font-medium text-ink">{{ m.first_name || m.username }}</p>
              <p class="text-xs text-muted">{{ m.username }}<template v-if="m.email"> · {{ m.email }}</template></p>
            </td>
            <td class="text-xs">
              {{ m.group?.name || "—" }}
              <span v-if="m.is_staff" class="ml-1.5 rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">
                Admin
              </span>
            </td>
            <td class="text-xs text-muted">
              {{ m.approval_mode === "automatic" ? t("account.approvalAuto") : t("account.approvalManual") }}
            </td>
            <td class="tabular-nums text-xs">{{ compactNumber(m.home_quota_gib) }} GiB</td>
            <td>
              <StatusBadge :status="m.is_active ? 'active' : 'stopped'" />
            </td>
            <td class="text-end">
              <button type="button" class="btn btn-secondary btn-sm" @click="openDialog(m)">
                <Pencil class="size-3.5" aria-hidden="true" />
                {{ t("app.edit") }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <PaginationBar
      :page="page"
      :page-size="25"
      :count="count"
      :next-page="nextPage"
      @change="load"
    />

    <ModalDialog
      v-model:open="dialogOpen"
      :title="editingId ? t('admin.editMember') : t('admin.addMember')"
      size="lg"
    >
      <form id="member-form" class="space-y-5" @submit.prevent="saveMember">
        <div class="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
          <div>
            <label for="mem-user" class="mb-1 block text-xs font-medium text-body">{{ t("auth.username") }}</label>
            <input id="mem-user" v-model="formUsername" type="text" required :disabled="editingId !== null" class="field-input" />
          </div>
          <div>
            <label for="mem-name" class="mb-1 block text-xs font-medium text-body">{{ t("admin.fullName") }}</label>
            <input id="mem-name" v-model="formFirstName" type="text" class="field-input" />
          </div>
          <div>
            <label for="mem-email" class="mb-1 block text-xs font-medium text-body">{{ t("admin.email") }}</label>
            <input id="mem-email" v-model="formEmail" type="email" class="field-input" />
          </div>
          <div>
            <label for="mem-group" class="mb-1 block text-xs font-medium text-body">{{ t("admin.group") }}</label>
            <select
              id="mem-group"
              class="field-input"
              :value="formGroupId ?? ''"
              @change="onGroupChange(Number(($event.target as HTMLSelectElement).value))"
            >
              <option v-for="g in groups" :key="g.id" :value="g.id">{{ g.name }}</option>
            </select>
          </div>
        </div>

        <div>
          <div class="mb-1 flex items-center gap-1.5">
            <label for="mem-pass" class="block text-xs font-medium text-body">{{ t("admin.initialPassword") }}</label>
            <HelpTip v-if="editingId !== null" :text="t('admin.passwordEditHint')" :label="t('admin.initialPassword')" />
          </div>
          <input
            id="mem-pass"
            v-model="formPassword"
            type="password"
            autocomplete="new-password"
            :required="editingId === null"
            class="field-input"
          />
        </div>

        <div class="space-y-3 border-y border-border-subtle py-4">
          <SwitchToggle id="mem-active" v-model="formActive" :label="t('admin.accountActive')" />
          <SwitchToggle v-if="canGrantAdmin" id="mem-staff" v-model="formStaff" :label="t('admin.adminRole')" />
        </div>

        <QuotaFields v-model="formQuota" :resources="quotaResources" id-prefix="member" />

        <p v-if="formError" role="alert" class="rounded-lg bg-danger-bg px-3 py-2 text-xs text-danger">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn btn-secondary" :disabled="saving" @click="dialogOpen = false">
          {{ t("app.cancel") }}
        </button>
        <button type="submit" form="member-form" class="btn btn-primary" :disabled="saving">
          {{ saving ? t("app.saving") : t("app.save") }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>
