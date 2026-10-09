/**
 * Peinture du fond d'APEX, une seule fois par thème et par taille de fenêtre (jamais à chaque image) :
 * - Dune : soie ivoire — une surface d'ondes continues (champ de hauteur déformé) éclairée (diffus, reflet
 *   spéculaire, nacre irisée discrète sur les flancs), calculée sur une grille réduite puis agrandie lissée ;
 * - Night : ciel profond — nébuleuse violette en voiles doux, poussières sombres, étoiles fixes.
 * Déterministe (aucun hasard non semé) : le même écran donne la même image. ~0,1 s en 1920×1080 (mesuré).
 */

import type { Theme } from "./appearance";

type Rgb = [number, number, number];
type Vec = [number, number, number];

function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function mix(a: Rgb, b: Rgb, t: number): Rgb {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

function ramp(stops: Array<[number, Rgb]>, x: number): Rgb {
  const v = Math.max(0, Math.min(1, x));
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const [x0, c0] = stops[i - 1];
      const [x1, c1] = stops[i];
      return mix(c0, c1, (v - x0) / (x1 - x0 || 1));
    }
  }
  return stops[stops.length - 1][1];
}

function normalize(v: Vec): Vec {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

function dot(a: Vec, b: Vec): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

const SILK_STOPS: Array<[number, Rgb]> = [
  [0, [196, 174, 140]],
  [0.3, [220, 203, 175]],
  [0.55, [236, 225, 205]],
  [0.78, [247, 240, 228]],
  [1, [255, 252, 246]],
];

function silkHeight(x: number, y: number): number {
  // Plis diagonaux qui ondulent (déformation du domaine), crêtes vives, houle lente en travers.
  const u = x * 1.0 + y * 0.85;
  const v = -x * 0.65 + y * 1.0;
  const warp = 0.13 * Math.sin(v * 4.6 + 1.3) + 0.05 * Math.sin(v * 8.3 + u * 2.4 + 0.8);
  const a = u * 10.5 + warp * 13.0;
  const fold = Math.sin(a) + 0.42 * Math.sin(2 * a + 0.9) + 0.14 * Math.sin(3 * a + 0.3);
  const cross = 0.35 * Math.sin(v * 6.5 + u * 2.2 + 0.4) + 0.18 * Math.sin(v * 13.0 - u * 3.0);
  const swell = Math.sin(v * 2.6 + u * 1.4 + 0.4);
  return fold * 0.034 + cross * 0.02 + swell * 0.05;
}

function paintSilk(ctx: CanvasRenderingContext2D, W: number, H: number): void {
  const scale = 4;
  const w = Math.max(1, Math.ceil(W / scale));
  const h = Math.max(1, Math.ceil(H / scale));
  const small = document.createElement("canvas");
  small.width = w;
  small.height = h;
  const sctx = small.getContext("2d");
  if (!sctx) return;
  const img = sctx.createImageData(w, h);
  const light = normalize([-0.45, -0.7, 0.55]);
  const half = normalize([light[0], light[1], light[2] + 1]);
  const e = 0.0015;
  for (let j = 0; j < h; j++) {
    for (let i = 0; i < w; i++) {
      const x = i / w;
      const y = (j / h) * (H / W);
      const dzdx = (silkHeight(x + e, y) - silkHeight(x - e, y)) / (2 * e);
      const dzdy = (silkHeight(x, y + e) - silkHeight(x, y - e)) / (2 * e);
      const n = normalize([-dzdx, -dzdy, 1]);
      const diffuse = Math.max(0, dot(n, light));
      const spec = Math.pow(Math.max(0, dot(n, half)), 120) * 255 * 0.42;
      const rgb = ramp(SILK_STOPS, 0.04 + diffuse * 0.96);
      // Nacre : teinte selon l'orientation du flanc, plus présente en haut à droite.
      const hue = Math.atan2(n[1], n[0]);
      const irid = Math.min(1, (1 - n[2]) * 2.2) * (0.25 + 0.75 * Math.max(0, x - 0.45) * (1 - y * 0.8)) * 0.24;
      const sheen: Rgb = [
        255 * (0.5 + 0.5 * Math.cos(hue)),
        235 * (0.5 + 0.5 * Math.cos(hue - 2.1)),
        240 * (0.5 + 0.5 * Math.cos(hue + 2.1)),
      ];
      const k = (j * w + i) * 4;
      img.data[k] = Math.min(255, rgb[0] * (1 - irid) + sheen[0] * irid + spec);
      img.data[k + 1] = Math.min(255, rgb[1] * (1 - irid) + sheen[1] * irid + spec);
      img.data[k + 2] = Math.min(255, rgb[2] * (1 - irid) + sheen[2] * irid + spec);
      img.data[k + 3] = 255;
    }
  }
  sctx.putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(small, 0, 0, W, H);
  // Voile clair au centre : les cartes de verre restent lisibles.
  const veil = ctx.createRadialGradient(W * 0.56, H * 0.52, Math.min(W, H) * 0.15, W * 0.56, H * 0.52, Math.max(W, H) * 0.7);
  veil.addColorStop(0, "rgba(250,246,238,0.42)");
  veil.addColorStop(1, "rgba(250,246,238,0)");
  ctx.fillStyle = veil;
  ctx.fillRect(0, 0, W, H);
}

function paintNebula(ctx: CanvasRenderingContext2D, W: number, H: number): void {
  const rand = rng(7);
  const base = ctx.createLinearGradient(0, 0, W * 0.4, H);
  base.addColorStop(0, "#090b22");
  base.addColorStop(0.55, "#0b0a24");
  base.addColorStop(1, "#05060f");
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, W, H);
  ctx.globalCompositeOperation = "lighter";
  // Nébuleuse : beaucoup de petits voiles peu opaques (filaments), centre-gauche.
  const clouds: Array<[number, number, number, Rgb, number, number]> = [
    [0.36, 0.42, 0.5, [92, 64, 190], 0.16, 26],
    [0.22, 0.58, 0.32, [60, 72, 170], 0.12, 16],
    [0.58, 0.3, 0.3, [128, 70, 176], 0.1, 14],
    [0.8, 0.72, 0.36, [54, 44, 132], 0.1, 12],
    [0.44, 0.46, 0.16, [176, 140, 255], 0.12, 10],
  ];
  for (const [x, y, r, c, a, n] of clouds) {
    for (let k = 0; k < n; k++) {
      const ang = rand() * Math.PI * 2;
      const dist = rand() * r * 0.55;
      const cx = (x + Math.cos(ang) * dist * 1.4) * W;
      const cy = (y + Math.sin(ang) * dist * 0.7) * H;
      const rr = r * Math.max(W, H) * (0.08 + rand() * 0.22);
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, rr);
      grad.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},${a * (0.4 + rand() * 0.6)})`);
      grad.addColorStop(0.6, `rgba(${c[0]},${c[1]},${c[2]},${a * 0.25})`);
      grad.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0)`);
      ctx.fillStyle = grad;
      ctx.fillRect(cx - rr, cy - rr, rr * 2, rr * 2);
    }
  }
  ctx.globalCompositeOperation = "source-over";
  for (let k = 0; k < 14; k++) {
    const cx = (0.2 + rand() * 0.5) * W;
    const cy = (0.25 + rand() * 0.5) * H;
    const rr = (0.05 + rand() * 0.12) * Math.max(W, H);
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, rr);
    grad.addColorStop(0, "rgba(4,4,14,0.35)");
    grad.addColorStop(1, "rgba(4,4,14,0)");
    ctx.fillStyle = grad;
    ctx.fillRect(cx - rr, cy - rr, rr * 2, rr * 2);
  }
  const count = Math.round((W * H) / 2600);
  for (let k = 0; k < count; k++) {
    const x = rand() * W;
    const y = rand() * H;
    const big = rand() < 0.012;
    const r = big ? 1.1 + rand() * 0.9 : 0.25 + rand() * 0.7;
    const a = big ? 0.85 : 0.18 + rand() * 0.55;
    const tint = rand();
    const col = tint < 0.15 ? "200,190,255" : tint < 0.25 ? "190,215,255" : "255,255,255";
    ctx.fillStyle = `rgba(${col},${a})`;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    if (big) {
      const halo = ctx.createRadialGradient(x, y, 0, x, y, r * 7);
      halo.addColorStop(0, `rgba(${col},0.22)`);
      halo.addColorStop(1, `rgba(${col},0)`);
      ctx.fillStyle = halo;
      ctx.fillRect(x - r * 7, y - r * 7, r * 14, r * 14);
    }
  }
}

/**
 * Peint le fond du thème dans `canvas` (taille CSS `width`×`height`). Faux si le canvas est indisponible.
 * Le canvas suit la densité de l'écran (`dpr`, plafonnée à 2 : Windows à 125-150 %) pour des étoiles nettes ;
 * le dessin reste exprimé en pixels CSS, donc même image et même coût de calcul de la soie à toute densité.
 */
export function paintAmbient(canvas: HTMLCanvasElement, theme: Theme, width: number, height: number, dpr = 1): boolean {
  const ctx = canvas.getContext("2d");
  if (!ctx || width < 1 || height < 1) return false;
  const W = Math.round(width);
  const H = Math.round(height);
  const ratio = Math.max(1, Math.min(2, dpr || 1));
  canvas.width = Math.round(W * ratio);
  canvas.height = Math.round(H * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  if (theme === "dune") paintSilk(ctx, W, H);
  else paintNebula(ctx, W, H);
  return true;
}
