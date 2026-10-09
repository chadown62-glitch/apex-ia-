import { describe, expect, it } from "vitest";

import css from "./theme.css?raw";
import { contrast, extractHexColors } from "./contrast";

/** Jetons d'un bloc de theme.css (`:root {` ou `:root[data-theme="dune"] {`), lus dans le VRAI fichier. */
function tokens(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  expect(start, `bloc ${selector} absent de theme.css`).toBeGreaterThanOrEqual(0);
  const block = css.slice(start, css.indexOf("\n}", start));
  const out: Record<string, string> = {};
  for (const match of block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-f]{6})\s*;/gi)) out[match[1]] = match[2];
  return out;
}

const night = tokens(":root");
const dune = { ...night, ...tokens(':root[data-theme="dune"]') };

describe("theme tokens — WCAG contrast measured on theme.css", () => {
  for (const [name, theme] of [["Night", night], ["Dune", dune]] as const) {
    it(`${name}: text, muted text and status colours reach AA (4.5:1) on every surface`, () => {
      for (const surface of ["bg", "bg-elevated", "bg-panel", "bg-hover", "glass-fallback"]) {
        for (const fg of ["text", "text-muted", "accent", "success", "error", "warning", "info"]) {
          const result = contrast(theme[fg], theme[surface]);
          expect(result, `${fg}/${surface}`).not.toBeNull();
          expect(result!.ratio, `${name} ${fg} ${theme[fg]} sur ${surface} ${theme[surface]}`).toBeGreaterThanOrEqual(4.5);
        }
      }
    });

    it(`${name}: status pills (READY, OFFLINE, ERROR, DEGRADED) reach AA on the cards and their fallback`, () => {
      for (const surface of ["bg", "bg-elevated", "bg-panel", "glass-fallback"]) {
        for (const fg of ["status-real", "status-offline", "status-fail", "status-degraded", "status-unknown"]) {
          const result = contrast(theme[fg], theme[surface]);
          expect(result, `${fg}/${surface}`).not.toBeNull();
          expect(result!.ratio, `${name} ${fg} ${theme[fg]} sur ${surface} ${theme[surface]}`).toBeGreaterThanOrEqual(4.5);
        }
      }
    });

    it(`${name}: faint text stays readable (≥ 4.5:1 on the page background) and accent buttons are legible`, () => {
      expect(contrast(theme["text-faint"], theme.bg)!.ratio).toBeGreaterThanOrEqual(4.5);
      expect(contrast(theme["text-on-accent"], theme.accent)!.ratio).toBeGreaterThanOrEqual(4.5);
      expect(contrast(theme["text-on-accent"], theme["accent-strong"])!.ratio, "survol des boutons pleins").toBeGreaterThanOrEqual(4.5);
    });
  }

  it("Dune is never a clinical white: every surface keeps a warm tint", () => {
    for (const surface of ["bg", "bg-elevated", "bg-panel"]) {
      expect(dune[surface]).not.toBe("#ffffff");
      const [r, , b] = [1, 3, 5].map((i) => parseInt(dune[surface].slice(i, i + 2), 16));
      expect(r - b, `${surface} doit rester chaud`).toBeGreaterThan(4);
    }
  });
});

describe("contrast helpers", () => {
  it("matches the WCAG reference values", () => {
    expect(contrast("#000000", "#ffffff")!.ratio).toBe(21);
    expect(contrast("#777777", "#ffffff")!.ratio).toBe(4.48);
    expect(contrast("#fff", "#fff")!.ratio).toBe(1);
    expect(contrast("not-a-colour", "#fff")).toBeNull();
  });

  it("extracts hex codes cited in a text without duplicates or false positives", () => {
    expect(extractHexColors("Fond #0F172A, texte #e2e8f0, accent #A78BFA puis #0f172a ; issue #12 et id#abcdef")).toEqual([
      "#0f172a",
      "#e2e8f0",
      "#a78bfa",
    ]);
    expect(extractHexColors("#fff et #12345 et #1234567")).toEqual(["#fff"]);
  });
});
