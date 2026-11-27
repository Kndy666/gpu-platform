import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";
import { i18n } from "@/i18n";
import { api, type UserProfile } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import LoginView from "@/views/LoginView.vue";
import NewRequestView from "@/views/NewRequestView.vue";
import AccountView from "@/views/AccountView.vue";
import DatasetsView from "@/views/DatasetsView.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import QuotaFields from "@/components/domain/QuotaFields.vue";

const userFixture: UserProfile = {
  id: 1,
  username: "researcher",
  first_name: "Researcher",
  email: "r@example.com",
  is_active: true,
  is_staff: false,
  is_superuser: false,
  group: { id: 1, name: "Members" },
  home_quota_gib: 50,
  resource_limits: {},
  effective_limits: {
    unlimited: false,
    concurrent_tasks: 2,
    interactive_hours: 12,
    extension_limit: 1,
    approval_mode: "automatic",
  },
  extension_limit: 1,
  approval_mode: "automatic",
  features: { public_datasets: false, synchronization: false, notifications: false },
  pending_approval_count: 0,
  language: "en",
  timezone: "Asia/Shanghai",
};

describe("View behavioral contracts", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        media: "",
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    );
  });

  it("LoginView authenticates and redirects to target route, or surfaces API errors", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/login", component: LoginView },
        { path: "/environments", component: { template: "<div>envs</div>" } },
      ],
    });
    await router.push("/login?next=/environments");
    await router.isReady();

    vi.spyOn(api, "getCsrf").mockResolvedValue("csrf-ok");
    const signInSpy = vi
      .spyOn(api, "signIn")
      .mockRejectedValueOnce(new ApiError(401, "invalid_credentials", "Bad credentials"))
      .mockResolvedValueOnce({ csrf_token: "csrf-2", user: userFixture });

    const wrapper = mount(LoginView, {
      global: { plugins: [router, i18n] },
    });

    await wrapper.find("#login-username").setValue("researcher");
    await wrapper.find("#login-password").setValue("wrong");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
    expect(router.currentRoute.value.path).toBe("/login");

    await wrapper.find("#login-password").setValue("correct");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(signInSpy).toHaveBeenCalledTimes(2);
    expect(router.currentRoute.value.path).toBe("/environments");
  });

  it("NewRequestView preserves purpose across server switches and submits slot extensions cleanly", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/environments/new", component: NewRequestView, props: { mode: "interactive" } },
        { path: "/tasks/:id", component: { template: "<div>detail</div>" } },
      ],
    });
    await router.push("/environments/new");
    await router.isReady();

    const auth = useAuthStore();
    auth.user = userFixture;
    auth.initialized = true;

    const makeOptions = (nodeId: number, name: string) => ({
      node: { id: nodeId, name, state: "online" },
      nodes: [
        { id: 1, name: "gpu-01", state: "online" },
        { id: 2, name: "gpu-02", state: "online" },
      ],
      images: [{ id: `img-${nodeId}`, name: `PyTorch-${nodeId}`, shared: true }],
      environments: [],
      source_workspaces: {},
      resources: [
        { key: "cpu", label_text: "CPU", unit: "cores", accounting: "task", requestable: true, default: 4, minimum: 1, maximum: 32, integral: true },
        { key: "memory", label_text: "RAM", unit: "GiB", accounting: "task", requestable: true, default: 16, minimum: 1, maximum: 128, integral: true },
        { key: "gpu_compute", label_text: "Compute", unit: "percent", accounting: "task", requestable: true, allocation_group: "gpu", allocation_field: "compute_percent", default: 100, minimum: 1, maximum: 100, integral: true, limit: 100, per_allocation_limit: 100 },
        { key: "gpu_memory", label_text: "VRAM", unit: "GiB", accounting: "task", requestable: true, allocation_group: "gpu", allocation_field: "memory_gib", default: 16, minimum: 1, maximum: 48, integral: true, limit: 48, per_allocation_limit: 48 },
      ],
      defaults: {
        node: nodeId,
        image: `img-${nodeId}`,
        starts_at: "2026-11-27T10:00:00+08:00",
        ends_at: "2026-11-27T14:00:00+08:00",
        working_dir: "/workspace",
        volume_sizes: { home: 50, work: 20 },
        resource_quantities: { cpu: "4", memory: "16" },
        resource_allocations: {},
      },
      capacity: {
        fresh: true,
        device_label: "GPU",
        device_memory_label: "VRAM",
        device_details_label: "GPUs",
        resources: [],
        gpus: [{ node_id: nodeId, node_name: name, index: 0, ready: true, compute_percent: 100, memory_gib: 48, memory_total_gib: 48 }],
      },
    });

    vi.spyOn(api, "getRequestOptions")
      .mockResolvedValueOnce(makeOptions(1, "gpu-01"))
      .mockResolvedValueOnce(makeOptions(2, "gpu-02"));
    vi.spyOn(api, "getMe").mockResolvedValue(userFixture);
    const createSpy = vi.spyOn(api, "createTask").mockResolvedValue({ id: "task-99" } as never);

    const wrapper = mount(NewRequestView, {
      props: { mode: "interactive" },
      global: { plugins: [router, i18n] },
    });
    await flushPromises();

    await wrapper.find("#req-purpose").setValue("ResNet training");
    await wrapper.find("#req-server").setValue("2");
    await flushPromises();

    // Purpose survives server change, while image updates to node 2
    expect((wrapper.find("#req-purpose").element as HTMLInputElement).value).toBe("ResNet training");
    expect((wrapper.find("#req-image").element as HTMLSelectElement).value).toBe("img-2");

    // Submit form
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(createSpy).toHaveBeenCalledTimes(1);
    const submittedPayload = createSpy.mock.calls[0][0] as Record<string, unknown>;
    expect(submittedPayload.node).toBe(2);
    expect(submittedPayload.purpose).toBe("ResNet training");
    expect("gpu" in submittedPayload).toBe(false);
    expect(router.currentRoute.value.path).toBe("/tasks/task-99");
  });

  it("ConfirmDialog uses native dialog showModal and emits confirm/cancel", async () => {
    const showModal = vi.fn();
    const close = vi.fn();
    HTMLDialogElement.prototype.showModal = showModal;
    HTMLDialogElement.prototype.close = close;

    const wrapper = mount(ConfirmDialog, {
      props: {
        open: false,
        title: "Confirm action",
        message: "Proceed?",
        danger: true,
      },
      global: { plugins: [i18n] },
    });

    await wrapper.setProps({ open: true });
    expect(showModal).toHaveBeenCalledTimes(1);

    const buttons = wrapper.findAll("footer button");
    await buttons[1].trigger("click");
    expect(wrapper.emitted("confirm")).toHaveLength(1);

    await buttons[0].trigger("click");
    expect(wrapper.emitted("update:open")?.[0]).toEqual([false]);
  });

  it("AccountView renders SSH key management and submits new keys", async () => {
    const auth = useAuthStore();
    auth.user = userFixture;
    auth.initialized = true;

    vi.spyOn(api, "listKeys").mockResolvedValue({ items: [], count: 0, page: 1, page_size: 20, next_page: null });
    vi.spyOn(api, "listTokens").mockResolvedValue({ items: [], count: 0, page: 1, page_size: 20, next_page: null });
    vi.spyOn(api, "getUserNotificationChannels").mockResolvedValue({ channels: [] });
    const addKeySpy = vi.spyOn(api, "addKey").mockResolvedValue({
      id: 10,
      name: "Workstation",
      public_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA demo@host",
      fingerprint: "SHA256:demo",
      enabled: true,
    });

    const wrapper = mount(AccountView, {
      global: { plugins: [i18n] },
    });
    await flushPromises();

    await wrapper.find("#ssh-name").setValue("Workstation");
    await wrapper.find("#ssh-pub").setValue("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA demo@host");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(addKeySpy).toHaveBeenCalledWith({
      name: "Workstation",
      public_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA demo@host",
    });
  });

  it("DatasetsView opens directory entries returned by the API", async () => {
    const auth = useAuthStore();
    auth.user = userFixture;
    auth.initialized = true;

    const listSpy = vi
      .spyOn(api, "listDatasets")
      .mockResolvedValueOnce({
        node: { id: 1, name: "main" },
        nodes: [{ id: 1, name: "main" }],
        items: [{ name: "Qwen", path: "Qwen", kind: "directory", modified: "2026-11-20T08:00:00Z" }],
        count: 1,
        page: 1,
        page_size: 50,
        next_page: null,
      })
      .mockResolvedValueOnce({
        node: { id: 1, name: "main" },
        nodes: [{ id: 1, name: "main" }],
        items: [{ name: "config.json", path: "Qwen/config.json", kind: "file", size_bytes: 1024, modified: "2026-11-20T08:00:00Z" }],
        count: 1,
        page: 1,
        page_size: 50,
        next_page: null,
      });

    const wrapper = mount(DatasetsView, {
      global: { plugins: [i18n] },
    });
    await flushPromises();

    const dirButton = wrapper.find("tbody button");
    expect(dirButton.exists()).toBe(true);
    await dirButton.trigger("click");
    await flushPromises();

    expect(listSpy).toHaveBeenLastCalledWith(expect.objectContaining({ node: 1, path: "Qwen" }));
    expect(wrapper.find('tbody a[href*="/datasets/download"]').exists()).toBe(true);
  });

  it("QuotaFields normalizes decimal quota values for display and emits numeric edits", async () => {
    const wrapper = mount(QuotaFields, {
      props: {
        idPrefix: "grp",
        resources: [
          { key: "cpu", label_text: "CPU", unit: "cores", integral: true },
          { key: "gpu_memory", label_text: "VRAM", unit: "GiB", allocation_group: "gpu", integral: false },
        ],
        modelValue: {
          unlimited: false,
          concurrent_tasks: 1,
          interactive_hours: 24,
          extension_limit: 1,
          approval_mode: "automatic",
          home_size_gib: 50,
          resource_limits: { cpu: "8.0000000000", gpu_memory: "24.5000000000" },
          per_allocation_limits: { gpu_memory: "12.0000000000" },
        },
      },
      global: { plugins: [i18n] },
    });

    expect((wrapper.find("#grp-res-cpu").element as HTMLInputElement).value).toBe("8");
    expect((wrapper.find("#grp-res-gpu_memory").element as HTMLInputElement).value).toBe("24.5");
    expect((wrapper.find("#grp-alloc-gpu_memory").element as HTMLInputElement).value).toBe("12");

    await wrapper.find("#grp-res-cpu").setValue("16");
    const emitted = wrapper.emitted("update:modelValue")?.[0]?.[0] as { resource_limits: Record<string, unknown> };
    expect(emitted.resource_limits.cpu).toBe(16);
  });
});
