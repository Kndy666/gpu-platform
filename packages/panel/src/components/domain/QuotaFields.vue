<script setup lang="ts">
import { useI18n } from "vue-i18n";
import SwitchToggle from "@/components/ui/SwitchToggle.vue";
import { cleanDecimal, unitSuffix } from "@/lib/format";

export interface QuotaResourceDef {
  key: string;
  label_text: string;
  unit: string;
  allocation_group?: string | null;
  integral?: boolean;
  step?: number | string | null;
}

export interface QuotaModel {
  unlimited: boolean;
  concurrent_tasks: number | null;
  interactive_hours: number | null;
  extension_limit: number;
  approval_mode: "manual" | "automatic";
  home_size_gib: number;
  resource_limits: Record<string, string | number | null>;
  per_allocation_limits: Record<string, string | number | null>;
}

const props = defineProps<{
  modelValue: QuotaModel;
  resources: QuotaResourceDef[];
  idPrefix?: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: QuotaModel];
}>();

const { t } = useI18n();

function update<K extends keyof QuotaModel>(key: K, value: QuotaModel[K]) {
  emit("update:modelValue", { ...props.modelValue, [key]: value });
}

function updateResource(key: string, value: string) {
  emit("update:modelValue", {
    ...props.modelValue,
    resource_limits: {
      ...props.modelValue.resource_limits,
      [key]: value === "" ? null : Number(value),
    },
  });
}

function updatePerAlloc(key: string, value: string) {
  emit("update:modelValue", {
    ...props.modelValue,
    per_allocation_limits: {
      ...props.modelValue.per_allocation_limits,
      [key]: value === "" ? null : Number(value),
    },
  });
}
</script>

<template>
  <div class="space-y-4">
    <h3 class="text-sm font-semibold text-ink">{{ t("admin.quotasHeading") }}</h3>

    <SwitchToggle
      :id="`${idPrefix || 'quota'}-unlimited`"
      :model-value="modelValue.unlimited"
      :label="t('admin.unlimitedResources')"
      @update:model-value="update('unlimited', $event)"
    />

    <div class="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
      <div>
        <label :for="`${idPrefix || 'quota'}-approval`" class="mb-1 block text-xs font-medium text-body">
          {{ t("admin.approvalPolicy") }}
        </label>
        <select
          :id="`${idPrefix || 'quota'}-approval`"
          class="field-input"
          :value="modelValue.approval_mode"
          @change="update('approval_mode', ($event.target as HTMLSelectElement).value as 'manual' | 'automatic')"
        >
          <option value="manual">{{ t("account.approvalManual") }}</option>
          <option value="automatic">{{ t("account.approvalAuto") }}</option>
        </select>
      </div>

      <div>
        <label :for="`${idPrefix || 'quota'}-home`" class="mb-1 block text-xs font-medium text-body">
          {{ t("admin.homeQuota") }}
        </label>
        <input
          :id="`${idPrefix || 'quota'}-home`"
          type="number"
          min="1"
          step="1"
          required
          class="field-input tabular-nums"
          :value="modelValue.home_size_gib"
          @input="update('home_size_gib', Number(($event.target as HTMLInputElement).value))"
        />
      </div>

      <div>
        <label :for="`${idPrefix || 'quota'}-ext`" class="mb-1 block text-xs font-medium text-body">
          {{ t("admin.extensionLimit") }}
        </label>
        <input
          :id="`${idPrefix || 'quota'}-ext`"
          type="number"
          min="0"
          step="1"
          required
          class="field-input tabular-nums"
          :value="modelValue.extension_limit"
          @input="update('extension_limit', Number(($event.target as HTMLInputElement).value))"
        />
      </div>

      <template v-if="!modelValue.unlimited">
        <div>
          <label :for="`${idPrefix || 'quota'}-concurrent`" class="mb-1 block text-xs font-medium text-body">
            {{ t("admin.concurrentTasks") }}
          </label>
          <input
            :id="`${idPrefix || 'quota'}-concurrent`"
            type="number"
            min="1"
            step="1"
            required
            class="field-input tabular-nums"
            :value="modelValue.concurrent_tasks ?? 1"
            @input="update('concurrent_tasks', Number(($event.target as HTMLInputElement).value))"
          />
        </div>

        <div>
          <label :for="`${idPrefix || 'quota'}-hours`" class="mb-1 block text-xs font-medium text-body">
            {{ t("admin.maxEnvHours") }}
          </label>
          <input
            :id="`${idPrefix || 'quota'}-hours`"
            type="number"
            min="1"
            step="1"
            required
            class="field-input tabular-nums"
            :value="modelValue.interactive_hours ?? 120"
            @input="update('interactive_hours', Number(($event.target as HTMLInputElement).value))"
          />
        </div>
      </template>
    </div>

    <div v-if="!modelValue.unlimited && resources.length" class="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
      <template v-for="res in resources" :key="res.key">
        <div>
          <label :for="`${idPrefix || 'quota'}-res-${res.key}`" class="mb-1 block text-xs font-medium text-body">
            {{ res.label_text }}
            <span class="text-muted">
              ({{ t("admin.totalLimit") }}{{ unitSuffix(res.unit, t("app.cores")) ? `, ${unitSuffix(res.unit, t("app.cores"))}` : "" }})
            </span>
          </label>
          <input
            :id="`${idPrefix || 'quota'}-res-${res.key}`"
            type="number"
            min="0"
            :step="res.integral ? 1 : (res.step || 'any')"
            class="field-input tabular-nums"
            :value="cleanDecimal(modelValue.resource_limits[res.key])"
            @input="updateResource(res.key, ($event.target as HTMLInputElement).value)"
          />
        </div>

        <div v-if="res.allocation_group">
          <label :for="`${idPrefix || 'quota'}-alloc-${res.key}`" class="mb-1 block text-xs font-medium text-body">
            {{ res.label_text }}
            <span class="text-muted">
              ({{ t("admin.perCardLimit") }}{{ unitSuffix(res.unit, t("app.cores")) ? `, ${unitSuffix(res.unit, t("app.cores"))}` : "" }})
            </span>
          </label>
          <input
            :id="`${idPrefix || 'quota'}-alloc-${res.key}`"
            type="number"
            min="0"
            :step="res.integral ? 1 : (res.step || 'any')"
            class="field-input tabular-nums"
            :value="cleanDecimal(modelValue.per_allocation_limits[res.key])"
            @input="updatePerAlloc(res.key, ($event.target as HTMLInputElement).value)"
          />
        </div>
      </template>
    </div>
  </div>
</template>
