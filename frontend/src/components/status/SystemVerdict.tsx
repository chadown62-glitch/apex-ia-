import { ApiError } from "../../api/client";
import { useSystemStatus } from "../../api/hooks";
import type { DataEnvelope, SystemStatus } from "../../api/types";
import "./status.css";

/**
 * Verdict d'APEX — LE MÊME que celui du Panel `APEX.exe` : calculé par le backend (`GET /status`,
 * `overall`), jamais recalculé ici. READY seulement si chaque composant mesuré est prêt ; DEGRADED
 * sinon, avec lesquels. Sans réponse : jamais READY par défaut — « injoignable » ou « session
 * expirée », avec quoi faire. Un démarrage refusé (BLOCKED) ne peut pas s'afficher ici : APEX ne
 * tourne pas, l'interface n'est pas servie — c'est le Panel qui le montre.
 */
export type SystemVerdictKind = "ready" | "degraded" | "unreachable" | "session" | "loading";

export interface SystemVerdictView {
  kind: SystemVerdictKind;
  label: string;
  detail: string;
}

export function deriveSystemVerdict(
  envelope: DataEnvelope<SystemStatus> | undefined,
  error: unknown,
): SystemVerdictView {
  if (error instanceof ApiError && error.status === 401) {
    return { kind: "session", label: "SESSION EXPIRÉE", detail: error.message };
  }
  if (error) {
    return { kind: "unreachable", label: "APEX INJOIGNABLE", detail: String(error) };
  }
  if (!envelope) return { kind: "loading", label: "…", detail: "mesure en cours" };
  const data = envelope.data;
  if (envelope.status !== "real" || !data) {
    return { kind: "unreachable", label: "APEX INJOIGNABLE", detail: envelope.detail ?? "aucune réponse d'APEX" };
  }
  if (data.overall.verdict === "ready") {
    return { kind: "ready", label: "APEX READY", detail: "Tous les composants d'APEX répondent." };
  }
  const notReady = data.components
    .filter((c) => data.overall.not_ready.includes(c.component))
    .map((c) => `${c.component} (${c.state} — ${c.detail})`);
  return { kind: "degraded", label: "APEX DEGRADED", detail: `Non prêts : ${notReady.join(" ; ")}` };
}

const BADGE_CLASS: Record<SystemVerdictKind, string> = {
  ready: "real",
  degraded: "degraded",
  unreachable: "fail",
  session: "fail",
  loading: "unknown",
};

export function SystemVerdictBadge({ view }: { view: SystemVerdictView }) {
  return (
    <span className={`status-badge status-badge--${BADGE_CLASS[view.kind]}`} title={view.detail}>
      <span className="status-badge__dot" aria-hidden="true" />
      {view.label}
    </span>
  );
}

export function useSystemVerdict(): { view: SystemVerdictView; status: SystemStatus | null } {
  const query = useSystemStatus();
  const view = deriveSystemVerdict(query.data, query.error);
  return { view, status: query.data?.status === "real" ? query.data.data : null };
}

const STATE_CLASS: Record<string, string> = {
  ready: "real",
  degraded: "degraded",
  offline: "unknown",
  error: "fail",
  unknown: "unknown",
};

/** Composants mesurés par APEX + décision du Gate (démarrage et phase 2, séparés) — mêmes lignes
 * que le Panel. Rien d'affiché qui n'ait été renvoyé par `GET /status`. */
export function SystemStatusTable({ status }: { status: SystemStatus }) {
  const gate = status.gate;
  return (
    <div className="kv-table-wrap">
      <table className="kv-table">
        <thead>
          <tr>
            <th>Composant</th>
            <th>État</th>
            <th>Détail</th>
          </tr>
        </thead>
        <tbody>
          {status.components.map((c) => (
            <tr key={c.component}>
              <td className="mono">{c.component}</td>
              <td>
                <span className={`status-badge status-badge--${STATE_CLASS[c.state] ?? "unknown"}`}>
                  <span className="status-badge__dot" aria-hidden="true" />
                  {c.state.toUpperCase()}
                </span>
              </td>
              <td>{c.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="page__subtitle">
        {gate === null
          ? "Décision du Gate : rapport illisible (non vérifiée)."
          : `Démarrage : ${gate.startup_authorized ? "autorisé" : "REFUSÉ"} · Phase 2 de la feuille de route : ${
              gate.phase_2_authorized ? "autorisée" : "non autorisée"
            } (décision séparée — elle n'empêche pas APEX de tourner)${
              gate.startup_unverified.length > 0 ? ` · non vérifiés : ${gate.startup_unverified.join(", ")}` : ""
            }.`}{" "}
        APEX {status.versions.apex} · API {status.versions.api} · mesuré à{" "}
        {new Date(status.measured_at).toLocaleTimeString()}.
      </p>
    </div>
  );
}
