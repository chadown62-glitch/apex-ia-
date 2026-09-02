import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, AgentExecution, AgentTemplate, ConfiguredAgent, DataEnvelope } from "../api/types";
import { AgentsPage } from "./AgentsPage";

function makeConfiguredAgent(overrides: Partial<ConfiguredAgent>): ConfiguredAgent {
  return {
    id: "ca-1111-0000-0000-0000-000000000000",
    name: "Mon développeur Python",
    description: "",
    role: "coder",
    specialty: null,
    model_role: "coder_main",
    required_capabilities: [],
    tools: [],
    permissions: [],
    instructions: null,
    enabled: true,
    status: "idle",
    project_id: null,
    template_id: null,
    created_by: "human",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeExecution(overrides: Partial<AgentExecution>): AgentExecution {
  return {
    id: "exec-1",
    agent_id: "ca-1111-0000-0000-0000-000000000000",
    mission_id: "m-1",
    status: "completed",
    model_id: "coder_main:qwen3.6:27b",
    provider_id: "ollama:http://localhost:11434",
    result: "def add(a, b): return a + b",
    error: null,
    fallback_reason: "success",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:00:05Z",
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

describe("AgentsPage — Mes Agents (ConfiguredAgent, Agents V1)", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists real configured agents with their derived status, distinct from Clones and Templates", async () => {
    const agent = makeConfiguredAgent({});
    globalThis.fetch = mockFetchRouter({
      "/configured-agents": () => ({ status: "real", data: [agent], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>),
      "/agent-templates": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<AgentTemplate[]>),
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
    });

    renderPage();

    await waitFor(() => expect(screen.getByText("Mon développeur Python")).toBeInTheDocument());
    expect(screen.getByText("IDLE")).toBeInTheDocument();
  });

  it("keeps the create button disabled until a name is filled in, and never auto-selects a tool or permission", async () => {
    globalThis.fetch = mockFetchRouter({
      "/configured-agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>),
      "/agent-templates": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<AgentTemplate[]>),
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
    });

    renderPage();

    const submit = await screen.findByRole("button", { name: /créer l'agent/i });
    expect(submit).toBeDisabled();
    await waitFor(() => expect(screen.getByText(/aucun agent créé pour l'instant/i)).toBeInTheDocument());

    // Les puces outils/permissions existent mais aucune n'est pré-activée.
    const filesChip = screen.getByRole("button", { name: "files" });
    expect(filesChip.className).not.toMatch(/chip--active/);
  });

  it("submits a real agent creation with explicitly toggled tools/permissions and displays the backend result", async () => {
    const created = makeConfiguredAgent({ id: "ca-new", name: "Nouvel agent", tools: ["files"], permissions: ["execute"] });
    let createBody: Record<string, unknown> | null = null;
    globalThis.fetch = mockFetchRouter({
      "/configured-agents": (init) => {
        if (init?.method === "POST") {
          createBody = JSON.parse(init.body as string);
          return { status: "real", data: created, detail: null } satisfies DataEnvelope<ConfiguredAgent>;
        }
        return { status: "real", data: [created], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>;
      },
      "/agent-templates": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<AgentTemplate[]>),
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
    });

    renderPage();

    fireEvent.change(await screen.findByPlaceholderText(/mon développeur python/i), { target: { value: "Nouvel agent" } });
    fireEvent.click(screen.getByRole("button", { name: "files" }));
    fireEvent.click(screen.getByRole("button", { name: "execute" }));

    const submit = screen.getByRole("button", { name: /créer l'agent/i });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ name: "Nouvel agent", tools: ["files"], permissions: ["execute"] });
  });

  it("shows the real execution history (model/provider used, fallback reason) when an agent is expanded — never fabricated", async () => {
    const agent = makeConfiguredAgent({});
    const execution = makeExecution({});
    globalThis.fetch = mockFetchRouter({
      "/configured-agents/ca-1111-0000-0000-0000-000000000000/executions": () =>
        ({ status: "real", data: [execution], detail: null } satisfies DataEnvelope<AgentExecution[]>),
      "/configured-agents": () => ({ status: "real", data: [agent], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>),
      "/agent-templates": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<AgentTemplate[]>),
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
    });

    renderPage();

    fireEvent.click(await screen.findByText("Mon développeur Python"));

    await waitFor(() => expect(screen.getByText("success")).toBeInTheDocument());
    expect(screen.getByText("coder_main:qwen3.6:27b")).toBeInTheDocument();
  });
});
