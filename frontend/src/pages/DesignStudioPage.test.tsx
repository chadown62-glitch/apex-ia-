import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DataEnvelope, DesignPackage } from "../api/types";
import { DesignStudioPage } from "./DesignStudioPage";

function makeDesign(overrides: Partial<DesignPackage>): DesignPackage {
  return {
    design_id: "design-1",
    request: { id: "req-1", text: "Un pavillon minimaliste", requested_by: "human", created_at: "2026-01-01T00:00:00Z" },
    state: "completed",
    steps: [
      {
        step: "analyze", state: "completed", input_summary: "requête", output_summary: "brief construit",
        provenance: "deterministic-template", logs: [], error: null,
        started_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:00:01Z", duration_s: 1,
      },
    ],
    brief: {
      design_id: "design-1", summary: "Un pavillon minimaliste", goals: ["simplicité"], constraints: [],
      audience: "grand public", style_keywords: ["minimaliste"], source_request: "Un pavillon minimaliste",
      provenance: "deterministic-template",
    },
    research: [],
    concepts: [
      {
        id: "concept-1", design_id: "design-1", version: 1, title: "Concept épuré",
        narrative: "Un volume simple.", key_elements: ["bois", "verre"], based_on_research: [],
        provenance: "deterministic-template",
      },
    ],
    visual_prompts: [
      {
        id: "prompt-1", concept_id: "concept-1", variant: 1, subject: "pavillon", environment: "parc",
        composition: "centré", architecture_design: "épuré", materials: "bois", colors: "neutre",
        light: "naturelle", camera: "grand angle", style: "minimaliste", constraints: "", elements_to_avoid: "",
        rendered: "Sujet : pavillon",
      },
    ],
    visual_results: [
      {
        id: "visual-1", prompt_id: "prompt-1", state: "unavailable", provider: "null",
        detail: "VISUAL_BACKEND_UNAVAILABLE — aucun backend connecté", artifact_content_hash: null,
      },
    ],
    validation: { design_id: "design-1", passed: true, checks: ["structure"], failures: [], validated_at: "2026-01-01T00:00:02Z" },
    artifacts: [{ content_hash: "sha256:abc", kind: "design_package", created_at: "2026-01-01T00:00:02Z" }],
    error: null,
    created_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:00:02Z",
    ready: true,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DesignStudioPage />
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

describe("DesignStudioPage", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("submits a real design request and displays the DesignPackage returned by the API", async () => {
    const created = makeDesign({});
    globalThis.fetch = mockFetchRouter({
      "/operator/designs/design-1": () => ({ status: "real", data: created, detail: null } satisfies DataEnvelope<DesignPackage>),
      "/operator/designs": (init) => {
        if (init?.method === "POST") {
          return { status: "real", data: created, detail: null } satisfies DataEnvelope<DesignPackage>;
        }
        return { status: "real", data: [created], detail: null } satisfies DataEnvelope<DesignPackage[]>;
      },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/décrivez ce que vous voulez concevoir/i), {
      target: { value: "Un pavillon minimaliste" },
    });
    const submit = screen.getByRole("button", { name: /lancer la conception/i });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);

    await waitFor(() => expect(screen.getByText("Concept épuré")).toBeInTheDocument());
  });

  it("keeps the submit button disabled until a request is typed", async () => {
    globalThis.fetch = mockFetchRouter({
      "/operator/designs": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<DesignPackage[]>),
    });

    renderPage();

    const submit = await screen.findByRole("button", { name: /lancer la conception/i });
    expect(submit).toBeDisabled();
  });

  it("reports the backend's real visual-generation unavailability instead of fabricating an image", async () => {
    const created = makeDesign({});
    globalThis.fetch = mockFetchRouter({
      "/operator/designs/design-1": () => ({ status: "real", data: created, detail: null } satisfies DataEnvelope<DesignPackage>),
      "/operator/designs": () => ({ status: "real", data: [created], detail: null } satisfies DataEnvelope<DesignPackage[]>),
    });

    renderPage();

    fireEvent.click(await screen.findByText("Un pavillon minimaliste"));

    await waitFor(() => expect(screen.getByText(/VISUAL_BACKEND_UNAVAILABLE/)).toBeInTheDocument());
  });

  it("shows a Resume action only when the selected design really failed", async () => {
    const failed = makeDesign({ design_id: "design-2", state: "failed", error: "échec réel du pipeline" });
    globalThis.fetch = mockFetchRouter({
      "/operator/designs/design-2": () => ({ status: "real", data: failed, detail: null } satisfies DataEnvelope<DesignPackage>),
      "/operator/designs": () => ({ status: "real", data: [failed], detail: null } satisfies DataEnvelope<DesignPackage[]>),
    });

    renderPage();

    fireEvent.click(await screen.findByText("Un pavillon minimaliste"));

    await waitFor(() => expect(screen.getByRole("button", { name: /reprendre la conception/i })).toBeInTheDocument());
  });

  it("never shows a Resume action for a completed design", async () => {
    const created = makeDesign({});
    globalThis.fetch = mockFetchRouter({
      "/operator/designs/design-1": () => ({ status: "real", data: created, detail: null } satisfies DataEnvelope<DesignPackage>),
      "/operator/designs": () => ({ status: "real", data: [created], detail: null } satisfies DataEnvelope<DesignPackage[]>),
    });

    renderPage();

    fireEvent.click(await screen.findByText("Un pavillon minimaliste"));

    await waitFor(() => expect(screen.getByText("Concept épuré")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /reprendre la conception/i })).not.toBeInTheDocument();
  });

  it("shows the backend's real rejection reason instead of a fabricated success when submission fails", async () => {
    globalThis.fetch = mockFetchRouter({
      "/operator/designs": (init) => {
        if (init?.method === "POST") {
          return { status: "unknown", data: null, detail: "APEX n'est pas prêt" } satisfies DataEnvelope<DesignPackage>;
        }
        return { status: "real", data: [], detail: null } satisfies DataEnvelope<DesignPackage[]>;
      },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/décrivez ce que vous voulez concevoir/i), {
      target: { value: "test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /lancer la conception/i }));

    await waitFor(() => expect(screen.getByText(/APEX n'est pas prêt/)).toBeInTheDocument());
  });
});
