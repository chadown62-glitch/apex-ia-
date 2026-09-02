import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "apex-theme";

function systemPrefersLight(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: light)").matches;
}

function readStoredTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  } catch {
    return null;
  }
}

/** Même ordre de priorité que le script bloquant d'index.html : choix
 * explicite persisté > préférence système > "dark" par défaut. */
function resolveInitialTheme(): Theme {
  return readStoredTheme() ?? (systemPrefersLight() ? "light" : "dark");
}

function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

/**
 * Thème APEX — persisté (localStorage), reflète le DOM déjà posé par le
 * script bloquant d'index.html (jamais de flash au premier rendu). Tant
 * qu'aucun choix explicite n'a été fait, suit en direct la préférence
 * système (écoute `matchMedia`) ; dès que l'utilisateur bascule une fois,
 * ce choix est persisté et prime définitivement sur le système.
 */
export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void; toggleTheme: () => void } {
  const [theme, setThemeState] = useState<Theme>(() => resolveInitialTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (readStoredTheme() !== null) return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setThemeState(media.matches ? "light" : "dark");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* stockage indisponible — le thème reste actif pour cette session. */
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return { theme, setTheme, toggleTheme };
}
