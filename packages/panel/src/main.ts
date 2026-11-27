import { createApp } from "vue";
import { createPinia } from "pinia";
import "@fontsource-variable/newsreader";
import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource-variable/jetbrains-mono";
import "./styles/main.css";
import App from "./App.vue";
import router from "./router";
import { i18n } from "./i18n";
import { useThemeStore } from "./stores/theme";
import { useAuthStore } from "./stores/auth";
import { useLocaleStore } from "./stores/locale";
import { useNotificationsStore } from "./stores/notifications";
import { setUnauthorizedHandler } from "./api/client";

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);
app.use(i18n);
app.use(router);

const theme = useThemeStore(pinia);
theme.init();
useLocaleStore(pinia);

setUnauthorizedHandler(() => {
  const auth = useAuthStore(pinia);
  const notifications = useNotificationsStore(pinia);
  if (auth.isAuthenticated) {
    auth.clearSession();
    notifications.reset();
    notifications.pushToast({
      level: "error",
      title: i18n.global.t("app.sessionExpired"),
      seconds: 6,
    });
  }
  if (router.currentRoute.value.path !== "/login") {
    const next = router.currentRoute.value.fullPath;
    void router.push({ path: "/login", query: next !== "/" ? { next } : {} });
  }
});

app.mount("#app");
