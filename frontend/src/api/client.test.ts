import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, SESSION_EXPIRED_MESSAGE, fetchEnvelope, isApiTokenConfigured } from "./client";

describe("fetchEnvelope", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.stubEnv("VITE_APEX_API_TOKEN", "test-token-value");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("passes a real envelope through unchanged", async () => {
    const envelope = { status: "real", data: { ok: true }, detail: null };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => envelope,
    }) as unknown as typeof fetch;

    const result = await fetchEnvelope("/health");
    expect(result).toEqual(envelope);
  });

  it("calls the API-prefixed path, never PostgreSQL or the Core directly", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: "real", data: null, detail: null }),
    }) as unknown as typeof fetch;

    await fetchEnvelope("/agents");
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/v1/agents", expect.anything());
  });

  it("turns a network failure into an unknown envelope instead of throwing", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("connection refused")) as unknown as typeof fetch;

    const result = await fetchEnvelope("/health");
    expect(result.status).toBe("unknown");
    expect(result.data).toBeNull();
    expect(result.detail).toContain("connection refused");
  });

  it("still reads a DataEnvelope carried by a non-200 response from the global exception handler", async () => {
    const envelope = { status: "unknown", data: null, detail: "internal error" };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => envelope,
    }) as unknown as typeof fetch;

    const result = await fetchEnvelope("/gate");
    expect(result).toEqual(envelope);
  });

  it("attaches the configured token as an Authorization bearer header", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: "real", data: null, detail: null }),
    }) as unknown as typeof fetch;

    await fetchEnvelope("/tasks");
    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer test-token-value");
  });

  it("without a build-time token, uses the session opened from the Panel — never a bearer header", async () => {
    vi.unstubAllEnvs();
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: "real", data: null, detail: null }),
    }) as unknown as typeof fetch;

    await fetchEnvelope("/tasks");
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe("/api/v1/tasks");
    expect(init.credentials).toBe("same-origin");
    expect(headers["X-APEX-UI"]).toBe("1");
    expect(headers.Authorization).toBeUndefined();
    expect(isApiTokenConfigured()).toBe(false);
  });

  it("tells the user to reopen APEX from the Panel when the session is missing or expired", async () => {
    vi.unstubAllEnvs();
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "authentification requise" }),
    }) as unknown as typeof fetch;

    await expect(fetchEnvelope("/status")).rejects.toMatchObject({ status: 401, message: SESSION_EXPIRED_MESSAGE });
  });

  it("throws a typed ApiError(401) on a missing/invalid token, never a DataEnvelope", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "jeton invalide" }),
    }) as unknown as typeof fetch;

    await expect(fetchEnvelope("/tasks")).rejects.toMatchObject({ status: 401 });
    await expect(fetchEnvelope("/tasks")).rejects.toBeInstanceOf(ApiError);
  });

  it("throws a typed ApiError(404) for a missing resource, never a DataEnvelope", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: "tâche « x » introuvable" }),
    }) as unknown as typeof fetch;

    let caught: unknown;
    try {
      await fetchEnvelope("/tasks/does-not-exist");
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(404);
    expect((caught as ApiError).message).toContain("introuvable");
  });

  it("throws a typed ApiError(422) for invalid input", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: "spécialité « x » inconnue" }),
    }) as unknown as typeof fetch;

    await expect(fetchEnvelope("/weakness/x")).rejects.toMatchObject({ status: 422 });
  });

  it("never exposes the token in a thrown error message", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "jeton invalide" }),
    }) as unknown as typeof fetch;

    try {
      await fetchEnvelope("/tasks");
    } catch (error) {
      expect(String(error)).not.toContain("test-token-value");
    }
  });
});

describe("isApiTokenConfigured", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("is true once a non-empty token is configured", () => {
    vi.stubEnv("VITE_APEX_API_TOKEN", "test-token-value");
    expect(isApiTokenConfigured()).toBe(true);
  });

  it("is false when no token is configured", () => {
    vi.unstubAllEnvs();
    expect(isApiTokenConfigured()).toBe(false);
  });
});
