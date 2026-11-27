import { onMounted, onBeforeUnmount } from "vue";

/**
 * Silent background auto-refresh helper.
 * Pauses automatically while the browser tab is hidden, prevents overlapping
 * requests, and triggers a fresh load when the user returns to the tab.
 */
export function useAutoRefresh(
  fn: () => Promise<void> | void,
  intervalMs: number | (() => number) = 15000,
) {
  let timer: number | null = null;
  let running = false;
  let stopped = false;

  function resolveInterval(): number {
    const ms = typeof intervalMs === "function" ? intervalMs() : intervalMs;
    return Math.max(2000, ms);
  }

  async function runOnce() {
    if (stopped || running || document.visibilityState !== "visible") return;
    running = true;
    try {
      await fn();
    } finally {
      running = false;
      scheduleNext();
    }
  }

  function scheduleNext() {
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
    if (stopped || document.visibilityState !== "visible") return;
    timer = window.setTimeout(() => {
      void runOnce();
    }, resolveInterval());
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "visible") {
      void runOnce();
    } else if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  onMounted(() => {
    scheduleNext();
    document.addEventListener("visibilitychange", handleVisibilityChange);
  });

  onBeforeUnmount(() => {
    stopped = true;
    if (timer !== null) window.clearTimeout(timer);
    document.removeEventListener("visibilitychange", handleVisibilityChange);
  });
}
