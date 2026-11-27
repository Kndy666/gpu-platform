import { defineStore } from "pinia";
import { ref } from "vue";
import { api, type InboxItem } from "@/api/endpoints";

export interface ToastNotice {
  id: string;
  serverId?: number;
  level: "info" | "success" | "error";
  title: string;
  content?: string;
  link?: string;
  seconds: number;
}

export const useNotificationsStore = defineStore("notifications", () => {
  const unreadCount = ref(0);
  const banner = ref<InboxItem | null>(null);
  const toasts = ref<ToastNotice[]>([]);
  const liveAnnouncement = ref("");
  let pollingTimer: number | null = null;
  let fetching = false;

  function hasOpenModal(): boolean {
    return Boolean(document.querySelector("dialog:modal"));
  }

  function pushToast(notice: Omit<ToastNotice, "id"> & { id?: string }): void {
    if (hasOpenModal() && notice.level !== "error") {
      return;
    }
    const id = notice.id ?? `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    if (toasts.value.some((item) => item.id === id)) return;
    toasts.value.push({ ...notice, id });
    liveAnnouncement.value = notice.content ? `${notice.title}. ${notice.content}` : notice.title;
  }

  function dismissToast(id: string): void {
    toasts.value = toasts.value.filter((item) => item.id !== id);
  }

  async function dismissBanner(): Promise<void> {
    const active = banner.value;
    if (!active) return;
    banner.value = null;
    await api.dismissBanner(active.id).catch(() => undefined);
  }

  async function pollOnce(): Promise<void> {
    if (fetching || document.visibilityState === "hidden") return;
    fetching = true;
    try {
      const shownIds = toasts.value
        .map((item) => item.serverId)
        .filter((id): id is number => typeof id === "number");
      const updates = await api.pollReminders(shownIds);
      unreadCount.value = updates.unread;
      if (updates.remove.length) {
        const removeSet = new Set(updates.remove);
        toasts.value = toasts.value.filter((item) => !item.serverId || !removeSet.has(item.serverId));
      }
      for (const item of updates.toasts) {
        pushToast({
          id: `server-${item.id}`,
          serverId: item.id,
          level: "info",
          title: item.title,
          content: item.content,
          link: item.link || undefined,
          seconds: item.seconds ?? 6,
        });
      }
      banner.value = updates.banners[0] ?? null;
    } catch {
      // Retry silently on next poll cycle
    } finally {
      fetching = false;
    }
  }

  function startPolling(): void {
    stopPolling();
    void pollOnce();
    pollingTimer = window.setInterval(() => void pollOnce(), 15000);
    document.addEventListener("visibilitychange", handleVisibility);
  }

  function handleVisibility(): void {
    if (document.visibilityState === "visible") {
      void pollOnce();
    }
  }

  function stopPolling(): void {
    if (pollingTimer !== null) {
      window.clearInterval(pollingTimer);
      pollingTimer = null;
    }
    document.removeEventListener("visibilitychange", handleVisibility);
  }

  function reset(): void {
    stopPolling();
    unreadCount.value = 0;
    banner.value = null;
    toasts.value = [];
  }

  return {
    unreadCount,
    banner,
    toasts,
    liveAnnouncement,
    pushToast,
    dismissToast,
    dismissBanner,
    pollOnce,
    startPolling,
    stopPolling,
    reset,
  };
});
