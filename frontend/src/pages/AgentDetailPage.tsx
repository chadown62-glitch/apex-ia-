import { useParams } from "react-router-dom";

import { useAgent, useEvents } from "../api/hooks";
import { AgentStatePill } from "../components/agents/AgentCard";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { MeasurementValue } from "../components/status/MeasurementValue";
import { EventTimeline } from "../components/events/EventTimeline";
import { specialtyForAgentType } from "../specialties/data";
import "./pages.css";

export function AgentDetailPage() {
  const { cloneId } = useParams<{ cloneId: string }>();
  const agent = useAgent(cloneId);
  // Pas de filtrage côté serveur par clone_id : /events est un journal
  // global (partitionné par mois, pas par clone) — le filtrage se fait ici,
  // même logique que apex.agents.view.clone_history côté backend.
  const events = useEvents(200);
  const history = events.data?.data?.filter((e) => e.context.clone_id === cloneId) ?? [];

  if (agent.isLoading) return <LoadingState />;
  if (agent.data?.status !== "real" || !agent.data.data) {
    return (
      <div className="page">
        <h1 className="page__title">Agent introuvable</h1>
        <ErrorState detail={agent.data?.detail} />
      </div>
    );
  }

  const a = agent.data.data;
  const specialty = specialtyForAgentType(a.type);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">
            {a.type} <span className="mono" style={{ color: "var(--text-faint)", fontWeight: 400 }}>#{a.clone_id.slice(0, 8)}</span>
          </h1>
          <p className="page__subtitle">Mission : {a.mission_id ?? "— aucune —"}</p>
        </div>
        <AgentStatePill state={a.state} />
      </div>

      <div className="two-col">
        <Panel title="Identité" dense>
          <div className="kv-table-wrap">
          <table className="kv-table">
            <tbody>
              <tr>
                <th>Clone ID</th>
                <td className="mono">{a.clone_id}</td>
              </tr>
              <tr>
                <th>Genome ref</th>
                <td className="mono">{a.genome_ref}</td>
              </tr>
              <tr>
                <th>Spécialité</th>
                <td>
                  {specialty ? (
                    <span>
                      {specialty.icon} {specialty.label}
                      {specialty.status === "planned" && <span className="mono" style={{ color: "var(--text-faint)" }}> (PLANNED)</span>}
                    </span>
                  ) : (
                    <span style={{ color: "var(--text-faint)" }}>aucune spécialité associée</span>
                  )}
                </td>
              </tr>
              <tr>
                <th>Pool</th>
                <td className="mono">{a.pool_id}</td>
              </tr>
              <tr>
                <th>Isolation</th>
                <td>
                  <MeasurementValue measurement={a.isolation_level} />
                </td>
              </tr>
              <tr>
                <th>Spawned</th>
                <td className="mono">{new Date(a.created_at).toLocaleString("fr-FR")}</td>
              </tr>
              <tr>
                <th>Démarré</th>
                <td className="mono">{a.started_at ? new Date(a.started_at).toLocaleString("fr-FR") : "—"}</td>
              </tr>
              <tr>
                <th>Terminé</th>
                <td className="mono">{a.terminated_at ? new Date(a.terminated_at).toLocaleString("fr-FR") : "—"}</td>
              </tr>
              <tr>
                <th>Retries</th>
                <td>
                  {a.retries_used} / {a.max_retries}
                </td>
              </tr>
              {a.last_error && (
                <tr>
                  <th>Dernière erreur</th>
                  <td className="mono">{a.last_error}</td>
                </tr>
              )}
            </tbody>
          </table>
          </div>
        </Panel>

        <Panel title="Ressources">
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <ResourceRow label="Durée" measurement={a.elapsed_s} unit="s" />
            <ResourceRow label="CPU" measurement={a.cpu_time_s} unit="s" />
            <ResourceRow label="RAM" measurement={a.ram_mb} unit="MB" />
            <ResourceRow label="GPU" measurement={a.gpu} unit="" />
          </div>
          <p style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 12, marginBottom: 0 }}>
            RAM/GPU restent UNKNOWN tant que SandboxProvider (PLANNED, phase 3) n'existe pas — jamais estimées.
          </p>
        </Panel>
      </div>

      <Panel title="Actions disponibles">
        {a.available_actions.length > 0 ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {a.available_actions.map((action) => (
              <button key={action} className="action-button" disabled title="Backend requis — non câblé dans cette étape">
                {action}
              </button>
            ))}
          </div>
        ) : (
          <ErrorState detail="Aucune action disponible pour votre niveau d'autorisation actuel." />
        )}
      </Panel>

      <Panel title="Historique du clone" dense>
        {events.isLoading ? <LoadingState /> : <EventTimeline events={history} />}
      </Panel>
    </div>
  );
}

function ResourceRow({
  label,
  measurement,
  unit,
}: {
  label: string;
  measurement: { value: number | string | null; status: "real" | "simulated" | "unknown" };
  unit: string;
}) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{label}</span>
      <MeasurementValue measurement={measurement} unit={unit ? ` ${unit}` : undefined} />
    </div>
  );
}
