import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DataEnvelope, SelfModelSpecialty, WeaknessReport } from "../api/types";
import { WeaknessesPage } from "./WeaknessesPage";

function renderWithQueryClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const selfModelEnvelope: DataEnvelope<SelfModelSpecialty> = {
  status: "real",
  data: {
    specialty: "research",
    acquired_skills: [],
    proposed_skills: [],
    weakness_report: {
      category: "no_weakness", pass_count: 3, fail_count: 0, error_count: 0,
      timeout_count: 0, unknown_count: 0, failure_rate: 0, evidence_run_ids: ["r1"], reason: "ok",
    },
    calibrated_confidence: {
      status: "calibrated", sample_size: 3, observed_success_rate: 1,
      calibrated_point_estimate: 0.8, calibrated_lower_bound: 0.5, calibrated_upper_bound: 0.95,
      declared_confidence: null, calibration_gap: null, reason: "ok",
    },
    eval_run_count: 3,
    methods_used: ["practice"],
    genome_refs_used: [],
    cost: {
      elapsed_s: { value: 1.5, status: "real" },
      cpu_time_s: { value: null, status: "unknown" },
      memory_peak_mb: { value: 42, status: "real" },
      token_count: { value: null, status: "unknown" },
      experiment_count: 3,
    },
    last_skill_validated_at: null,
  },
  detail: null,
};

function mockFetchSequence(envelopes: unknown[]) {
  let call = 0;
  globalThis.fetch = vi.fn().mockImplementation(() => {
    const envelope = envelopes[Math.min(call, envelopes.length - 1)];
    call += 1;
    return Promise.resolve({ ok: true, status: 200, json: async () => envelope });
  }) as unknown as typeof fetch;
}

describe("WeaknessesPage", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows a loading state before data arrives", () => {
    globalThis.fetch = vi.fn().mockReturnValue(new Promise(() => {})) as unknown as typeof fetch;
    renderWithQueryClient(<WeaknessesPage />);
    expect(screen.getByText("Chargement…")).toBeInTheDocument();
  });

  it("renders a real, populated weakness report with calibration/cost from Self-Model", async () => {
    const weaknessEnvelope: DataEnvelope<WeaknessReport> = {
      status: "real",
      data: selfModelEnvelope.data!.weakness_report,
      detail: null,
    };
    mockFetchSequence([weaknessEnvelope, selfModelEnvelope]);

    renderWithQueryClient(<WeaknessesPage />);

    await waitFor(() => expect(screen.getByText("Aucune faiblesse")).toBeInTheDocument());
    expect(screen.getAllByText("3").length).toBeGreaterThan(0); // pass_count/eval_run_count
    await waitFor(() => expect(screen.getByText("calibrated")).toBeInTheDocument());
  });

  it("renders an honest insufficient-data empty state, never a fabricated weakness", async () => {
    const emptyReport: WeaknessReport = {
      category: "insufficient_data", pass_count: 0, fail_count: 0, error_count: 0,
      timeout_count: 0, unknown_count: 0, failure_rate: null, evidence_run_ids: [], reason: "aucune donnée",
    };
    mockFetchSequence([
      { status: "real", data: emptyReport, detail: null },
      { ...selfModelEnvelope, data: { ...selfModelEnvelope.data, weakness_report: emptyReport } },
    ]);

    renderWithQueryClient(<WeaknessesPage />);

    await waitFor(() => expect(screen.getByText("Données insuffisantes")).toBeInTheDocument());
    expect(screen.getByText("aucune donnée")).toBeInTheDocument();
  });

  it("shows an ErrorState when the backend is unreachable, never fabricated data", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED")) as unknown as typeof fetch;
    renderWithQueryClient(<WeaknessesPage />);

    await waitFor(() => expect(screen.getByText("Donnée indisponible")).toBeInTheDocument());
  });
});
