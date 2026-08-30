import { describe, expect, it } from "vitest";

import { specialtyForAgentType } from "./data";
import { SPECIALTIES } from "./registry";

describe("SPECIALTIES registry", () => {
  it("has unique ids and unique paths", () => {
    const ids = SPECIALTIES.map((s) => s.id);
    const paths = SPECIALTIES.map((s) => s.path);
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("never claims status real without a wired component — no false REAL specialty", () => {
    for (const s of SPECIALTIES) {
      if (s.status === "real") {
        expect(s.component, `${s.id} is "real" but has no component`).toBeDefined();
        expect(s.devStage).toBe("connected");
      }
    }
  });

  it("keeps devStage consistent with status: ui-scaffold and concept are always planned", () => {
    for (const s of SPECIALTIES) {
      if (s.devStage === "ui-scaffold" || s.devStage === "concept") {
        expect(s.status, `${s.id} has devStage ${s.devStage} but status ${s.status}`).toBe("planned");
      }
    }
  });

  it("gives every ui-scaffold specialty a real page component (not the generic placeholder)", () => {
    for (const s of SPECIALTIES) {
      if (s.devStage === "ui-scaffold") {
        expect(s.component, `${s.id} is ui-scaffold but has no dedicated component`).toBeDefined();
      }
    }
  });

  it("leaves concept specialties without a component, so they fall back to PlannedSpecialtyPage", () => {
    for (const s of SPECIALTIES) {
      if (s.devStage === "concept") {
        expect(s.component).toBeUndefined();
      }
    }
  });
});

describe("specialtyForAgentType", () => {
  it("maps a developer clone to Code Intelligence", () => {
    expect(specialtyForAgentType("developer")?.id).toBe("code-intelligence");
  });

  it("maps a research clone to Research", () => {
    expect(specialtyForAgentType("research")?.id).toBe("research");
  });

  it("never maps a type to the generic 'agents' specialty itself, even though it lists all 9 types", () => {
    // "agents" (Agent Center) intentionally lists every clone type in its
    // own workspace metadata (it covers all of them) — specialtyForAgentType
    // must skip it, or every filter in Agent Center would resolve to itself.
    for (const type of ["research", "developer", "integrator"] as const) {
      expect(specialtyForAgentType(type)?.id).not.toBe("agents");
    }
  });
});
