<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import { Calendar, ChevronLeft, ChevronRight, X } from "lucide-vue-next";
import { useLocaleStore } from "@/stores/locale";
import { useAuthStore } from "@/stores/auth";
import { todayInZone } from "@/lib/format";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    id?: string;
    ariaLabel?: string;
    placeholder?: string;
    clearable?: boolean;
    disabled?: boolean;
    compact?: boolean;
  }>(),
  { clearable: false, disabled: false, compact: false },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const { t } = useI18n();
const locale = useLocaleStore();
const auth = useAuthStore();

const popoverId = `date-pop-${Math.random().toString(36).slice(2, 10)}`;
const popoverRef = ref<HTMLElement | null>(null);
const isOpen = ref(false);

function parseIsoDate(iso: string): { year: number; month: number; day: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
  if (!m) return null;
  return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) };
}

function initialView() {
  const parsed = parseIsoDate(props.modelValue) ?? parseIsoDate(todayInZone(auth.timeZone));
  return parsed ? { year: parsed.year, month: parsed.month } : { year: 2026, month: 1 };
}

const viewYear = ref(initialView().year);
const viewMonth = ref(initialView().month);

watch(
  () => props.modelValue,
  (val) => {
    const parsed = parseIsoDate(val);
    if (parsed) {
      viewYear.value = parsed.year;
      viewMonth.value = parsed.month;
    }
  },
);

const isZh = computed(() => locale.current === "zh-hans");

const formattedDisplay = computed(() => {
  const parsed = parseIsoDate(props.modelValue);
  if (!parsed) return "";
  const mm = String(parsed.month).padStart(2, "0");
  const dd = String(parsed.day).padStart(2, "0");
  return isZh.value ? `${parsed.year}年${mm}月${dd}日` : `${parsed.year}-${mm}-${dd}`;
});

const effectivePlaceholder = computed(() => {
  if (props.placeholder) return props.placeholder;
  return isZh.value ? "年 / 月 / 日" : "YYYY-MM-DD";
});

const monthHeading = computed(() => {
  if (isZh.value) return `${viewYear.value}年 ${viewMonth.value}月`;
  const d = new Date(Date.UTC(viewYear.value, viewMonth.value - 1, 1));
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(d);
});

const weekdays = computed(() =>
  isZh.value ? ["一", "二", "三", "四", "五", "六", "日"] : ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
);

const todayStr = computed(() => todayInZone(auth.timeZone));

const calendarCells = computed(() => {
  const year = viewYear.value;
  const month = viewMonth.value;
  const firstDow = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7; // Monday = 0
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
      // Ignore if already closed
    }
  }
}

function selectIso(iso: string) {
  emit("update:modelValue", iso);
  closePopover();
}

function selectToday() {
  selectIso(todayStr.value);
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
  <div class="inline-flex items-center">
    <button
      :id="id"
      type="button"
      :disabled="disabled"
      :aria-label="ariaLabel"
      :aria-expanded="isOpen"
      :popovertarget="popoverId"
      class="field-input inline-flex items-center justify-between gap-2 text-start tabular-nums"
      :class="compact ? 'min-h-9 py-1.5 text-sm w-auto min-w-[9.5rem]' : 'w-full'"
    >
      <span :class="formattedDisplay ? 'text-ink' : 'text-muted'">
        {{ formattedDisplay || effectivePlaceholder }}
      </span>
      <span class="inline-flex items-center gap-1 text-muted">
        <X
          v-if="clearable && modelValue"
          class="size-3.5 hover:text-ink"
          aria-hidden="true"
          @click.stop="clearValue"
        />
        <Calendar class="size-4 shrink-0" aria-hidden="true" />
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
        <button
          type="button"
          class="btn btn-ghost btn-sm px-2"
          :aria-label="t('app.previous')"
          @click="prevMonth"
        >
          <ChevronLeft class="size-4" aria-hidden="true" />
        </button>
        <span class="text-sm font-semibold text-ink tabular-nums">{{ monthHeading }}</span>
        <button
          type="button"
          class="btn btn-ghost btn-sm px-2"
          :aria-label="t('app.next')"
          @click="nextMonth"
        >
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
            cell.iso === modelValue
              ? 'bg-accent font-semibold text-accent-ink'
              : cell.iso === todayStr
                ? 'border border-accent/60 font-medium text-accent hover:bg-accent-soft'
                : cell.inMonth
                  ? 'text-ink hover:bg-subtle'
                  : 'text-muted/60 hover:bg-subtle/60',
          ]"
          @click="selectIso(cell.iso)"
        >
          {{ cell.day }}
        </button>
      </div>

      <div class="mt-2.5 flex items-center justify-between border-t border-border-subtle pt-2">
        <button type="button" class="btn btn-ghost btn-sm px-2 text-xs text-accent" @click="selectToday">
          {{ t("overview.today") }}
        </button>
        <button
          v-if="clearable && modelValue"
          type="button"
          class="btn btn-ghost btn-sm px-2 text-xs text-muted"
          @click="clearValue"
        >
          {{ t("app.clear") }}
        </button>
      </div>
    </div>
  </div>
</template>
