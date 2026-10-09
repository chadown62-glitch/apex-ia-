import type { UseQueryResult } from "@tanstack/react-query";

import { errorMessage, type ModeId, type ModeInfo } from "./api";

/** Libellés des outils RÉELLEMENT exposés par le backend (apex/assistant/modes.py) — rien d'autre n'est annoncé. */
export const TOOL_LABELS: Record<string, string> = {
  "fs.list": "lister les fichiers",
  "fs.read": "lire un fichier",
  "fs.write": "écrire un fichier",
  "project.inspect": "inspecter le projet",
  "code.run": "exécuter du Python (sandbox)",
  "tests.run": "lancer les tests (sandbox)",
  "color.contrast": "contraste WCAG",
  "calc.evaluate": "calculatrice exacte",
  "finance.projection": "projection d'épargne",
  "finance.loan": "mensualités de prêt",
};

const GLYPHS: Record<ModeId, string> = { normal: "◌", code: "</>", design: "◐", finance: "∑" };

export function ModePicker({
  modes,
  value,
  onChange,
  compact,
}: {
  modes: UseQueryResult<ModeInfo[]>;
  value: ModeId;
  onChange: (mode: ModeId) => void;
  compact?: boolean;
}) {
  if (modes.isLoading) {
    return <div className="ax-modes ax-modes--loading" aria-label="Chargement des modes" />;
  }
  if (modes.isError || !modes.data) {
    return (
      <div className="ax-banner ax-banner--error" role="alert">
        <span>Modes indisponibles : {errorMessage(modes.error)}</span>
        <button type="button" className="ax-chip-btn" onClick={() => modes.refetch()}>
          Réessayer
        </button>
      </div>
    );
  }
  const selected = modes.data.find((m) => m.id === value) ?? modes.data[0];
  return (
    <div className={`ax-modes ${compact ? "ax-modes--compact" : ""}`}>
      <div className="ax-modes__grid" role="radiogroup" aria-label="Mode de la conversation">
        {modes.data.map((mode) => (
          <button
            key={mode.id}
            type="button"
            role="radio"
            aria-checked={mode.id === selected.id}
            className={`ax-mode glass ax-mode--${mode.id} ${mode.id === selected.id ? "is-selected" : ""}`}
            onClick={() => onChange(mode.id)}
          >
            <span className="ax-mode__glyph" aria-hidden="true">
              {GLYPHS[mode.id]}
            </span>
            <span className="ax-mode__label">{mode.label}</span>
            {!compact && <span className="ax-mode__summary">{mode.summary}</span>}
          </button>
        ))}
      </div>
      {!compact && (
        <div className="ax-mode-detail" aria-live="polite">
          <span>
            <strong>Outils :</strong>{" "}
            {selected.tools.length === 0 ? "aucun (conversation seule)" : selected.tools.map((t) => TOOL_LABELS[t] ?? t).join(" · ")}
          </span>
          {selected.limits.map((limit) => (
            <span key={limit} className="ax-mode-detail__limit">
              {limit}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
