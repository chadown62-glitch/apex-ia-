import { describe, expect, it } from "vitest";

import { paintAmbient } from "./ambient";

/** Contexte 2D factice qui enregistre chaque appel (jsdom n'a pas de canvas) : on vérifie la taille, la
 * densité et le déterminisme du décor, pas son rendu (vérifié, lui, sur captures réelles). */
function recorder() {
  const calls: string[] = [];
  const gradient = () => ({ addColorStop: (o: number, c: string) => calls.push(`stop ${o} ${c}`) });
  const ctx = new Proxy(
    {
      setTransform: (...a: number[]) => calls.push(`setTransform ${a.join(",")}`),
      createLinearGradient: gradient,
      createRadialGradient: gradient,
      fillRect: (...a: number[]) => calls.push(`fillRect ${a.map((v) => v.toFixed(2)).join(",")}`),
      arc: (...a: number[]) => calls.push(`arc ${a.map((v) => v.toFixed(2)).join(",")}`),
      beginPath: () => undefined,
      fill: () => undefined,
    } as Record<string, unknown>,
    {
      set(target, key, value) {
        calls.push(`${String(key)}=${String(value)}`);
        target[key as string] = value;
        return true;
      },
    },
  );
  const canvas = { width: 0, height: 0, getContext: () => ctx } as unknown as HTMLCanvasElement;
  return { canvas, calls };
}

describe("paintAmbient", () => {
  it("sizes the canvas to the screen density (capped at 2) while drawing in CSS pixels", () => {
    const a = recorder();
    expect(paintAmbient(a.canvas, "night", 800, 500, 1.5)).toBe(true);
    expect([a.canvas.width, a.canvas.height]).toEqual([1200, 750]);
    expect(a.calls[0]).toBe("setTransform 1.5,0,0,1.5,0,0");
    expect(a.calls).toContain("fillRect 0.00,0.00,800.00,500.00");

    const b = recorder();
    paintAmbient(b.canvas, "night", 800, 500, 3);
    expect([b.canvas.width, b.canvas.height]).toEqual([1600, 1000]);
  });

  it("is deterministic: the same window gives the same sky, star for star", () => {
    const a = recorder();
    const b = recorder();
    paintAmbient(a.canvas, "night", 640, 400);
    paintAmbient(b.canvas, "night", 640, 400);
    expect(a.calls.filter((c) => c.startsWith("arc")).length).toBeGreaterThan(50);
    expect(a.calls).toEqual(b.calls);
  });

  it("reports failure instead of pretending, when no canvas is available or the window is empty", () => {
    const none = { width: 0, height: 0, getContext: () => null } as unknown as HTMLCanvasElement;
    expect(paintAmbient(none, "dune", 800, 600)).toBe(false);
    expect(paintAmbient(recorder().canvas, "night", 0, 600)).toBe(false);
  });
});
