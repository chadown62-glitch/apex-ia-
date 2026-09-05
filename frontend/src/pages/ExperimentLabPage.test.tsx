import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, EvalRun, ExperimentCycle } from "../api/types";
import { ExperimentLabPage } from "./ExperimentLabPage";

function renderWithQueryClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const evalRun: EvalRun = {
  id: "run-1", task_id: "task-1", method: "practice", oracle_verdict: "pass", oracle_detail: "ok",
  exit_code: 0, elapsed_s: 1.2, facts: { strategy: { generation_mode: "normal" } }, created_by: "core",
  clone_id: "clone-1", sandbox_status: "success", model_id: null, seed: null, dataset_ref: null,
  experiment_ref: null, started_at: "2025-01-01T00:00:00Z", completed_at: "2025-01-01T00:00:01Z",
  created_at: "2025-01-01T00:00:00Z",
};

const agent: Agent = {
  clone_id: "clone-1", type: "research", state: "terminated", mission_id: null, genome_ref: "sha256:x",
  pool_id: "local", isolation_level: { value: "namespaced", status: "real" },
  created_at: "2025-01-01T00:00:00Z", started_at: null, terminated_at: null,
  elapsed_s: { value: null, status: "unknown" }, cpu_time_s: { value: null, status: "unknown" },
  ram_mb: { value: null, status: "unknown" }, gpu: { value: null, status: "unknown" },
  retries_used: 0, max_retries: 2, last_error: null, available_actions: [],
};

function jsonResponse(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => body });
}

describe("ExperimentLabPage", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("displays the real list of EvalRuns", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [evalRun], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    renderWithQueryClient(<ExperimentLabPage />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("practice")).toBeInTheDocument();
  });

  it("displays the details of a selected run", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/eval-runs/run-1")) return jsonResponse({ status: "real", data: evalRun, detail: null });
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [evalRun], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    renderWithQueryClient(<ExperimentLabPage />);

    const table = await screen.findByRole("table");
    fireEvent.click(within(table).getByText("practice"));

    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
    expect(screen.getByText("normal")).toBeInTheDocument(); // strategy.generation_mode from facts
  });

  it("sends the experiment cycle request through the authenticated client and refreshes the run list on success", async () => {
    const cycle: ExperimentCycle = {
      decision: {
        specialty: "research", mode: null, level: 1,
        curriculum_decision: { current_level: 1, recommended_level: 1, direction: "hold", reason: "bootstrap" },
        reason: "bootstrap",
      },
      task: {
        id: "task-2", split: "train", specialty: "research", title: "Diagnostic", description: "d",
        command: ["python3"], dataset_ref: null, created_by: "human", expected_answer: null,
        created_at: "2025-01-01T00:00:00Z",
      },
      eval_run: { ...evalRun, id: "run-2" },
      weakness_before: {
        category: "insufficient_data", pass_count: 0, fail_count: 0, error_count: 0,
        timeout_count: 0, unknown_count: 0, failure_rate: null, evidence_run_ids: [], reason: "none",
      },
      weakness_after: {
        category: "no_weakness", pass_count: 1, fail_count: 0, error_count: 0,
        timeout_count: 0, unknown_count: 0, failure_rate: 0, evidence_run_ids: ["run-2"], reason: "ok",
      },
    };

    let postBody: string | undefined;
    let postHeaders: Record<string, string> | undefined;
    globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.includes("/experiment-cycles")) {
        postBody = init.body as string;
        postHeaders = init.headers as Record<string, string>;
        return jsonResponse({ status: "real", data: cycle, detail: null });
      }
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [evalRun], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    renderWithQueryClient(<ExperimentLabPage />);

    await screen.findByRole("table");
    await screen.findByRole("option", { name: /clone-1/ });

    fireEvent.change(screen.getByDisplayValue("research"), { target: { value: "research" } });
    fireEvent.change(screen.getByDisplayValue("— sélectionner un clone réel —"), {
      target: { value: "clone-1" },
    });
    fireEvent.click(screen.getByText("Lancer le cycle"));

    await waitFor(() => expect(screen.getByText("Diagnostic")).toBeInTheDocument());

    // Authenticated client used (Authorization header present, sourced from configuration).
    expect(postHeaders?.Authorization).toBe("Bearer test-suite-token");
    // Security: no client-controlled identity/subject field is ever sent.
    const parsedBody = JSON.parse(postBody ?? "{}");
    expect(parsedBody).not.toHaveProperty("subject");
    expect(parsedBody).not.toHaveProperty("requested_by");
    expect(parsedBody).toEqual({
      specialty: "research", current_level: 1, clone_id: "clone-1", method: "practice", split: "train",
    });
  });

  it("displays a useful error and never a stack trace when cycle creation fails", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.includes("/experiment-cycles")) {
        return Promise.resolve({ ok: false, status: 422, json: async () => ({ detail: "clone « x » introuvable" }) });
      }
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [evalRun], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    renderWithQueryClient(<ExperimentLabPage />);

    await screen.findByRole("option", { name: /clone-1/ });
    fireEvent.change(screen.getByDisplayValue("— sélectionner un clone réel —"), {
      target: { value: "clone-1" },
    });
    fireEvent.click(screen.getByText("Lancer le cycle"));

    await waitFor(() => expect(screen.getByText(/clone « x » introuvable/)).toBeInTheDocument());
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
    expect(screen.queryByText(/at Object\./)).not.toBeInTheDocument();
  });

  it("never renders the API token anywhere in the page", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [evalRun], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    const { container } = renderWithQueryClient(<ExperimentLabPage />);
    await waitFor(() => expect(screen.getByText("practice")).toBeInTheDocument());

    expect(container.innerHTML).not.toContain("test-suite-token");
  });
});
