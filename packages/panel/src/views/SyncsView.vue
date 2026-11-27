<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { ArrowLeftRight, RefreshCw } from "lucide-vue-next";
import { api, type SyncJobItem } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { formatBytes, formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import MetricBar from "@/components/ui/MetricBar.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import HelpTip from "@/components/ui/HelpTip.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const syncNodes = ref<{ id: number; name: string; architecture: string }[]>([]);
const syncImages = ref<{ id: string; name: string; node_id: number; architecture: string; shared: boolean }[]>([]);
const canSyncImages = ref(false);
const canSyncDatasets = ref(false);

// Image sync form
const selectedImageId = ref("");
const imageTargetId = ref<number | null>(null);
const imageBandwidth = ref<number | "">("");
const imageSubmitting = ref(false);

// Dataset sync form (admin)
const dsSourceId = ref<number | null>(null);
const dsTargetId = ref<number | null>(null);
const dsPaths = ref("");
const dsBandwidth = ref<number | "">("");
const dsSubmitting = ref(false);

// Jobs list
const jobs = ref<SyncJobItem[]>([]);
const page = ref(1);
const count = ref(0);
const nextPage = ref<number | null>(null);
const loading = ref(true);

const selectedImage = computed(() => syncImages.value.find((i) => i.id === selectedImageId.value) ?? null);

const validImageTargets = computed(() => {
  if (!selectedImage.value) return [];
  return syncNodes.value.filter(
    (node) => node.id !== selectedImage.value!.node_id && node.architecture === selectedImage.value!.architecture,
  );
});

const validDatasetTargets = computed(() =>
  syncNodes.value.filter((node) => node.id !== dsSourceId.value),
);

async function loadOptions() {
  try {
    const res = await api.getSyncOptions();
    syncNodes.value = res.nodes;
    syncImages.value = res.images;
    canSyncImages.value = res.can_sync_images;
    canSyncDatasets.value = res.can_sync_datasets;
    if (!selectedImageId.value && res.images.length) {
      selectedImageId.value = res.images[0].id;
    }
    if (!imageTargetId.value && validImageTargets.value.length) {
      imageTargetId.value = validImageTargets.value[0].id;
    }
    if (!dsSourceId.value && res.nodes.length >= 2) {
      dsSourceId.value = res.nodes[0].id;
      dsTargetId.value = res.nodes[1].id;
    }
  } catch {
    canSyncImages.value = false;
    canSyncDatasets.value = false;
  }
}

async function loadJobs(targetPage = page.value, silent = false) {
  if (!silent) loading.value = true;
  try {
    const res = await api.listSyncJobs(targetPage);
    jobs.value = res.items;
    page.value = res.page;
    count.value = res.count;
    nextPage.value = res.next_page;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function submitImageSync() {
  if (!selectedImageId.value || !imageTargetId.value) return;
  imageSubmitting.value = true;
  try {
    await api.syncImage(selectedImageId.value, {
      target: imageTargetId.value,
      bandwidth: imageBandwidth.value ? Number(imageBandwidth.value) : undefined,
    });
    notices.pushToast({ level: "success", title: t("syncs.syncStarted"), seconds: 5 });
    await loadJobs(1);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    imageSubmitting.value = false;
  }
}

async function submitDatasetSync() {
  if (!dsSourceId.value || !dsTargetId.value) return;
  dsSubmitting.value = true;
  try {
    const paths = dsPaths.value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    await api.syncDatasets({
      source: dsSourceId.value,
      target: dsTargetId.value,
      paths: paths.length ? paths : undefined,
      bandwidth: dsBandwidth.value ? Number(dsBandwidth.value) : undefined,
    });
    notices.pushToast({ level: "success", title: t("syncs.syncStarted"), seconds: 5 });
    dsPaths.value = "";
    await loadJobs(1);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    dsSubmitting.value = false;
  }
}

function formatSyncError(err: SyncJobItem["error"]): string {
  if (!err) return "";
  if (typeof err === "string") return err.trim();
  if (typeof err === "object") {
    const entries = Object.entries(err).filter(([, v]) => v !== null && v !== undefined && v !== "");
    if (!entries.length) return "";
    const msg = err.message ?? err.detail ?? err.error;
    if (typeof msg === "string" && msg.trim()) return msg.trim();
    return entries.map(([k, v]) => `${k}: ${String(v)}`).join(" · ");
  }
  return String(err);
}

function progressPercent(job: SyncJobItem): number {
  if (job.state === "succeeded") return 100;
  if (!job.total_bytes) return 0;
  return Math.min(100, (job.transferred_bytes / job.total_bytes) * 100);
}

onMounted(async () => {
  await Promise.all([loadOptions(), loadJobs(1)]);
});

useAutoRefresh(
  () => loadJobs(page.value, true),
  () => (jobs.value.some((j) => ["queued", "running", "pending"].includes(j.state)) ? 5000 : 20000),
);
</script>

<template>
  <div class="space-y-6">
    <PageHeader :title="t('syncs.title')" :subtitle="t('syncs.subtitle')" />

    <EmptyState
      v-if="!loading && !canSyncImages && !canSyncDatasets"
      :icon="ArrowLeftRight"
      :title="t('syncs.noSyncAvailable')"
    />

    <div v-else class="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <!-- Image sync form -->
      <form v-if="canSyncImages" class="card space-y-4 p-5" @submit.prevent="submitImageSync">
        <div class="flex items-center gap-1.5">
          <h2>{{ t("syncs.imageSyncTitle") }}</h2>
          <HelpTip :text="t('syncs.imageSyncHint')" :label="t('syncs.imageSyncTitle')" />
        </div>

        <div>
          <label for="sync-img" class="mb-1 block text-sm font-medium text-ink">{{ t("syncs.selectImage") }}</label>
          <select id="sync-img" v-model="selectedImageId" required class="field-input">
            <option v-for="img in syncImages" :key="img.id" :value="img.id">
              {{ img.name }} ({{ img.architecture }})
            </option>
          </select>
        </div>

        <div>
          <label for="sync-img-target" class="mb-1 block text-sm font-medium text-ink">{{ t("syncs.targetServer") }}</label>
          <select id="sync-img-target" v-model.number="imageTargetId" required class="field-input">
            <option v-for="node in validImageTargets" :key="node.id" :value="node.id">
              {{ node.name }} ({{ node.architecture }})
            </option>
          </select>
        </div>

        <div>
          <label for="sync-img-bw" class="mb-1 block text-sm font-medium text-ink">{{ t("syncs.bandwidth") }}</label>
          <input id="sync-img-bw" v-model="imageBandwidth" type="number" min="1" step="1" class="field-input tabular-nums" />
        </div>

        <button type="submit" class="btn btn-primary" :disabled="imageSubmitting || !validImageTargets.length">
          {{ imageSubmitting ? t("app.saving") : t("syncs.startImageSync") }}
        </button>
      </form>

      <!-- Dataset sync form (admin) -->
      <form v-if="canSyncDatasets" class="card space-y-4 p-5" @submit.prevent="submitDatasetSync">
        <div class="flex items-center gap-1.5">
          <h2>{{ t("syncs.datasetSyncTitle") }}</h2>
          <HelpTip :text="t('syncs.datasetSyncHint')" :label="t('syncs.datasetSyncTitle')" />
        </div>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label for="sync-ds-src" class="mb-1 block text-sm font-medium text-ink">{{ t("syncs.sourceServer") }}</label>
            <select id="sync-ds-src" v-model.number="dsSourceId" required class="field-input">
              <option v-for="node in syncNodes" :key="node.id" :value="node.id">{{ node.name }}</option>
            </select>
          </div>
          <div>
            <label for="sync-ds-dst" class="mb-1 block text-sm font-medium text-ink">{{ t("syncs.targetServer") }}</label>
            <select id="sync-ds-dst" v-model.number="dsTargetId" required class="field-input">
              <option v-for="node in validDatasetTargets" :key="node.id" :value="node.id">{{ node.name }}</option>
            </select>
          </div>
        </div>

        <div>
          <label for="sync-ds-paths" class="mb-1 block text-sm font-medium text-ink">{{ t("syncs.datasetPaths") }}</label>
          <input id="sync-ds-paths" v-model="dsPaths" type="text" class="field-input font-mono text-sm" />
        </div>

        <div>
          <label for="sync-ds-bw" class="mb-1 block text-sm font-medium text-ink">{{ t("syncs.bandwidth") }}</label>
          <input id="sync-ds-bw" v-model="dsBandwidth" type="number" min="1" step="1" class="field-input tabular-nums" />
        </div>

        <button type="submit" class="btn btn-primary" :disabled="dsSubmitting || !validDatasetTargets.length">
          {{ dsSubmitting ? t("app.saving") : t("syncs.startDatasetSync") }}
        </button>
      </form>
    </div>

    <!-- Sync Jobs -->
    <section>
      <div class="mb-3 flex items-center justify-between gap-3">
        <h2>{{ t("syncs.jobsTitle") }}</h2>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="loadJobs(page)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
      </div>

      <EmptyState v-if="!loading && !jobs.length" :icon="ArrowLeftRight" :title="t('syncs.emptyJobs')" />

      <template v-else>
        <div class="table-shell">
          <table class="data-table">
            <thead>
              <tr>
                <th>{{ t("syncs.target") }}</th>
                <th>{{ t("syncs.route") }}</th>
                <th>{{ t("syncs.progress") }}</th>
                <th>{{ t("tasks.status") }}</th>
                <th>{{ t("admin.time") }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="job in jobs" :key="job.id">
                <td>
                  <p class="font-medium text-ink">
                    {{ job.kind === "image" ? (job.image_name || t("syncs.kindImage")) : t("syncs.kindDataset") }}
                  </p>
                  <p v-if="job.paths?.length" class="font-mono text-xs text-muted">{{ job.paths.join(", ") }}</p>
                  <p v-if="formatSyncError(job.error)" class="mt-0.5 text-xs text-danger">{{ formatSyncError(job.error) }}</p>
                </td>
                <td class="whitespace-nowrap text-xs">
                  {{ job.source_name || job.source }} → {{ job.target_name || job.target }}
                </td>
                <td class="min-w-44">
                  <MetricBar compact :percent="progressPercent(job)" :tone="job.state === 'failed' ? 'danger' : 'accent'" />
                  <p class="mt-1 text-[11px] text-muted tabular-nums">
                    {{ formatBytes(job.transferred_bytes) }}
                    <template v-if="job.total_bytes"> / {{ formatBytes(job.total_bytes) }}</template>
                  </p>
                </td>
                <td><StatusBadge :status="job.state" :label="job.state_label || job.state" /></td>
                <td class="whitespace-nowrap tabular-nums text-xs text-muted">
                  {{ formatDateTime(job.created_at, locale.current, auth.timeZone) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <PaginationBar
          :page="page"
          :page-size="20"
          :count="count"
          :next-page="nextPage"
          @change="loadJobs"
        />
      </template>
    </section>
  </div>
</template>
