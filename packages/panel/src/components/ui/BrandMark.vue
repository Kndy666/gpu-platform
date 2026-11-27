<script setup lang="ts">
withDefaults(
  defineProps<{
    size?: "sm" | "md" | "lg";
  }>(),
  { size: "md" },
);
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center justify-center rounded-xl bg-accent/12 text-accent ring-1 ring-accent/25"
    :class="size === 'lg' ? 'size-11' : size === 'sm' ? 'size-7 rounded-lg' : 'size-8 rounded-lg'"
    aria-hidden="true"
  >
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      :class="size === 'lg' ? 'size-6' : size === 'sm' ? 'size-4' : 'size-[18px]'"
    >
      <rect x="6" y="6" width="12" height="12" rx="2.5" />
      <rect x="9.5" y="9.5" width="5" height="5" rx="1" fill="currentColor" stroke="none" />
      <path d="M9 2.75V6M15 2.75V6M9 18v3.25M15 18v3.25M2.75 9H6M2.75 15H6M18 9h3.25M18 15h3.25" />
    </svg>
  </span>
</template>
