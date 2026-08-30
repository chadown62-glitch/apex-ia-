import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, DataEnvelope } from "../api/types";
import { AgentsPage } from "./AgentsPage";

function makeAgent(overrides: Partial<Agent>): Agent {
  return {
    clone_id: "00000000-0000-0000-0000-000000000000",
    type: "research",
    state: "working",
    mission_id: null,
    genome_ref: "genome-1",
    pool_id: "pool-1",
    isolation_level: { value: null, status: "unknown" },
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    terminated_at: null,
    elapsed_s: { value: null, status: "unknown" },
    cpu_time_s: { value: null, status: "unknown" },
    ram_mb: { value: null, status: "unknown" },
    gpu: { value: null, status: "unknown" },
    retries_used: 0,
    max_retries: 3,
    last_error: null,
    available_actions: [],
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AgentsPage (Agent Center)", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("filters the real agent list client-side by search text without any extra network call", async () => {
    const envelope: DataEnvelope<Agent[]> = {
      status: "real",
      data: [
        makeAgent({ clone_id: "aaaa1111-0000-0000-0000-000000000000", type: "research", mission_id: "explore-x" }),
        makeAgent({ clone_id: "bbbb2222-0000-0000-0000-000000000000", type: "developer", mission_id: "fix-bug" }),
      ],
      detail: null,
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => envelope });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    renderPage();

    await waitFor(() => expect(screen.getByText("explore-x")).toBeInTheDocument());
    expect(screen.getByText("fix-bug")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/rechercher/i), { target: { value: "fix-bug" } });

    expect(screen.queryByText("explore-x")).not.toBeInTheDocument();
    expect(screen.getByText("fix-bug")).toBeInTheDocument();

    // Le filtrage est purement local : aucun appel réseau supplémentaire.
    const callCount = fetchMock.mock.calls.length;
    fireEvent.change(screen.getByPlaceholderText(/rechercher/i), { target: { value: "" } });
    expect(fetchMock.mock.calls.length).toBe(callCount);
  });

  it("filters by state chip", async () => {
    const envelope: DataEnvelope<Agent[]> = {
      status: "real",
      data: [
        makeAgent({ clone_id: "aaaa1111-0000-0000-0000-000000000000", state: "working", mission_id: "m-working" }),
        makeAgent({ clone_id: "bbbb2222-0000-0000-0000-000000000000", state: "crashed", mission_id: "m-crashed" }),
      ],
      detail: null,
    };
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => envelope }) as unknown as typeof fetch;

    renderPage();

    await waitFor(() => expect(screen.getByText("m-working")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "crashed" }));

    expect(screen.queryByText("m-working")).not.toBeInTheDocument();
    expect(screen.getByText("m-crashed")).toBeInTheDocument();
  });

  it("shows an honest empty state instead of a blank grid when no clone matches the filters", async () => {
    const envelope: DataEnvelope<Agent[]> = {
      status: "real",
      data: [makeAgent({ clone_id: "aaaa1111-0000-0000-0000-000000000000", type: "research" })],
      detail: null,
    };
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => envelope }) as unknown as typeof fetch;

    renderPage();

    await waitFor(() => expect(screen.getByPlaceholderText(/rechercher/i)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/rechercher/i), { target: { value: "no-such-agent-xyz" } });

    await waitFor(() => expect(screen.getByText("Aucun clone ne correspond aux filtres actuels.")).toBeInTheDocument());
  });
});
