<script setup lang="ts">
defineProps<{
  title: string;
  subtitle?: string;
}>();
</script>

<template>
  <header class="mb-7 flex flex-wrap items-end justify-between gap-4">
    <div class="min-w-0 max-w-3xl">
      <h1 class="font-serif text-ink">{{ title }}</h1>
      <p v-if="subtitle" class="mt-1.5 text-sm text-muted leading-relaxed">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2.5">
      <slot name="actions" />
    </div>
  </header>
</template>
