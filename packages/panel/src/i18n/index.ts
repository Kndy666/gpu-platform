import { createI18n } from "vue-i18n";
import en from "./en";
import zhHans from "./zh-Hans";

export type SupportedLocale = "en" | "zh-hans";

export function detectInitialLocale(): SupportedLocale {
  try {
    const stored = localStorage.getItem("gpu-platform-locale");
    if (stored === "en" || stored === "zh-hans") return stored;
  } catch {
    // Ignore storage access errors
  }
  const browser = (navigator.language || "en").toLowerCase();
  return browser.startsWith("zh") ? "zh-hans" : "en";
}

export const i18n = createI18n({
  legacy: false,
  locale: detectInitialLocale(),
  fallbackLocale: "en",
  messages: {
    en,
    "zh-hans": zhHans,
  },
});
