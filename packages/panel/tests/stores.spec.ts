import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useThemeStore } from "@/stores/theme";
import { useAuthStore } from "@/stores/auth";
import { useNotificationsStore } from "@/stores/notifications";
import { api, type UserProfile } from "@/api/endpoints";

function mockMatchMedia(matchesDark: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({
      matches: matchesDark,
      media: "(prefers-color-scheme: dark)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  );
}

const sampleUser: UserProfile = {
  id: 7,
  username: "alice",
  first_name: "Alice",
  email: "alice@example.com",
  is_active: true,
  is_staff: true,
  is_superuser: false,
  group: { id: 1, name: "Lab" },
  home_quota_gib: 50,
  resource_limits: {},
  effective_limits: {
    unlimited: false,
    concurrent_tasks: 2,
    interactive_hours: 24,
    extension_limit: 1,
    approval_mode: "automatic",
  },
  extension_limit: 1,
  approval_mode: "automatic",
  features: { public_datasets: true, synchronization: false, notifications: true },
  pending_approval_count: 3,
  language: "en",
  timezone: "Asia/Shanghai",
};

function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
  };
}

describe("Pinia stores behavior", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.stubGlobal("localStorage", createMemoryStorage());
    document.head.innerHTML = '<meta name="color-scheme" content="light dark">';
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("toggles color theme cleanly and resumes system tracking when matching OS scheme", () => {
    mockMatchMedia(false);
    const theme = useThemeStore();
    theme.init();

    expect(theme.preference).toBe("system");
    expect(theme.resolvedTheme).toBe("light");

    // Flip to dark -> stores explicit override
    theme.toggle();
    expect(theme.preference).toBe("dark");
    expect(theme.resolvedTheme).toBe("dark");
    expect(localStorage.getItem("color-scheme")).toBe("dark");
    expect(document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]')?.content).toBe("dark");

    // Flip back to light (matches OS) -> clears override, resumes system
    theme.toggle();
    expect(theme.preference).toBe("system");
    expect(theme.resolvedTheme).toBe("light");
    expect(localStorage.getItem("color-scheme")).toBeNull();
    expect(document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]')?.content).toBe("light dark");
  });

  it("handles auth bootstrap, sign-in, and sign-out transitions", async () => {
    vi.spyOn(api, "getCsrf").mockResolvedValue("csrf-tok");
    vi.spyOn(api, "getMe").mockRejectedValueOnce(new Error("unauth"));
    vi.spyOn(api, "signIn").mockResolvedValue({ csrf_token: "csrf-2", user: sampleUser });
    vi.spyOn(api, "signOut").mockResolvedValue({ signed_out: true });

    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.initialized).toBe(true);
    expect(auth.isAuthenticated).toBe(false);

    await auth.signIn("alice", "secret");
    expect(auth.isAuthenticated).toBe(true);
    expect(auth.isStaff).toBe(true);
    expect(auth.pendingApprovals).toBe(3);
    expect(auth.features.public_datasets).toBe(true);

    await auth.signOut();
    expect(auth.isAuthenticated).toBe(false);
    expect(auth.user).toBeNull();
  });

  it("dedupes server notifications and removes withdrawn toasts on poll", async () => {
    vi.spyOn(api, "pollReminders")
      .mockResolvedValueOnce({
        unread: 2,
        toasts: [
          {
            id: 42,
            title: "Notice",
            content: "Body",
            link: "/tasks/1",
            sent_at: "2026-01-01T00:00:00Z",
            unread: true,
            withdrawn: false,
            seconds: 6,
          },
        ],
        banners: [],
        remove: [],
      })
      .mockResolvedValueOnce({
        unread: 1,
        toasts: [],
        banners: [],
        remove: [42],
      });

    const notifications = useNotificationsStore();
    await notifications.pollOnce();
    expect(notifications.unreadCount).toBe(2);
    expect(notifications.toasts).toHaveLength(1);

    await notifications.pollOnce();
    expect(notifications.unreadCount).toBe(1);
    expect(notifications.toasts).toHaveLength(0);
  });
});
