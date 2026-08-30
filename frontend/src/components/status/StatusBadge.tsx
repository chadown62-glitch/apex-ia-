import type { DataStatus } from "../../api/types";
import "./status.css";

/** "planned" n'existe QUE côté frontend (un module entier sans backend) —
 * jamais une valeur que l'API peut renvoyer pour une donnée. Voir
 * src/api/types.ts et src/specialties/types.ts. */
export type BadgeStatus = DataStatus | "planned";

const LABEL: Record<BadgeStatus, string> = {
  real: "REAL",
  simulated: "SIMULATED",
  unknown: "UNKNOWN",
  planned: "PLANNED",
};

export function StatusBadge({ status, title }: { status: BadgeStatus; title?: string }) {
  return (
    <span className={`status-badge status-badge--${status}`} title={title}>
      <span className="status-badge__dot" aria-hidden="true" />
      {LABEL[status]}
    </span>
  );
}

/** Vocabulaire du Gate (apex.gate.model.GateStatus) — distinct de DataStatus :
 * répond à "la mesure est-elle conforme", pas "la donnée est-elle réelle". */
export type GateStatusValue = "PASS" | "FAIL" | "DEGRADED" | "UNKNOWN" | "SIMULATED";

const GATE_CLASS: Record<GateStatusValue, string> = {
  PASS: "real",
  FAIL: "fail",
  DEGRADED: "degraded",
  UNKNOWN: "unknown",
  SIMULATED: "simulated",
};

export function GateStatusBadge({ status }: { status: string }) {
  const known = (GATE_CLASS as Record<string, string>)[status] ?? "unknown";
  return (
    <span className={`status-badge status-badge--${known}`}>
      <span className="status-badge__dot" aria-hidden="true" />
      {status}
    </span>
  );
}

/** Vocabulaire de santé (apex.obs.health.Status) — répond à "le composant
 * fonctionne-t-il", pas "la mesure est-elle conforme" ni "la donnée est-elle réelle". */
export type ComponentStatusValue = "ok" | "degraded" | "failed";

const HEALTH_CLASS: Record<ComponentStatusValue, string> = {
  ok: "real",
  degraded: "degraded",
  failed: "fail",
};

export function HealthStatusBadge({ status }: { status: ComponentStatusValue }) {
  return (
    <span className={`status-badge status-badge--${HEALTH_CLASS[status]}`}>
      <span className="status-badge__dot" aria-hidden="true" />
      {status.toUpperCase()}
    </span>
  );
}
