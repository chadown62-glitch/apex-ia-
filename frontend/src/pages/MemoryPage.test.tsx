import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MemoryPage } from "./MemoryPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryPage />
    </QueryClientProvider>,
  );
}

describe("MemoryPage", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("indexes a document and searches with the chosen categories, showing provenance and score", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const body = url.includes("/memory/documents")
        ? { status: "real", data: { document_id: "d1", chunks: 2, record_ids: ["a", "b"] }, detail: null }
        : { status: "real", detail: null, data: [
            { id: "a", category: "knowledge", source: "document:d1#0", score: 2.5, snippet: "Le Startup Gate…", created_at: "x" }] };
      return { ok: true, status: 200, json: async () => body };
    }) as unknown as typeof fetch;

    renderPage();
    fireEvent.change(screen.getByLabelText("Titre du document"), { target: { value: "Guide" } });
    fireEvent.change(screen.getByLabelText("Contenu du document"), { target: { value: "Le Startup Gate protège." } });
    fireEvent.click(screen.getByText("Indexer le document"));
    expect(await screen.findByText(/indexé en 2 morceau/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Conversations"));
    fireEvent.change(screen.getByLabelText("Requête"), { target: { value: "Startup Gate" } });
    fireEvent.click(screen.getByText("Rechercher"));
    expect(await screen.findByText(/document:d1#0/)).toBeInTheDocument();
    const search = calls.find((c) => c.url.includes("/memory/search"));
    expect(search?.url).toContain("q=Startup+Gate");
    expect(search?.url).not.toContain("categories=conversation");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ title: "Guide", content: "Le Startup Gate protège." });
  });

  it("says when nothing is relevant instead of showing an empty list", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ status: "real", data: [], detail: null }) })) as unknown as typeof fetch;
    renderPage();
    fireEvent.change(screen.getByLabelText("Requête"), { target: { value: "astronomie" } });
    fireEvent.click(screen.getByText("Rechercher"));
    await waitFor(() => expect(screen.getByText("Aucun souvenir pertinent")).toBeInTheDocument());
  });
});
