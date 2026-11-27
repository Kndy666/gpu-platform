export class ApiError extends Error {
  status: number;
  code: string;
  fields?: Record<string, string[]>;

  constructor(status: number, code: string, message: string, fields?: Record<string, string[]>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

let csrfToken = "";
let currentLocale = "en";
let onUnauthorized: (() => void) | null = null;

export function setApiLocale(locale: string): void {
  currentLocale = locale;
}

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export function getCsrfToken(): string {
  return csrfToken;
}

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : "";
}

export async function ensureCsrf(): Promise<string> {
  if (csrfToken) return csrfToken;
  const fromCookie = readCookie("csrftoken");
  if (fromCookie) {
    csrfToken = fromCookie;
    return csrfToken;
  }
  const response = await fetch("/api/v1/auth/csrf", {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json", "Accept-Language": currentLocale },
  });
  if (response.ok) {
    const payload = (await response.json()) as { data?: { csrf_token?: string } };
    csrfToken = payload.data?.csrf_token ?? readCookie("csrftoken");
  }
  return csrfToken;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined | string[]>;
  idempotencyKey?: string;
  signal?: AbortSignal;
  skipAuthRedirect?: boolean;
}

export function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const cleanPath = path.startsWith("/api/v1") ? path : `/api/v1${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return cleanPath;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== "") params.append(key, item);
      }
    } else {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${cleanPath}?${qs}` : cleanPath;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const isMutation = method !== "GET";
  const url = buildUrl(path, options.query);

  const run = async (retriedCsrf = false): Promise<T> => {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Accept-Language": currentLocale,
    };
    if (isMutation) {
      const token = await ensureCsrf();
      if (token) headers["X-CSRFToken"] = token;
    }
    if (options.idempotencyKey) {
      headers["Idempotency-Key"] = options.idempotencyKey;
    }
    let body: string | undefined;
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.body);
    }

    const response = await fetch(url, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers,
      body,
      signal: options.signal,
    });

    const contentType = response.headers.get("content-type") ?? "";
    const json = contentType.includes("application/json")
      ? ((await response.json()) as { data?: T; error?: { code?: string; message?: string; fields?: Record<string, string[]> } })
      : null;

    if (!response.ok) {
      const code = json?.error?.code ?? `http_${response.status}`;
      if (response.status === 403 && code === "csrf_failed" && isMutation && !retriedCsrf) {
        csrfToken = "";
        await ensureCsrf();
        return run(true);
      }
      if (response.status === 401 && !options.skipAuthRedirect && onUnauthorized) {
        onUnauthorized();
      }
      const message = json?.error?.message ?? `Request failed (${response.status})`;
      throw new ApiError(response.status, code, message, json?.error?.fields);
    }

    return (json?.data ?? (undefined as unknown as T));
  };

  return run(false);
}

export function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
}
