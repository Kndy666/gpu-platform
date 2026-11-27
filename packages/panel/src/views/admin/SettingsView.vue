<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { useNotificationsStore } from "@/stores/notifications";
import PageHeader from "@/components/layout/PageHeader.vue";
import HelpTip from "@/components/ui/HelpTip.vue";

const { t } = useI18n();
const notices = useNotificationsStore();

const workspaceDays = ref(7);
const snapshotHours = ref(72);
const failedSnapshotHours = ref(24);
const publicHost = ref("");
const sshPorts = ref<[number, number]>([0, 0]);
const loading = ref(true);
const saving = ref(false);
const errorMessage = ref("");

async function load() {
  loading.value = true;
  try {
    const res = await api.getRetentionSettings();
    workspaceDays.value = res.workspace_retention_days;
    snapshotHours.value = res.snapshot_hours;
    failedSnapshotHours.value = res.failed_snapshot_hours;
    publicHost.value = res.public_host;
    sshPorts.value = res.ssh_ports;
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    loading.value = false;
  }
}

async function save() {
  errorMessage.value = "";
  saving.value = true;
  try {
    await api.updateRetentionSettings({
      workspace_retention_days: Number(workspaceDays.value),
      snapshot_hours: Number(snapshotHours.value),
      failed_snapshot_hours: Number(failedSnapshotHours.value),
    });
    notices.pushToast({ level: "success", title: t("admin.settingsSaved"), seconds: 4 });
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div>
    <PageHeader :title="t('admin.settingsTitle')" :subtitle="t('admin.settingsSubtitle')" />

    <div class="grid grid-cols-1 gap-6 lg:grid-cols-12">
      <form class="card space-y-4 p-6 lg:col-span-7" @submit.prevent="save">
        <div class="flex items-center gap-1.5">
          <h2>{{ t("admin.settingsTitle") }}</h2>
          <HelpTip :text="t('admin.retentionHint')" :label="t('admin.settingsTitle')" />
        </div>
        <div>
          <label for="set-work" class="mb-1 block text-sm font-medium text-ink">
            {{ t("admin.workRetentionDays") }}
          </label>
          <input id="set-work" v-model.number="workspaceDays" type="number" min="1" max="3650" required class="field-input tabular-nums" />
        </div>

        <div>
          <label for="set-snap" class="mb-1 block text-sm font-medium text-ink">
            {{ t("admin.snapshotHours") }}
          </label>
          <input id="set-snap" v-model.number="snapshotHours" type="number" min="1" max="8760" required class="field-input tabular-nums" />
        </div>

        <div>
          <label for="set-fail" class="mb-1 block text-sm font-medium text-ink">
            {{ t("admin.failedSnapshotHours") }}
          </label>
          <input id="set-fail" v-model.number="failedSnapshotHours" type="number" min="1" max="8760" required class="field-input tabular-nums" />
        </div>

        <p v-if="errorMessage" role="alert" class="rounded-lg bg-danger-bg px-3 py-2 text-xs text-danger">{{ errorMessage }}</p>

        <button type="submit" class="btn btn-primary" :disabled="saving || loading">
          {{ saving ? t("app.saving") : t("app.save") }}
        </button>
      </form>

      <section class="card h-fit space-y-3 p-6 lg:col-span-5">
        <h2>{{ t("admin.platformInfo") }}</h2>
        <dl class="space-y-2.5 text-sm">
          <div class="flex justify-between gap-2">
            <dt class="text-muted">{{ t("admin.publicHost") }}</dt>
            <dd class="font-mono text-xs text-ink">{{ publicHost || "—" }}</dd>
          </div>
          <div class="flex justify-between gap-2">
            <dt class="text-muted">{{ t("admin.sshPortRange") }}</dt>
            <dd class="font-mono text-xs text-ink tabular-nums">{{ sshPorts[0] }} – {{ sshPorts[1] }}</dd>
          </div>
        </dl>
      </section>
    </div>
  </div>
</template>
