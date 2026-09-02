import { useHealth, useRegistry } from "../api/hooks";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { StatCard } from "../components/common/StatCard";
import { HealthStatusBadge, StatusBadge } from "../components/status/StatusBadge";
import type { AbstractionStatus } from "../api/types";
import "./pages.css";

const ABSTRACTION_BADGE: Record<AbstractionStatus, "real" | "simulated" | "unknown" | "planned"> = {
  implemented: "real",
  stub: "simulated",
  defined: "unknown",
  planned: "planned",
};

export function SystemPage() {
  const registry = useRegistry();
  const health = useHealth();
  const registryData = registry.data?.data;
  const healthData = health.data?.data;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">System / Configuration</h1>
          <p className="page__subtitle">apex.interfaces.registry — honnêteté du registre (ADR-0007) et santé des composants.</p>
        </div>
      </div>

      {registryData && (
        <div className="stat-grid">
          {Object.entries(registryData.summary).map(([status, count]) => (
            <StatCard key={status} label={status} value={count} />
          ))}
        </div>
      )}

      <Panel title="Santé des composants" dense>
        {health.isLoading ? (
          <LoadingState />
        ) : healthData ? (
          <div className="kv-table-wrap">
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Composant</th>
                  <th>Statut</th>
                  <th>Détail</th>
                  <th>Version</th>
                  <th>Fallback</th>
                </tr>
              </thead>
              <tbody>
                {healthData.components.map((c) => (
                  <tr key={c.component}>
                    <td className="mono">{c.component}</td>
                    <td>
                      <HealthStatusBadge status={c.status} />
                    </td>
                    <td>{c.detail}</td>
                    <td className="mono">{c.version ?? "—"}</td>
                    <td>{c.fallback_active ? "actif" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ErrorState detail={health.data?.detail} />
        )}
      </Panel>

      <Panel title={registryData ? `${registryData.abstractions.length} abstractions` : "Abstractions"} dense>
        {registry.isLoading ? (
          <LoadingState />
        ) : registryData ? (
          <div className="kv-table-wrap">
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Nom</th>
                  <th>Statut</th>
                  <th>Objet</th>
                  <th>Module</th>
                  <th>Fallback</th>
                </tr>
              </thead>
              <tbody>
                {registryData.abstractions.map((a) => (
                  <tr key={a.name}>
                    <td className="mono">{a.name}</td>
                    <td>
                      <StatusBadge status={ABSTRACTION_BADGE[a.status]} title={a.status.toUpperCase()} />
                    </td>
                    <td>{a.purpose}</td>
                    <td className="mono">{a.module ?? "—"}</td>
                    <td>{a.fallback}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ErrorState detail={registry.data?.detail} />
        )}
      </Panel>
    </div>
  );
}
