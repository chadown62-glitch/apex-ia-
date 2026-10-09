/**
 * Fond d'APEX — dégradé du thème (CSS `--backdrop`) et, si les particules sont activées, un canvas léger :
 * ciel étoilé discret (Night) ou poussière de sable en suspension (Dune).
 *
 * Sobriété : ~70 points au plus, 30 images/s maximum, résolution plafonnée (DPR ≤ 1,5), pause quand la
 * fenêtre est masquée. Sans mouvement autorisé (animations désactivées, « réduire les animations », ou
 * préférence système) : une seule image FIXE est dessinée, aucune boucle ne tourne.
 */

import { useEffect, useRef } from "react";

import { motionAllowed, useAppearance, type Theme } from "../theme/appearance";

interface Particle {
  x: number;
  y: number;
  r: number;
  alpha: number;
  phase: number;
  speed: number;
  drift: number;
  accent: boolean;
}

function seed(theme: Theme, width: number, height: number): Particle[] {
  const count = Math.round(Math.min(70, (width * height) / 26000));
  return Array.from({ length: count }, () => ({
    x: Math.random() * width,
    y: Math.random() * height,
    r: theme === "night" ? Math.random() * 1.1 + 0.3 : Math.random() * 1.6 + 0.6,
    alpha: theme === "night" ? Math.random() * 0.55 + 0.2 : Math.random() * 0.22 + 0.08,
    phase: Math.random() * Math.PI * 2,
    speed: Math.random() * 0.6 + 0.2,
    drift: (Math.random() - 0.5) * 0.25,
    accent: Math.random() < (theme === "night" ? 0.12 : 0.2),
  }));
}

function cssRgb(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function Backdrop() {
  const appearance = useAppearance();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animate = motionAllowed(appearance);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!appearance.particles || !canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return; // canvas indisponible (environnement de test, rendu logiciel) : dégradé seul
    const ctx: CanvasRenderingContext2D = context;
    const theme = appearance.theme;
    let width = 0;
    let height = 0;
    let particles: Particle[] = [];
    let frame = 0;
    let lastDraw = 0;
    const base = cssRgb("--particle-rgb", "226, 220, 255");
    const accent = cssRgb("--particle-accent-rgb", "167, 139, 250");

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas!.width = Math.round(width * dpr);
      canvas!.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      particles = seed(theme, width, height);
    }

    function draw(time: number) {
      ctx.clearRect(0, 0, width, height);
      for (const p of particles) {
        let alpha = p.alpha;
        if (animate) {
          if (theme === "night") {
            alpha = p.alpha * (0.65 + 0.35 * Math.sin(time / 1000 * p.speed + p.phase)); // scintillement lent
            p.y -= 0.012 * p.speed;
          } else {
            p.x += p.drift + 0.08 * p.speed; // la poussière dérive avec le vent
            p.y += Math.sin(time / 2400 + p.phase) * 0.05;
          }
          if (p.x > width + 4) p.x = -4;
          if (p.x < -4) p.x = width + 4;
          if (p.y < -4) p.y = height + 4;
        }
        ctx.beginPath();
        ctx.fillStyle = `rgba(${p.accent ? accent : base}, ${alpha.toFixed(3)})`;
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    function loop(time: number) {
      frame = window.requestAnimationFrame(loop);
      if (document.hidden || time - lastDraw < 33) return;
      lastDraw = time;
      draw(time);
    }

    resize();
    draw(0);
    const onResize = () => {
      resize();
      draw(performance.now());
    };
    window.addEventListener("resize", onResize);
    if (animate) frame = window.requestAnimationFrame(loop);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
    };
  }, [appearance.particles, appearance.theme, animate]);

  return (
    <div className="ax-backdrop" aria-hidden="true">
      {appearance.particles && <canvas ref={canvasRef} className="ax-backdrop__canvas" data-animated={animate ? "true" : "false"} />}
    </div>
  );
}
