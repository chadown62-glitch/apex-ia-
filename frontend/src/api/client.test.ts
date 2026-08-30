import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchEnvelope } from "./client";

describe("fetchEnvelope", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
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
});
