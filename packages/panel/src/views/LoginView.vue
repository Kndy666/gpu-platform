<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { Eye, EyeOff, Sun, Moon, Globe } from "lucide-vue-next";
import { useAuthStore } from "@/stores/auth";
import { useThemeStore } from "@/stores/theme";
import { useLocaleStore } from "@/stores/locale";
import { ApiError } from "@/api/client";
import { isImeComposing } from "@/lib/ime";
import BrandMark from "@/components/ui/BrandMark.vue";
import FlowingBackdrop from "@/components/ui/FlowingBackdrop.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const theme = useThemeStore();
const locale = useLocaleStore();

const username = ref("");
const password = ref("");
const showPassword = ref(false);
const errorMessage = ref("");
const busy = ref(false);

function toggleLocale() {
  void locale.switchLocale(locale.current === "zh-hans" ? "en" : "zh-hans", false);
}

function onEnterKey(event: KeyboardEvent) {
  if (isImeComposing(event)) {
    event.preventDefault();
  }
}

async function handleSubmit() {
  errorMessage.value = "";
  const u = username.value.trim();
  const p = password.value;
  if (!u || !p) {
    errorMessage.value = t("auth.invalidFields");
    return;
  }
  busy.value = true;
  try {
    const profile = await auth.signIn(u, p);
    locale.syncFromUser(profile.language, profile.default_language);
    const next = typeof route.query.next === "string" && route.query.next.startsWith("/") ? route.query.next : "/";
    await router.replace(next);
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t("app.loadError");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="relative flex min-h-dvh flex-col overflow-hidden bg-canvas">
    <FlowingBackdrop />

    <!-- Top-right viewport controls -->
    <header class="relative z-10 flex w-full items-center justify-end gap-1.5 px-5 pt-4 sm:px-8 sm:pt-5">
      <button
        type="button"
        class="btn btn-ghost btn-sm border border-border-subtle/60 bg-card/55 px-2.5 backdrop-blur-md"
        :aria-label="t('app.language')"
        @click="toggleLocale"
      >
        <Globe class="size-4" aria-hidden="true" />
        <span class="text-xs font-medium">{{ locale.current === "zh-hans" ? "EN" : "中文" }}</span>
      </button>
      <button
        type="button"
        class="btn btn-ghost btn-sm border border-border-subtle/60 bg-card/55 px-2.5 backdrop-blur-md"
        :aria-label="t('app.toggleTheme')"
        :aria-pressed="theme.resolvedTheme === 'dark'"
        @click="theme.toggle()"
      >
        <Sun v-if="theme.resolvedTheme === 'dark'" class="size-4" aria-hidden="true" />
        <Moon v-else class="size-4" aria-hidden="true" />
      </button>
    </header>

    <main class="relative z-10 mx-auto my-auto w-full max-w-md px-4 py-8">
      <div class="mb-7 text-center">
        <div class="mb-4 flex justify-center">
          <BrandMark size="lg" />
        </div>
        <h1 class="font-serif text-ink">{{ t("app.name") }}</h1>
        <p class="mt-2 text-sm text-muted">{{ t("auth.subtitle") }}</p>
      </div>

      <form
        class="card space-y-5 border-border-strong/55 bg-card/78 p-6 shadow-xl backdrop-blur-xl ring-1 ring-white/35 dark:ring-white/8 sm:p-7"
        @submit.prevent="handleSubmit"
        @keydown.enter="onEnterKey"
      >
        <div>
          <label for="login-username" class="mb-1.5 block text-sm font-medium text-ink">
            {{ t("auth.username") }}
          </label>
          <input
            id="login-username"
            v-model="username"
            type="text"
            name="username"
            autocomplete="username"
            required
            autofocus
            class="field-input"
          />
        </div>

        <div>
          <label for="login-password" class="mb-1.5 block text-sm font-medium text-ink">
            {{ t("auth.password") }}
          </label>
          <div class="relative">
            <input
              id="login-password"
              v-model="password"
              :type="showPassword ? 'text' : 'password'"
              name="password"
              autocomplete="current-password"
              required
              class="field-input pr-11"
            />
            <button
              type="button"
              class="btn btn-ghost btn-sm absolute top-1/2 right-1.5 -translate-y-1/2 px-2"
              :aria-label="showPassword ? t('auth.hidePassword') : t('auth.showPassword')"
              @click="showPassword = !showPassword"
            >
              <EyeOff v-if="showPassword" class="size-4" aria-hidden="true" />
              <Eye v-else class="size-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        <p
          v-if="errorMessage"
          role="alert"
          class="rounded-lg border border-danger/30 bg-danger-bg px-3.5 py-2.5 text-sm text-danger"
        >
          {{ errorMessage }}
        </p>

        <button type="submit" class="btn btn-primary w-full" :disabled="busy">
          {{ busy ? t("auth.signingIn") : t("auth.signIn") }}
        </button>
      </form>
    </main>
  </div>
</template>
