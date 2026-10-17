import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GameStudioPage } from "./GameStudioPage";

describe("Game Studio multi-asset project", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => { globalThis.fetch = originalFetch; vi.restoreAllMocks(); });

  it("sends the selected resource and judge options to the project API and shows dependencies", async () => {
    let submitted: Record<string, unknown> | null = null;
    globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^.*\/api\/v1/, "");
      if (path === "/game-studio/projects" && init?.method === "POST") {
        submitted = JSON.parse(String(init.body));
        return { ok: true, status: 200, json: async () => ({ status: "real", data: {
          id: "p1", mission_id: "m1", mission_history: ["m1"], mission_state: "running", status: "running",
          created_at: "2026-01-01T00:00:00Z", recipe: "garage-v1", request: submitted,
          steps: [
            { key: "vehicle", creation_type: "vehicule", depends_on: [], specialist: "Game Graphics", status: "running", job_id: null, mission: null },
            { key: "building", creation_type: "batiment", depends_on: ["vehicle"], specialist: "Game Graphics", status: "queued", job_id: null, mission: null },
          ], limitations: [], events: [],
        } }) };
      }
      if (path === "/game-studio/projects/p1") {
        return { ok: true, status: 200, json: async () => ({ status: "real", data: {
          id: "p1", mission_id: "m1", mission_history: ["m1"], mission_state: "running", status: "running",
          created_at: "2026-01-01T00:00:00Z", recipe: "garage-v1", request: submitted,
          steps: [
            { key: "vehicle", creation_type: "vehicule", depends_on: [], specialist: "Game Graphics", status: "running", job_id: null, mission: null },
            { key: "building", creation_type: "batiment", depends_on: ["vehicle"], specialist: "Game Graphics", status: "queued", job_id: null, mission: null },
          ], limitations: [], events: [], assembly: {
            verdict: "pass_technical", artifacts: [], source_mesh_counts: { vehicle: 20, building: 30, furniture: 10 },
            visual_quality_certified: false, blender_execution: {}, reopen_execution: {},
            engineer_handoff: { status: "blocked", reason: "vehicle_contract_mismatch", missing_vehicle_contract_wheel_nodes: [] },
          },
        } }) };
      }
      return { ok: true, status: 200, json: async () => ({ status: "real", data: path === "/game-studio/capabilities" ? {} : [] }) };
    }) as unknown as typeof fetch;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><GameStudioPage /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("Calcul du modèle"), { target: { value: "cpu" } });
    fireEvent.click(screen.getByRole("button", { name: "Créer la mission APEX" }));
    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted).toMatchObject({ creation_type: "vehicule", compute_backend: "cpu",
      judges_all_tasks: true, automatic_corrections: true });
    await waitFor(() => expect(screen.getByText("Dépendances : vehicle")).toBeInTheDocument());
    expect(screen.getByText(/Scène assemblée/)).toBeInTheDocument();
    expect(screen.getByText(/Game Engineer : blocked/)).toBeInTheDocument();
  });
});
