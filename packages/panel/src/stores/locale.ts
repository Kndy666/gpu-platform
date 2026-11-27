import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "@/api/endpoints";
import { setApiLocale } from "@/api/client";
import { i18n, detectInitialLocale, type SupportedLocale } from "@/i18n";

const STORAGE_KEY = "gpu-platform-locale";

export const useLocaleStore = defineStore("locale", () => {
  const current = ref<SupportedLocale>(detectInitialLocale());

  function apply(locale: SupportedLocale): void {
    current.value = locale;
    i18n.global.locale.value = locale;
    setApiLocale(locale);
    document.documentElement.lang = locale === "zh-hans" ? "zh-CN" : "en";
    try {
      localStorage.setItem(STORAGE_KEY, locale);
    } catch {
      // Ignore
    }
  }

  function syncFromUser(userLanguage?: string, defaultLanguage?: string): void {
    if (userLanguage === "en" || userLanguage === "zh-hans") {
      apply(userLanguage);
      return;
    }
    const stored = (() => {
      try {
        return localStorage.getItem(STORAGE_KEY);
      } catch {
        return null;
      }
    })();
    if (stored === "en" || stored === "zh-hans") {
      apply(stored);
      return;
    }
    if (defaultLanguage === "en" || defaultLanguage === "zh-hans") {
      apply(defaultLanguage);
    }
  }

  async function switchLocale(locale: SupportedLocale, authenticated: boolean): Promise<void> {
    apply(locale);
    if (authenticated) {
      await api.setLanguage(locale).catch(() => undefined);
    }
  }

  apply(current.value);

  return {
    current,
    apply,
    syncFromUser,
    switchLocale,
  };
});
