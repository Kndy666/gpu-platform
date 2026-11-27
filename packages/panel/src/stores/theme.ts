import { defineStore } from "pinia";
import { ref, computed } from "vue";

export type ThemePreference = "system" | "light" | "dark";

const STORAGE_KEY = "color-scheme";

function readStored(): ThemePreference {
  try {
    const val = localStorage.getItem(STORAGE_KEY);
    if (val === "light" || val === "dark") return val;
  } catch {
    // Storage disabled
  }
  return "system";
}

function applyMeta(pref: ThemePreference): void {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]');
  const value = pref === "system" ? "light dark" : pref;
  if (meta) {
    meta.content = value;
  }
}

export const useThemeStore = defineStore("theme", () => {
  const preference = ref<ThemePreference>(readStored());
  const media = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const systemDark = ref<boolean>(media?.matches ?? false);

  const resolvedTheme = computed<"light" | "dark">(() => {
    if (preference.value === "system") return systemDark.value ? "dark" : "light";
    return preference.value;
  });

  function init(): void {
    applyMeta(preference.value);
    media?.addEventListener("change", (event) => {
      systemDark.value = event.matches;
    });
    window.addEventListener("storage", (event) => {
      if (event.key === STORAGE_KEY) {
        preference.value = readStored();
        applyMeta(preference.value);
      }
    });
  }

  function setPreference(next: ThemePreference): void {
    preference.value = next;
    applyMeta(next);
    try {
      if (next === "system") {
        localStorage.removeItem(STORAGE_KEY);
      } else {
        localStorage.setItem(STORAGE_KEY, next);
      }
    } catch {
      // Ignore storage failure
    }
  }

  /**
   * Two-state toggle following modern-web-guidance `dark-mode-toggle`:
   * Switches to the opposite of the currently rendered scheme; if that matches
   * the current OS preference, removes the stored override so system tracking resumes.
   */
  function toggle(): void {
    const target: "light" | "dark" = resolvedTheme.value === "dark" ? "light" : "dark";
    const system: "light" | "dark" = systemDark.value ? "dark" : "light";
    setPreference(target === system ? "system" : target);
  }

  return {
    preference,
    systemDark,
    resolvedTheme,
    init,
    setPreference,
    toggle,
  };
});
