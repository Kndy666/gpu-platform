import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { api, type UserProfile } from "@/api/endpoints";

export const useAuthStore = defineStore("auth", () => {
  const user = ref<UserProfile | null>(null);
  const initialized = ref(false);
  const loading = ref(false);

  const isAuthenticated = computed(() => user.value !== null);
  const isStaff = computed(() => Boolean(user.value?.is_staff));
  const isSuperuser = computed(() => Boolean(user.value?.is_superuser));
  const features = computed(
    () =>
      user.value?.features ?? {
        public_datasets: false,
        synchronization: false,
        notifications: false,
      },
  );
  const pendingApprovals = computed(() => user.value?.pending_approval_count ?? 0);
  const timeZone = computed(() => user.value?.timezone ?? "Asia/Shanghai");

  async function bootstrap(): Promise<UserProfile | null> {
    if (initialized.value) return user.value;
    loading.value = true;
    try {
      await api.getCsrf().catch(() => undefined);
      user.value = await api.getMe(true);
    } catch {
      user.value = null;
    } finally {
      initialized.value = true;
      loading.value = false;
    }
    return user.value;
  }

  async function refresh(): Promise<UserProfile | null> {
    try {
      user.value = await api.getMe(true);
    } catch {
      user.value = null;
    }
    return user.value;
  }

  async function signIn(username: string, password: string): Promise<UserProfile> {
    await api.getCsrf();
    const res = await api.signIn(username, password);
    user.value = res.user;
    initialized.value = true;
    return res.user;
  }

  async function signOut(): Promise<void> {
    try {
      await api.signOut();
    } finally {
      user.value = null;
      await api.getCsrf().catch(() => undefined);
    }
  }

  function clearSession(): void {
    user.value = null;
  }

  return {
    user,
    initialized,
    loading,
    isAuthenticated,
    isStaff,
    isSuperuser,
    features,
    pendingApprovals,
    timeZone,
    bootstrap,
    refresh,
    signIn,
    signOut,
    clearSession,
  };
});
