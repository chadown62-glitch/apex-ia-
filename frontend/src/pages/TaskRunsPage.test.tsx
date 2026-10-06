import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseCases, RunDetail, TaskRunsPage } from "./TaskRunsPage";

function renderWithQueryClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

type Route = { match: (url: string, init?: RequestInit) => boolean; status?: number; body: unknown };

function mockRoutes(routes: Route[]) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const route = routes.find((r) => r.match(url, init));
    const status = route?.status ?? 200;
    return Promise.resolve({
      ok: status < 400,
      status,
      json: async () => route?.body ?? { status: "real", data: [], detail: null },
    });
  }) as unknown as typeof fetch;
  return calls;
}

const TASK = {
  id: "t1", split: "train", specialty: "research", title: "Tâche utilisateur — add", description: "d",
  command: [], dataset_ref: null, created_by: "human", expected_answer: "[3]", created_at: "2026-10-05T10:00:00",
};

const RUN_DETAIL = {
  id: "r1", task_id: "t1", status: "completed", objective: "o", failure: null, stop_reason: "success",
  started_at: "2026-10-05T10:00:00", completed_at: "2026-10-05T10:00:05", condition_on_run: null,
  strategy: null, base_temperature: null, retry_adaptation: "failure_facts", plan_first: true,
  tool_permissions: ["workspace.read", "workspace.write", "sandbox.execute"], max_tool_steps: 10,
  attempts: [
    { attempt_number: 1, provider_name: "m", judge_result: null, reason: "x", category: "incorrect_solution", duration_s: 1 },
    { attempt_number: 2, provider_name: "m", judge_result: "pass", reason: "succès", category: null, duration_s: 1 },
  ],
  eval_runs: [
    { id: "e1", failure_category: "incorrect_solution", model_id: "m", oracle_verdict: "fail",
      oracle_detail: "1/3 cas incorrects (indices [1])", sandbox_status: "success", isolation: "docker",
      generated_code: "def add(a, b):\n    return a - b\n", generation_elapsed_s: 1 },
    { id: "e2", failure_category: "success", model_id: "m", oracle_verdict: "pass",
      oracle_detail: "3/3 résultats conformes", sandbox_status: "success", isolation: "docker",
      generated_code: "def add(a, b):\n    return a + b\n", generation_elapsed_s: 1 },
  ],
  experiences: [],
  events: [
    { ts: "1", kind: "task.created", trace_id: "r1" },
    { ts: "2", kind: "task.plan.generated", plan: "Additionner les deux entiers.", trace_id: "r1" },
    { ts: "3", kind: "judge.completed", verdict: "fail", detail: "1/3 cas incorrects", trace_id: "r1" },
    { ts: "4", kind: "strategy.adapted", description: "tentative 2 : faits d'échec de la tentative 1", trace_id: "r1" },
    { ts: "4b", kind: "agent.step", step: 3, tool: "tests.run", ok: true, error: null, submit: false, trace_id: "r1" },
    { ts: "4c", kind: "agent.step", step: 4, tool: null, ok: true, error: null, submit: true, trace_id: "r1" },
    { ts: "4d", kind: "memory.retrieved", items: [{ id: "m1", source: "task_run:r0", score: 1.2 }], trace_id: "r1" },
    { ts: "5", kind: "task.completed", trace_id: "r1" },
  ],
};

describe("parseCases", () => {
  it("accepts a list of {args, expected}", () => {
    expect(parseCases('[{"args": [1, 2], "expected": 3}]').cases).toEqual([{ args: [1, 2], expected: 3 }]);
  });
  it("refuses invalid JSON, empty lists, missing args or expected", () => {
    expect(parseCases("{").error).toMatch(/JSON/);
    expect(parseCases("[]").error).toMatch(/non vide/);
    expect(parseCases('[{"expected": 1}]').error).toMatch(/args/);
    expect(parseCases('[{"args": []}]').error).toMatch(/expected/);
  });
});

describe("TaskRunsPage", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("creates a user task with the typed cases and never invents a result", async () => {
    const calls = mockRoutes([
      { match: (u) => u.endsWith("/tasks/reference/kinds"), body: { status: "real", data: ["is_palindrome"], detail: null } },
      { match: (u, i) => u.endsWith("/tasks/function") && i?.method === "POST", body: { status: "real", data: TASK, detail: null } },
      { match: (u) => u.includes("/tasks?") || u.endsWith("/tasks"), body: { status: "real", data: [TASK], detail: null } },
      { match: (u) => u.endsWith("/tasks/t1/runs"), body: { status: "real", data: [], detail: null } },
    ]);
    renderWithQueryClient(<TaskRunsPage />);
    fireEvent.change(screen.getByLabelText("Énoncé"), { target: { value: "Additionne." } });
    fireEvent.change(screen.getByLabelText("Nom de la fonction"), { target: { value: "add" } });
    fireEvent.change(screen.getByLabelText("Cas cachés (JSON)"), { target: { value: '[{"args": [1, 2], "expected": 3}]' } });
    fireEvent.click(screen.getByText("Créer ma tâche"));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/tasks/function"))).toBe(true));
    const post = calls.find((c) => c.url.endsWith("/tasks/function"));
    expect(JSON.parse(String(post?.init?.body))).toEqual({
      description: "Additionne.", function: "add", cases: [{ args: [1, 2], expected: 3 }],
    });
    expect(await screen.findByText("Aucune exécution")).toBeInTheDocument();
  });

  it("shows the server's refusal of a task instead of hiding it", async () => {
    mockRoutes([
      { match: (u) => u.endsWith("/tasks/function"), status: 422, body: { detail: "nom de fonction « print » refusé" } },
    ]);
    renderWithQueryClient(<TaskRunsPage />);
    fireEvent.change(screen.getByLabelText("Énoncé"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Nom de la fonction"), { target: { value: "print" } });
    fireEvent.click(screen.getByText("Créer ma tâche"));
    expect(await screen.findByText(/nom de fonction « print » refusé/)).toBeInTheDocument();
  });

  it("refuses malformed cases locally without calling the server", async () => {
    const calls = mockRoutes([]);
    renderWithQueryClient(<TaskRunsPage />);
    fireEvent.change(screen.getByLabelText("Énoncé"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Nom de la fonction"), { target: { value: "f" } });
    fireEvent.change(screen.getByLabelText("Cas cachés (JSON)"), { target: { value: "pas du json" } });
    fireEvent.click(screen.getByText("Créer ma tâche"));
    expect(await screen.findByText(/JSON valide/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/tasks/function"))).toBe(false);
  });
});

describe("RunDetail", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("renders the real trace: plan, failed attempt, adaptation, success", async () => {
    mockRoutes([{ match: (u) => u.endsWith("/task-runs/r1"), body: { status: "real", data: RUN_DETAIL, detail: null } }]);
    renderWithQueryClient(<RunDetail runId="r1" />);
    expect(await screen.findByText(/Statut : completed/)).toBeInTheDocument();
    expect(screen.getByText(/Additionner les deux entiers/)).toBeInTheDocument();
    expect(screen.getByText(/tentative 2 : faits d'échec de la tentative 1/)).toBeInTheDocument();
    expect(screen.getByText(/verdict fail/)).toBeInTheDocument();
    expect(screen.getByText(/verdict pass/)).toBeInTheDocument();
    expect(screen.getByText(/tour 3 : tests.run ok/)).toBeInTheDocument();
    expect(screen.getByText(/tour 4 : solution soumise/)).toBeInTheDocument();
    expect(screen.getByText(/souvenirs : task_run:r0/)).toBeInTheDocument();
  });
});
