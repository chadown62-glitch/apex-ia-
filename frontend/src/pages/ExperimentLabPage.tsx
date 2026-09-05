import { useState } from "react";

import { ApiError } from "../api/client";
import { useAgents, useCreateExperimentCycle, useEvalRun, useEvalRuns } from "../api/hooks";
import type { ExperimentCycle } from "../api/types";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { StatCard } from "../components/common/StatCard";
import { StatusBadge } from "../components/status/StatusBadge";
import { Button } from "../components/ui/Button";
import { Input, Select } from "../components/ui/Field";
import "./pages.css";
import "./mission-center.css";

const DEFAULT_SPECIALTY = "research";

const METHODS = [
  "practice", "spaced_repetition", "problem_generation", "research",
  "imitation", "self_explanation", "teaching", "experimentation", "combination",
];

const VERDICT_BADGE: Record<string, "real" | "unknown" | "simulated"> = {
  pass: "real",
  fail: "unknown",
  error: "unknown",
  timeout: "unknown",
  unknown: "unknown",
};

function FactsView({ facts }: { facts: Record<string, unknown> }) {
  const { strategy, memory_peak_mb, model_response, ...rest } = facts as Record<string, unknown> & {
    strategy?: Record<string, unknown>;
    memory_peak_mb?: number;
    model_response?: Record<string, unknown>;
  };
  const restEntries = Object.entries(rest);
  return (
    <div className="kv-table-wrap">
      <table className="kv-table">
        <tbody>
          {strategy && (
            <>
              <tr>
                <td colSpan={2}>
                  <strong>Stratégie (MetaLearner)</strong>
                </td>
              </tr>
              {Object.entries(strategy).map(([k, v]) => (
                <tr key={`strategy-${k}`}>
                  <td>{k}</td>
                  <td className="mono">{String(v)}</td>
                </tr>
              ))}
            </>
          )}
          {typeof memory_peak_mb === "number" && (
            <tr>
              <td>memory_peak_mb</td>
              <td className="mono">{memory_peak_mb.toFixed(1)} Mo</td>
            </tr>
          )}
          {model_response && (
            <tr>
              <td>model_response</td>
              <td className="mono">{JSON.stringify(model_response)}</td>
            </tr>
          )}
          {restEntries.map(([k, v]) => (
            <tr key={k}>
              <td>{k}</td>
              <td className="mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
            </tr>
          ))}
          {!strategy && memory_peak_mb === undefined && !model_response && restEntries.length === 0 && (
            <tr>
              <td colSpan={2}>Aucun fait supplémentaire.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function EvalRunDetail({ evalRunId }: { evalRunId: string }) {
  const run = useEvalRun(evalRunId);
  const error = run.error instanceof ApiError ? run.error : null;

  if (run.isLoading) return <LoadingState />;
  if (error?.status === 404) return <ErrorState detail="Cet EvalRun n'existe plus." />;
  if (error) return <ErrorState detail={`Erreur API (HTTP ${error.status}) : ${error.message}`} />;
  if (run.data?.status !== "real" || !run.data.data) {
    return <ErrorState detail={run.data?.detail ?? "Backend indisponible."} />;
  }

  const r = run.data.data;
  return (
    <div>
      <div className="stat-grid">
        <StatCard label="Verdict" value={r.oracle_verdict} status={VERDICT_BADGE[r.oracle_verdict] ?? "unknown"} />
        <StatCard label="Méthode" value={r.method} />
        <StatCard label="Durée" value={r.elapsed_s !== null ? `${r.elapsed_s.toFixed(2)}s` : "—"} />
        <StatCard label="Code de sortie" value={r.exit_code ?? "—"} />
        <StatCard label="Clone" value={r.clone_id ? r.clone_id.slice(0, 8) : "—"} />
        <StatCard label="Modèle" value={r.model_id ?? "— (chemin sandbox déterministe)"} />
      </div>
      {r.oracle_detail && <p className="page__subtitle">{r.oracle_detail}</p>}
      <p className="page__subtitle mono">
        Tâche : {r.task_id} · créé le {new Date(r.created_at).toLocaleString("fr-FR")}
      </p>
      <FactsView facts={r.facts} />
    </div>
  );
}

function CycleResultSummary({ cycle }: { cycle: ExperimentCycle }) {
  return (
    <div className="stat-grid">
      <StatCard label="Tâche générée" value={cycle.task.title} />
      <StatCard
        label="EvalRun"
        value={cycle.eval_run.oracle_verdict}
        status={VERDICT_BADGE[cycle.eval_run.oracle_verdict] ?? "unknown"}
      />
      <StatCard label="Mode" value={cycle.decision.mode ?? "— (bootstrap)"} />
      <StatCard label="Direction curriculum" value={cycle.decision.curriculum_decision.direction} />
      <StatCard label="Faiblesse avant" value={cycle.weakness_before.category} />
      <StatCard label="Faiblesse après" value={cycle.weakness_after.category} />
    </div>
  );
}

/**
 * `GET /eval-runs` (+ détail) et `POST /experiment-cycles` — le seul point
 * d'écriture de cette page. `requested_by=Subject.HUMAN` est forcé côté
 * backend (`apex/api/routes/evals.py`) : aucun champ d'identité n'est
 * envoyé ni proposé ici, jamais un contournement de PolicyEngine.
 */
export function ExperimentLabPage() {
  const evalRuns = useEvalRuns();
  const agents = useAgents();
  const createCycle = useCreateExperimentCycle();

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [specialty, setSpecialty] = useState(DEFAULT_SPECIALTY);
  const [currentLevel, setCurrentLevel] = useState(1);
  const [cloneId, setCloneId] = useState("");
  const [method, setMethod] = useState("practice");
  const [split, setSplit] = useState<"train" | "heldout">("train");
  const [cycleError, setCycleError] = useState<string | null>(null);
  const [lastCycle, setLastCycle] = useState<ExperimentCycle | null>(null);

  const runList = evalRuns.data?.status === "real" ? (evalRuns.data.data ?? []) : [];
  const agentList = agents.data?.status === "real" ? (agents.data.data ?? []) : [];

  async function handleCreateCycle() {
    setCycleError(null);
    setLastCycle(null);
    try {
      const envelope = await createCycle.mutateAsync({
        specialty,
        current_level: currentLevel,
        clone_id: cloneId,
        method,
        split,
      });
      if (envelope.status === "real" && envelope.data) {
        setLastCycle(envelope.data);
        setSelectedRunId(envelope.data.eval_run.id);
      } else {
        setCycleError(envelope.detail ?? "Cycle refusé par le backend.");
      }
    } catch (error) {
      if (error instanceof ApiError) {
        setCycleError(`HTTP ${error.status} : ${error.message}`);
      } else {
        setCycleError(error instanceof Error ? error.message : String(error));
      }
    }
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Experiment Lab</h1>
          <p className="page__subtitle">
            EvalRuns réels (<code>apex.evalrunner</code>) et déclenchement réel d&apos;un cycle expérimental (
            <code>apex.metalearner.cycle.run_experiment_cycle</code>) — sandbox réelle, jamais simulé.
          </p>
        </div>
      </div>

      <div className="two-col">
        <Panel title="Nouveau cycle expérimental">
          <div className="mission-form">
            <div className="mission-form__row">
              <label className="mission-form__field">
                <span>Spécialité</span>
                <Input value={specialty} onChange={(e) => setSpecialty(e.target.value)} />
              </label>
              <label className="mission-form__field">
                <span>Niveau actuel</span>
                <Input
                  type="number"
                  min={1}
                  value={currentLevel}
                  onChange={(e) => setCurrentLevel(Number(e.target.value) || 1)}
                />
              </label>
            </div>
            <label className="mission-form__field">
              <span>Clone ({agentList.length} disponible{agentList.length > 1 ? "s" : ""})</span>
              <Select value={cloneId} onChange={(e) => setCloneId(e.target.value)} disabled={agentList.length === 0}>
                <option value="">— sélectionner un clone réel —</option>
                {agentList.map((a) => (
                  <option key={a.clone_id} value={a.clone_id}>
                    {a.type} · {a.clone_id.slice(0, 8)} · {a.state}
                  </option>
                ))}
              </Select>
            </label>
            <div className="mission-form__row">
              <label className="mission-form__field">
                <span>Méthode</span>
                <Select value={method} onChange={(e) => setMethod(e.target.value)}>
                  {METHODS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="mission-form__field">
                <span>Split</span>
                <Select value={split} onChange={(e) => setSplit(e.target.value as "train" | "heldout")}>
                  <option value="train">train</option>
                  <option value="heldout">heldout</option>
                </Select>
              </label>
            </div>
            <p className="mission-form__hint">
              Lance réellement : specialty=&quot;{specialty}&quot;, niveau {currentLevel}, clone{" "}
              {cloneId ? cloneId.slice(0, 8) : "(aucun sélectionné)"} — requis par{" "}
              <code>Subject.HUMAN</code> côté backend.
            </p>
            <Button
              variant="primary"
              disabled={!specialty.trim() || !cloneId || createCycle.isPending}
              onClick={handleCreateCycle}
            >
              {createCycle.isPending ? "Cycle en cours…" : "Lancer le cycle"}
            </Button>
            {cycleError && <ErrorState detail={cycleError} />}
            {lastCycle && (
              <>
                <p className="page__subtitle">Dernier cycle réussi :</p>
                <CycleResultSummary cycle={lastCycle} />
              </>
            )}
          </div>
        </Panel>

        <Panel title="EvalRuns" dense>
          {evalRuns.isLoading ? (
            <LoadingState />
          ) : evalRuns.data?.status !== "real" ? (
            <ErrorState detail={evalRuns.data?.detail ?? "Backend indisponible."} />
          ) : runList.length === 0 ? (
            <ErrorState detail="Aucun EvalRun pour l'instant." />
          ) : (
            <div className="kv-table-wrap">
              <table className="kv-table">
                <thead>
                  <tr>
                    <th>Verdict</th>
                    <th>Méthode</th>
                    <th>Créé le</th>
                  </tr>
                </thead>
                <tbody>
                  {runList.map((r) => (
                    <tr
                      key={r.id}
                      className={selectedRunId === r.id ? "kv-table__row--selected" : ""}
                      onClick={() => setSelectedRunId(r.id)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        <StatusBadge status={VERDICT_BADGE[r.oracle_verdict] ?? "unknown"} title={r.oracle_verdict} />
                      </td>
                      <td>{r.method}</td>
                      <td className="mono">{new Date(r.created_at).toLocaleString("fr-FR")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      {selectedRunId && (
        <Panel title={`EvalRun ${selectedRunId.slice(0, 8)}`}>
          <EvalRunDetail evalRunId={selectedRunId} />
        </Panel>
      )}
    </div>
  );
}
