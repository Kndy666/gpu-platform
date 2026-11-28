<script setup lang="ts">
import type { Component } from "vue";

defineProps<{
  icon?: Component;
  title: string;
  hint?: string;
}>();
</script>

<template>
  <div class="card flex cursor-default select-none flex-col items-center justify-center px-6 py-12 text-center">
    <div
      v-if="icon"
      class="mb-3 flex size-11 items-center justify-center rounded-xl bg-subtle text-muted"
      aria-hidden="true"
    >
      <component :is="icon" class="size-5" />
    </div>
    <p class="font-medium text-ink">{{ title }}</p>
    <p v-if="hint" class="mt-1.5 max-w-md text-sm text-muted">{{ hint }}</p>
    <div v-if="$slots.action" class="mt-4">
      <slot name="action" />
    </div>
  </div>
</template>
