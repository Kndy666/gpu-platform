<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { Plus, Pencil, RefreshCw } from "lucide-vue-next";
import { api, type ServerNode } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import ModalDialog from "@/components/ui/ModalDialog.vue";
import SwitchToggle from "@/components/ui/SwitchToggle.vue";
import HelpTip from "@/components/ui/HelpTip.vue";

interface SchemaProperty {
  title?: string;
  type?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
}

interface TransportModuleDef {
  name: string;
  schema: { properties?: Record<string, SchemaProperty>; required?: string[] };
}

const { t, te } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

function transportModuleLabel(name: string): string {
  const key = `transport.modules.${name}`;
  return te(key) ? t(key) : name;
}

function transportFieldLabel(fieldKey: string, prop: SchemaProperty): string {
  const key = `transport.fields.${fieldKey}.label`;
  return te(key) ? t(key) : prop.title || fieldKey;
}

function transportFieldDescription(fieldKey: string, prop: SchemaProperty): string {
  const key = `transport.fields.${fieldKey}.description`;
  return te(key) ? t(key) : prop.description || "";
}

const nodes = ref<ServerNode[]>([]);
const loading = ref(true);

const dialogOpen = ref(false);
const editingNodeId = ref<number | null>(null);
const isLocal = ref(false);
const modules = ref<TransportModuleDef[]>([]);
const formName = ref("");
const formArch = ref<"amd64" | "arm64">("amd64");
const formAddress = ref("");
const formTransport = ref("");
const formTransportOptions = ref<Record<string, string | number | boolean>>({});
const formEnabled = ref(true);
const formMaintenance = ref(false);
const formError = ref("");
const saving = ref(false);

const activeModule = computed(() => modules.value.find((m) => m.name === formTransport.value) ?? null);
const activeSchemaProps = computed(() => activeModule.value?.schema?.properties ?? {});
const activeRequired = computed(() => new Set(activeModule.value?.schema?.required ?? []));

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    const res = await api.listNodes(1, 100);
    nodes.value = res.items;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function openDialog(node: ServerNode | null, transportOverride?: string) {
  formError.value = "";
  editingNodeId.value = node?.id ?? null;
  try {
    const opts = await api.getNodeOptions(node?.id ?? null, transportOverride);
    modules.value = opts.modules as unknown as TransportModuleDef[];
    isLocal.value = opts.local;
    const init = opts.initial;
    if (!transportOverride) {
      formName.value = String(init.name ?? node?.name ?? "");
      formArch.value = (init.architecture === "arm64" ? "arm64" : "amd64");
      formAddress.value = String(init.address ?? "");
      formEnabled.value = Boolean(init.enabled ?? true);
      formMaintenance.value = Boolean(init.maintenance ?? false);
    }
    formTransport.value = String(transportOverride ?? init.transport ?? modules.value[0]?.name ?? "");
    const nextOpts: Record<string, string | number | boolean> = {};
    for (const [key, value] of Object.entries(init)) {
      if (key.startsWith("transport:")) {
        nextOpts[key.slice("transport:".length)] = value as string | number | boolean;
      }
    }
    formTransportOptions.value = nextOpts;
    dialogOpen.value = true;
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  }
}

async function switchTransport(nextTransport: string) {
  const existing = editingNodeId.value ? nodes.value.find((n) => n.id === editingNodeId.value) ?? null : null;
  await openDialog(existing, nextTransport);
}

async function saveNode() {
  formError.value = "";
  saving.value = true;
  try {
    const payload: Record<string, unknown> = {
      name: formName.value.trim(),
      enabled: formEnabled.value,
      maintenance: formMaintenance.value,
    };
    if (!isLocal.value) {
      payload.architecture = formArch.value;
      payload.address = formAddress.value.trim();
      payload.transport = formTransport.value;
      const typedOptions: Record<string, unknown> = {};
      for (const [k, prop] of Object.entries(activeSchemaProps.value)) {
        const raw = formTransportOptions.value[k];
        if (raw === "" || raw === undefined) continue;
        if (prop.type === "integer" || prop.type === "number") {
          typedOptions[k] = Number(raw);
        } else if (prop.type === "boolean") {
          typedOptions[k] = Boolean(raw);
        } else {
          typedOptions[k] = raw;
        }
      }
      payload.transport_options = typedOptions;
    }

    if (editingNodeId.value) {
      await api.updateNode(editingNodeId.value, payload);
    } else {
      await api.createNode(payload);
    }
    dialogOpen.value = false;
    notices.pushToast({ level: "success", title: t("admin.serverSaved"), seconds: 5 });
    await load();
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  void load(false);
});

useAutoRefresh(() => load(true), 15000);
</script>

<template>
  <div>
    <PageHeader :title="t('admin.serversTitle')" :subtitle="t('admin.serversSubtitle')">
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="load(false)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
        <button type="button" class="btn btn-primary" @click="openDialog(null)">
          <Plus class="size-4" aria-hidden="true" />
          {{ t("admin.addServer") }}
        </button>
      </template>
    </PageHeader>

    <div class="table-shell">
      <table class="data-table">
        <thead>
          <tr>
            <th>{{ t("admin.serverName") }}</th>
            <th>{{ t("admin.architecture") }}</th>
            <th>{{ t("tasks.status") }}</th>
            <th>{{ t("admin.maintenance") }}</th>
            <th>{{ t("admin.lastSeen") }}</th>
            <th class="text-end">{{ t("app.actions") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="node in nodes" :key="node.id">
            <td>
              <p class="font-medium text-ink">{{ node.name }}</p>
              <p v-if="node.error" class="mt-0.5 max-w-md text-xs text-danger">{{ node.error }}</p>
            </td>
            <td class="font-mono text-xs">{{ node.architecture }}</td>
            <td><StatusBadge :status="node.state" :label="node.state_label" /></td>
            <td class="text-xs text-muted">{{ node.maintenance ? t("app.yes") : t("app.no") }}</td>
            <td class="whitespace-nowrap tabular-nums text-xs text-muted">
              {{ formatDateTime(node.last_seen, locale.current, auth.timeZone) }}
            </td>
            <td class="text-end">
              <button type="button" class="btn btn-secondary btn-sm" @click="openDialog(node)">
                <Pencil class="size-3.5" aria-hidden="true" />
                {{ t("app.edit") }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <ModalDialog
      v-model:open="dialogOpen"
      :title="editingNodeId ? t('admin.editServer') : t('admin.addServer')"
      size="lg"
    >
      <form id="server-form" class="space-y-4" @submit.prevent="saveNode">
        <p v-if="isLocal" class="rounded-lg bg-subtle px-3.5 py-2.5 text-xs text-body">
          {{ t("admin.localFixedHint") }}
        </p>

        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label for="srv-name" class="mb-1 block text-xs font-medium text-body">{{ t("admin.serverName") }}</label>
            <input id="srv-name" v-model="formName" type="text" required :disabled="editingNodeId !== null" class="field-input" />
          </div>
          <div v-if="!isLocal">
            <label for="srv-arch" class="mb-1 block text-xs font-medium text-body">{{ t("admin.architecture") }}</label>
            <select id="srv-arch" v-model="formArch" class="field-input">
              <option value="amd64">amd64</option>
              <option value="arm64">arm64</option>
            </select>
          </div>
        </div>

        <template v-if="!isLocal">
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label for="srv-addr" class="mb-1 block text-xs font-medium text-body">{{ t("admin.address") }}</label>
              <input id="srv-addr" v-model="formAddress" type="text" required class="field-input font-mono text-sm" />
            </div>
            <div>
              <label for="srv-transport" class="mb-1 block text-xs font-medium text-body">{{ t("admin.transport") }}</label>
              <select
                id="srv-transport"
                class="field-input"
                :value="formTransport"
                @change="switchTransport(($event.target as HTMLSelectElement).value)"
              >
                <option v-for="mod in modules" :key="mod.name" :value="mod.name">
                  {{ transportModuleLabel(mod.name) }}
                </option>
              </select>
            </div>
          </div>

          <div v-if="Object.keys(activeSchemaProps).length" class="grid grid-cols-1 gap-3.5 rounded-xl border border-border-subtle bg-canvas/55 p-4 sm:grid-cols-2">
            <div v-for="(prop, key) in activeSchemaProps" :key="key">
              <div class="mb-1 flex items-center gap-1.5">
                <label :for="`tr-${key}`" class="block text-xs font-medium text-body">
                  {{ transportFieldLabel(String(key), prop) }}
                </label>
                <HelpTip
                  v-if="transportFieldDescription(String(key), prop)"
                  :text="transportFieldDescription(String(key), prop)"
                  :label="transportFieldLabel(String(key), prop)"
                />
              </div>
              <input
                :id="`tr-${key}`"
                v-model="formTransportOptions[key]"
                :type="prop.type === 'integer' || prop.type === 'number' ? 'number' : 'text'"
                :required="activeRequired.has(String(key))"
                class="field-input font-mono text-sm"
              />
            </div>
          </div>
        </template>

        <div class="space-y-3 border-t border-border-subtle pt-4">
          <SwitchToggle id="srv-enabled" v-model="formEnabled" :label="t('admin.enabled')" />
          <SwitchToggle
            id="srv-maint"
            v-model="formMaintenance"
            :label="t('admin.maintenance')"
            :description="t('admin.maintenanceHelp')"
          />
        </div>

        <p v-if="formError" role="alert" class="rounded-lg bg-danger-bg px-3 py-2 text-xs text-danger">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn btn-secondary" :disabled="saving" @click="dialogOpen = false">
          {{ t("app.cancel") }}
        </button>
        <button type="submit" form="server-form" class="btn btn-primary" :disabled="saving">
          {{ saving ? t("app.saving") : t("app.save") }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>
