<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import { CalendarClock, ChevronLeft, ChevronRight, X } from "lucide-vue-next";
import { useLocaleStore } from "@/stores/locale";
import { useAuthStore } from "@/stores/auth";
import { todayInZone } from "@/lib/format";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    id?: string;
    ariaLabel?: string;
    clearable?: boolean;
    disabled?: boolean;
  }>(),
  { clearable: false, disabled: false },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const { t } = useI18n();
const locale = useLocaleStore();
const auth = useAuthStore();

const popoverId = `dt-pop-${Math.random().toString(36).slice(2, 10)}`;
const popoverRef = ref<HTMLElement | null>(null);
const isOpen = ref(false);

function splitDateTime(val: string): { date: string; hour: number; minute: number } {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(val.trim());
  if (!m) return { date: "", hour: 0, minute: 0 };
  return { date: m[1], hour: Number(m[2]), minute: Number(m[3]) };
}

function parseIsoDate(iso: string): { year: number; month: number; day: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
  if (!m) return null;
  return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) };
}

const currentParts = computed(() => splitDateTime(props.modelValue));

function initialView() {
  const parsed = parseIsoDate(currentParts.value.date) ?? parseIsoDate(todayInZone(auth.timeZone));
  return parsed ? { year: parsed.year, month: parsed.month } : { year: 2026, month: 1 };
}

const viewYear = ref(initialView().year);
const viewMonth = ref(initialView().month);

watch(
  () => props.modelValue,
  (val) => {
    const parsed = parseIsoDate(splitDateTime(val).date);
    if (parsed) {
      viewYear.value = parsed.year;
      viewMonth.value = parsed.month;
    }
  },
);

const isZh = computed(() => locale.current === "zh-hans");
const todayStr = computed(() => todayInZone(auth.timeZone));

const formattedDisplay = computed(() => {
  const parts = currentParts.value;
  const parsed = parseIsoDate(parts.date);
  if (!parsed) return "";
  const mm = String(parsed.month).padStart(2, "0");
  const dd = String(parsed.day).padStart(2, "0");
  const hh = String(parts.hour).padStart(2, "0");
  const min = String(parts.minute).padStart(2, "0");
  return isZh.value
    ? `${parsed.year}年${mm}月${dd}日 ${hh}:${min}`
    : `${parsed.year}-${mm}-${dd} ${hh}:${min}`;
});

const placeholderText = computed(() =>
  isZh.value ? "年 / 月 / 日  时:分" : "YYYY-MM-DD HH:mm",
);

const monthHeading = computed(() => {
  if (isZh.value) return `${viewYear.value}年 ${viewMonth.value}月`;
  const d = new Date(Date.UTC(viewYear.value, viewMonth.value - 1, 1));
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(d);
});

const weekdays = computed(() =>
  isZh.value ? ["一", "二", "三", "四", "五", "六", "日"] : ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
);

const calendarCells = computed(() => {
  const year = viewYear.value;
  const month = viewMonth.value;
  const firstDow = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7;
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const prevDays = new Date(Date.UTC(year, month - 1, 0)).getUTCDate();
  const cells: { iso: string; day: number; inMonth: boolean }[] = [];
  for (let i = firstDow - 1; i >= 0; i--) {
    const d = prevDays - i;
    const py = month === 1 ? year - 1 : year;
    const pm = month === 1 ? 12 : month - 1;
    cells.push({
      iso: `${py}-${String(pm).padStart(2, "0")}-${String(d).padStart(2, "0")}`,
      day: d,
      inMonth: false,
    });
  }
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push({
      iso: `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`,
      day: d,
      inMonth: true,
    });
  }
  const rem = (7 - (cells.length % 7)) % 7;
  for (let d = 1; d <= rem; d++) {
    const ny = month === 12 ? year + 1 : year;
    const nm = month === 12 ? 1 : month + 1;
    cells.push({
      iso: `${ny}-${String(nm).padStart(2, "0")}-${String(d).padStart(2, "0")}`,
      day: d,
      inMonth: false,
    });
  }
  return cells;
});

function emitParts(date: string, hour: number, minute: number) {
  const d = date || todayStr.value;
  emit("update:modelValue", `${d}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`);
}

function selectDay(iso: string) {
  emitParts(iso, currentParts.value.hour, currentParts.value.minute);
}

function onHourChange(val: string) {
  emitParts(currentParts.value.date || todayStr.value, Number(val), currentParts.value.minute);
}

function onMinuteChange(val: string) {
  emitParts(currentParts.value.date || todayStr.value, currentParts.value.hour, Number(val));
}

function prevMonth() {
  if (viewMonth.value === 1) {
    viewMonth.value = 12;
    viewYear.value -= 1;
  } else {
    viewMonth.value -= 1;
  }
}

function nextMonth() {
  if (viewMonth.value === 12) {
    viewMonth.value = 1;
    viewYear.value += 1;
  } else {
    viewMonth.value += 1;
  }
}

function closePopover() {
  const el = popoverRef.value as (HTMLElement & { hidePopover?: () => void }) | null;
  if (el?.hidePopover) {
    try {
      el.hidePopover();
    } catch {
      // Ignore
    }
  }
}

function clearValue() {
  emit("update:modelValue", "");
  closePopover();
}

function onToggle(event: Event) {
  const e = event as Event & { newState?: string };
  isOpen.value = e.newState === "open";
}
</script>

<template>
  <div class="w-full">
    <button
      :id="id"
      type="button"
      :disabled="disabled"
      :aria-label="ariaLabel"
      :aria-expanded="isOpen"
      :popovertarget="popoverId"
      class="field-input inline-flex w-full items-center justify-between gap-2 text-start tabular-nums"
    >
      <span :class="formattedDisplay ? 'text-ink' : 'text-muted'">
        {{ formattedDisplay || placeholderText }}
      </span>
      <span class="inline-flex items-center gap-1 text-muted">
        <X
          v-if="clearable && modelValue"
          class="size-3.5 hover:text-ink"
          aria-hidden="true"
          @click.stop="clearValue"
        />
        <CalendarClock class="size-4 shrink-0" aria-hidden="true" />
      </span>
    </button>

    <div
      :id="popoverId"
      ref="popoverRef"
      popover="auto"
      class="date-popover"
      @toggle="onToggle"
    >
      <div class="mb-2.5 flex items-center justify-between gap-2">
        <button type="button" class="btn btn-ghost btn-sm px-2" :aria-label="t('app.previous')" @click="prevMonth">
          <ChevronLeft class="size-4" aria-hidden="true" />
        </button>
        <span class="text-sm font-semibold text-ink tabular-nums">{{ monthHeading }}</span>
        <button type="button" class="btn btn-ghost btn-sm px-2" :aria-label="t('app.next')" @click="nextMonth">
          <ChevronRight class="size-4" aria-hidden="true" />
        </button>
      </div>

      <div class="mb-1 grid grid-cols-7 text-center text-xs font-medium text-muted">
        <span v-for="wd in weekdays" :key="wd" class="py-1">{{ wd }}</span>
      </div>

      <div class="grid grid-cols-7 gap-0.5 text-center text-sm tabular-nums">
        <button
          v-for="cell in calendarCells"
          :key="cell.iso"
          type="button"
          class="flex size-8 items-center justify-center rounded-md transition-colors"
          :class="[
            cell.iso === currentParts.date
              ? 'bg-accent font-semibold text-accent-ink'
              : cell.iso === todayStr
                ? 'border border-accent/60 font-medium text-accent hover:bg-accent-soft'
                : cell.inMonth
                  ? 'text-ink hover:bg-subtle'
                  : 'text-muted/60 hover:bg-subtle/60',
          ]"
          @click="selectDay(cell.iso)"
        >
          {{ cell.day }}
        </button>
      </div>

      <!-- Time selector -->
      <div class="mt-3 flex items-center justify-between gap-2 border-t border-border-subtle pt-2.5">
        <div class="flex items-center gap-1.5">
          <select
            class="field-input min-h-8 w-auto py-1 text-sm tabular-nums"
            :value="currentParts.hour"
            :aria-label="t('app.hours')"
            @change="onHourChange(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="h in 24" :key="h - 1" :value="h - 1">
              {{ String(h - 1).padStart(2, "0") }}
            </option>
          </select>
          <span class="font-semibold text-muted">:</span>
          <select
            class="field-input min-h-8 w-auto py-1 text-sm tabular-nums"
            :value="currentParts.minute"
            :aria-label="t('app.minutes')"
            @change="onMinuteChange(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="m in 60" :key="m - 1" :value="m - 1">
              {{ String(m - 1).padStart(2, "0") }}
            </option>
          </select>
        </div>

        <div class="flex items-center gap-1">
          <button
            v-if="clearable && modelValue"
            type="button"
            class="btn btn-ghost btn-sm px-2 text-xs text-muted"
            @click="clearValue"
          >
            {{ t("app.clear") }}
          </button>
          <button type="button" class="btn btn-primary btn-sm px-3 text-xs" @click="closePopover">
            {{ t("app.confirm") }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
