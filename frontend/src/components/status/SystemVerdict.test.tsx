import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError, SESSION_EXPIRED_MESSAGE } from "../../api/client";
import type { DataEnvelope, SystemStatus } from "../../api/types";
import { SystemStatusTable, SystemVerdictBadge, deriveSystemVerdict } from "./SystemVerdict";

function status(overrides: Partial<SystemStatus> = {}): SystemStatus {
  return {
    service: "apex",
    versions: { apex: "0.2.2", api: "v1", panel_protocol: 1 },
    backend: { pid: 42, started_at: "2026-10-02T19:00:00+00:00" },
    components: [
      { component: "api", state: "ready", detail: "requête authentifiée servie", latency_ms: null },
      { component: "ollama", state: "offline", detail: "connexion refusée", latency_ms: null },
    ],
    overall: { verdict: "degraded", not_ready: ["ollama"] },
    gate: { startup_authorized: true, phase_2_authorized: false, startup_unverified: ["GATE-17"], generated_at: null },
    measured_at: "2026-10-02T19:00:05+00:00",
    ...overrides,
  };
}

const real = (data: SystemStatus): DataEnvelope<SystemStatus> => ({ status: "real", data, detail: null });

describe("deriveSystemVerdict", () => {
  it("shows exactly the backend verdict, with the components that are not ready", () => {
    const view = deriveSystemVerdict(real(status()), null);
    expect(view.kind).toBe("degraded");
    expect(view.label).toBe("APEX DEGRADED");
    expect(view.detail).toContain("ollama (offline — connexion refusée)");
  });

  it("is READY only when the backend says so", () => {
    const ready = status({ overall: { verdict: "ready", not_ready: [] } });
    expect(deriveSystemVerdict(real(ready), null).label).toBe("APEX READY");
  });

  it("never shows READY without an answer", () => {
    expect(deriveSystemVerdict(undefined, null).kind).toBe("loading");
    const unknown: DataEnvelope<SystemStatus> = { status: "unknown", data: null, detail: "API injoignable : refused" };
    expect(deriveSystemVerdict(unknown, null)).toMatchObject({ kind: "unreachable", detail: "API injoignable : refused" });
  });

  it("tells the user to reopen APEX from the Panel when the session expired", () => {
    const view = deriveSystemVerdict(real(status()), new ApiError(SESSION_EXPIRED_MESSAGE, 401));
    expect(view).toMatchObject({ kind: "session", label: "SESSION EXPIRÉE", detail: SESSION_EXPIRED_MESSAGE });
  });
});

describe("SystemStatusTable", () => {
  it("lists every measured component and keeps phase 2 apart from the startup decision", () => {
    render(<SystemStatusTable status={status()} />);
    expect(screen.getByText("ollama")).toBeInTheDocument();
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
    expect(screen.getByText(/Démarrage : autorisé · Phase 2 de la feuille de route : non autorisée/)).toBeInTheDocument();
  });

  it("never invents a gate decision it could not read", () => {
    render(<SystemStatusTable status={status({ gate: null })} />);
    expect(screen.getByText(/rapport illisible \(non vérifiée\)/)).toBeInTheDocument();
  });

  it("renders the badge label", () => {
    render(<SystemVerdictBadge view={{ kind: "ready", label: "APEX READY", detail: "ok" }} />);
    expect(screen.getByText("APEX READY")).toBeInTheDocument();
  });
});
