import { apiRequest, buildUrl, createIdempotencyKey, setCsrfToken } from "./client";

export interface PageResult<T> {
  items: T[];
  count: number;
  page: number;
  page_size: number;
  next_page: number | null;
}

export interface PlatformFeatures {
  public_datasets: boolean;
  synchronization: boolean;
  notifications: boolean;
}

export interface UserLimits {
  unlimited: boolean;
  concurrent_tasks: number | null;
  interactive_hours: number | null;
  extension_limit: number;
  approval_mode: "manual" | "automatic";
  cpu?: number | null;
  memory_gib?: number | null;
  gpu_compute_percent?: number | null;
  gpu_memory_gib?: number | null;
  gpu_compute_per_card_percent?: number | null;
  gpu_memory_per_card_gib?: number | null;
  workspace_gib?: number | null;
  image_gib?: number | null;
  home_size_gib?: number | null;
  resources?: Record<string, number | string | null>;
  per_allocation?: Record<string, number | string | null>;
}

export interface UserProfile {
  id: number;
  username: string;
  first_name: string;
  email: string;
  is_active: boolean;
  is_staff: boolean;
  is_superuser: boolean;
  group: { id: number; name: string } | null;
  home_quota_gib: number;
  resource_limits: Record<string, unknown>;
  effective_limits: UserLimits;
  extension_limit: number;
  approval_mode: "manual" | "automatic";
  permissions?: Record<string, boolean>;
  usage?: {
    gpu: number;
    cpu: number;
    memory_gib: number;
    active_tasks: number;
    gpu_compute_percent: number;
    gpu_memory_gib: number;
  };
  features?: PlatformFeatures;
  pending_approval_count?: number;
  language?: string;
  default_language?: string;
  timezone?: string;
}

export interface DisplayResource {
  key: string;
  label: string;
  unit: string;
  available: number;
  total: number;
  used?: number;
}

export interface GpuDevice {
  node_id?: number;
  node_name?: string;
  uuid?: string;
  index: number;
  name?: string;
  memory_mib?: number;
  usable_memory_mib?: number;
  ready: boolean;
  used_compute_percent: number;
  available_compute_percent: number;
  available_memory_gib: number;
  used_memory_gib: number;
  memory_total_gib: number;
  device_label: string;
  device_memory_label: string;
}

export interface NodeResourceCard {
  id: number;
  name: string;
  state: string;
  state_label: string;
  model?: string | null;
  gpu_available: number;
  storage_tib?: number;
  device_label: string;
  device_memory_label: string;
  device_details_label: string;
  accelerator_resource_keys: string[];
  display_resources: DisplayResource[];
  gpus: GpuDevice[];
}

export interface ResourcesOverview {
  now: string;
  timezone: string;
  fresh: boolean;
  running_count: number;
  queued_count: number;
  accelerator_counts: { count: number; label: string }[];
  nodes: NodeResourceCard[];
  request_capacity: RequestCapacityData;
  default_image_id: string | null;
}

export interface CapacityResourceRow {
  key: string;
  label: string;
  unit: string;
  available: number;
  total: number;
  quota_available: number | null;
  volume_kind?: string | null;
  allocation_group?: string | null;
  accounting?: string;
}

export interface RequestCapacityData {
  fresh: boolean;
  device_label: string;
  device_memory_label: string;
  device_details_label: string;
  resources: CapacityResourceRow[];
  gpus: {
    node_id: number;
    node_name: string;
    index: number;
    ready: boolean;
    compute_percent: number;
    memory_gib: number;
    memory_total_gib: number;
  }[];
}

export interface TimelineBlock {
  node_id?: number;
  id?: string | null;
  row?: string;
  title: string;
  owner?: string;
  url?: string | null;
  state: "reserved" | "running" | "finished";
  open?: boolean;
  amount?: string;
  start: string | null;
  end: string | null;
  left: number;
  width: number;
  gpu: number;
  cpu: number;
  memory: number;
  device_label?: string;
  gpu_requests?: { compute_percent: number; memory_gib: number }[];
}

export interface TimelineGroup {
  id: number;
  name: string;
  gpu_count: number;
  rows: { id: string; node_id: number; name: string; detail?: string }[];
  blocks: TimelineBlock[];
  entries: TimelineBlock[];
  queued: {
    id: string | null;
    node_id: number;
    title: string;
    url: string | null;
    status: string;
    status_label: string;
    gpu: number;
    cpu: number;
    memory_gib: number;
    device_label?: string;
  }[];
}

export interface TimelineData {
  date: string;
  now: number | null;
  groups: TimelineGroup[];
}

export interface ExtensionItem {
  id: string;
  request_id: string;
  request_purpose?: string;
  username?: string;
  node_id?: number;
  node_name?: string;
  previous_ends_at: string;
  ends_at: string;
  reason: string;
  status: string;
  status_label?: string;
  review_reason?: string;
  reviewed_at?: string | null;
  created_at: string;
}

export interface TaskItem {
  id: string;
  purpose: string;
  mode: "interactive" | "batch";
  status: string;
  status_label: string;
  node_id: number;
  node_name: string;
  node_state?: string;
  node_state_label?: string;
  user_id: number;
  username: string;
  image_id: string | null;
  image_name: string | null;
  source_environment_id?: string | null;
  source_environment_purpose?: string | null;
  source_environment_name?: string | null;
  gpu: number;
  gpu_requests?: { compute_percent: number; memory_gib: number }[];
  gpu_compute_total?: number;
  gpu_memory_total_gib?: number;
  gpu_allocations?: { device_id?: string; index?: number; slot?: number; compute_percent?: number; memory_gib?: number }[];
  cpu: number;
  memory_gib: number;
  workspace_gib: number;
  starts_at: string | null;
  ends_at: string | null;
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  working_dir?: string;
  priority?: number;
  login_user?: string;
  login_name?: string;
  home_dir?: string;
  stop_requested?: boolean;
  exit_code?: number | null;
  error?: string;
  command?: string;
  environment?: Record<string, string>;
  notes?: string;
  review_reason?: string;
  can_stop?: boolean;
  can_save_environment?: boolean;
  can_extend?: boolean;
  can_shorten_time?: boolean;
  can_adjust_time?: boolean;
  extension_count?: number;
  extension_limit?: number;
  extension_hours?: number | null;
  pending_extension?: ExtensionItem | null;
  extension_history?: ExtensionItem[];
  volume?: {
    id: string;
    size_gib: number;
    retain_until: string | null;
    notified_at: string | null;
    state: string;
  } | null;
  connection?: SshConnection | null;
  extensions?: { resources?: Record<string, unknown>; allocations?: Record<string, unknown> };
}

export interface SshConnection {
  host: string | null;
  port: number | null;
  user: string | null;
  alias: string | null;
  command: string | null;
  config: string | null;
}

export interface TaskConnectionData {
  status: string;
  connection: SshConnection | null;
  internal_ssh?: { alias: string; command: string } | null;
  internal_ssh_peers?: { name: string; command: string; reachable: boolean }[];
  administrator_access?: { ready: boolean; sudo: boolean; keys: { id: number; name: string; fingerprint: string }[] } | null;
}

export interface ImageItem {
  id: string;
  name: string;
  owner_id: number | null;
  owner_name: string | null;
  node_id: number;
  node_name: string;
  reference: string;
  digest?: string;
  size_bytes: number;
  architecture: string;
  shared: boolean;
  verified: boolean;
  state: string;
  state_label: string;
  recycle_after: string | null;
  cleanup_error: string;
  created_at: string;
}

export interface BuildItem {
  id: string;
  node_id: number;
  node_name: string;
  name: string;
  context_git: string;
  status: string;
  status_label: string;
  artifact_id: string | null;
  error: string;
  created_at: string;
  dockerfile?: string;
  log?: string;
}

export interface SnapshotItem {
  id: string;
  source_id: string;
  source_name?: string;
  source_purpose?: string;
  node_id?: number;
  node_name?: string;
  status: string;
  status_label?: string;
  artifact_id: string | null;
  artifact_name?: string | null;
  error: string;
  created_at: string;
}

export interface SshKeyItem {
  id: number;
  name: string;
  public_key: string;
  fingerprint: string;
  enabled: boolean;
  created_at?: string;
}

export interface ApiTokenItem {
  id: string;
  name: string;
  masked: string;
  token?: string;
  created_at: string | null;
  expires_at: string | null;
  status: "active" | "expired" | "invalidated";
  current: boolean;
}

export interface DatasetEntry {
  name: string;
  path: string;
  kind: "directory" | "dir" | "file" | "link";
  size?: number | null;
  size_bytes?: number | null;
  modified?: string | null;
  modified_at?: string | null;
  container_path?: string;
}

export interface DatasetPage extends PageResult<DatasetEntry> {
  node: { id: number; name: string };
  nodes: { id: number; name: string }[];
}

export interface SyncJobItem {
  id: string;
  kind: "image" | "dataset";
  source: number;
  target: number;
  source_name?: string;
  target_name?: string;
  image?: string | null;
  image_name?: string | null;
  result_image?: string | null;
  paths?: string[];
  state: string;
  state_label?: string;
  phase: string;
  phase_label?: string;
  transferred_bytes: number;
  total_bytes: number;
  error: string | Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface InboxItem {
  id: number;
  title: string;
  content: string;
  link: string;
  sent_at: string;
  unread: boolean;
  withdrawn: boolean;
  seconds: number | null;
}

export interface RemindersPayload {
  unread: number;
  toasts: InboxItem[];
  banners: InboxItem[];
  remove: number[];
}

export interface ServerNode {
  id: number;
  name: string;
  architecture: "amd64" | "arm64";
  enabled: boolean;
  maintenance: boolean;
  state: string;
  state_label: string;
  last_seen: string | null;
  error: string;
}

export interface GroupItem {
  id: number;
  name: string;
  is_default: boolean;
  unlimited: boolean;
  extension_limit: number;
  approval_mode: "manual" | "automatic";
  home_size_gib?: number | null;
  cpu?: number | null;
  memory_gib?: number | null;
  gpu_compute_percent?: number | null;
  gpu_memory_gib?: number | null;
  gpu_compute_per_card_percent?: number | null;
  gpu_memory_per_card_gib?: number | null;
  workspace_gib?: number | null;
  image_gib?: number | null;
  concurrent_tasks?: number | null;
  interactive_hours?: number | null;
  member_count?: number;
  extensions?: { resources: Record<string, number | string | null>; per_allocation: Record<string, number | string | null> };
}

export interface StoragePool {
  id: number;
  node_id: number;
  node_name: string;
  name: string;
  capacity_gib: number;
  free_gib: number;
  available_gib: number;
  reserved_gib: number;
  healthy: boolean;
  observed_at: string | null;
  percent: number;
}

export interface VolumeItem {
  id: string;
  user_id: number;
  username: string;
  node_id: number;
  node_name: string;
  pool_id: number;
  pool_name: string;
  request_id: string | null;
  request_name: string | null;
  kind: "home" | "work";
  size_gib: number;
  used_bytes: number | null;
  location: string;
  state: string;
  retain_until: string | null;
  notified_at: string | null;
  deletion_error: string;
  created_at: string;
  busy: boolean;
}

export interface AuditEntry {
  id: number;
  actor_id: number | null;
  actor_name: string | null;
  action: string;
  label: string;
  target: string;
  target_label: string;
  details: { label: string; value: string; structured: boolean }[];
  created_at: string;
}

export interface PerformanceNode {
  id: number;
  name: string;
  architecture: string;
  state: string;
  fresh: boolean;
  status_label: string;
  sampled_at: string | null;
  device_label: string;
  device_memory_label: string;
  metrics: {
    sampled_at?: string;
    cpu?: { percent?: number; cores?: number; load?: number[]; temperature_c?: number | null };
    memory?: { total_gib?: number; used_gib?: number; available_gib?: number; percent?: number };
    gpus?: {
      index: number;
      uuid?: string;
      name?: string;
      utilization_percent?: number | null;
      memory_used_mib?: number | null;
      memory_total_mib?: number | null;
      memory_percent?: number | null;
      temperature_c?: number | null;
      power_w?: number | null;
      power_limit_w?: number | null;
      fan_percent?: number | null;
    }[];
    disks?: { name: string; total_gib?: number; used_gib?: number; available_gib?: number; percent?: number }[];
    network?: { name: string; rx_mib_s?: number; tx_mib_s?: number }[];
    errors?: string[];
  };
}

export interface PerformanceData {
  refresh_seconds: number;
  nodes: PerformanceNode[];
  archive?: {
    used_mib: number;
    max_mib: number;
    state: string;
    last_at: string | null;
  };
  archive_notice?: string | null;
}

export interface PerformanceHistoryData {
  history: {
    timestamps: number[];
    step: number;
    label: string;
    unit: string;
    days: number;
    has_data: boolean;
    zone: string;
    coverage_note: string | null;
    nodes: {
      id: number;
      name: string;
      color: number;
      mean: number | null;
      peak: number | null;
      samples: number;
      points: [number | null, number | null, number | null, number][];
    }[];
  };
  history_nodes: { id: number; name: string; selected: boolean }[];
  history_ranges: number[];
  history_metric: string;
  history_metrics: Record<string, string>;
}

export interface UsageLiveData {
  as_of: string;
  online: number;
  node_count: number;
  sampled: number;
  running_count: number;
  cpu: number | null;
  memory: number | null;
  capacity: Record<string, { available: number; allocated: number; percent: number | null; actual: number | null }>;
  nodes: {
    id: number;
    name: string;
    state: string;
    sampled_at: string | null;
    cpu: number | null;
    memory: number | null;
    available: number;
    allocated: number;
    kind: string;
    running: { id: string; purpose: string; name: string; username: string; started_at: string | null }[];
  }[];
  models: { name: string; kind: string; available: number; allocated: number; actual: number | null; samples: number[] }[];
}

export interface UsageRow {
  label: string;
  username?: string;
  kind?: string;
  applications: number;
  tasks: number;
  hours: number;
  gpu_hours: number;
  npu_hours: number;
}

export interface UsageHistoryData {
  as_of: string;
  first: string;
  total_accounts: number;
  applicants: number;
  totals: UsageRow;
  people: UsageRow[];
  by_node: UsageRow[];
  by_model: UsageRow[];
  filters: Record<string, string>;
  account_options: { id: string; label: string }[];
  node_options: { id: string; label: string }[];
  model_options: { id: string; label: string }[];
  trend: { dates: string[]; series: { name: string; kind: string; values: number[] }[] };
  ranking: { name: string; gpu: number; npu: number }[];
}

export const api = {
  // Auth & session
  async getCsrf() {
    const res = await apiRequest<{ csrf_token: string }>("/auth/csrf", { skipAuthRedirect: true });
    if (res.csrf_token) setCsrfToken(res.csrf_token);
    return res.csrf_token;
  },
  async signIn(username: string, password: string) {
    const res = await apiRequest<{ csrf_token: string; user: UserProfile }>("/auth/session", {
      method: "POST",
      body: { username, password },
      skipAuthRedirect: true,
    });
    if (res.csrf_token) setCsrfToken(res.csrf_token);
    return res;
  },
  async signOut() {
    return apiRequest<{ signed_out: boolean }>("/auth/session", {
      method: "DELETE",
      skipAuthRedirect: true,
    });
  },
  getMe(skipAuthRedirect = false) {
    return apiRequest<UserProfile>("/me", { skipAuthRedirect });
  },
  changePassword(payload: { old_password: string; new_password1: string; new_password2: string }) {
    return apiRequest<Record<string, unknown>>("/me/password", { method: "POST", body: payload });
  },
  setLanguage(language: "en" | "zh-hans" | "") {
    return apiRequest<{ language: string }>("/me/language", { method: "PATCH", body: { language } });
  },
  getUserNotificationChannels() {
    return apiRequest<{ channels: Record<string, unknown>[] }>("/me/notifications");
  },
  saveUserNotificationChannel(channel: string, values: Record<string, string>) {
    return apiRequest<Record<string, unknown>>("/me/notifications", { method: "PATCH", body: { channel, values } });
  },

  // Overview & Timeline
  getResources(node?: number | null) {
    return apiRequest<ResourcesOverview>("/resources", { query: { node } });
  },
  getTimeline(date?: string, node?: number | null, signal?: AbortSignal) {
    return apiRequest<TimelineData>("/timeline", { query: { date, node }, signal });
  },

  // Tasks & Request Options
  getRequestOptions(query: { node?: number | string | null; mode?: "interactive" | "batch"; image?: string; source?: string }) {
    return apiRequest<Record<string, unknown>>("/request-options", { query });
  },
  listTasks(query: { scope?: "own" | "all"; mode?: string; status?: string; node?: number | string; page?: number; page_size?: number }) {
    return apiRequest<PageResult<TaskItem>>("/tasks", { query });
  },
  createTask(payload: Record<string, unknown>, idempotencyKey = createIdempotencyKey()) {
    return apiRequest<TaskItem>("/tasks", { method: "POST", body: payload, idempotencyKey });
  },
  getTask(id: string) {
    return apiRequest<TaskItem>(`/tasks/${id}`);
  },
  getTaskConnection(id: string) {
    return apiRequest<TaskConnectionData>(`/tasks/${id}/connection`);
  },
  getTaskLogs(id: string, tail = 200) {
    return apiRequest<{ text: string; available: boolean; workspace_log_directory: string }>(`/tasks/${id}/logs`, {
      query: { tail },
    });
  },
  stopTask(id: string) {
    return apiRequest<TaskItem>(`/tasks/${id}/stop`, {
      method: "POST",
      body: {},
      idempotencyKey: createIdempotencyKey(),
    });
  },
  saveEnvironment(id: string) {
    return apiRequest<SnapshotItem>(`/tasks/${id}/save`, {
      method: "POST",
      body: {},
      idempotencyKey: createIdempotencyKey(),
    });
  },
  requestExtension(id: string, payload: { ends_at: string; reason: string }) {
    return apiRequest<ExtensionItem>(`/tasks/${id}/extensions`, {
      method: "POST",
      body: payload,
      idempotencyKey: createIdempotencyKey(),
    });
  },
  shortenTask(id: string, ends_at: string) {
    return apiRequest<TaskItem>(`/tasks/${id}/shorten`, {
      method: "POST",
      body: { ends_at },
      idempotencyKey: createIdempotencyKey(),
    });
  },
  reviewTask(id: string, payload: { action: "approve" | "reject" | "stop" | "extend" | "adjust_time"; reason?: string; priority?: 0 | 100; ends_at?: string }) {
    return apiRequest<TaskItem>(`/tasks/${id}/review`, {
      method: "POST",
      body: payload,
      idempotencyKey: createIdempotencyKey(),
    });
  },
  reviewExtension(id: string, payload: { action: "approve" | "reject"; reason?: string }) {
    return apiRequest<ExtensionItem>(`/extensions/${id}/review`, {
      method: "POST",
      body: payload,
      idempotencyKey: createIdempotencyKey(),
    });
  },

  // Images, Builds & Snapshots
  listImages(query: { kind?: "permanent"; node?: number | string; available?: boolean; page?: number; page_size?: number }) {
    return apiRequest<PageResult<ImageItem>>("/images", { query });
  },
  deleteImage(id: string) {
    return apiRequest<ImageItem>(`/images/${id}`, {
      method: "DELETE",
      idempotencyKey: createIdempotencyKey(),
    });
  },
  getBuildOptions() {
    return apiRequest<{ nodes: ServerNode[]; initial: { node: number | null } }>("/builds/options");
  },
  listBuilds(page = 1, page_size = 10) {
    return apiRequest<PageResult<BuildItem>>("/builds", { query: { page, page_size } });
  },
  getBuild(id: string) {
    return apiRequest<BuildItem>(`/builds/${id}`);
  },
  createBuild(payload: { node?: number; name: string; dockerfile: string; context_git?: string }) {
    return apiRequest<BuildItem>("/builds", {
      method: "POST",
      body: payload,
      idempotencyKey: createIdempotencyKey(),
    });
  },
  listSnapshots(page = 1, page_size = 10) {
    return apiRequest<PageResult<SnapshotItem>>("/snapshots", { query: { page, page_size } });
  },

  // Datasets & Sync
  listDatasets(query: { node?: number | string; path?: string; q?: string; page?: number; page_size?: number }) {
    return apiRequest<DatasetPage>("/datasets", { query });
  },
  datasetDownloadUrl(node: number | string, path: string) {
    return buildUrl("/datasets/download", { node, path });
  },
  getSyncOptions() {
    return apiRequest<{
      nodes: { id: number; name: string; architecture: string }[];
      images: { id: string; name: string; node_id: number; architecture: string; shared: boolean }[];
      can_sync_images: boolean;
      can_sync_datasets: boolean;
    }>("/syncs/options");
  },
  listSyncJobs(page = 1) {
    return apiRequest<PageResult<SyncJobItem>>("/syncs", { query: { page } });
  },
  syncImage(imageId: string, payload: { target: number; bandwidth?: number }) {
    return apiRequest<SyncJobItem>(`/images/${imageId}/sync`, {
      method: "POST",
      body: payload,
      idempotencyKey: createIdempotencyKey(),
    });
  },
  syncDatasets(payload: { source: number; target: number; paths?: string[]; bandwidth?: number }) {
    return apiRequest<SyncJobItem>("/admin/datasets/sync", {
      method: "POST",
      body: payload,
      idempotencyKey: createIdempotencyKey(),
    });
  },

  // SSH Keys & API Tokens
  listKeys(page = 1, page_size = 50) {
    return apiRequest<PageResult<SshKeyItem>>("/keys", { query: { page, page_size } });
  },
  addKey(payload: { name: string; public_key: string }) {
    return apiRequest<SshKeyItem>("/keys", {
      method: "POST",
      body: payload,
      idempotencyKey: createIdempotencyKey(),
    });
  },
  updateKey(id: number, payload: { name?: string; enabled?: boolean }) {
    return apiRequest<SshKeyItem>(`/keys/${id}`, { method: "PATCH", body: payload });
  },
  disableKey(id: number) {
    return apiRequest<SshKeyItem>(`/keys/${id}`, { method: "DELETE" });
  },
  listTokens(page = 1, page_size = 50) {
    return apiRequest<PageResult<ApiTokenItem>>("/auth/tokens", { query: { page, page_size } });
  },
  createToken(payload: { name?: string; expires_in?: number }) {
    return apiRequest<ApiTokenItem & { token: string }>("/auth/tokens", { method: "POST", body: payload });
  },
  revealToken(id: string) {
    return apiRequest<ApiTokenItem & { token: string }>(`/auth/tokens/${id}`);
  },
  revokeToken(id: string) {
    return apiRequest<{ revoked: boolean }>(`/auth/tokens/${id}`, { method: "DELETE" });
  },

  // Inbox & Reminders
  listInbox(page = 1) {
    return apiRequest<PageResult<InboxItem>>("/inbox", { query: { page } });
  },
  getInboxItem(id: number) {
    return apiRequest<InboxItem>(`/inbox/${id}`);
  },
  markInboxRead(id: number) {
    return apiRequest<InboxItem>(`/inbox/${id}/read`, { method: "POST", body: {} });
  },
  dismissBanner(id: number) {
    return apiRequest<{ dismissed: boolean }>(`/inbox/${id}/dismiss`, { method: "POST", body: {} });
  },
  pollReminders(shown: number[]) {
    return apiRequest<RemindersPayload>("/inbox/reminders", { method: "POST", body: { shown } });
  },

  // Admin endpoints
  listApprovals(kind: "tasks" | "extensions", page = 1) {
    return apiRequest<PageResult<TaskItem | ExtensionItem>>("/admin/approvals", { query: { kind, page, page_size: 50 } });
  },
  listNodes(page = 1, page_size = 100) {
    return apiRequest<PageResult<ServerNode>>("/nodes", { query: { page, page_size } });
  },
  getNodeOptions(node_id?: number | null, transport?: string) {
    return apiRequest<{
      initial: Record<string, unknown>;
      modules: { name: string; schema: Record<string, unknown> }[];
      editing: boolean;
      local: boolean;
    }>("/admin/nodes/options", { query: { node_id, transport } });
  },
  createNode(payload: Record<string, unknown>) {
    return apiRequest<ServerNode>("/nodes", { method: "POST", body: payload, idempotencyKey: createIdempotencyKey() });
  },
  updateNode(id: number, payload: Record<string, unknown>) {
    return apiRequest<ServerNode>(`/nodes/${id}`, { method: "PATCH", body: payload });
  },
  getPerformance(node?: number | string | null, signal?: AbortSignal) {
    return apiRequest<PerformanceData>("/admin/performance", { query: { node }, signal });
  },
  getPerformanceHistory(query: { nodes?: string[]; days?: number; metric?: string }, signal?: AbortSignal) {
    return apiRequest<PerformanceHistoryData>("/admin/performance/history", { query, signal });
  },
  getUsageLive(signal?: AbortSignal) {
    return apiRequest<UsageLiveData>("/admin/usage/live", { signal });
  },
  getUsageHistory(query: { from?: string; to?: string; account?: string; node?: string; model?: string }, signal?: AbortSignal) {
    return apiRequest<UsageHistoryData>("/admin/usage/history", { query, signal });
  },
  listContainers(query: { node?: number | string; q?: string; mode?: string; status?: string; page?: number; page_size?: number }) {
    return apiRequest<PageResult<TaskItem>>("/admin/containers", { query });
  },
  getStorage(query: { node?: number | string; page?: number; page_size?: number }) {
    return apiRequest<{ pools: StoragePool[]; volumes: PageResult<VolumeItem> }>("/admin/storage", { query });
  },
  deleteVolume(id: string) {
    return apiRequest<VolumeItem>(`/volumes/${id}`, { method: "DELETE", idempotencyKey: createIdempotencyKey() });
  },
  listManagedImages(query: { node?: number | string; page?: number; page_size?: number }) {
    return apiRequest<PageResult<ImageItem> & { summary?: Record<string, unknown> }>("/admin/images", { query });
  },
  listMembers(query: { q?: string; page?: number; page_size?: number }) {
    return apiRequest<PageResult<UserProfile>>("/admin/members", { query });
  },
  getMemberOptions(member_id?: number | null) {
    return apiRequest<Record<string, unknown>>("/admin/members/options", { query: { member_id } });
  },
  createMember(payload: Record<string, unknown>) {
    return apiRequest<UserProfile>("/admin/members", { method: "POST", body: payload, idempotencyKey: createIdempotencyKey() });
  },
  updateMember(id: number, payload: Record<string, unknown>) {
    return apiRequest<UserProfile>(`/admin/members/${id}`, { method: "PATCH", body: payload });
  },
  listGroups(page = 1, page_size = 100) {
    return apiRequest<PageResult<GroupItem>>("/admin/groups", { query: { page, page_size } });
  },
  getGroupOptions(group_id?: number | null) {
    return apiRequest<Record<string, unknown>>("/admin/groups/options", { query: { group_id } });
  },
  createGroup(payload: Record<string, unknown>) {
    return apiRequest<GroupItem>("/admin/groups", { method: "POST", body: payload, idempotencyKey: createIdempotencyKey() });
  },
  updateGroup(id: number, payload: Record<string, unknown>) {
    return apiRequest<GroupItem>(`/admin/groups/${id}`, { method: "PATCH", body: payload });
  },
  deleteGroup(id: number, destination?: number | null) {
    return apiRequest<Record<string, unknown>>(`/admin/groups/${id}`, {
      method: "DELETE",
      body: destination ? { destination } : {},
    });
  },
  getNoticeComposer() {
    return apiRequest<{
      available: boolean;
      recipient_counts: { all: number; groups: Record<string, number> };
      members: [number, string][];
      groups: [number, string][];
      audiences: [string, string][];
      presentations: [string, string][];
    }>("/admin/notices/composer");
  },
  sendNotice(payload: Record<string, unknown>) {
    return apiRequest<{ id: number; recipients: number }>("/admin/notices", {
      method: "POST",
      body: payload,
      idempotencyKey: createIdempotencyKey(),
    });
  },
  listNoticeHistory(query: { origin?: string; page?: number; page_size?: number }) {
    return apiRequest<
      PageResult<{
        id: number;
        title: string;
        content: string;
        manual: boolean;
        state: string;
        created_at: string;
        sender: string | null;
        audience: string;
        channel: string;
        count: number;
        delivered: number;
        read: number;
        show_read: boolean;
        can_cancel: boolean;
        can_revoke: boolean;
        changed_by: string | null;
        changed_at: string | null;
      }>
    >("/admin/notices/history", { query });
  },
  stopNotice(id: number, action: "cancel" | "withdraw") {
    return apiRequest<{ stopped: boolean; action: string }>(`/admin/notices/${id}/stop`, {
      method: "POST",
      body: { action },
    });
  },
  getNotificationSettings() {
    return apiRequest<{
      enabled: boolean;
      inapp_enabled: boolean;
      wechat_available: boolean;
      inapp_events: string[];
      events: string[];
      available_events: Record<string, string>;
      reminder_minutes: number;
      channels: {
        channel: string;
        label: string;
        fields: Record<string, { label: string; required?: boolean; secret?: boolean; help?: string }>;
        values: Record<string, string>;
      }[];
    }>("/admin/notifications");
  },
  updateNotificationSettings(payload: Record<string, unknown>) {
    return apiRequest<Record<string, unknown>>("/admin/notifications", { method: "PATCH", body: payload });
  },
  getRetentionSettings() {
    return apiRequest<{
      workspace_retention_days: number;
      snapshot_hours: number;
      failed_snapshot_hours: number;
      public_host: string;
      ssh_ports: [number, number];
      timezone: string;
    }>("/admin/settings");
  },
  updateRetentionSettings(payload: { workspace_retention_days: number; snapshot_hours: number; failed_snapshot_hours: number }) {
    return apiRequest<Record<string, unknown>>("/admin/settings", { method: "PATCH", body: payload });
  },
  listAudits(query: { action?: string; page?: number; page_size?: number }) {
    return apiRequest<PageResult<AuditEntry>>("/admin/audits", { query });
  },
};
