import type { ComponentStatus, Mission } from "../../api/types";
import "./status.css";

/**
 * Vocabulaire d'état système APEX — SIX symboles demandés, mais seuls QUATRE
 * sont dérivés d'un signal réel persistant (`/health`, `/missions`) :
 * READY/WAITING/USING_TOOL/ERROR. `ANALYZING` n'existe qu'en direct pendant
 * une génération de Chat (voir `ChatPage`, indicateur éphémère lié au cycle
 * de vie RÉEL d'une requête HTTP en cours, jamais affiché ici au repos).
 * `LEARNING` reste `PLANNED` : aucun signal persistant n'existe encore pour
 * un cycle `apex.metalearner` en cours (non exposé par l'API) — jamais
 * affiché comme actif sans preuve.
 */
export type ApexSystemStatus = "ready" | "analyzing" | "learning" | "using_tool" | "waiting" | "error";

const STATUS_META: Record<ApexSystemStatus, { icon: string; label: string }> = {
  ready: { icon: "🟢", label: "READY" },
  analyzing: { icon: "🔵", label: "ANALYZING" },
  learning: { icon: "🟣", label: "LEARNING" },
  using_tool: { icon: "🟠", label: "USING TOOL" },
  waiting: { icon: "🟡", label: "WAITING" },
  error: { icon: "🔴", label: "ERROR" },
};

/** Dérivation PURE — jamais un état inventé. `healthStatus`/`missions`
 * viennent directement des enveloppes déjà réelles (`/health`, `/missions`).
 * `healthKnown=false` (aucune mesure, backend injoignable) -> `WAITING`,
 * jamais `READY` par optimisme. */
export function deriveApexStatus(
  healthStatus: ComponentStatus | null,
  healthKnown: boolean,
  missions: Mission[],
): ApexSystemStatus {
  if (!healthKnown) return "waiting";
  if (healthStatus === "failed") return "error";
  if (missions.some((m) => m.state === "running")) return "using_tool";
  if (healthStatus === "degraded") return "waiting";
  return "ready";
}

export function ApexStatusBadge({ status }: { status: ApexSystemStatus }) {
  const meta = STATUS_META[status];
  return (
    <span className={`apex-status apex-status--${status}`}>
      <span aria-hidden="true">{meta.icon}</span>
      {meta.label}
    </span>
  );
}
