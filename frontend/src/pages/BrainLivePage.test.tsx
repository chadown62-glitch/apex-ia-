import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BrainEvent, BrainState, DataEnvelope } from "../api/types";
import { BrainLivePage } from "./BrainLivePage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BrainLivePage />
    </QueryClientProvider>,
  );
}

const state: BrainState = {
  status: "real",
  workdir: "/w",
  world: {
    status: "real",
    fingerprint: "sha256:abcdef0123456789abcdef",
    parent: null,
    capabilities: ["code_repair"],
    domains: ["code"],
    architecture: { vocab_size: 1024, hidden_size: 128, num_layers: 3, num_heads: 4, context_length: 256 },
    train_documents: 4000,
  },
  loop: {
    status: "real",
    state: "running",
    pid: 42,
    last_event: null,
    resources: { status: "real", rss_mb: 1100, cpu_seconds: 90, loadavg: ["1.0", "0.9", "0.8"] },
  },
  champion: {
    status: "real",
    name: "exp-1",
    index: 2,
    kind: "experiment",
    artifact: "sha256:x",
    at: null,
    attested: false,
    attestation_problem: "« exp-1 » : l'expérience n'a pas consigné cette promotion",
    reports: { heldout: 2.151 },
    metrics: { heldout: "bits_per_byte" },
    versions: 3,
    promotions: 2,
  },
  protections: { status: "not_available", reason: "aucune version promue : aucune ancre" },
  current: {
    experiment_id: "exp-2",
    journal_status: "started",
    phase: "training_progress",
    phase_at: 1,
    progress: { t: 1, phase: "training_progress", step: 100, of: 400, loss: 3.2109, lr: 0.001 },
    telemetry: [],
  },
  experiments: [],
};

const events: BrainEvent[] = [
  {
    seq: 5,
    at: "2026-09-28T00:00:00",
    kind: "experiment",
    event: "judged",
    experiment_id: "exp-1",
    label: "JUDGE_REJECT",
    summary: "oubli de « text » sur « heldout:stdlib »",
    regressions: [{ group: "text", suite: "heldout:stdlib", before: 2.4, after: 2.7, gain_ci: [-0.3, -0.2], tolerance: 0.01 }],
  },
];

function mockBackend(stateEnvelope: DataEnvelope<BrainState>, eventsEnvelope: DataEnvelope<BrainEvent[]>) {
  globalThis.fetch = vi.fn().mockImplementation((url: string) => {
    const body = String(url).includes("/brain/events") ? eventsEnvelope : stateEnvelope;
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  }) as unknown as typeof fetch;
}

describe("BrainLivePage", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows what the engine reports, including an unattested champion and missing anchors", async () => {
    mockBackend({ status: "real", data: state, detail: null }, { status: "real", data: events, detail: null });
    renderPage();
    await waitFor(() => expect(screen.getByText(/Champion en service : exp-1/)).toBeInTheDocument());
    expect(screen.getByText(/NON ATTESTÉ/)).toBeInTheDocument();
    expect(screen.getByText(/pas 100 \/ 400/)).toBeInTheDocument();
    expect(screen.getByText(/aucune version promue : aucune ancre/)).toBeInTheDocument();
    expect(screen.getByText("EN COURS")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("JUDGE_REJECT")).toBeInTheDocument());
    expect(screen.getByText(/RÉGRESSION text \/ heldout:stdlib/)).toBeInTheDocument();
    // Le journal numérote à partir de 0 : la première lecture ne doit perdre aucun événement.
    const urls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/brain/events?after=-1"))).toBe(true);
  });

  it("says the brain is not available instead of showing a simulated state", async () => {
    mockBackend(
      { status: "unknown", data: null, detail: "APEX_BRAIN_WORKDIR non configuré : aucune boucle observée" },
      { status: "unknown", data: null, detail: "APEX_BRAIN_WORKDIR non configuré" },
    );
    renderPage();
    await waitFor(() => expect(screen.getByText(/APEX_BRAIN_WORKDIR non configuré : aucune boucle observée/)).toBeInTheDocument());
    expect(screen.queryByText(/Champion en service/)).not.toBeInTheDocument();
  });
});
