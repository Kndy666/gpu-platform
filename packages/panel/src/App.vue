<script setup lang="ts">
import { watch } from "vue";
import { RouterView, useRoute } from "vue-router";
import AppShell from "@/components/layout/AppShell.vue";
import ToastRegion from "@/components/layout/ToastRegion.vue";
import { useAuthStore } from "@/stores/auth";
import { useNotificationsStore } from "@/stores/notifications";

const route = useRoute();
const auth = useAuthStore();
const notifications = useNotificationsStore();

watch(
  [() => auth.isAuthenticated, () => auth.features.notifications],
  ([authenticated, enabled]) => {
    if (authenticated && enabled) {
      notifications.startPolling();
    } else {
      notifications.stopPolling();
    }
  },
  { immediate: true },
);
</script>

<template>
  <template v-if="route.meta.public">
    <RouterView />
    <ToastRegion />
  </template>
  <AppShell v-else>
    <RouterView />
  </AppShell>
</template>
