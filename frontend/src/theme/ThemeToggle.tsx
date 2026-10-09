import { useState } from "react";

import { useTheme } from "./useTheme";

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const [saveError, setSaveError] = useState<string | null>(null);
  const isNight = theme === "night";
  const label = isNight ? "Passer au thème APEX Dune" : "Passer au thème APEX Night";

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={async () => setSaveError(await toggleTheme())}
      aria-label={label}
      title={saveError ? `Thème appliqué sur cet appareil, mais non enregistré : ${saveError}` : label}
      data-save-error={saveError ? "true" : undefined}
    >
      <span aria-hidden="true">{isNight ? "☾" : "☀"}</span>
    </button>
  );
}
