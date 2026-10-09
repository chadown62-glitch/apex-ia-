/**
 * Contraste WCAG 2.x — même formule que l'outil `color.contrast` du backend (apex/assistant/tools.py) :
 * ratio = (L1 + 0,05) / (L2 + 0,05), L1 la luminance relative la plus claire. Sert au nuancier du mode
 * Design et au test des jetons de thème ; jamais une valeur estimée.
 */

export interface ContrastResult {
  ratio: number;
  aaNormal: boolean;
  aaLarge: boolean;
  aaaNormal: boolean;
}

const HEX = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i;

export function parseHex(color: string): [number, number, number] | null {
  const match = HEX.exec(color.trim());
  if (!match) return null;
  let value = match[1];
  if (value.length === 3) value = value.split("").map((c) => c + c).join("");
  return [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16) / 255) as [number, number, number];
}

export function relativeLuminance(color: string): number | null {
  const rgb = parseHex(color);
  if (!rgb) return null;
  const [r, g, b] = rgb.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(foreground: string, background: string): ContrastResult | null {
  const a = relativeLuminance(foreground);
  const b = relativeLuminance(background);
  if (a === null || b === null) return null;
  const [l1, l2] = a >= b ? [a, b] : [b, a];
  const ratio = Math.round(((l1 + 0.05) / (l2 + 0.05)) * 100) / 100;
  return { ratio, aaNormal: ratio >= 4.5, aaLarge: ratio >= 3, aaaNormal: ratio >= 7 };
}

/** Codes #RRGGBB / #RGB cités dans un texte (dans l'ordre, sans doublon, normalisés en minuscules). */
export function extractHexColors(text: string, limit = 24): string[] {
  const found: string[] = [];
  for (const match of text.matchAll(/(?<![\w#])#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z])/g)) {
    const value = match[0].toLowerCase();
    if (!found.includes(value)) found.push(value);
    if (found.length >= limit) break;
  }
  return found;
}
