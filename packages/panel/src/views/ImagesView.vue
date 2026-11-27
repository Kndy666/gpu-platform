<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import { Layers, RefreshCw, Trash2, FileCode2, ChevronDown, ChevronUp } from "lucide-vue-next";
import { api, type ImageItem, type BuildItem, type SnapshotItem, type ServerNode } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { formatBytes, formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import SegmentedTabs from "@/components/domain/SegmentedTabs.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CopyButton from "@/components/ui/CopyButton.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const activeTab = ref("permanent");
const nodes = ref<ServerNode[]>([]);
const filterNode = ref("");

// Saved image list
const images = ref<ImageItem[]>([]);
const imageCount = ref(0);
const imagePage = ref(1);
const imageNext = ref<number | null>(null);
const loadingImages = ref(true);

const deleteTarget = ref<ImageItem | null>(null);
const deleting = ref(false);

// Build form
const buildNodes = ref<ServerNode[]>([]);
const buildNodeId = ref<number | null>(null);
const buildName = ref("");
const buildGit = ref("");
const buildDockerfile = ref("FROM ubuntu:24.04\n");
const buildSubmitting = ref(false);
const buildError = ref("");

// History
const builds = ref<BuildItem[]>([]);
const buildLogs = ref<Record<string, string>>({});
const openBuildLogs = ref<Record<string, boolean>>({});
const snapshots = ref<SnapshotItem[]>([]);
const loadingHistory = ref(false);

const tabs = computed(() => [
  { value: "permanent", label: t("images.tabPermanent") },
  { value: "build", label: t("images.tabBuild") },
  { value: "history", label: t("images.tabHistory") },
]);

async function loadImages(page = 1, silent = false) {
  if (!silent) loadingImages.value = true;
  try {
    const res = await api.listImages({
      kind: "permanent",
      node: filterNode.value || undefined,
      page,
      page_size: 20,
    });
    images.value = res.items;
    imageCount.value = res.count;
    imagePage.value = res.page;
    imageNext.value = res.next_page;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loadingImages.value = false;
  }
}

async function loadBuildOptions() {
  try {
    const res = await api.getBuildOptions();
    buildNodes.value = res.nodes;
    if (!buildNodeId.value) buildNodeId.value = res.initial?.node ?? res.nodes[0]?.id ?? null;
  } catch {
    buildNodes.value = [];
  }
}

async function loadHistory(silent = false) {
  if (!silent) loadingHistory.value = true;
  try {
    const [bRes, sRes] = await Promise.all([api.listBuilds(1, 15), api.listSnapshots(1, 15)]);
    builds.value = bRes.items;
    snapshots.value = sRes.items;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loadingHistory.value = false;
  }
}

async function toggleBuildLog(id: string) {
  if (openBuildLogs.value[id]) {
    openBuildLogs.value[id] = false;
    return;
  }
  openBuildLogs.value[id] = true;
  if (!(id in buildLogs.value)) {
    try {
      const detail = await api.getBuild(id);
      buildLogs.value[id] = detail.log || detail.error || "";
    } catch {
      buildLogs.value[id] = "";
    }
  }
}

async function confirmDelete() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  try {
    await api.deleteImage(deleteTarget.value.id);
    notices.pushToast({ level: "success", title: t("images.imageDeleted"), seconds: 5 });
    deleteTarget.value = null;
    await loadImages(imagePage.value);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    deleting.value = false;
  }
}

async function submitBuild() {
  if (!buildNodeId.value) return;
  buildError.value = "";
  buildSubmitting.value = true;
  try {
    await api.createBuild({
      node: buildNodeId.value,
      name: buildName.value.trim(),
      dockerfile: buildDockerfile.value,
      context_git: buildGit.value.trim() || undefined,
    });
    notices.pushToast({ level: "success", title: t("images.buildSubmitted"), seconds: 5 });
    buildName.value = "";
    buildGit.value = "";
    activeTab.value = "history";
    await loadHistory();
  } catch (err) {
    buildError.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    buildSubmitting.value = false;
  }
}

function canModify(image: ImageItem): boolean {
  if (!auth.user) return false;
  if (auth.isStaff) return true;
  return !image.shared && image.owner_id === auth.user.id;
}

watch(activeTab, (tab) => {
  if (tab === "permanent") {
    void loadImages(1);
  } else if (tab === "build") {
    void loadBuildOptions();
  } else if (tab === "history") {
    void loadHistory();
  }
});

watch(filterNode, () => {
  if (activeTab.value === "permanent") {
    void loadImages(1);
  }
});

onMounted(async () => {
  const res = await api.listNodes().catch(() => null);
  if (res) nodes.value = res.items;
  void loadImages(1);
});

useAutoRefresh(() => {
  if (activeTab.value === "permanent") return loadImages(imagePage.value, true);
  if (activeTab.value === "history") return loadHistory(true);
}, 15000);
</script>

<template>
  <div>
    <PageHeader :title="t('images.title')" :subtitle="t('images.subtitle')" />

    <SegmentedTabs v-model="activeTab" :tabs="tabs">
      <!-- Saved images -->
      <template #permanent>
        <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
          <select v-model="filterNode" class="field-input w-auto min-w-44" :aria-label="t('tasks.server')">
            <option value="">{{ t("tasks.allServers") }}</option>
            <option v-for="node in nodes" :key="node.id" :value="String(node.id)">{{ node.name }}</option>
          </select>
          <button type="button" class="btn btn-ghost btn-sm" :disabled="loadingImages" @click="loadImages(imagePage)">
            <RefreshCw class="size-4" :class="{ 'animate-spin': loadingImages }" aria-hidden="true" />
            {{ t("app.refresh") }}
          </button>
        </div>

        <div v-if="loadingImages && !images.length" class="card p-10 text-center text-sm text-muted">
          {{ t("app.loading") }}
        </div>

        <EmptyState
          v-else-if="!images.length"
          :icon="Layers"
          :title="t('images.emptyPermanent')"
        />

        <template v-else>
          <div class="space-y-3">
            <article v-for="img in images" :key="img.id" class="card flex flex-col gap-3 p-4 lg:flex-row lg:items-center lg:justify-between">
              <div class="min-w-0 space-y-1.5">
                <div class="flex flex-wrap items-center gap-2">
                  <h3 class="truncate text-base font-semibold text-ink">{{ img.name }}</h3>
                  <StatusBadge :status="img.state" :label="img.state_label" />
                  <span class="rounded-full bg-subtle px-2.5 py-0.5 text-xs text-muted">
                    {{ img.shared ? t("images.shared") : t("images.private") }}
                  </span>
                </div>
                <p class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted tabular-nums">
                  <span>{{ img.node_name }}</span>
                  <span>{{ img.architecture }}</span>
                  <span>{{ formatBytes(img.size_bytes) }}</span>
                </p>
                <p v-if="img.cleanup_error" class="text-xs text-danger">{{ t("images.deletionRetry") }}</p>
              </div>

              <div class="flex shrink-0 flex-wrap items-center justify-end gap-2 lg:flex-nowrap">
                <template v-if="img.state === 'ready' && img.verified">
                  <RouterLink
                    :to="{ path: '/environments/new', query: { node: String(img.node_id), image: img.id } }"
                    class="btn btn-secondary btn-sm"
                  >
                    {{ t("images.useForEnv") }}
                  </RouterLink>
                  <RouterLink
                    :to="{ path: '/jobs/new', query: { node: String(img.node_id), image: img.id } }"
                    class="btn btn-secondary btn-sm"
                  >
                    {{ t("images.useForJob") }}
                  </RouterLink>
                </template>
                <CopyButton :text="img.reference" :label="t('images.copyReference')" variant="secondary" />
                <button
                  v-if="img.state !== 'deleting' && canModify(img)"
                  type="button"
                  class="btn btn-danger btn-sm"
                  @click="deleteTarget = img"
                >
                  <Trash2 class="size-3.5" aria-hidden="true" />
                  {{ t("images.deleteImage") }}
                </button>
              </div>
            </article>
          </div>

          <PaginationBar
            :page="imagePage"
            :page-size="20"
            :count="imageCount"
            :next-page="imageNext"
            @change="loadImages"
          />
        </template>
      </template>

      <!-- Build from Dockerfile -->
      <template #build>
        <EmptyState
          v-if="!buildNodes.length"
          :icon="FileCode2"
          :title="t('images.buildUnavailable')"
        />
        <form v-else class="card space-y-4 p-6" @submit.prevent="submitBuild">
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label for="bld-node" class="mb-1.5 block text-sm font-medium text-ink">
                {{ t("images.buildServer") }}
              </label>
              <select id="bld-node" v-model.number="buildNodeId" required class="field-input">
                <option v-for="node in buildNodes" :key="node.id" :value="node.id">{{ node.name }}</option>
              </select>
            </div>
            <div>
              <label for="bld-name" class="mb-1.5 block text-sm font-medium text-ink">
                {{ t("images.buildName") }}
              </label>
              <input
                id="bld-name"
                v-model="buildName"
                type="text"
                required
                class="field-input"
                :placeholder="t('images.buildNamePlaceholder')"
              />
            </div>
          </div>

          <div>
            <label for="bld-git" class="mb-1.5 block text-sm font-medium text-ink">
              {{ t("images.buildGit") }}
            </label>
            <input
              id="bld-git"
              v-model="buildGit"
              type="url"
              class="field-input font-mono text-sm"
              :placeholder="t('images.buildGitPlaceholder')"
            />
          </div>

          <div>
            <label for="bld-dockerfile" class="mb-1.5 block text-sm font-medium text-ink">
              {{ t("images.buildDockerfile") }}
            </label>
            <textarea
              id="bld-dockerfile"
              v-model="buildDockerfile"
              rows="10"
              required
              class="field-input font-mono text-sm"
            />
          </div>

          <p v-if="buildError" role="alert" class="rounded-lg border border-danger/30 bg-danger-bg px-3.5 py-2.5 text-sm text-danger">
            {{ buildError }}
          </p>

          <button type="submit" class="btn btn-primary" :disabled="buildSubmitting">
            {{ buildSubmitting ? t("app.saving") : t("images.buildSubmit") }}
          </button>
        </form>
      </template>

      <!-- Build & Save History -->
      <template #history>
        <div class="mb-4 flex justify-end">
          <button type="button" class="btn btn-ghost btn-sm" :disabled="loadingHistory" @click="loadHistory(false)">
            <RefreshCw class="size-4" :class="{ 'animate-spin': loadingHistory }" aria-hidden="true" />
            {{ t("app.refresh") }}
          </button>
        </div>

        <div class="space-y-6">
          <section>
            <h2 class="mb-3">{{ t("images.buildsHeading") }}</h2>
            <p v-if="!builds.length" class="card p-6 text-sm text-muted">{{ t("images.noBuilds") }}</p>
            <div v-else class="space-y-3">
              <article v-for="b in builds" :key="b.id" class="card p-4">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div class="flex items-center gap-2">
                      <span class="font-medium text-ink">{{ b.name }}</span>
                      <StatusBadge :status="b.status" :label="b.status_label" />
                    </div>
                    <p class="mt-0.5 text-xs text-muted tabular-nums">
                      {{ b.node_name }} · {{ formatDateTime(b.created_at, locale.current, auth.timeZone) }}
                    </p>
                    <p v-if="b.error" class="mt-1 text-xs text-danger">{{ b.error }}</p>
                  </div>
                  <button type="button" class="btn btn-secondary btn-sm" @click="toggleBuildLog(b.id)">
                    <ChevronUp v-if="openBuildLogs[b.id]" class="size-3.5" aria-hidden="true" />
                    <ChevronDown v-else class="size-3.5" aria-hidden="true" />
                    {{ openBuildLogs[b.id] ? t("images.hideLog") : t("images.viewLog") }}
                  </button>
                </div>
                <pre v-if="openBuildLogs[b.id]" class="code-block mt-3 max-h-72 overflow-y-auto text-xs">{{ buildLogs[b.id] || t("taskDetail.logsEmpty") }}</pre>
              </article>
            </div>
          </section>

          <section>
            <h2 class="mb-3">{{ t("images.snapshotsHeading") }}</h2>
            <p v-if="!snapshots.length" class="card p-6 text-sm text-muted">{{ t("images.noSnapshots") }}</p>
            <div v-else class="table-shell">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>{{ t("tasks.purpose") }}</th>
                    <th>{{ t("tasks.server") }}</th>
                    <th>{{ t("tasks.status") }}</th>
                    <th>{{ t("admin.time") }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="snap in snapshots" :key="snap.id">
                    <td>
                      <RouterLink :to="`/tasks/${snap.source_id}`" class="font-medium text-ink no-underline hover:text-accent">
                        {{ snap.source_name || snap.source_purpose || snap.source_id }}
                      </RouterLink>
                      <p v-if="snap.error" class="mt-0.5 text-xs text-danger">{{ snap.error }}</p>
                    </td>
                    <td>{{ snap.node_name || "—" }}</td>
                    <td><StatusBadge :status="snap.status" :label="snap.status_label" /></td>
                    <td class="tabular-nums text-xs text-muted">
                      {{ formatDateTime(snap.created_at, locale.current, auth.timeZone) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </template>
    </SegmentedTabs>

    <ConfirmDialog
      :open="deleteTarget !== null"
      :title="t('images.deleteConfirmTitle')"
      :message="t('images.deleteConfirmBody')"
      :confirm-label="t('images.deleteImage')"
      danger
      :busy="deleting"
      @update:open="!$event && (deleteTarget = null)"
      @confirm="confirmDelete"
    />
  </div>
</template>
