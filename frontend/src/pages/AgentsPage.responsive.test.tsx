import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, AgentTemplate, ConfiguredAgent, DataEnvelope } from "../api/types";
import { AgentsPage } from "./AgentsPage";

/**
 * Verrouille les points structurels dont dépend le correctif responsive
 * « Agents < 900px » (audit UI V1, Priorité 2) — jsdom n'évalue aucune
 * media query, donc ces tests ne prouvent pas le reflow visuel lui-même
 * (prouvé séparément par capture Playwright réelle), mais garantissent que
 * les classes CSS dont ce reflow dépend restent bien posées sur les bons
 * éléments : si quelqu'un les retire (ex. retour à des styles inline non
 * réactifs), ce test échoue immédiatement.
 */
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

function makeConfiguredAgent(overrides: Partial<ConfiguredAgent>): ConfiguredAgent {
  return {
    id: "ca-1", name: "Un agent au nom volontairement assez long pour tester", description: "",
    role: "coder", specialty: null, model_role: "coder_main", required_capabilities: [],
    tools: [], permissions: [], instructions: null, enabled: true, status: "idle",
    project_id: null, template_id: null, created_by: "human",
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeTemplate(overrides: Partial<AgentTemplate>): AgentTemplate {
  return {
    id: "t-1", name: "Un template", role: "coder", model_role: null, tools: [],
    description: "", status: "defined", created_by: "human", created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("AgentsPage — structure responsive (verrouillage anti-régression)", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("gives the configured-agent list header a shrinkable, truncating title element (never a bare unstyled div)", async () => {
    const agent = makeConfiguredAgent({});
    globalThis.fetch = mockFetchRouter({
      "/configured-agents": () => ({ status: "real", data: [agent], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>),
      "/agent-templates": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<AgentTemplate[]>),
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
    });

    renderPage();

    const title = await screen.findByText(agent.name);
    const titleEl = title.closest(".configured-agent-list__title");
    expect(titleEl).not.toBeNull();
    // Le pill de statut doit être un frère direct du header, jamais imbriqué
    // dans le bloc titre — sinon il rétrécirait avec lui au lieu de rester
    // à taille fixe (`flex-shrink: 0` posé sur `.configured-agent-status`).
    const header = title.closest(".configured-agent-list__header");
    expect(header?.querySelector(".configured-agent-status")).not.toBeNull();
  });

  it("renders agent templates with the reusable agent-template-list classes, never inline overflow-prone styles", async () => {
    const template = makeTemplate({ name: "Codeur Python — un nom assez long pour la revue" });
    globalThis.fetch = mockFetchRouter({
      "/agent-templates": () => ({ status: "real", data: [template], detail: null } satisfies DataEnvelope<AgentTemplate[]>),
      "/configured-agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>),
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
    });

    renderPage();

    const title = await screen.findByText((content) => content.includes(template.name));
    expect(title.closest(".agent-template-list__title")).not.toBeNull();
    expect(title.closest(".agent-template-list__item")).not.toBeNull();
    expect(document.querySelector(".agent-template-list")).not.toBeNull();
    // Aucun style inline résiduel sur le conteneur de liste — la
    // réorganisation responsive doit passer entièrement par le CSS
    // (media queries), jamais par des styles calculés en JS.
    const list = document.querySelector("ul.agent-template-list") as HTMLElement | null;
    expect(list?.getAttribute("style")).toBeNull();
  });

  it("wraps the execution history table in a horizontally scrollable container instead of letting it overflow the page", async () => {
    const agent = makeConfiguredAgent({ id: "ca-2" });
    globalThis.fetch = mockFetchRouter({
      // Le motif spécifique DOIT être vérifié avant "/configured-agents"
      // (générique) — mockFetchRouter teste `path.startsWith(pattern)` dans
      // l'ordre d'insertion, donc le motif le plus court intercepterait
      // sinon aussi les requêtes d'exécutions.
      "/configured-agents/ca-2/executions": () =>
        ({
          status: "real",
          data: [
            {
              id: "e-1", agent_id: "ca-2", mission_id: "m-1", status: "completed",
              model_id: "qwen3-coder:30b-a3b-instruct:q4_k_m", provider_id: "ollama:http://127.0.0.1:11434",
              result: "ok", error: null, fallback_reason: "success",
              started_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:00:01Z",
            },
          ],
          detail: null,
        }),
      "/configured-agents": () => ({ status: "real", data: [agent], detail: null } satisfies DataEnvelope<ConfiguredAgent[]>),
      "/agent-templates": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<AgentTemplate[]>),
      "/agents": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Agent[]>),
    });

    renderPage();

    const header = await screen.findByText(agent.name);
    fireEvent.click(header.closest("button") as HTMLElement);

    await waitFor(() => expect(screen.getByText("qwen3-coder:30b-a3b-instruct:q4_k_m")).toBeInTheDocument());
    const table = screen.getByText("qwen3-coder:30b-a3b-instruct:q4_k_m").closest("table");
    expect(table?.closest(".agent-table-wrap")).not.toBeNull();
  });
});
