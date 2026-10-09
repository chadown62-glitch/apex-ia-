import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatCard, tone } from "./StatCard";

describe("StatCard value tone", () => {
  it("colours a value only from the word it shows: OK/READY green, failures red, unknown muted", () => {
    expect(tone("OK")).toBe("ok");
    expect(tone(" ready ")).toBe("ok");
    expect(tone("OFFLINE")).toBe("fail");
    expect(tone("ERROR")).toBe("fail");
    expect(tone("DEGRADED")).toBe("warn");
    expect(tone("UNKNOWN")).toBe("unknown");
    expect(tone("57")).toBeUndefined();
    expect(tone(57 as unknown as string)).toBeUndefined();
  });

  it("puts the tone on the rendered value", () => {
    render(<StatCard label="Base de données" value="READY" status="real" />);
    expect(screen.getByText("READY").getAttribute("data-tone")).toBe("ok");
  });
});
