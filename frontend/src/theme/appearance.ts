/**
 * Apparence d'APEX — UNE source d'état pour toute l'interface (assistant et centre de contrôle).
 *
 * - Vérité : les paramètres enregistrés par le backend (`appearance` dans /assistant/settings) ;
 *   `AppearanceSync` (assistant/AppearanceSync.tsx) les charge au démarrage.
 * - Cache local (`localStorage`, clé `apex-appearance`) : lu par public/theme-init.js AVANT le premier
 *   rendu, pour qu'aucun flash de thème n'apparaisse ; jamais une source de vérité concurrente.
 * - DOM : `data-theme` (night|dune), `data-animations` (on|off), `data-particles` (on|off),
 *   `data-reduce-motion` (system|always|never) sur <html> ; theme.css et le fond animé s'y conforment.
 */

import { useSyncExternalStore } from "react";

export type Theme = "night" | "dune";
export type ReduceMotion = "system" | "always" | "never";

export interface Appearance {
  theme: Theme;
  animations: boolean;
  particles: boolean;
  reduce_motion: ReduceMotion;
}

export const STORAGE_KEY = "apex-appearance";
/** Clé de la version précédente (valeurs "dark" | "light") — lue une fois pour ne pas perdre le choix. */
export const LEGACY_STORAGE_KEY = "apex-theme";

export const DEFAULT_APPEARANCE: Appearance = { theme: "night", animations: true, particles: true, reduce_motion: "system" };

function systemPrefersLight(): boolean {
  try {
    return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: light)").matches;
  } catch {
    return false;
  }
}

function asTheme(value: unknown): Theme | null {
  if (value === "night" || value === "dark") return "night";
  if (value === "dune" || value === "light") return "dune";
  return null;
}

/** État initial : cache local (format courant, sinon ancien choix "dark"/"light"), sinon préférence système. */
export function readCachedAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Appearance>;
      return {
        theme: asTheme(parsed.theme) ?? DEFAULT_APPEARANCE.theme,
        animations: typeof parsed.animations === "boolean" ? parsed.animations : DEFAULT_APPEARANCE.animations,
        particles: typeof parsed.particles === "boolean" ? parsed.particles : DEFAULT_APPEARANCE.particles,
        reduce_motion: ["system", "always", "never"].includes(parsed.reduce_motion as string)
          ? (parsed.reduce_motion as ReduceMotion)
          : DEFAULT_APPEARANCE.reduce_motion,
      };
    }
    const legacy = asTheme(localStorage.getItem(LEGACY_STORAGE_KEY));
    if (legacy) return { ...DEFAULT_APPEARANCE, theme: legacy };
  } catch {
    /* stockage indisponible ou valeur corrompue : préférence système. */
  }
  return { ...DEFAULT_APPEARANCE, theme: systemPrefersLight() ? "dune" : "night" };
}

export function applyAppearance(appearance: Appearance): void {
  const root = document.documentElement;
  root.setAttribute("data-theme", appearance.theme);
  root.setAttribute("data-animations", appearance.animations ? "on" : "off");
  root.setAttribute("data-particles", appearance.particles ? "on" : "off");
  root.setAttribute("data-reduce-motion", appearance.reduce_motion);
}

/** Mouvement décoratif autorisé (fond animé) : animations activées ET pas de réduction demandée. */
export function motionAllowed(appearance: Appearance): boolean {
  if (!appearance.animations || appearance.reduce_motion === "always") return false;
  if (appearance.reduce_motion === "never") return true;
  try {
    return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
}

let state: Appearance = readCachedAppearance();
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function persistLocally(appearance: Appearance): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(appearance));
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    /* stockage indisponible : l'apparence reste active pour cette session. */
  }
}

/** Applique un changement local (DOM + cache). L'enregistrement serveur est fait par l'appelant
 * (`saveAppearance`), pour que l'échec éventuel soit affiché là où l'utilisateur a agi. */
export function setAppearance(changes: Partial<Appearance>): Appearance {
  state = { ...state, ...changes };
  applyAppearance(state);
  persistLocally(state);
  emit();
  return state;
}

export function getAppearance(): Appearance {
  return state;
}

/** Réinitialise depuis le cache (tests). */
export function resetAppearanceFromCache(): void {
  state = readCachedAppearance();
  applyAppearance(state);
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAppearance(): Appearance {
  return useSyncExternalStore(subscribe, getAppearance, getAppearance);
}

if (typeof document !== "undefined") {
  applyAppearance(state);
}
