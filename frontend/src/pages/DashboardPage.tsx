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
import { StatCard } from "../components/common/StatCard";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { AgentCard } from "../components/agents/AgentCard";
import { EventTimeline } from "../components/events/EventTimeline";
import { ApexStatusBadge, deriveApexStatus } from "../components/status/ApexStatus";
import "./pages.css";

/**
 * Dashboard — n'affiche QUE des données mesurées à l'instant, chacune avec
 * son statut. Aucun chiffre n'est un placeholder : un compteur "0" veut
 * dire "zéro mesuré", pas "pas encore branché" — voir docs/COGNITIVE_ARCHITECTURE.md §12.
 */
export function DashboardPage() {
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
  const agentsData = agents.data?.data;
  const missionsData = missions.data?.status === "real" ? missions.data.data ?? [] : [];
  const conversationsData = conversations.data?.status === "real" ? conversations.data.data ?? [] : [];
  const modelsData = catalogModels.data?.status === "real" ? catalogModels.data.data ?? [] : [];
  const providersData = providers.data?.status === "real" ? providers.data.data ?? [] : [];

  const systemStatus = deriveApexStatus(
    health.data?.data?.status ?? null, health.data?.status === "real", missionsData,
  );

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Dashboard</h1>
          <p className="page__subtitle">Vue d'ensemble — APEX Control Center</p>
        </div>
        <ApexStatusBadge status={systemStatus} />
      </div>

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
          label="Clones actifs"
          value={agentsData ? agentsData.filter((a) => !["terminated", "failed"].includes(a.state)).length : "—"}
          status={agents.data?.status ?? "unknown"}
          hint={agentsData ? `${agentsData.length} au total` : undefined}
        />
        <StatCard
          label="Abstractions"
          value={registryData ? Object.values(registryData.summary).reduce((a, b) => a + b, 0) : "—"}
          status={registry.data?.status ?? "unknown"}
          hint={registryData ? `${registryData.summary.implemented ?? 0} implémentées` : undefined}
        />
      </div>

      <div className="two-col">
        <Panel title="Modèles / Providers déclarés" action={<Link to="/models">catalogue →</Link>}>
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

        <Panel title="Conversations récentes" action={<Link to="/chat">chat →</Link>}>
          {conversations.isLoading ? (
            <LoadingState />
          ) : conversations.data?.status !== "real" ? (
            <ErrorState detail={conversations.data?.detail} />
          ) : conversationsData.length === 0 ? (
            <ErrorState detail="Aucune conversation pour l'instant." />
          ) : (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {conversationsData.slice(0, 5).map((c) => (
                <li key={c.id}>
                  {c.title} <span className="mono" style={{ color: "var(--text-faint)" }}>
                    · {new Date(c.updated_at).toLocaleString("fr-FR")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="two-col">
        <Panel title="Missions récentes" action={<Link to="/mission-center">toutes →</Link>}>
          {missions.isLoading ? (
            <LoadingState />
          ) : missions.data?.status !== "real" ? (
            <ErrorState detail={missions.data?.detail} />
          ) : missionsData.length === 0 ? (
            <ErrorState detail="Aucune mission créée pour l'instant." />
          ) : (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {missionsData.slice(0, 5).map((m) => (
                <li key={m.id}>
                  {m.title} <span className="mono" style={{ color: "var(--text-faint)" }}>· {m.state}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Agents récents" action={<Link to="/agents">tous →</Link>}>
          {agents.isLoading ? (
            <LoadingState />
          ) : agents.data?.status !== "real" ? (
            <ErrorState detail={agents.data?.detail} />
          ) : agentsData && agentsData.length > 0 ? (
            <div className="agent-grid">
              {agentsData.slice(0, 6).map((a) => (
                <AgentCard key={a.clone_id} agent={a} />
              ))}
            </div>
          ) : (
            <ErrorState detail="Aucun clone spawné pour l'instant." />
          )}
        </Panel>

        <Panel title="Événements récents" dense>
          {events.isLoading ? (
            <LoadingState />
          ) : events.data?.status === "real" && events.data.data ? (
            <EventTimeline events={events.data.data} />
          ) : (
            <ErrorState detail={events.data?.detail} />
          )}
        </Panel>
      </div>

      <Panel title="Dernières décisions de gouvernance (audit_log)" action={<Link to="/evolution-timeline">historique complet →</Link>}>
        {audit.isLoading ? (
          <LoadingState />
        ) : audit.data?.status === "real" && audit.data.data ? (
          audit.data.data.length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
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
        )}
      </Panel>
    </div>
  );
}
