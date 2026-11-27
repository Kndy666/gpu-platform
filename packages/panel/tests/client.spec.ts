import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  apiRequest,
  ApiError,
  buildUrl,
  setApiLocale,
  setCsrfToken,
  setUnauthorizedHandler,
} from "@/api/client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("API client contract", () => {
  beforeEach(() => {
    setCsrfToken("");
    setApiLocale("en");
    setUnauthorizedHandler(() => {});
    document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    vi.restoreAllMocks();
  });

  it("builds URLs with query parameters and arrays", () => {
    expect(buildUrl("/tasks", { mode: "batch", page: 2, empty: "", skip: null })).toBe(
      "/api/v1/tasks?mode=batch&page=2",
    );
    expect(buildUrl("/admin/performance/history", { nodes: ["1", "3"], days: 7 })).toBe(
      "/api/v1/admin/performance/history?nodes=1&nodes=3&days=7",
    );
  });

  it("fetches CSRF token before mutations and retries once on csrf_failed", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ data: { csrf_token: "tok-1" } }))
      .mockResolvedValueOnce(
        jsonResponse({ error: { code: "csrf_failed", message: "CSRF check failed." } }, 403),
      )
      .mockResolvedValueOnce(jsonResponse({ data: { csrf_token: "tok-2" } }))
      .mockResolvedValueOnce(jsonResponse({ data: { ok: true } }, 201));

    vi.stubGlobal("fetch", fetchMock);
    setApiLocale("zh-hans");

    const result = await apiRequest<{ ok: boolean }>("/tasks", {
      method: "POST",
      body: { purpose: "run" },
      idempotencyKey: "key-123",
    });

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(4);

    const firstMutationHeaders = fetchMock.mock.calls[1][1]?.headers as Record<string, string>;
    expect(firstMutationHeaders["X-CSRFToken"]).toBe("tok-1");
    expect(firstMutationHeaders["Idempotency-Key"]).toBe("key-123");
    expect(firstMutationHeaders["Accept-Language"]).toBe("zh-hans");

    const retriedMutationHeaders = fetchMock.mock.calls[3][1]?.headers as Record<string, string>;
    expect(retriedMutationHeaders["X-CSRFToken"]).toBe("tok-2");
  });

  it("invokes unauthorized handler on 401 unless skipAuthRedirect is true", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockImplementation(async () =>
        jsonResponse({ error: { code: "unauthorized", message: "Auth required" } }, 401),
      ),
    );

    await expect(apiRequest("/me", { skipAuthRedirect: true })).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).not.toHaveBeenCalled();

    await expect(apiRequest("/tasks")).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});
