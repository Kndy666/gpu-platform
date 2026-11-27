<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { RefreshCw, Trash2, Search, Layers } from "lucide-vue-next";
import { api, type ImageItem, type ServerNode } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { formatBytes } from "@/lib/format";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CopyButton from "@/components/ui/CopyButton.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";

const { t } = useI18n();
const notices = useNotificationsStore();

const nodes = ref<ServerNode[]>([]);
const filterNode = ref("");
const filterVisibility = ref<"" | "shared" | "private">("");
const filterStatus = ref("");
const searchQuery = ref("");

const allImages = ref<ImageItem[]>([]);
const page = ref(1);
const pageSize = 25;
const loading = ref(true);

const deleteTarget = ref<ImageItem | null>(null);
const deleting = ref(false);

const filteredImages = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  return allImages.value.filter((img) => {
    if (filterVisibility.value === "shared" && !img.shared) return false;
    if (filterVisibility.value === "private" && img.shared) return false;
    if (filterStatus.value && img.state !== filterStatus.value) return false;
    if (q) {
      const hay = `${img.name} ${img.owner_name ?? ""} ${img.node_name} ${img.architecture}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
});

const pagedImages = computed(() => {
  const start = (page.value - 1) * pageSize;
  return filteredImages.value.slice(start, start + pageSize);
});

const nextPage = computed(() =>
  page.value * pageSize < filteredImages.value.length ? page.value + 1 : null,
);

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    const collected: ImageItem[] = [];
    let current = 1;
    // Load across pages so filters work accurately over all non-temporary managed images
    while (current <= 10) {
      const res = await api.listManagedImages({
        node: filterNode.value || undefined,
        page: current,
        page_size: 100,
      });
      collected.push(...res.items);
      if (!res.next_page) break;
      current = res.next_page;
    }
    allImages.value = collected;
    const maxPage = Math.max(1, Math.ceil(filteredImages.value.length / pageSize));
    if (page.value > maxPage) page.value = maxPage;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function confirmDelete() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  try {
    await api.deleteImage(deleteTarget.value.id);
    notices.pushToast({ level: "success", title: t("images.imageDeleted"), seconds: 4 });
    deleteTarget.value = null;
    await load(false);
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    deleting.value = false;
  }
}

watch(filterNode, () => {
  page.value = 1;
  void load(false);
});

watch([filterVisibility, filterStatus, searchQuery], () => {
  page.value = 1;
});

onMounted(async () => {
  const n = await api.listNodes().catch(() => null);
  if (n) nodes.value = n.items;
  void load(false);
});

useAutoRefresh(() => load(true), 20000);
</script>

<template>
  <div>
    <PageHeader :title="t('admin.managedImagesTitle')" :subtitle="t('admin.managedImagesSubtitle')">
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="load(false)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
      </template>
    </PageHeader>

    <!-- Filters -->
    <div class="mb-4 flex flex-wrap items-center gap-3">
      <div class="relative min-w-56 flex-1 max-w-sm">
        <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden="true" />
        <input
          v-model="searchQuery"
          type="search"
          class="field-input pl-9"
          :placeholder="t('admin.searchImages')"
        />
      </div>
      <select v-model="filterNode" class="field-input w-auto min-w-40" :aria-label="t('tasks.server')">
        <option value="">{{ t("tasks.allServers") }}</option>
        <option v-for="n in nodes" :key="n.id" :value="String(n.id)">{{ n.name }}</option>
      </select>
      <select v-model="filterVisibility" class="field-input w-auto min-w-36" :aria-label="t('admin.visibility')">
        <option value="">{{ t("admin.allVisibility") }}</option>
        <option value="shared">{{ t("images.shared") }}</option>
        <option value="private">{{ t("images.private") }}</option>
      </select>
      <select v-model="filterStatus" class="field-input w-auto min-w-36" :aria-label="t('tasks.status')">
        <option value="">{{ t("tasks.allStatuses") }}</option>
        <option value="ready">{{ t("status.ready") }}</option>
        <option value="deleting">{{ t("status.deleting") }}</option>
        <option value="failed">{{ t("status.failed") }}</option>
      </select>
    </div>

    <div v-if="loading && !allImages.length" class="card p-10 text-center text-sm text-muted">
      {{ t("app.loading") }}
    </div>

    <EmptyState v-else-if="!pagedImages.length" :icon="Layers" :title="t('images.emptyPermanent')" />

    <template v-else>
      <div class="table-shell">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t("images.buildName") }}</th>
              <th>{{ t("tasks.server") }}</th>
              <th>{{ t("admin.owner") }}</th>
              <th>{{ t("admin.size") }}</th>
              <th>{{ t("tasks.status") }}</th>
              <th class="text-end">{{ t("app.actions") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="img in pagedImages" :key="img.id">
              <td class="font-medium text-ink">{{ img.name }}</td>
              <td class="whitespace-nowrap text-sm">{{ img.node_name }} ({{ img.architecture }})</td>
              <td class="text-sm">
                {{ img.shared ? t("images.shared") : (img.owner_name || t("images.private")) }}
              </td>
              <td class="whitespace-nowrap tabular-nums text-sm text-muted">{{ formatBytes(img.size_bytes) }}</td>
              <td><StatusBadge :status="img.state" :label="img.state_label" /></td>
              <td class="text-end whitespace-nowrap">
                <div class="inline-flex items-center gap-1.5">
                  <CopyButton :text="img.reference" :label="t('images.copyReference')" variant="ghost" />
                  <button
                    v-if="img.state !== 'deleting'"
                    type="button"
                    class="btn btn-danger btn-sm"
                    @click="deleteTarget = img"
                  >
                    <Trash2 class="size-3.5" aria-hidden="true" />
                    {{ t("images.deleteImage") }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <PaginationBar
        :page="page"
        :page-size="pageSize"
        :count="filteredImages.length"
        :next-page="nextPage"
        @change="page = $event"
      />
    </template>

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
