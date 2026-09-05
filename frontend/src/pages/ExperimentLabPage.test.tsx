import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, AutonomousLearningStatus, EvalRun, ExperimentCycle, LearningCycleStatus } from "../api/types";
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

describe("ExperimentLabPage — autonomous learning panel", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function idleCycle(): LearningCycleStatus {
    return {
      state: "idle", cycle_id: null, specialty: null, started_at: null, completed_at: null,
      error: null, eval_run_id: null, task_id: null, oracle_verdict: null,
      skill_candidate_id: null, genome_candidate_id: null,
    };
  }

  it("shows the scheduler disabled by default and the cycle as inactive", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      if (url.includes("/learning/status")) {
        const data: AutonomousLearningStatus = {
          enabled: false, scheduler_running: false, interval_s: 3600, cycle: idleCycle(),
        };
        return jsonResponse({ status: "real", data, detail: null });
      }
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    renderWithQueryClient(<ExperimentLabPage />);

    await waitFor(() => expect(screen.getByText("désactivé")).toBeInTheDocument());
    expect(screen.getByText("inactif")).toBeInTheDocument();
  });

  it("starts an autonomous cycle through the authenticated client with no identity field, and reflects completion", async () => {
    let cycleState: LearningCycleStatus = idleCycle();
    let postBody: string | undefined;
    let postHeaders: Record<string, string> | undefined;

    globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.includes("/learning/cycles")) {
        postBody = init.body as string;
        postHeaders = init.headers as Record<string, string>;
        cycleState = {
          state: "completed", cycle_id: "cyc-1", specialty: "research",
          started_at: "2025-01-01T00:00:00Z", completed_at: "2025-01-01T00:00:01Z",
          error: null, eval_run_id: "run-9", task_id: "task-9", oracle_verdict: "pass",
          skill_candidate_id: "skill-1", genome_candidate_id: "genome-1",
        };
        return jsonResponse({ status: "real", data: cycleState, detail: null });
      }
      if (url.includes("/learning/status")) {
        const data: AutonomousLearningStatus = {
          enabled: false, scheduler_running: false, interval_s: 3600, cycle: cycleState,
        };
        return jsonResponse({ status: "real", data, detail: null });
      }
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    renderWithQueryClient(<ExperimentLabPage />);

    await waitFor(() => expect(screen.getByText("inactif")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Démarrer un cycle autonome"));

    await waitFor(() => expect(screen.getByText("terminé")).toBeInTheDocument());
    expect(screen.getByText("pass")).toBeInTheDocument();
    expect(screen.getByText("skill-1 (proposé)")).toBeInTheDocument();
    expect(screen.getByText("genome-1 (proposé)")).toBeInTheDocument();

    expect(postHeaders?.Authorization).toBe("Bearer test-suite-token");
    const parsedBody = JSON.parse(postBody ?? "{}");
    expect(parsedBody).not.toHaveProperty("subject");
    expect(parsedBody).not.toHaveProperty("requested_by");
    expect(parsedBody).toEqual({
      specialty: "research", current_level: 1, method: "practice", split: "train", clone_id: null,
    });
  });

  it("shows a cancel action while a cycle is running and calls the cancel endpoint", async () => {
    let cycleState: LearningCycleStatus = {
      state: "running", cycle_id: "cyc-2", specialty: "research", started_at: "2025-01-01T00:00:00Z",
      completed_at: null, error: null, eval_run_id: null, task_id: null, oracle_verdict: null,
      skill_candidate_id: null, genome_candidate_id: null,
    };
    let cancelCalled = false;

    globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.includes("/learning/cycles/cancel")) {
        cancelCalled = true;
        cycleState = { ...cycleState, state: "cancel_requested" };
        return jsonResponse({ status: "real", data: cycleState, detail: null });
      }
      if (url.includes("/learning/status")) {
        const data: AutonomousLearningStatus = {
          enabled: false, scheduler_running: false, interval_s: 3600, cycle: cycleState,
        };
        return jsonResponse({ status: "real", data, detail: null });
      }
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    renderWithQueryClient(<ExperimentLabPage />);

    const cancelButton = await screen.findByText("Annuler");
    fireEvent.click(cancelButton);

    await waitFor(() => expect(cancelCalled).toBe(true));
  });

  it("displays a clear error, never a stack trace, when starting a cycle is refused (duplicate)", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.includes("/learning/cycles")) {
        return Promise.resolve({
          ok: false, status: 409, json: async () => ({ detail: "un cycle est déjà en cours (cyc-3)" }),
        });
      }
      if (url.includes("/learning/status")) {
        const data: AutonomousLearningStatus = {
          enabled: false, scheduler_running: false, interval_s: 3600, cycle: idleCycle(),
        };
        return jsonResponse({ status: "real", data, detail: null });
      }
      if (url.includes("/eval-runs")) return jsonResponse({ status: "real", data: [], detail: null });
      if (url.includes("/agents")) return jsonResponse({ status: "real", data: [agent], detail: null });
      return jsonResponse({ status: "real", data: null, detail: null });
    }) as unknown as typeof fetch;

    renderWithQueryClient(<ExperimentLabPage />);

    await waitFor(() => expect(screen.getByText("inactif")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Démarrer un cycle autonome"));

    await waitFor(() => expect(screen.getByText(/cycle est déjà en cours/)).toBeInTheDocument());
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
    expect(screen.queryByText(/at Object\./)).not.toBeInTheDocument();
  });
});
