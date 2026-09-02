import { useGate } from "../api/hooks";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { StatCard } from "../components/common/StatCard";
import { GateStatusBadge } from "../components/status/StatusBadge";
import "./pages.css";

/** Sous-ensemble RÉEL de `apex.gate` — aucune mesure recalculée ici, cette
 * page filtre seulement les contrôles matériels déjà produits par le Gate
 * (`apex/gate/checks.py`) : GPU/VRAM/CUDA/disque/RAM/CPU. Le détail complet
 * (y compris les contrôles logiciels/réseau) reste sur `/security-gate`. */
const HARDWARE_CHECK_IDS = new Set([
  "gate_07_gpu_from_wsl",
  "gate_08_gpu_model",
  "gate_09_vram",
  "gate_10_cuda_driver",
  "gate_11_disk",
  "gate_12_ram",
  "gate_13_cpu",
]);

export function HardwarePage() {
  const gate = useGate();
  const data = gate.data?.data;
  const hardwareChecks = data?.checks.filter((c) => HARDWARE_CHECK_IDS.has(c.check_id)) ?? [];

  const gpuCheck = data?.checks.find((c) => c.check_id === "gate_08_gpu_model");
  const vramCheck = data?.checks.find((c) => c.check_id === "gate_09_vram");
  const ramCheck = data?.checks.find((c) => c.check_id === "gate_12_ram");
  const cpuCheck = data?.checks.find((c) => c.check_id === "gate_13_cpu");

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Hardware</h1>
          <p className="page__subtitle">
            Mesures matérielles réelles, dérivées de apex.gate — jamais une estimation.
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
            <StatCard label="GPU" value={gpuCheck?.message ?? "—"} status={gate.data?.status} />
            <StatCard label="VRAM" value={vramCheck?.message ?? "—"} status={gate.data?.status} />
            <StatCard label="RAM" value={ramCheck?.message ?? "—"} status={gate.data?.status} />
            <StatCard label="CPU" value={cpuCheck?.message ?? "—"} status={gate.data?.status} />
          </div>

          <Panel title="Empreinte machine">
            <table className="kv-table">
              <tbody>
                <tr><th>Machine</th><td>{data.machine_label ?? "non étiquetée"}</td></tr>
                <tr><th>Mesuré sur cette machine</th><td>{data.measured_on_target_machine ? "OUI" : "NON"}</td></tr>
                <tr><th>Empreinte</th><td className="mono">{data.fingerprint}</td></tr>
              </tbody>
            </table>
          </Panel>

          <Panel title={`${hardwareChecks.length} contrôles matériels (sur ${data.checks.length} au total)`} dense>
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Contrôle</th>
                  <th>Statut</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {hardwareChecks.map((check) => (
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
