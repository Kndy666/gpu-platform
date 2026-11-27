<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { RefreshCw, Trash2, ChevronDown, ChevronUp, HardDrive } from "lucide-vue-next";
import { api, type StoragePool, type VolumeItem, type ServerNode } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { formatBytes, formatDateTime, formatShortDateTime, compactNumber } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import { useAutoRefresh } from "@/composables/useAutoRefresh";
import PageHeader from "@/components/layout/PageHeader.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import MetricBar from "@/components/ui/MetricBar.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";

interface NodeVolumeState {
  open: boolean;
  loading: boolean;
  loaded: boolean;
  items: VolumeItem[];
  page: number;
  count: number;
  nextPage: number | null;
}

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const nodes = ref<ServerNode[]>([]);
const filterNode = ref("");
const pools = ref<StoragePool[]>([]);
const loading = ref(true);

const nodeVolumes = ref<Record<number, NodeVolumeState>>({});
const deleteTarget = ref<VolumeItem | null>(null);
const deleting = ref(false);

const groupedNodes = computed(() => {
  const poolMap = new Map<number, StoragePool[]>();
  for (const pool of pools.value) {
    const list = poolMap.get(pool.node_id) ?? [];
    list.push(pool);
    poolMap.set(pool.node_id, list);
  }

  const orderedNodes: { id: number; name: string; state: string; state_label: string }[] = [];
  const seen = new Set<number>();

  for (const n of nodes.value) {
    if (filterNode.value && String(n.id) !== filterNode.value) continue;
    orderedNodes.push({ id: n.id, name: n.name, state: n.state, state_label: n.state_label });
    seen.add(n.id);
  }
  // Include any node present in pools even if not in nodes list
  for (const [nodeId, list] of poolMap.entries()) {
    if (seen.has(nodeId)) continue;
    if (filterNode.value && String(nodeId) !== filterNode.value) continue;
    orderedNodes.push({ id: nodeId, name: list[0]?.node_name ?? `#${nodeId}`, state: "online", state_label: "" });
  }

  return orderedNodes.map((node) => {
    const nodePools = poolMap.get(node.id) ?? [];
    const totalAvailable = nodePools.reduce((sum, p) => sum + (Number(p.available_gib ?? p.free_gib) || 0), 0);
    const totalCapacity = nodePools.reduce((sum, p) => sum + (Number(p.capacity_gib) || 0), 0);
    return {
      ...node,
      pools: nodePools,
      totalAvailable,
      totalCapacity,
    };
  });
});

function ensureNodeState(nodeId: number): NodeVolumeState {
  if (!nodeVolumes.value[nodeId]) {
    nodeVolumes.value = {
      ...nodeVolumes.value,
      [nodeId]: {
        open: false,
        loading: false,
        loaded: false,
        items: [],
        page: 1,
        count: 0,
        nextPage: null,
      },
    };
  }
  return nodeVolumes.value[nodeId];
}

async function loadPools(silent = false) {
  if (!silent) loading.value = true;
  try {
    const [nodesRes, storageRes] = await Promise.all([
      api.listNodes(1, 100).catch(() => null),
      api.getStorage({ page: 1, page_size: 1 }),
    ]);
    if (nodesRes) nodes.value = nodesRes.items;
    pools.value = storageRes.pools;
    // Refresh any already-expanded node volume drawers
    const openIds = Object.entries(nodeVolumes.value)
      .filter(([, st]) => st.open)
      .map(([id]) => Number(id));
    await Promise.all(openIds.map((id) => loadNodeVolumes(id, nodeVolumes.value[id]?.page ?? 1, silent)));
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) loading.value = false;
  }
}

async function loadNodeVolumes(nodeId: number, targetPage = 1, silent = false) {
  const st = ensureNodeState(nodeId);
  if (!silent) st.loading = true;
  try {
    const res = await api.getStorage({ node: nodeId, page: targetPage, page_size: 15 });
    st.items = res.volumes.items;
    st.page = res.volumes.page;
    st.count = res.volumes.count;
    st.nextPage = res.volumes.next_page;
    st.loaded = true;
  } catch (err) {
    if (!silent) {
      notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
    }
  } finally {
    if (!silent) st.loading = false;
  }
}

function toggleNodeVolumes(nodeId: number) {
  const st = ensureNodeState(nodeId);
  st.open = !st.open;
  if (st.open && !st.loaded) {
    void loadNodeVolumes(nodeId, 1);
  }
}

async function confirmDelete() {
  if (!deleteTarget.value) return;
  const targetNodeId = deleteTarget.value.node_id;
  deleting.value = true;
  try {
    await api.deleteVolume(deleteTarget.value.id);
    notices.pushToast({ level: "success", title: t("admin.volumeDeleting"), seconds: 5 });
    deleteTarget.value = null;
    await loadPools();
    if (nodeVolumes.value[targetNodeId]?.open) {
      await loadNodeVolumes(targetNodeId, nodeVolumes.value[targetNodeId].page);
    }
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof ApiError ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    deleting.value = false;
  }
}

onMounted(() => {
  void loadPools(false);
});

useAutoRefresh(() => loadPools(true), 20000);
</script>

<template>
  <div class="space-y-6">
    <PageHeader :title="t('admin.storageTitle')" :subtitle="t('admin.storageSubtitle')">
      <template #actions>
        <select v-model="filterNode" class="field-input min-h-8 w-auto py-1 text-xs" :aria-label="t('tasks.server')">
          <option value="">{{ t("tasks.allServers") }}</option>
          <option v-for="n in nodes" :key="n.id" :value="String(n.id)">{{ n.name }}</option>
        </select>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="loadPools(false)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          {{ t("app.refresh") }}
        </button>
      </template>
    </PageHeader>

    <div v-if="loading && !groupedNodes.length" class="card p-10 text-center text-sm text-muted">
      {{ t("app.loading") }}
    </div>

    <EmptyState v-else-if="!groupedNodes.length" :icon="HardDrive" :title="t('admin.noNodes')" />

    <!-- Grouped by node -->
    <div v-else class="space-y-5">
      <section v-for="group in groupedNodes" :key="group.id" class="card space-y-4 p-5">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-2.5">
            <h2 class="text-base font-semibold text-ink">{{ group.name }}</h2>
            <StatusBadge :status="group.state" :label="group.state_label || undefined" />
          </div>
          <p v-if="group.pools.length" class="text-xs text-muted tabular-nums">
            {{
              t("admin.poolFree", {
                free: `${compactNumber(group.totalAvailable)} GiB`,
                total: `${compactNumber(group.totalCapacity)} GiB`,
              })
            }}
          </p>
        </div>

        <!-- Node storage pools grid -->
        <div v-if="group.pools.length" class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="pool in group.pools"
            :key="pool.id"
            class="space-y-2.5 rounded-xl border border-border-subtle bg-canvas/60 p-4"
          >
            <div class="flex items-center justify-between gap-2">
              <strong class="text-sm font-semibold text-ink">{{ pool.name }}</strong>
              <StatusBadge
                :status="pool.healthy ? 'healthy' : 'error'"
                :label="pool.healthy ? t('admin.poolReady') : t('admin.poolPending')"
              />
            </div>
            <p class="text-xs font-medium text-body tabular-nums">
              {{
                t("admin.poolFree", {
                  free: `${compactNumber(pool.available_gib ?? pool.free_gib)} GiB`,
                  total: `${compactNumber(pool.capacity_gib)} GiB`,
                })
              }}
            </p>
            <MetricBar :percent="pool.percent" :tone="pool.percent > 85 ? 'warn' : 'accent'" />
            <div class="flex items-center justify-between gap-2 text-[11px] text-muted tabular-nums">
              <span>{{ t("admin.poolReserved", { value: `${compactNumber(pool.reserved_gib)} GiB` }) }}</span>
              <time>
                {{
                  pool.observed_at
                    ? formatShortDateTime(pool.observed_at, locale.current, auth.timeZone)
                    : t("admin.poolPendingSync")
                }}
              </time>
            </div>
          </article>
        </div>
        <p v-else class="text-xs text-muted">{{ t("admin.poolWaitingReport") }}</p>

        <!-- Collapsible node volumes section -->
        <div class="border-t border-border-subtle pt-3">
          <button
            type="button"
            class="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-subtle/60"
            :aria-expanded="Boolean(nodeVolumes[group.id]?.open)"
            @click="toggleNodeVolumes(group.id)"
          >
            <span class="inline-flex items-center gap-2">
              <span>
                {{ nodeVolumes[group.id]?.open ? t("admin.collapseVolumes") : t("admin.expandVolumes") }}
              </span>
              <span
                v-if="nodeVolumes[group.id]?.loaded"
                class="rounded-full bg-subtle px-2 py-0.5 text-xs font-medium text-muted tabular-nums"
              >
                {{ nodeVolumes[group.id].count }}
              </span>
            </span>
            <ChevronUp v-if="nodeVolumes[group.id]?.open" class="size-4 text-muted" aria-hidden="true" />
            <ChevronDown v-else class="size-4 text-muted" aria-hidden="true" />
          </button>

          <div v-if="nodeVolumes[group.id]?.open" class="mt-3">
            <div v-if="nodeVolumes[group.id]?.loading && !nodeVolumes[group.id]?.loaded" class="py-6 text-center text-xs text-muted">
              {{ t("app.loading") }}
            </div>

            <p v-else-if="!nodeVolumes[group.id]?.items.length" class="py-4 text-center text-xs text-muted">
              {{ t("admin.noVolumes") }}
            </p>

            <template v-else>
              <div class="table-shell">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>{{ t("admin.owner") }}</th>
                      <th>{{ t("admin.volumesHeading") }}</th>
                      <th>{{ t("admin.size") }}</th>
                      <th>{{ t("admin.poolDisk") }}</th>
                      <th>{{ t("taskDetail.workRetainUntil") }}</th>
                      <th>{{ t("tasks.status") }}</th>
                      <th class="text-end">{{ t("app.actions") }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="vol in nodeVolumes[group.id].items" :key="vol.id">
                      <td>
                        <p class="font-medium text-ink">{{ vol.username }}</p>
                        <p v-if="vol.deletion_error" class="text-xs text-danger">{{ vol.deletion_error }}</p>
                      </td>
                      <td class="text-xs">
                        {{ vol.kind === "home" ? t("admin.volumeKindHome") : (vol.request_name || t("admin.volumeKindWork")) }}
                      </td>
                      <td class="tabular-nums text-xs">
                        {{ vol.used_bytes != null ? `${formatBytes(vol.used_bytes)} / ` : "" }}{{ compactNumber(vol.size_gib) }} GiB
                      </td>
                      <td class="text-xs text-muted">{{ vol.pool_name }}</td>
                      <td class="tabular-nums text-xs text-muted">
                        {{ formatDateTime(vol.retain_until, locale.current, auth.timeZone) }}
                      </td>
                      <td>
                        <StatusBadge
                          :status="vol.state === 'deleting' ? 'deleting' : vol.busy ? 'running' : 'stopped'"
                          :label="vol.state === 'deleting' ? t('status.deleting') : vol.busy ? t('admin.inUse') : t('admin.idle')"
                        />
                      </td>
                      <td class="text-end">
                        <button
                          type="button"
                          class="btn btn-danger btn-sm"
                          :disabled="vol.busy || vol.state === 'deleting'"
                          :title="vol.busy ? t('admin.volumeBusyHint') : undefined"
                          @click="deleteTarget = vol"
                        >
                          <Trash2 class="size-3.5" aria-hidden="true" />
                          {{ t("admin.deleteVolume") }}
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <PaginationBar
                :page="nodeVolumes[group.id].page"
                :page-size="15"
                :count="nodeVolumes[group.id].count"
                :next-page="nodeVolumes[group.id].nextPage"
                @change="loadNodeVolumes(group.id, $event)"
              />
            </template>
          </div>
        </div>
      </section>
    </div>

    <ConfirmDialog
      :open="deleteTarget !== null"
      :title="t('admin.deleteVolumeTitle')"
      :message="t('admin.deleteVolumeBody')"
      :confirm-label="t('admin.deleteVolume')"
      danger
      :busy="deleting"
      @update:open="!$event && (deleteTarget = null)"
      @confirm="confirmDelete"
    />
  </div>
</template>
