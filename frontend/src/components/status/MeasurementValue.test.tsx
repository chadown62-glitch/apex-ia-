import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MeasurementValue } from "./MeasurementValue";

describe("MeasurementValue", () => {
  it("displays the value when status is real", () => {
    render(<MeasurementValue measurement={{ value: 42, status: "real" }} unit=" s" />);
    expect(screen.getByText("42 s")).toBeInTheDocument();
    expect(screen.getByText("REAL")).toBeInTheDocument();
  });

  it("never displays a value when status is unknown, even if a stray value were present", () => {
    render(<MeasurementValue measurement={{ value: null, status: "unknown" }} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("42")).not.toBeInTheDocument();
  });

  it("never displays a value when status is simulated — a real 0 must never be confused with a placeholder", () => {
    render(<MeasurementValue measurement={{ value: 0, status: "simulated" }} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("SIMULATED")).toBeInTheDocument();
  });

  it("displays a real 0 as 0, not as a dash", () => {
    render(<MeasurementValue measurement={{ value: 0, status: "real" }} />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
