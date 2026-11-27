<script setup lang="ts">
import { TabsRoot, TabsList, TabsTrigger, TabsContent } from "reka-ui";

export interface TabOption {
  value: string;
  label: string;
  badge?: number;
}

defineProps<{
  modelValue: string;
  tabs: TabOption[];
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();
</script>

<template>
  <TabsRoot
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', String($event))"
  >
    <TabsList
      class="mb-6 flex flex-wrap gap-1 border-b border-border-subtle"
    >
      <TabsTrigger
        v-for="tab in tabs"
        :key="tab.value"
        :value="tab.value"
        class="relative -mb-px inline-flex items-center gap-2 border-b-2 border-transparent px-3.5 py-2.5 text-sm font-medium text-muted transition-colors hover:text-ink data-[state=active]:border-accent data-[state=active]:text-ink"
      >
        <span>{{ tab.label }}</span>
        <span
          v-if="typeof tab.badge === 'number' && tab.badge > 0"
          class="rounded-full bg-accent-soft px-1.5 py-0.5 text-xs font-semibold text-accent tabular-nums"
        >
          {{ tab.badge }}
        </span>
      </TabsTrigger>
    </TabsList>

    <TabsContent
      v-for="tab in tabs"
      :key="tab.value"
      :value="tab.value"
      class="focus:outline-none"
    >
      <slot :name="tab.value" />
    </TabsContent>
  </TabsRoot>
</template>
