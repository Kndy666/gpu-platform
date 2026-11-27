<script setup lang="ts">
import { SwitchRoot, SwitchThumb } from "reka-ui";
import HelpTip from "@/components/ui/HelpTip.vue";

defineProps<{
  modelValue: boolean;
  label: string;
  description?: string;
  disabled?: boolean;
  id?: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();
</script>

<template>
  <div class="flex items-center justify-between gap-4">
    <div class="flex min-w-0 items-center gap-1.5">
      <label :for="id" class="cursor-pointer text-sm font-medium text-ink">
        {{ label }}
      </label>
      <HelpTip v-if="description" :text="description" :label="label" />
    </div>
    <SwitchRoot
      :id="id"
      :model-value="modelValue"
      :disabled="disabled"
      class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border border-border-strong bg-subtle transition-colors data-[state=checked]:border-accent data-[state=checked]:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
      @update:model-value="emit('update:modelValue', $event)"
    >
      <SwitchThumb
        class="pointer-events-none block size-4 translate-x-1 rounded-full bg-elevated shadow-xs transition-transform data-[state=checked]:translate-x-5.5"
      />
    </SwitchRoot>
  </div>
</template>
