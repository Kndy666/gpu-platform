<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { Folder, FileText, Link2, Download, ArrowUp, Search, Database } from "lucide-vue-next";
import { api, type DatasetEntry } from "@/api/endpoints";
import { formatBytes, formatDateTime } from "@/lib/format";
import { isImeComposing } from "@/lib/ime";
import { useAuthStore } from "@/stores/auth";
import { useLocaleStore } from "@/stores/locale";
import { useNotificationsStore } from "@/stores/notifications";
import PageHeader from "@/components/layout/PageHeader.vue";
import PaginationBar from "@/components/ui/PaginationBar.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

const { t } = useI18n();
const auth = useAuthStore();
const locale = useLocaleStore();
const notices = useNotificationsStore();

const nodes = ref<{ id: number; name: string }[]>([]);
const activeNodeId = ref<number | null>(null);
const currentPath = ref("");
const queryInput = ref("");
const activeQuery = ref("");

const items = ref<DatasetEntry[]>([]);
const page = ref(1);
const count = ref(0);
const nextPage = ref<number | null>(null);
const loading = ref(true);

const breadcrumbs = computed(() => {
  if (!currentPath.value) return [];
  const parts = currentPath.value.split("/").filter(Boolean);
  return parts.map((name, idx) => ({
    name,
    path: parts.slice(0, idx + 1).join("/"),
  }));
});

const parentPath = computed(() => {
  if (!currentPath.value) return null;
  const parts = currentPath.value.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
});

async function load(targetPage = 1) {
  loading.value = true;
  try {
    const res = await api.listDatasets({
      node: activeNodeId.value ?? undefined,
      path: currentPath.value || undefined,
      q: activeQuery.value || undefined,
      page: targetPage,
      page_size: 50,
    });
    nodes.value = res.nodes;
    activeNodeId.value = res.node.id;
    items.value = res.items;
    page.value = res.page;
    count.value = res.count;
    nextPage.value = res.next_page;
  } catch (err) {
    notices.pushToast({ level: "error", title: err instanceof Error ? err.message : t("app.loadError"), seconds: 6 });
  } finally {
    loading.value = false;
  }
}

function navigatePath(nextPath: string) {
  currentPath.value = nextPath;
  queryInput.value = "";
  activeQuery.value = "";
  void load(1);
}

function selectNode(nodeId: number) {
  activeNodeId.value = nodeId;
  currentPath.value = "";
  queryInput.value = "";
  activeQuery.value = "";
  void load(1);
}

function runSearch() {
  activeQuery.value = queryInput.value.trim();
  void load(1);
}

function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !isImeComposing(event)) {
    event.preventDefault();
    runSearch();
  }
}

function isDirectory(entry: DatasetEntry): boolean {
  return entry.kind === "directory" || entry.kind === "dir";
}

onMounted(() => {
  void load(1);
});
</script>

<template>
  <div>
    <PageHeader :title="t('datasets.title')" :subtitle="t('datasets.subtitle')" />

    <div class="mb-4 flex flex-wrap items-center gap-3">
      <select
        v-if="nodes.length"
        class="field-input w-auto min-w-44"
        :aria-label="t('datasets.server')"
        :value="activeNodeId ?? ''"
        @change="selectNode(Number(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.name }}</option>
      </select>

      <div class="relative min-w-60 flex-1 max-w-md">
        <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden="true" />
        <input
          v-model="queryInput"
          type="search"
          class="field-input pl-9"
          :placeholder="t('datasets.searchPlaceholder')"
          @keydown="onSearchKeydown"
        />
      </div>
      <button type="button" class="btn btn-secondary" @click="runSearch">{{ t("app.search") }}</button>
    </div>

    <!-- Unified file browser frame with integrated path bar -->
    <div class="card overflow-hidden">
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle bg-sidebar/60 px-4 py-2.5">
        <nav class="dataset-breadcrumb min-w-0 flex-1" :aria-label="t('datasets.pathLabel')">
          <ol>
            <li>
              <a
                href="#dataset-root"
                class="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium no-underline transition-colors"
                :class="
                  breadcrumbs.length === 0
                    ? 'border-accent/40 bg-accent-soft text-accent'
                    : 'border-border-strong bg-elevated text-ink hover:bg-subtle'
                "
                :aria-current="breadcrumbs.length === 0 ? 'page' : undefined"
                @click.prevent="navigatePath('')"
              >
                <Database class="size-3.5 shrink-0" aria-hidden="true" />
                <span>{{ t("datasets.rootCrumb") }}</span>
              </a>
            </li>
            <li v-for="(crumb, idx) in breadcrumbs" :key="crumb.path">
              <a
                :href="`#${encodeURIComponent(crumb.path)}`"
                class="inline-flex items-center rounded-md border px-2.5 py-1 text-xs font-medium no-underline transition-colors"
                :class="
                  idx === breadcrumbs.length - 1
                    ? 'border-accent/40 bg-accent-soft text-accent'
                    : 'border-border-strong bg-elevated text-ink hover:bg-subtle'
                "
                :aria-current="idx === breadcrumbs.length - 1 ? 'page' : undefined"
                @click.prevent="navigatePath(crumb.path)"
              >
                {{ crumb.name }}
              </a>
            </li>
          </ol>
        </nav>

        <button
          v-if="parentPath !== null"
          type="button"
          class="btn btn-secondary btn-sm shrink-0"
          @click="navigatePath(parentPath)"
        >
          <ArrowUp class="size-3.5" aria-hidden="true" />
          {{ t("datasets.parentDir") }}
        </button>
      </div>

      <div v-if="loading && !items.length" class="p-10 text-center text-sm text-muted">
        {{ t("app.loading") }}
      </div>

      <EmptyState
        v-else-if="!items.length"
        class="border-0 bg-transparent"
        :icon="Database"
        :title="activeQuery ? t('datasets.noMatches') : t('datasets.empty')"
      />

      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t("datasets.name") }}</th>
              <th>{{ t("datasets.type") }}</th>
              <th>{{ t("datasets.size") }}</th>
              <th>{{ t("datasets.modified") }}</th>
              <th class="text-end">{{ t("app.actions") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in items" :key="entry.path">
              <td>
                <button
                  v-if="isDirectory(entry)"
                  type="button"
                  class="inline-flex items-center gap-2 font-medium text-ink hover:text-accent"
                  @click="navigatePath(entry.path)"
                >
                  <Folder class="size-4 shrink-0 text-accent" aria-hidden="true" />
                  <span>{{ entry.name }}</span>
                </button>
                <span v-else class="inline-flex items-center gap-2 text-ink">
                  <Link2 v-if="entry.kind === 'link'" class="size-4 shrink-0 text-muted" aria-hidden="true" />
                  <FileText v-else class="size-4 shrink-0 text-muted" aria-hidden="true" />
                  <span>{{ entry.name }}</span>
                </span>
              </td>
              <td class="text-xs text-muted">
                {{ isDirectory(entry) ? t("datasets.folder") : entry.kind === "link" ? t("datasets.link") : t("datasets.file") }}
              </td>
              <td class="tabular-nums text-xs text-muted">
                {{ entry.kind === "file" ? formatBytes(entry.size_bytes ?? entry.size) : "—" }}
              </td>
              <td class="tabular-nums text-xs text-muted">
                {{ formatDateTime(entry.modified || entry.modified_at, locale.current, auth.timeZone) }}
              </td>
              <td class="text-end">
                <a
                  v-if="entry.kind === 'file' && activeNodeId"
                  :href="api.datasetDownloadUrl(activeNodeId, entry.path)"
                  class="btn btn-secondary btn-sm"
                >
                  <Download class="size-3.5" aria-hidden="true" />
                  {{ t("datasets.download") }}
                </a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <PaginationBar
      v-if="items.length"
      :page="page"
      :page-size="50"
      :count="count"
      :next-page="nextPage"
      @change="load"
    />
  </div>
</template>
