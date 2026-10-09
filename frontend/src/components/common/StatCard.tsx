import type { ReactNode } from "react";
import type { BadgeStatus } from "../status/StatusBadge";
import { StatusBadge } from "../status/StatusBadge";
import "./common.css";

const TONES: Record<string, string> = {
  OK: "ok",
  PASS: "ok",
  READY: "ok",
  FAIL: "fail",
  FAILED: "fail",
  ERROR: "fail",
  OFFLINE: "fail",
  DEGRADED: "warn",
  UNKNOWN: "unknown",
};

/** Ton visuel d'une valeur textuelle (OK, FAIL…) ; aucun pour un nombre ou un autre mot. */
export function tone(value: ReactNode): string | undefined {
  return typeof value === "string" ? TONES[value.trim().toUpperCase()] : undefined;
}

/**
 * Carte de statut — un chiffre ou un mot, jamais affiché sans son
 * `StatusBadge` si `status` est fourni. C'est la règle du projet, pas
 * une préférence esthétique : voir docs/COGNITIVE_ARCHITECTURE.md §12.
 */
export function StatCard({
  label,
  value,
  status,
  hint,
  icon,
}: {
  label: string;
  value: ReactNode;
  status?: BadgeStatus;
  hint?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="stat-card">
      <div className="stat-card__top">
        <span className="stat-card__label">{label}</span>
        {icon && <span className="stat-card__icon">{icon}</span>}
      </div>
      <div className="stat-card__value" data-tone={tone(value)}>
        {value}
      </div>
      <div className="stat-card__bottom">
        {status && <StatusBadge status={status} />}
        {hint && <span className="stat-card__hint">{hint}</span>}
      </div>
    </div>
  );
}
