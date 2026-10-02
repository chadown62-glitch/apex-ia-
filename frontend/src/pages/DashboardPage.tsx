import { Link } from "react-router-dom";

import {
  useAgents,
  useAuditLog,
  useCatalogModels,
  useConversations,
  useEvents,
  useGate,
  useHealth,
  useMissions,
  useProviders,
  useRegistry,
} from "../api/hooks";
import { AgentCard } from "../components/agents/AgentCard";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { StatCard } from "../components/common/StatCard";
import { EventTimeline } from "../components/events/EventTimeline";
import { SystemStatusTable, SystemVerdictBadge, useSystemVerdict } from "../components/status/SystemVerdict";
import { Tabs } from "../components/ui/Tabs";
import "./pages.css";

const ACTIVE_STATES = new Set(["spawning", "ready", "working", "reporting"]);

/** Mêmes IDs de contrôle que HardwarePage — filtre RÉUTILISÉ, jamais
 * dupliqué avec une seconde logique de sélection. */
const HARDWARE_CHECK_IDS = new Set([
  "gate_08_gpu_model", "gate_09_vram", "gate_12_ram", "gate_13_cpu",
]);

/**
 * Dashboard — centre de contrôle APEX. N'affiche QUE des données mesurées à
 * l'instant, chacune avec son statut. Aucun chiffre n'est un placeholder :
 * un compteur "0" veut dire "zéro mesuré", pas "pas encore branché" — voir
 * docs/COGNITIVE_ARCHITECTURE.md §12. L'indicateur d'état APEX global vit
 * désormais dans la TopBar (toujours visible) — jamais dupliqué ici.
 */
export function DashboardPage() {
  const verdict = useSystemVerdict();
  const health = useHealth();
  const gate = useGate();
  const registry = useRegistry();
  const agents = useAgents();
  const events = useEvents(15);
  const audit = useAuditLog(5);
  const missions = useMissions();
  const conversations = useConversations();
  const catalogModels = useCatalogModels();
  const providers = useProviders();

  const registryData = registry.data?.data;
  // Sonde réelle de la base (GET /status, même mesure que le Panel) — jamais déduite d'une autre route.
  const databaseComponent = verdict.status?.components.find((c) => c.component === "database") ?? null;
  const agentsData = agents.data?.data;
  const missionsData = missions.data?.status === "real" ? missions.data.data ?? [] : [];
  const conversationsData = conversations.data?.status === "real" ? conversations.data.data ?? [] : [];
  const modelsData = catalogModels.data?.status === "real" ? catalogModels.data.data ?? [] : [];
  const providersData = providers.data?.status === "real" ? providers.data.data ?? [] : [];
  const activeAgents = agentsData?.filter((a) => ACTIVE_STATES.has(a.state)) ?? [];

  const gateChecks = gate.data?.data?.checks ?? [];
  const hardwareChecks = gateChecks.filter((c) => HARDWARE_CHECK_IDS.has(c.check_id));

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Dashboard</h1>
          <p className="page__subtitle">Centre de contrôle APEX — état réel du système, jamais une projection.</p>
        </div>
      </div>

      <section aria-labelledby="dashboard-apex-state">
        <h2 id="dashboard-apex-state" className="section-label">
          État d'APEX
        </h2>
        <Panel action={<SystemVerdictBadge view={verdict.view} />}>
          {verdict.status ? (
            <SystemStatusTable status={verdict.status} />
          ) : verdict.view.kind === "loading" ? (
            <LoadingState />
          ) : (
            <ErrorState detail={verdict.view.detail} />
          )}
        </Panel>
      </section>

      <section aria-labelledby="dashboard-system-status">
        <h2 id="dashboard-system-status" className="section-label">
          System status
        </h2>
        <div className="stat-grid">
          <StatCard
            label="Statut système"
            value={health.data?.data?.status.toUpperCase() ?? "—"}
            status={health.data?.status ?? "unknown"}
          />
          <StatCard
            label="Gate"
            value={gate.data?.data?.gate_status ?? "—"}
            status={gate.data?.status ?? "unknown"}
            hint={gate.data?.data ? `${gate.data.data.checks.length} contrôles` : gate.data?.detail ?? undefined}
          />
          <StatCard
            label="Base de données"
            value={databaseComponent ? databaseComponent.state.toUpperCase() : "—"}
            status={databaseComponent ? "real" : "unknown"}
            hint={databaseComponent?.detail ?? "non mesurée (GET /status indisponible)"}
          />
          <StatCard
            label="Abstractions"
            value={registryData ? Object.values(registryData.summary).reduce((a, b) => a + b, 0) : "—"}
            status={registry.data?.status ?? "unknown"}
            hint={registryData ? `${registryData.summary.implemented ?? 0} implémentées` : undefined}
          />
        </div>
      </section>

      <section aria-labelledby="dashboard-hardware">
        <h2 id="dashboard-hardware" className="section-label">
          Hardware
        </h2>
        <Panel action={<Link to="/hardware">détail →</Link>}>
          {gate.isLoading ? (
            <LoadingState />
          ) : hardwareChecks.length === 0 ? (
            <ErrorState detail={gate.data?.detail ?? "Aucune mesure matérielle disponible."} />
          ) : (
            <div className="stat-grid">
              {hardwareChecks.map((check) => (
                <StatCard
                  key={check.check_id}
                  label={check.check_id.replace(/^gate_\d+_/, "").toUpperCase()}
                  value={check.message}
                  status={gate.data?.status ?? "unknown"}
                />
              ))}
            </div>
          )}
        </Panel>
      </section>

      <div className="two-col">
        <section aria-labelledby="dashboard-active-agents">
          <h2 id="dashboard-active-agents" className="section-label">
            Active agents
          </h2>
          <Panel action={<Link to="/agents">tous →</Link>}>
            {agents.isLoading ? (
              <LoadingState />
            ) : agents.data?.status !== "real" ? (
              <ErrorState detail={agents.data?.detail} />
            ) : activeAgents.length > 0 ? (
              <div className="agent-grid">
                {activeAgents.slice(0, 6).map((a) => (
                  <AgentCard key={a.clone_id} agent={a} />
                ))}
              </div>
            ) : (
              <ErrorState detail="Aucun clone actif pour l'instant." />
            )}
          </Panel>
        </section>

        <section aria-labelledby="dashboard-recent-missions">
          <h2 id="dashboard-recent-missions" className="section-label">
            Recent missions
          </h2>
          <Panel action={<Link to="/mission-center">toutes →</Link>}>
            {missions.isLoading ? (
              <LoadingState />
            ) : missions.data?.status !== "real" ? (
              <ErrorState detail={missions.data?.detail} />
            ) : missionsData.length === 0 ? (
              <ErrorState detail="Aucune mission créée pour l'instant." />
            ) : (
              <ul className="dashboard-list">
                {missionsData.slice(0, 5).map((m) => (
                  <li key={m.id}>
                    {m.title} <span className="mono dashboard-list__meta">· {m.state}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </section>
      </div>

      <div className="two-col">
        <section aria-labelledby="dashboard-models-providers">
          <h2 id="dashboard-models-providers" className="section-label">
            Models / Providers
          </h2>
          <Panel action={<Link to="/models">catalogue →</Link>}>
            {catalogModels.isLoading || providers.isLoading ? (
              <LoadingState />
            ) : catalogModels.data?.status !== "real" ? (
              <ErrorState detail={catalogModels.data?.detail} />
            ) : (
              <div className="stat-grid">
                <StatCard label="Modèles déclarés" value={modelsData.length} status="real" />
                <StatCard label="Providers déclarés" value={providersData.length} status="real" />
                <StatCard
                  label="Modèles locaux"
                  value={modelsData.filter((m) => m.is_local).length}
                  status="real"
                  hint="jamais sondé au chargement — voir Models/Providers"
                />
              </div>
            )}
          </Panel>
        </section>

        <section aria-labelledby="dashboard-conversations">
          <h2 id="dashboard-conversations" className="section-label">
            Conversations récentes
          </h2>
          <Panel action={<Link to="/chat">chat →</Link>}>
            {conversations.isLoading ? (
              <LoadingState />
            ) : conversations.data?.status !== "real" ? (
              <ErrorState detail={conversations.data?.detail} />
            ) : conversationsData.length === 0 ? (
              <ErrorState detail="Aucune conversation pour l'instant." />
            ) : (
              <ul className="dashboard-list">
                {conversationsData.slice(0, 5).map((c) => (
                  <li key={c.id}>
                    {c.title}
                    <span className="mono dashboard-list__meta"> · {new Date(c.updated_at).toLocaleString("fr-FR")}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </section>
      </div>

      <section aria-labelledby="dashboard-recent-activity">
        <h2 id="dashboard-recent-activity" className="section-label">
          Recent activity
        </h2>
        <Panel>
          <Tabs
            items={[
              {
                id: "events",
                label: "Événements système",
                content:
                  events.isLoading ? (
                    <LoadingState />
                  ) : events.data?.status === "real" && events.data.data ? (
                    <EventTimeline events={events.data.data} />
                  ) : (
                    <ErrorState detail={events.data?.detail} />
                  ),
              },
              {
                id: "governance",
                label: "Gouvernance (audit_log)",
                content:
                  audit.isLoading ? (
                    <LoadingState />
                  ) : audit.data?.status === "real" && audit.data.data ? (
                    audit.data.data.length > 0 ? (
                      <ul className="dashboard-list">
                        {audit.data.data.map((entry) => (
                          <li key={entry.id}>
                            <strong>{entry.actor}</strong> · {entry.action}
                            {entry.target && <span className="mono"> · {entry.target}</span>}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <ErrorState detail="Aucune entrée d'audit." />
                    )
                  ) : (
                    <ErrorState detail={audit.data?.detail} />
                  ),
              },
            ]}
          />
          <div className="dashboard-activity-footer">
            <Link to="/evolution-timeline">Historique complet des décisions de gouvernance →</Link>
          </div>
        </Panel>
      </section>
    </div>
  );
}
