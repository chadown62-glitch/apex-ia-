import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GateStatusBadge, HealthStatusBadge, StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders the three DataStatus values and the frontend-only PLANNED value", () => {
    render(<StatusBadge status="real" />);
    expect(screen.getByText("REAL")).toBeInTheDocument();

    render(<StatusBadge status="simulated" />);
    expect(screen.getByText("SIMULATED")).toBeInTheDocument();

    render(<StatusBadge status="unknown" />);
    expect(screen.getByText("UNKNOWN")).toBeInTheDocument();

    render(<StatusBadge status="planned" />);
    expect(screen.getByText("PLANNED")).toBeInTheDocument();
  });
});

describe("GateStatusBadge", () => {
  it("renders a known Gate status verbatim", () => {
    render(<GateStatusBadge status="DEGRADED" />);
    expect(screen.getByText("DEGRADED")).toBeInTheDocument();
  });

  it("falls back to the unknown style for an unrecognized status without hiding the label", () => {
    render(<GateStatusBadge status="SOMETHING_NEW" />);
    const el = screen.getByText("SOMETHING_NEW");
    expect(el.className).toContain("status-badge--unknown");
  });
});

describe("HealthStatusBadge", () => {
  it("uses the health vocabulary (OK/DEGRADED/FAILED), distinct from DataStatus", () => {
    render(<HealthStatusBadge status="failed" />);
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });
});
