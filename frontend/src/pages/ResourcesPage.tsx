import { useAgents, useHealth } from "../api/hooks";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { StatCard } from "../components/common/StatCard";
import { HealthStatusBadge } from "../components/status/StatusBadge";
import "./pages.css";

/**
 * Page volontairement mince : aucun endpoint /resources n'existe côté
 * backend (ComputeBudget n'est pas encore exposé par l'API). Cette page
 * n'affiche que ce qui est réellement dérivable de /health et /agents —
 * jamais une estimation présentée comme mesure.
 */
export function ResourcesPage() {
  const health = useHealth();
  const agents = useAgents();
  const data = agents.data?.data;
  const withCpu = data?.filter((a) => a.cpu_time_s.status === "real") ?? [];

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Resources</h1>
          <p className="page__subtitle">Aucun endpoint /resources dédié pour l'instant — dérivé de /health et /agents.</p>
        </div>
      </div>

      <div className="stat-grid">
        <StatCard
          label="Base de données"
          value={
            health.data?.data
              ? (() => {
                  const db = health.data.data.components.find((c) => c.component === "database");
                  return db ? <HealthStatusBadge status={db.status} /> : "—";
                })()
              : "—"
          }
          status={health.data?.status ?? "unknown"}
        />
        <StatCard
          label="Clones avec CPU mesuré"
          value={withCpu.length}
          status={agents.data?.status ?? "unknown"}
          hint={data ? `sur ${data.length} clones` : undefined}
        />
        <StatCard label="RAM par clone" value="—" status="unknown" hint="SandboxProvider requis (PLANNED, phase 3)" />
        <StatCard label="GPU par clone" value="—" status="unknown" hint="SandboxProvider requis (PLANNED, phase 3)" />
      </div>

      <Panel title="ComputeBudget (config.yaml)">
        <ErrorState detail="Non exposé par l'API dans cette étape — nightly_gpu_minutes / reserve_fraction existent en configuration mais n'ont pas encore de route dédiée." />
      </Panel>

      {agents.isLoading || health.isLoading ? <LoadingState /> : null}
    </div>
  );
}
