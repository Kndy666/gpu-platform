<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { AlertTriangle, ChevronDown, ChevronUp, Sparkles } from "lucide-vue-next";
import { api, type RequestCapacityData } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import {
  initialAllocationValue,
  resolveAllocationResource,
  type AllocationResourceDef,
  type AllocationValue,
} from "@/lib/allocation";
import {
  compactNumber,
  formatGpuMemory,
  formatQuantity,
  toLocalDateTimeInput,
  localInputToOffsetIso,
  unitSuffix,
} from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import PageHeader from "@/components/layout/PageHeader.vue";
import MetricBar from "@/components/ui/MetricBar.vue";
import HelpTip from "@/components/ui/HelpTip.vue";
import DateTimePickerInput from "@/components/ui/DateTimePickerInput.vue";

interface RequestOptionNode {
  id: number;
  name: string;
  state: string;
}
interface RequestOptionImage {
  id: string;
  name: string;
  shared: boolean;
}
interface RequestOptionEnv {
  id: string;
  purpose: string;
  volume_sizes?: Record<string, number>;
}
interface ResourceOption extends AllocationResourceDef {
  accounting: string;
  requestable: boolean;
  volume_kind?: string | null;
  quota_available?: number | null;
}

interface RequestOptionsPayload {
  node: RequestOptionNode;
  nodes: RequestOptionNode[];
  images: RequestOptionImage[];
  environments: RequestOptionEnv[];
  source_workspaces: Record<string, { size_gib: number }>;
  resources: ResourceOption[];
  defaults: {
    node: number;
    image: string | null;
    starts_at?: string;
    ends_at?: string;
    working_dir: string;
    volume_sizes: Record<string, number>;
    resource_quantities: Record<string, string | number | null>;
    resource_allocations: Record<string, Record<string, string | number>[]>;
  };
  initial?: {
    node?: number;
    image?: string;
    source_environment?: string;
    source_kind?: "image" | "environment";
    purpose?: string;
  };
  capacity: RequestCapacityData;
}

const props = defineProps<{
  mode: "interactive" | "batch";
}>();

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const isEnv = computed(() => props.mode === "interactive");

const options = ref<RequestOptionsPayload | null>(null);
const loading = ref(true);
const submitting = ref(false);
const errorMessage = ref("");
const showAdvanced = ref(false);

// Form fields preserved across server switches
const selectedNodeId = ref<number | null>(null);
const purpose = ref("");
const sourceKind = ref<"image" | "environment">("image");
const selectedImageId = ref("");
const selectedSourceEnvId = ref("");
const startsAt = ref("");
const endsAt = ref("");
const command = ref("");
const workingDir = ref("/workspace");
const envText = ref("");
const notes = ref("");

// Resource quantities & allocations
const quantities = ref<Record<string, number | string>>({});
const workVolumeGib = ref(20);
const homeVolumeGib = ref(50);
const allocationRows = ref<Record<string, Record<string, AllocationValue>[]>>({});

const taskResources = computed(() =>
  (options.value?.resources ?? []).filter(
    (res) => res.accounting === "task" && res.requestable && !res.allocation_group,
  ),
);

const allocationGroups = computed(() => {
  const groups = new Map<string, ResourceOption[]>();
  for (const res of options.value?.resources ?? []) {
    if (res.allocation_group && res.requestable) {
      const list = groups.get(res.allocation_group) ?? [];
      list.push(res);
      groups.set(res.allocation_group, list);
    }
  }
  return Array.from(groups.entries()).map(([group, defs]) => ({ group, defs }));
});

const maxDeviceCount = computed(() => {
  const gpus = options.value?.capacity?.gpus ?? [];
  return Math.max(gpus.length, 0);
});

const durationHours = computed(() => {
  if (!startsAt.value || !endsAt.value) return null;
  const start = new Date(startsAt.value).getTime();
  const end = new Date(endsAt.value).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return Math.round(((end - start) / 3600000) * 10) / 10;
});

const approvalAutomatic = computed(() => auth.user?.effective_limits?.approval_mode === "automatic");

const resolvedAllocations = computed(() => {
  const out: Record<string, { resolved: Record<string, number>[]; invalid: boolean }> = {};
  const gpus = options.value?.capacity?.gpus ?? [];
  for (const { group, defs } of allocationGroups.value) {
    const rows = allocationRows.value[group] ?? [];
    if (!rows.length) {
      out[group] = { resolved: [], invalid: false };
      continue;
    }
    const perRow: Record<string, number>[] = rows.map(() => ({}));
    let invalid = false;
    for (const def of defs) {
      const resolved = resolveAllocationResource(rows, def, gpus);
      if (!resolved) {
        invalid = true;
      } else {
        resolved.forEach((val, idx) => {
          perRow[idx][def.key] = val;
        });
      }
    }
    out[group] = { resolved: perRow, invalid };
  }
  return out;
});

const hasAllocationError = computed(() =>
  Object.values(resolvedAllocations.value).some((entry) => entry.invalid),
);

function capacityFor(key: string) {
  return options.value?.capacity?.resources?.find((item) => item.key === key);
}

function setDeviceCount(group: string, count: number) {
  const defs = allocationGroups.value.find((g) => g.group === group)?.defs ?? [];
  const current = [...(allocationRows.value[group] ?? [])];
  while (current.length < count) {
    const row: Record<string, AllocationValue> = {};
    for (const def of defs) {
      row[def.key] = initialAllocationValue(def);
    }
    current.push(row);
  }
  if (current.length > count) {
    current.splice(count);
  }
  allocationRows.value = { ...allocationRows.value, [group]: current };
}

function toggleMax(group: string, index: number, key: string, def: AllocationResourceDef) {
  const rows = [...(allocationRows.value[group] ?? [])];
  const row = { ...rows[index] };
  row[key] = row[key] === "max" ? initialAllocationValue(def) : "max";
  rows[index] = row;
  allocationRows.value = { ...allocationRows.value, [group]: rows };
}

function updateAllocValue(group: string, index: number, key: string, value: string) {
  const rows = [...(allocationRows.value[group] ?? [])];
  const row = { ...rows[index] };
  row[key] = value === "" ? "" : Number(value);
  rows[index] = row;
  allocationRows.value = { ...allocationRows.value, [group]: rows };
}

async function loadOptions(nodeId?: number | string | null, preserveUserInputs = false) {
  loading.value = true;
  errorMessage.value = "";
  try {
    const query: { node?: number | string | null; mode: "interactive" | "batch"; image?: string; source?: string } = {
      node: nodeId ?? undefined,
      mode: props.mode,
    };
    if (!preserveUserInputs) {
      if (typeof route.query.image === "string") query.image = route.query.image;
      if (typeof route.query.source === "string") query.source = route.query.source;
    }
    const payload = (await api.getRequestOptions(query)) as unknown as RequestOptionsPayload;
    options.value = payload;
    selectedNodeId.value = payload.node?.id ?? payload.defaults.node;

    if (!preserveUserInputs) {
      if (payload.initial?.purpose) purpose.value = payload.initial.purpose;
      if (payload.initial?.source_kind) sourceKind.value = payload.initial.source_kind;
      workingDir.value = payload.defaults.working_dir || "/workspace";
      if (payload.defaults.starts_at) {
        startsAt.value = toLocalDateTimeInput(payload.defaults.starts_at, auth.timeZone);
      }
      if (payload.defaults.ends_at) {
        endsAt.value = toLocalDateTimeInput(payload.defaults.ends_at, auth.timeZone);
      }
    }

    // Update server-scoped images & running environments
    const initImg = payload.initial?.image || payload.defaults.image || payload.images[0]?.id || "";
    selectedImageId.value = payload.images.some((img) => img.id === selectedImageId.value)
      ? selectedImageId.value
      : initImg;

    const initEnv = payload.initial?.source_environment || payload.environments[0]?.id || "";
    selectedSourceEnvId.value = payload.environments.some((env) => env.id === selectedSourceEnvId.value)
      ? selectedSourceEnvId.value
      : initEnv;

    if (!preserveUserInputs || !Object.keys(quantities.value).length) {
      const nextQ: Record<string, number | string> = {};
      for (const [k, v] of Object.entries(payload.defaults.resource_quantities ?? {})) {
        if (v !== null && v !== undefined) nextQ[k] = Number(v);
      }
      quantities.value = nextQ;
      workVolumeGib.value = payload.defaults.volume_sizes?.work ?? 20;
      homeVolumeGib.value = payload.defaults.volume_sizes?.home ?? auth.user?.home_quota_gib ?? 50;
    }

    const nextAllocs: Record<string, Record<string, AllocationValue>[]> = {};
    for (const [group, rows] of Object.entries(payload.defaults.resource_allocations ?? {})) {
      nextAllocs[group] = rows.map((row) => {
        const converted: Record<string, AllocationValue> = {};
        for (const [k, v] of Object.entries(row)) {
          converted[k] = Number(v);
        }
        return converted;
      });
    }
    allocationRows.value = nextAllocs;
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t("app.loadError");
  } finally {
    loading.value = false;
  }
}

watch(selectedSourceEnvId, (envId) => {
  if (!isEnv.value && sourceKind.value === "environment" && envId && options.value) {
    const size = options.value.source_workspaces?.[envId]?.size_gib;
    if (size) workVolumeGib.value = size;
  }
});

watch(
  () => props.mode,
  () => {
    void loadOptions(selectedNodeId.value, false);
  },
);

onMounted(() => {
  void loadOptions(typeof route.query.node === "string" ? route.query.node : null, false);
});

function parseEnvLines(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq > 0) {
      out[line.slice(0, eq).trim()] = line.slice(eq + 1);
    }
  }
  return out;
}

async function handleSubmit() {
  if (!options.value || hasAllocationError.value) return;
  errorMessage.value = "";
  submitting.value = true;
  try {
    const allocationsPayload: Record<string, Record<string, number>[]> = {};
    for (const [group, entry] of Object.entries(resolvedAllocations.value)) {
      if (entry.resolved.length) {
        allocationsPayload[group] = entry.resolved;
      }
    }

    const resourcePayload: Record<string, number> = {};
    for (const def of taskResources.value) {
      const val = quantities.value[def.key];
      if (val !== "" && val !== undefined && val !== null) {
        resourcePayload[def.key] = Number(val);
      }
    }

    const payload: Record<string, unknown> = {
      node: selectedNodeId.value,
      mode: props.mode,
      purpose: purpose.value.trim(),
      working_dir: workingDir.value.trim() || "/workspace",
      notes: notes.value.trim(),
      environment: parseEnvLines(envText.value),
      extensions: {
        resources: resourcePayload,
        allocations: allocationsPayload,
        volume_sizes: {
          home: Number(homeVolumeGib.value),
          work: Number(workVolumeGib.value),
        },
      },
    };

    if (isEnv.value) {
      payload.image = selectedImageId.value;
      payload.starts_at = localInputToOffsetIso(startsAt.value);
      payload.ends_at = localInputToOffsetIso(endsAt.value);
    } else {
      payload.command = command.value;
      if (sourceKind.value === "environment") {
        payload.source_environment = selectedSourceEnvId.value;
      } else {
        payload.image = selectedImageId.value;
      }
    }

    const created = await api.createTask(payload);
    await auth.refresh();
    await router.push(`/tasks/${created.id}`);
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div>
    <PageHeader :title="isEnv ? t('request.newEnvTitle') : t('request.newJobTitle')" />

    <div v-if="loading && !options" class="card p-10 text-center text-sm text-muted">
      {{ t("app.loading") }}
    </div>

    <form v-else-if="options" class="grid grid-cols-1 gap-6 lg:grid-cols-12" @submit.prevent="handleSubmit">
      <!-- Left Main Form -->
      <div class="space-y-6 lg:col-span-8">
        <!-- Step 1: Server & Environment -->
        <section class="card space-y-4 p-5">
          <h2>{{ t("request.stepSource") }}</h2>

          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label for="req-server" class="mb-1.5 block text-sm font-medium text-ink">
                {{ t("request.server") }}
              </label>
              <select
                id="req-server"
                class="field-input"
                :value="selectedNodeId ?? ''"
                @change="loadOptions(Number(($event.target as HTMLSelectElement).value), true)"
              >
                <option v-for="node in options.nodes" :key="node.id" :value="node.id">
                  {{ node.name }}
                </option>
              </select>
            </div>

            <div>
              <label for="req-purpose" class="mb-1.5 block text-sm font-medium text-ink">
                {{ t("request.purpose") }}
              </label>
              <input
                id="req-purpose"
                v-model="purpose"
                type="text"
                required
                maxlength="120"
                class="field-input"
                :placeholder="t('request.purposePlaceholder')"
              />
            </div>
          </div>

          <!-- Batch job source switcher -->
          <div v-if="!isEnv" class="space-y-3">
            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class="btn btn-sm"
                :class="sourceKind === 'image' ? 'btn-primary' : 'btn-secondary'"
                @click="sourceKind = 'image'"
              >
                {{ t("request.sourceImage") }}
              </button>
              <button
                type="button"
                class="btn btn-sm"
                :class="sourceKind === 'environment' ? 'btn-primary' : 'btn-secondary'"
                :disabled="!options.environments.length"
                @click="sourceKind = 'environment'"
              >
                {{ t("request.sourceEnv") }}
              </button>
            </div>
            <p v-if="sourceKind === 'environment'" class="rounded-lg bg-accent-soft px-3.5 py-2.5 text-xs text-body">
              {{ t("request.sourceEnvHint") }}
            </p>
          </div>

          <div v-if="isEnv || sourceKind === 'image'">
            <label for="req-image" class="mb-1.5 block text-sm font-medium text-ink">
              {{ t("request.image") }}
            </label>
            <select
              v-if="options.images.length"
              id="req-image"
              v-model="selectedImageId"
              required
              class="field-input"
            >
              <option v-for="img in options.images" :key="img.id" :value="img.id">
                {{ img.name }} ({{ img.shared ? t("images.shared") : t("images.private") }})
              </option>
            </select>
            <p v-else class="rounded-lg bg-warn-bg px-3.5 py-2.5 text-sm text-warn">
              {{ t("request.noImages") }}
            </p>
          </div>

          <div v-else>
            <label for="req-source-env" class="mb-1.5 block text-sm font-medium text-ink">
              {{ t("request.sourceEnvironment") }}
            </label>
            <select
              v-if="options.environments.length"
              id="req-source-env"
              v-model="selectedSourceEnvId"
              required
              class="field-input"
            >
              <option v-for="env in options.environments" :key="env.id" :value="env.id">
                {{ env.purpose }}
              </option>
            </select>
            <p v-else class="rounded-lg bg-warn-bg px-3.5 py-2.5 text-sm text-warn">
              {{ t("request.noRunningEnvs") }}
            </p>
          </div>
        </section>

        <!-- Step 2: Compute & Storage -->
        <section class="card space-y-5 p-5">
          <h2>{{ t("request.stepResources") }}</h2>

          <div v-if="!options.capacity.fresh" class="flex items-start gap-2.5 rounded-lg border border-warn/30 bg-warn-bg px-3.5 py-2.5 text-xs text-warn">
            <AlertTriangle class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>{{ t("request.capacityStale") }}</span>
          </div>

          <!-- Accelerator allocation groups (GPU/NPU) -->
          <div v-for="{ group, defs } in allocationGroups" :key="group" class="space-y-3 rounded-xl border border-border-subtle bg-canvas/55 p-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <label :for="`count-${group}`" class="text-sm font-medium text-ink">
                {{ t("request.deviceCount", { label: options.capacity.device_label || group.toUpperCase() }) }}
              </label>
              <div class="flex items-center gap-1.5">
                <button
                  v-for="n in Array.from({ length: maxDeviceCount + 1 }, (_, i) => i)"
                  :key="n"
                  type="button"
                  class="btn btn-sm min-w-9 px-2.5 tabular-nums"
                  :class="(allocationRows[group]?.length ?? 0) === n ? 'btn-primary' : 'btn-secondary'"
                  @click="setDeviceCount(group, n)"
                >
                  {{ n }}
                </button>
              </div>
            </div>

            <div v-if="allocationRows[group]?.length" class="space-y-2.5">
              <div
                v-for="(row, idx) in allocationRows[group]"
                :key="idx"
                class="rounded-lg border border-border-subtle bg-card p-3"
              >
                <p class="mb-2 text-xs font-semibold text-ink">
                  {{ t("request.deviceRow", { label: options.capacity.device_label || group.toUpperCase(), index: idx + 1 }) }}
                </p>
                <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div v-for="def in defs" :key="def.key">
                    <div class="mb-1 flex items-center justify-between gap-2">
                      <label :for="`alloc-${group}-${idx}-${def.key}`" class="text-xs font-medium text-body">
                        {{ def.label_text }}
                        <span v-if="unitSuffix(def.unit, t('app.cores'))" class="text-muted">
                          ({{ unitSuffix(def.unit, t("app.cores")) }})
                        </span>
                      </label>
                      <button
                        type="button"
                        class="inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors"
                        :class="
                          row[def.key] === 'max'
                            ? 'border-accent bg-accent-soft text-accent'
                            : 'border-border-strong bg-elevated text-muted hover:text-ink'
                        "
                        @click="toggleMax(group, idx, def.key, def)"
                      >
                        <Sparkles class="size-3" aria-hidden="true" />
                        {{ t("request.maxAvailable") }}
                      </button>
                    </div>
                    <input
                      :id="`alloc-${group}-${idx}-${def.key}`"
                      type="number"
                      :disabled="row[def.key] === 'max'"
                      :min="def.minimum ?? 1"
                      :max="def.per_allocation_limit ?? def.maximum ?? undefined"
                      :step="def.integral ? 1 : (def.step || 1)"
                      class="field-input tabular-nums"
                      :value="
                        row[def.key] === 'max'
                          ? (resolvedAllocations[group]?.resolved?.[idx]?.[def.key] ?? '')
                          : row[def.key]
                      "
                      @input="updateAllocValue(group, idx, def.key, ($event.target as HTMLInputElement).value)"
                    />
                  </div>
                </div>
              </div>
            </div>

            <p v-if="resolvedAllocations[group]?.invalid" class="text-xs font-medium text-danger">
              {{ t("request.maxError") }}
            </p>
          </div>

          <!-- Scalar compute resources (CPU, Memory, System storage) -->
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div v-for="def in taskResources" :key="def.key">
              <label :for="`res-${def.key}`" class="mb-1 block text-sm font-medium text-ink">
                {{ def.label_text }}
                <span v-if="unitSuffix(def.unit, t('app.cores'))" class="text-xs font-normal text-muted">
                  ({{ unitSuffix(def.unit, t("app.cores")) }})
                </span>
              </label>
              <input
                :id="`res-${def.key}`"
                v-model.number="quantities[def.key]"
                type="number"
                required
                :min="def.minimum ?? 1"
                :max="def.limit ?? def.maximum ?? undefined"
                :step="def.integral ? 1 : (def.step || 'any')"
                class="field-input tabular-nums"
              />
              <p v-if="capacityFor(def.key)" class="mt-1 text-xs text-muted tabular-nums">
                {{ formatQuantity(capacityFor(def.key)?.available, def.unit, t("app.cores")) }}
                {{ t("overview.available") }}
                <template v-if="capacityFor(def.key)?.quota_available != null">
                  · {{ t("request.quotaRemaining", { value: formatQuantity(capacityFor(def.key)?.quota_available, def.unit, t("app.cores")) }) }}
                </template>
              </p>
            </div>

            <!-- Work volume -->
            <div>
              <div class="mb-1 flex items-center gap-1.5">
                <label for="req-work-vol" class="block text-sm font-medium text-ink">
                  {{ t("request.workVolume") }}
                </label>
                <HelpTip :text="t('request.workVolumeHelp')" :label="t('request.workVolume')" />
              </div>
              <input
                id="req-work-vol"
                v-model.number="workVolumeGib"
                type="number"
                min="1"
                step="1"
                required
                :disabled="!isEnv && sourceKind === 'environment'"
                class="field-input tabular-nums"
              />
            </div>

            <!-- Home volume -->
            <div>
              <div class="mb-1 flex items-center gap-1.5">
                <label for="req-home-vol" class="block text-sm font-medium text-ink">
                  {{ t("request.homeVolume") }}
                </label>
                <HelpTip
                  :text="t('request.homeVolumeHelp', { user: auth.user?.username || 'user' })"
                  :label="t('request.homeVolume')"
                />
              </div>
              <input
                id="req-home-vol"
                v-model.number="homeVolumeGib"
                type="number"
                :min="auth.user?.home_quota_gib ? Math.min(homeVolumeGib, auth.user.home_quota_gib) : 1"
                :max="auth.user?.home_quota_gib || undefined"
                step="1"
                required
                class="field-input tabular-nums"
              />
            </div>
          </div>
        </section>

        <!-- Step 3: Schedule (Interactive) OR Command (Batch) -->
        <section v-if="isEnv" class="card space-y-4 p-5">
          <h2>{{ t("request.stepSchedule") }}</h2>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label for="req-start" class="mb-1.5 block text-sm font-medium text-ink">
                {{ t("request.startsAt") }}
              </label>
              <DateTimePickerInput id="req-start" v-model="startsAt" :aria-label="t('request.startsAt')" />
            </div>
            <div>
              <label for="req-end" class="mb-1.5 block text-sm font-medium text-ink">
                {{ t("request.endsAt") }}
              </label>
              <DateTimePickerInput id="req-end" v-model="endsAt" :aria-label="t('request.endsAt')" />
            </div>
          </div>
          <p class="text-xs text-muted">
            {{ durationHours ? t("request.durationSummary", { hours: durationHours }) : t("request.selectTimeRange") }}
          </p>
        </section>

        <section v-else class="card space-y-4 p-5">
          <h2>{{ t("request.stepCommand") }}</h2>
          <div>
            <label for="req-command" class="mb-1.5 block text-sm font-medium text-ink">
              {{ t("request.command") }}
            </label>
            <textarea
              id="req-command"
              v-model="command"
              rows="5"
              required
              class="field-input font-mono text-sm"
              :placeholder="t('request.commandPlaceholder')"
            />
          </div>
        </section>

        <!-- Advanced Options -->
        <section class="card p-5">
          <button
            type="button"
            class="flex w-full items-center justify-between text-left text-sm font-semibold text-ink"
            :aria-expanded="showAdvanced"
            @click="showAdvanced = !showAdvanced"
          >
            <span>{{ t("request.stepAdvanced") }}</span>
            <ChevronUp v-if="showAdvanced" class="size-4 text-muted" aria-hidden="true" />
            <ChevronDown v-else class="size-4 text-muted" aria-hidden="true" />
          </button>

          <div v-if="showAdvanced" class="mt-4 space-y-4 border-t border-border-subtle pt-4">
            <div>
              <label for="req-workdir" class="mb-1 block text-sm font-medium text-ink">
                {{ t("request.workingDir") }}
              </label>
              <input id="req-workdir" v-model="workingDir" type="text" class="field-input font-mono text-sm" />
            </div>
            <div>
              <div class="mb-1 flex items-center gap-1.5">
                <label for="req-env" class="block text-sm font-medium text-ink">
                  {{ t("request.envVars") }}
                </label>
                <HelpTip :text="t('request.envVarsHint')" :label="t('request.envVars')" />
              </div>
              <textarea
                id="req-env"
                v-model="envText"
                rows="3"
                class="field-input font-mono text-sm"
                placeholder="CUDA_VISIBLE_DEVICES=0"
              />
            </div>
            <div>
              <label for="req-notes" class="mb-1 block text-sm font-medium text-ink">
                {{ t("request.notes") }}
              </label>
              <textarea id="req-notes" v-model="notes" rows="2" class="field-input text-sm" />
            </div>
          </div>
        </section>
      </div>

      <!-- Right Sticky Summary & Live Capacity -->
      <aside class="space-y-4 lg:col-span-4">
        <div class="card sticky top-20 space-y-4 p-5">
          <h2>{{ t("request.summaryTitle") }}</h2>

          <dl class="space-y-2.5 text-sm">
            <div class="flex justify-between gap-2">
              <dt class="text-muted">{{ t("request.server") }}</dt>
              <dd class="font-medium text-ink">{{ options.node.name }}</dd>
            </div>
            <div v-for="{ group } in allocationGroups" :key="group" class="flex justify-between gap-2">
              <dt class="text-muted">{{ options.capacity.device_label || group.toUpperCase() }}</dt>
              <dd class="font-medium text-ink tabular-nums">{{ allocationRows[group]?.length ?? 0 }}</dd>
            </div>
            <div v-for="def in taskResources" :key="def.key" class="flex justify-between gap-2">
              <dt class="text-muted">{{ def.label_text }}</dt>
              <dd class="font-medium text-ink tabular-nums">
                {{ formatQuantity(quantities[def.key], def.unit, t("app.cores")) }}
              </dd>
            </div>
            <div class="flex justify-between gap-2">
              <dt class="text-muted">{{ t("request.workVolume") }}</dt>
              <dd class="font-medium text-ink tabular-nums">{{ compactNumber(workVolumeGib) }} GiB</dd>
            </div>
            <div v-if="isEnv && durationHours" class="flex justify-between gap-2">
              <dt class="text-muted">{{ t("tasks.schedule") }}</dt>
              <dd class="font-medium text-ink tabular-nums">{{ t("request.durationSummary", { hours: durationHours }) }}</dd>
            </div>
          </dl>

          <p class="rounded-lg bg-subtle/70 px-3 py-2 text-xs text-body">
            {{ approvalAutomatic ? t("request.summaryApprovalAuto") : t("request.summaryApprovalManual") }}
          </p>

          <p v-if="errorMessage" role="alert" class="rounded-lg border border-danger/30 bg-danger-bg px-3 py-2 text-xs text-danger">
            {{ errorMessage }}
          </p>

          <button
            type="submit"
            class="btn btn-primary w-full"
            :disabled="submitting || hasAllocationError"
          >
            {{ submitting ? t("request.submitting") : (isEnv ? t("request.submitEnv") : t("request.submitJob")) }}
          </button>

          <!-- Compact live GPU capacity on selected server -->
          <div v-if="options.capacity.gpus.length" class="border-t border-border-subtle pt-4">
            <p class="mb-2.5 text-xs font-semibold text-muted">
              {{ t("request.capacityTitle", { server: options.node.name }) }}
            </p>
            <div class="space-y-2">
              <div
                v-for="gpu in options.capacity.gpus"
                :key="gpu.index"
                class="rounded-lg border border-border-subtle bg-canvas/60 px-2.5 py-2 text-xs"
              >
                <div class="mb-1 flex justify-between gap-2 tabular-nums">
                  <span class="shrink-0 font-medium text-ink">{{ options.capacity.device_label }} #{{ gpu.index }}</span>
                  <span class="text-end text-muted">
                    {{ compactNumber(gpu.compute_percent, 0) }}% · {{ formatGpuMemory(gpu.memory_gib) }} / {{ formatGpuMemory(gpu.memory_total_gib) }}
                  </span>
                </div>
                <MetricBar
                  compact
                  :percent="gpu.compute_percent"
                  :tone="gpu.compute_percent <= 15 ? 'danger' : gpu.compute_percent <= 35 ? 'warn' : 'accent'"
                />
              </div>
            </div>
          </div>
        </div>
      </aside>
    </form>
  </div>
</template>
