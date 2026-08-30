import type { ReactNode } from "react";
import type { BadgeStatus } from "../status/StatusBadge";
import { StatusBadge } from "../status/StatusBadge";
import "./common.css";

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
      <div className="stat-card__value">{value}</div>
      <div className="stat-card__bottom">
        {status && <StatusBadge status={status} />}
        {hint && <span className="stat-card__hint">{hint}</span>}
      </div>
    </div>
  );
}
