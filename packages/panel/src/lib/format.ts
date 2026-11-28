const BYTE_SCALES: Record<string, number> = {
  B: 1,
  KiB: 1024,
  MiB: 1024 ** 2,
  GiB: 1024 ** 3,
  TiB: 1024 ** 4,
};

export function cleanDecimal(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const raw = String(value).trim();
  if (!raw) return "";
  if (/^-?\d+(\.\d+)?$/.test(raw)) {
    const normalized = raw.includes(".") ? raw.replace(/\.?0+$/, "") : raw;
    return normalized === "-0" || normalized === "" ? "0" : normalized;
  }
  const num = Number(raw);
  return Number.isFinite(num) ? String(num) : raw;
}

export function compactNumber(value: number | string | null | undefined, digits = 1): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  const factor = 10 ** digits;
  const rounded = Math.round(num * factor) / factor;
  return Number.isInteger(rounded) ? String(rounded) : String(rounded);
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "—";
  const units: [number, string][] = [
    [1024 ** 4, "TiB"],
    [1024 ** 3, "GiB"],
    [1024 ** 2, "MiB"],
    [1024, "KiB"],
    [1, "B"],
  ];
  const abs = Math.abs(bytes);
  const [divisor, label] = units.find(([size]) => abs >= size || size === 1) ?? [1, "B"];
  const digits = divisor === 1 ? 0 : 1;
  return `${compactNumber(bytes / divisor, digits)} ${label}`;
}

export function formatByteRate(mibPerSec: number | null | undefined): string {
  if (mibPerSec === null || mibPerSec === undefined || !Number.isFinite(mibPerSec)) return "—";
  const bytesPerSec = Math.max(0, mibPerSec) * 1024 * 1024;
  return `${formatBytes(bytesPerSec)}/s`;
}

export function formatQuantity(
  value: number | string | null | undefined,
  unit = "GiB",
  coresLabel = "cores",
): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  if (unit in BYTE_SCALES) {
    if (num === 0) return `0 ${unit}`;
    return formatBytes(num * BYTE_SCALES[unit]);
  }
  if (unit === "percent") return `${compactNumber(num)}%`;
  if (unit === "cores") return `${compactNumber(num)} ${coresLabel}`;
  if (unit === "count") return compactNumber(num);
  return `${compactNumber(num)} ${unit}`.trim();
}

export function unitSuffix(unit: string, coresLabel = "cores"): string {
  if (unit === "percent") return "%";
  if (unit === "cores") return coresLabel;
  if (unit === "count") return "";
  return unit;
}

export function formatGpuMemory(gib: number | null | undefined): string {
  if (gib === null || gib === undefined || !Number.isFinite(gib)) return "—";
  if (gib <= 0) return "0 GiB";
  return `${compactNumber(gib, 2)} GiB`;
}

export function formatDateTime(
  iso: string | null | undefined,
  locale = "en",
  timeZone = "Asia/Shanghai",
): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale === "zh-hans" ? "zh-CN" : "en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function formatShortDateTime(
  iso: string | null | undefined,
  locale = "en",
  timeZone = "Asia/Shanghai",
): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale === "zh-hans" ? "zh-CN" : "en-US", {
    timeZone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function formatTimeOnly(
  iso: string | null | undefined,
  locale = "en",
  timeZone = "Asia/Shanghai",
): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale === "zh-hans" ? "zh-CN" : "en-US", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function toLocalDateTimeInput(iso: string | null | undefined, timeZone = "Asia/Shanghai"): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

export function localInputToOffsetIso(localValue: string, offset = "+08:00"): string {
  if (!localValue) return "";
  if (/[zZ]|[+-]\d{2}:\d{2}$/.test(localValue)) return localValue;
  const normalized = localValue.length === 16 ? `${localValue}:00` : localValue;
  return `${normalized}${offset}`;
}

export function todayInZone(timeZone = "Asia/Shanghai"): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

export async function copyToClipboard(text: string): Promise<boolean> {
  const value = text.trim();
  if (!value) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    try {
      textarea.select();
      return document.execCommand("copy");
    } finally {
      textarea.remove();
    }
  } catch {
    return false;
  }
}
