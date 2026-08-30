import { useGate } from "../api/hooks";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { StatCard } from "../components/common/StatCard";
import { GateStatusBadge } from "../components/status/StatusBadge";
import "./pages.css";

export function SecurityGatePage() {
  const gate = useGate();
  const data = gate.data?.data;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Security / Gate</h1>
          <p className="page__subtitle">
            apex.gate — matériel/environnement (EXISTS). Extensions Code/Security/Learning Gate : PLANNED.
          </p>
        </div>
      </div>

      {gate.isLoading ? (
        <LoadingState />
      ) : !data ? (
        <ErrorState detail={gate.data?.detail ?? "Aucun rapport de Gate."} />
      ) : (
        <>
          <div className="stat-grid">
            <StatCard label="Statut du Gate" value={<GateStatusBadge status={data.gate_status} />} />
            <StatCard label="Phase 2 autorisée" value={data.phase_2_authorized ? "OUI" : "NON"} />
            <StatCard
              label="Mesuré sur cette machine"
              value={data.measured_on_target_machine ? "OUI" : "NON"}
              status={gate.data?.status}
            />
            <StatCard label="Machine" value={data.machine_label ?? "non étiquetée"} />
          </div>

          <Panel title={`Empreinte matérielle`}>
            <code className="mono" style={{ fontSize: 12 }}>
              {data.fingerprint}
            </code>
          </Panel>

          <Panel title={`${data.checks.length} contrôles`} dense>
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Contrôle</th>
                  <th>Statut</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {data.checks.map((check) => (
                  <tr key={check.check_id}>
                    <td className="mono">{check.check_id}</td>
                    <td>
                      <GateStatusBadge status={check.status} />
                    </td>
                    <td>{check.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </>
      )}
    </div>
  );
}
