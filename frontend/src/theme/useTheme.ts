import { useCallback } from "react";

import { assistantApi, errorMessage } from "../assistant/api";
import { setAppearance, useAppearance, type Appearance, type Theme } from "./appearance";

export type { Theme } from "./appearance";

/**
 * Applique un changement d'apparence tout de suite (DOM + cache local) puis l'enregistre dans les
 * paramètres du backend. Renvoie `null` si l'enregistrement a réussi, sinon le message d'erreur — à
 * afficher par l'appelant : un échec n'est jamais présenté comme un enregistrement.
 */
export async function saveAppearance(changes: Partial<Appearance>): Promise<string | null> {
  setAppearance(changes);
  try {
    await assistantApi.updateSettings({ appearance: changes });
    return null;
  } catch (error) {
    return errorMessage(error);
  }
}

/** Thème APEX (Night | Dune) — même état pour toute l'interface (theme/appearance.ts). */
export function useTheme(): {
  theme: Theme;
  setTheme: (t: Theme) => Promise<string | null>;
  toggleTheme: () => Promise<string | null>;
} {
  const { theme } = useAppearance();
  const setTheme = useCallback((next: Theme) => saveAppearance({ theme: next }), []);
  const toggleTheme = useCallback(() => setTheme(theme === "night" ? "dune" : "night"), [theme, setTheme]);
  return { theme, setTheme, toggleTheme };
}
