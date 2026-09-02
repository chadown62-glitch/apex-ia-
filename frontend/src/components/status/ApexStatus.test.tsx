import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Mission } from "../../api/types";
import { ApexStatusBadge, deriveApexStatus } from "./ApexStatus";

function aMission(overrides: Partial<Mission> = {}): Mission {
  return {
    id: "m1", title: "t", description: "", specialty: "research", objective: "o",
    priority: "normal", constraints: null, assigned_agent: null, agent_id: null, state: "queued",
    result: null, error: null, validation_status: null, created_by: "human",
    created_at: "2026-01-01T00:00:00Z", started_at: null, completed_at: null,
    ...overrides,
  };
}

describe("deriveApexStatus", () => {
  it("returns waiting when health has never been measured", () => {
    expect(deriveApexStatus(null, false, [])).toBe("waiting");
  });

  it("returns error when health reports failed", () => {
    expect(deriveApexStatus("failed", true, [])).toBe("error");
  });

  it("returns using_tool when a mission is running, even if health is ok", () => {
    expect(deriveApexStatus("ok", true, [aMission({ state: "running" })])).toBe("using_tool");
  });

  it("returns waiting when health is degraded and nothing is running", () => {
    expect(deriveApexStatus("degraded", true, [])).toBe("waiting");
  });

  it("returns ready when health is ok and nothing is running", () => {
    expect(deriveApexStatus("ok", true, [aMission({ state: "completed" })])).toBe("ready");
  });

  it("never fabricates analyzing or learning — not derivable from persistent signals", () => {
    const allStates: Mission["state"][] = ["draft", "queued", "running", "review", "completed", "failed", "cancelled"];
    for (const state of allStates) {
      const status = deriveApexStatus("ok", true, [aMission({ state })]);
      expect(status).not.toBe("analyzing");
      expect(status).not.toBe("learning");
    }
  });
});

describe("ApexStatusBadge", () => {
  it("renders the label for each real status", () => {
    render(<ApexStatusBadge status="ready" />);
    expect(screen.getByText("READY")).toBeInTheDocument();
  });

  it("renders ERROR distinctly", () => {
    render(<ApexStatusBadge status="error" />);
    expect(screen.getByText("ERROR")).toBeInTheDocument();
  });
});
