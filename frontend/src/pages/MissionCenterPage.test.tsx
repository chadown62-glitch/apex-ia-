import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, AgentExecution, ConfiguredAgent, DataEnvelope, Mission } from "../api/types";
import { MissionCenterPage } from "./MissionCenterPage";

function makeAgent(overrides: Partial<Agent>): Agent {
  return {
    clone_id: "cccc3333-0000-0000-0000-000000000000",
    type: "research",
    state: "ready",
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

function makeMission(overrides: Partial<Mission>): Mission {
  return {
    id: "m-1",
    title: "Explorer X",
    description: "",
    specialty: "research",
    objective: "trouver",
    priority: "normal",
    constraints: null,
    assigned_agent: null,
    agent_id: null,
    state: "queued",
    result: null,
    error: null,
    validation_status: null,
    created_by: "human",
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

function makeConfiguredAgent(overrides: Partial<ConfiguredAgent>): ConfiguredAgent {
  return {
    id: "ca-1", name: "Mon développeur Python", description: "", role: "coder", specialty: null,
    model_role: "coder_main", required_capabilities: [], tools: [], permissions: [], instructions: null,
    enabled: true, status: "idle", project_id: null, template_id: null, created_by: "human",
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MissionCenterPage />
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

describe("MissionCenterPage", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("submits a real mission creation and displays the mission returned by the API", async () => {
    const created = makeMission({ id: "new-mission-id", title: "Ma mission" });
    const agent = makeAgent({});
    globalThis.fetch = mockFetchRouter({
      "/agents": () => ({ status: "real", data: [agent], detail: null } satisfies DataEnvelope<Agent[]>),
      "/missions/new-mission-id": () => ({ status: "real", data: created, detail: null } satisfies DataEnvelope<Mission>),
      "/missions": (init) => {
        if (init?.method === "POST") {
          return { status: "real", data: created, detail: null } satisfies DataEnvelope<Mission>;
        }
        return { status: "real", data: [created], detail: null } satisfies DataEnvelope<Mission[]>;
      },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("Titre de la mission"), { target: { value: "Ma mission" } });
    fireEvent.change(screen.getByPlaceholderText(/ce que la mission doit accomplir/i), { target: { value: "trouver" } });

    const submit = screen.getByRole("button", { name: /créer la mission/i });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);

    await waitFor(() => expect(screen.getByText("new-mission-id")).toBeInTheDocument());
  });

  it("keeps the create button disabled until a title and an objective are filled in", async () => {
    globalThis.fetch = mockFetchRouter({
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
      "/missions": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Mission[]>),
    });

    renderPage();

    const submit = await screen.findByRole("button", { name: /créer la mission/i });
    expect(submit).toBeDisabled();
  });

  it("never activates validate/reject unless the selected mission is really in REVIEW", async () => {
    const queued = makeMission({ id: "m-queued", state: "queued" });
    globalThis.fetch = mockFetchRouter({
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
      "/missions/m-queued": () => ({ status: "real", data: queued, detail: null } satisfies DataEnvelope<Mission>),
      "/missions": () => ({ status: "real", data: [queued], detail: null } satisfies DataEnvelope<Mission[]>),
    });

    renderPage();

    fireEvent.click(await screen.findByText("Explorer X"));

    await waitFor(() => expect(screen.getByRole("button", { name: /valider/i })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /valider/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /rejeter/i })).toBeDisabled();
  });

  it("activates validate/reject once the selected mission is really REVIEW, from real API data", async () => {
    const inReview = makeMission({ id: "m-review", state: "review", title: "Mission prête à valider" });
    globalThis.fetch = mockFetchRouter({
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
      "/missions/m-review": () => ({ status: "real", data: inReview, detail: null } satisfies DataEnvelope<Mission>),
      "/missions": () => ({ status: "real", data: [inReview], detail: null } satisfies DataEnvelope<Mission[]>),
    });

    renderPage();

    fireEvent.click(await screen.findByText("Mission prête à valider"));

    await waitFor(() => expect(screen.getByRole("button", { name: /valider/i })).not.toBeDisabled());
    expect(screen.getByRole("button", { name: /rejeter/i })).not.toBeDisabled();
  });

  it("keeps the human-validation checkbox checked and disabled — never bypassable from the UI", async () => {
    globalThis.fetch = mockFetchRouter({
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
      "/missions": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Mission[]>),
    });

    renderPage();

    const checkbox = await screen.findByRole("checkbox");
    expect(checkbox).toBeChecked();
    expect(checkbox).toBeDisabled();
  });

  it("shows the backend's real rejection reason instead of a fabricated success when creation fails", async () => {
    globalThis.fetch = mockFetchRouter({
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
      "/missions": (init) => {
        if (init?.method === "POST") {
          return { status: "unknown", data: null, detail: "spécialité « x » est PLANNED" } satisfies DataEnvelope<Mission>;
        }
        return { status: "real", data: [], detail: null } satisfies DataEnvelope<Mission[]>;
      },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("Titre de la mission"), { target: { value: "t" } });
    fireEvent.change(screen.getByPlaceholderText(/ce que la mission doit accomplir/i), { target: { value: "o" } });
    fireEvent.click(screen.getByRole("button", { name: /créer la mission/i }));

    await waitFor(() => expect(screen.getByText(/PLANNED/)).toBeInTheDocument());
  });

  it("includes the selected Configured Agent's id when creating a mission (Agents V1, distinct from the Clone selector)", async () => {
    const configuredAgent = makeConfiguredAgent({});
    const created = makeMission({ id: "new-mission-id", title: "Ma mission", agent_id: "ca-1" });
    let createBody: Record<string, unknown> | null = null;
    globalThis.fetch = mockFetchRouter({
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
      "/configured-agents": () => ({ status: "real", data: [configuredAgent], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>),
      "/missions/new-mission-id": () => ({ status: "real", data: created, detail: null } satisfies DataEnvelope<Mission>),
      "/missions": (init) => {
        if (init?.method === "POST") {
          createBody = JSON.parse(init.body as string);
          return { status: "real", data: created, detail: null } satisfies DataEnvelope<Mission>;
        }
        return { status: "real", data: [created], detail: null } satisfies DataEnvelope<Mission[]>;
      },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("Titre de la mission"), { target: { value: "Ma mission" } });
    fireEvent.change(screen.getByPlaceholderText(/ce que la mission doit accomplir/i), { target: { value: "trouver" } });
    await waitFor(() => expect(screen.getByLabelText(/mon agent/i)).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText(/mon agent/i), { target: { value: "ca-1" } });
    fireEvent.click(screen.getByRole("button", { name: /créer la mission/i }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ agent_id: "ca-1" });
  });

  it("runs a queued mission via its assigned Agent and shows the real model/provider used, never a fabricated result", async () => {
    const configuredAgent = makeConfiguredAgent({});
    const queued = makeMission({ id: "m-agent", state: "queued", agent_id: "ca-1", title: "Mission avec agent" });
    const running = makeMission({ id: "m-agent", state: "review", agent_id: "ca-1", title: "Mission avec agent", result: "def add(a, b): return a + b" });
    const execution: AgentExecution = {
      id: "exec-1", agent_id: "ca-1", mission_id: "m-agent", status: "completed",
      model_id: "coder_main:qwen3.6:27b", provider_id: "ollama:http://localhost:11434",
      result: "def add(a, b): return a + b", error: null, fallback_reason: "success",
      started_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:00:05Z",
    };
    let missionState: Mission = queued;
    globalThis.fetch = mockFetchRouter({
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
      "/configured-agents": () => ({ status: "real", data: [configuredAgent], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>),
      "/missions/m-agent/run": () => {
        missionState = running;
        return { status: "real", data: execution, detail: null } satisfies DataEnvelope<AgentExecution>;
      },
      "/missions/m-agent/executions": () =>
        ({ status: "real", data: missionState === running ? [execution] : [], detail: null } satisfies DataEnvelope<AgentExecution[]>),
      "/missions/m-agent": () => ({ status: "real", data: missionState, detail: null } satisfies DataEnvelope<Mission>),
      "/missions": () => ({ status: "real", data: [queued], detail: null } satisfies DataEnvelope<Mission[]>),
    });

    renderPage();

    fireEvent.click(await screen.findByText("Mission avec agent"));

    const runButton = await screen.findByRole("button", { name: /lancer via agent/i });
    await waitFor(() => expect(runButton).not.toBeDisabled());
    fireEvent.click(runButton);

    await waitFor(() => expect(screen.getByText("coder_main:qwen3.6:27b")).toBeInTheDocument());
    expect(screen.getByText("ollama:http://localhost:11434")).toBeInTheDocument();
  });
});
