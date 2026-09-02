import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, DataEnvelope, Gate } from "../api/types";
import { DashboardPage } from "./DashboardPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockFetchRouter(handlers: Record<string, (init?: RequestInit) => unknown>) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace(/^.*\/api\/v1/, "");
    for (const [pattern, handler] of Object.entries(handlers)) {
      if (path === pattern || path.startsWith(pattern)) {
        return { ok: true, status: 200, json: async () => handler(init) };
      }
    }
    return { ok: true, status: 200, json: async () => ({ status: "unknown", data: null, detail: "no handler" }) };
  }) as unknown as typeof fetch;
}

describe("DashboardPage", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders every real section label required by the UI Foundation, never a flat page", async () => {
    globalThis.fetch = mockFetchRouter({});
    renderPage();

    expect(await screen.findByText("System status")).toBeInTheDocument();
    expect(screen.getByText("Hardware")).toBeInTheDocument();
    expect(screen.getByText("Active agents")).toBeInTheDocument();
    expect(screen.getByText("Recent missions")).toBeInTheDocument();
    expect(screen.getByText("Models / Providers")).toBeInTheDocument();
    expect(screen.getByText("Recent activity")).toBeInTheDocument();
  });

  it("shows the real Gate hardware checks — never a fabricated GPU/RAM number", async () => {
    const gate: DataEnvelope<Gate> = {
      status: "real",
      data: {
        gate_status: "PASS",
        phase_2_authorized: true,
        machine_label: "dev-box",
        fingerprint: "abc123",
        measured_on_target_machine: true,
        checks: [
          { check_id: "gate_08_gpu_model", title: "GPU", status: "PASS", message: "RTX 4060 Ti" },
          { check_id: "gate_09_vram", title: "VRAM", status: "PASS", message: "8 Go" },
        ],
      },
      detail: null,
    };
    globalThis.fetch = mockFetchRouter({ "/gate": () => gate });
    renderPage();

    expect(await screen.findByText("RTX 4060 Ti")).toBeInTheDocument();
    expect(screen.getByText("8 Go")).toBeInTheDocument();
  });

  it("only counts genuinely active clones under Active agents, never every clone regardless of state", async () => {
    const agents: DataEnvelope<Agent[]> = {
      status: "real",
      data: [
        makeAgent({ clone_id: "aaaa1111-0000-0000-0000-000000000000", state: "working", mission_id: "m-active" }),
        makeAgent({ clone_id: "bbbb2222-0000-0000-0000-000000000000", state: "terminated", mission_id: "m-done" }),
      ],
      detail: null,
    };
    globalThis.fetch = mockFetchRouter({ "/agents": () => agents });
    renderPage();

    await waitFor(() => expect(screen.getByText("m-active")).toBeInTheDocument());
    expect(screen.queryByText("m-done")).not.toBeInTheDocument();
  });

  it("switches between Events and Governance tabs in Recent activity without any extra network call", async () => {
    globalThis.fetch = mockFetchRouter({});
    renderPage();

    const eventsTab = await screen.findByRole("tab", { name: /événements système/i });
    expect(eventsTab).toBeInTheDocument();
    const governanceTab = screen.getByRole("tab", { name: /gouvernance/i });

    const callCount = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.click(governanceTab);
    expect(governanceTab).toHaveAttribute("aria-selected", "true");
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBe(callCount);
  });

  it("never shows a hardcoded READY badge in the page body — the APEX status now lives only in the TopBar", async () => {
    globalThis.fetch = mockFetchRouter({});
    renderPage();
    await screen.findByText("System status");
    expect(screen.queryByText("READY")).not.toBeInTheDocument();
  });
});

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
