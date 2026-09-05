import { useState } from "react";

import { ApiError } from "../api/client";
import { useSelfModel, useWeakness } from "../api/hooks";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { StatCard } from "../components/common/StatCard";
import { MeasurementValue } from "../components/status/MeasurementValue";
import { StatusBadge } from "../components/status/StatusBadge";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Field";
import "./pages.css";

const DEFAULT_SPECIALTY = "research";

const CATEGORY_LABEL: Record<string, string> = {
  insufficient_data: "Données insuffisantes",
  no_weakness: "Aucune faiblesse",
  isolated_failure: "Échec isolé",
  repeated_weakness: "Faiblesse répétée",
  statistically_credible_weakness: "Faiblesse statistiquement crédible",
};

const CATEGORY_BADGE: Record<string, "real" | "unknown" | "simulated"> = {
  insufficient_data: "unknown",
  no_weakness: "real",
  isolated_failure: "unknown",
  repeated_weakness: "unknown",
  statistically_credible_weakness: "unknown",
};

/**
 * `GET /weakness/{specialty}` (apex.weakness.WeaknessDetectorService, réel) +
 * `GET /self-model/{specialty}` pour la confiance calibrée et le coût déjà
 * mesurés — deux projections read-only du même backend, jamais une deuxième
 * logique de calcul ici.
 */
export function WeaknessesPage() {
  const [specialtyInput, setSpecialtyInput] = useState(DEFAULT_SPECIALTY);
  const [specialty, setSpecialty] = useState(DEFAULT_SPECIALTY);
  const weakness = useWeakness(specialty);
  const selfModel = useSelfModel(specialty);

  const weaknessError = weakness.error instanceof ApiError ? weakness.error : null;
  const report = weakness.data?.status === "real" ? weakness.data.data : null;
  const knowledge = selfModel.data?.status === "real" ? selfModel.data.data : null;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Weaknesses</h1>
          <p className="page__subtitle">
            Détection de faiblesse réelle par spécialité — <code>apex.weakness.WeaknessDetectorService</code>, jamais
            une donnée fabriquée.
          </p>
        </div>
      </div>

      <Panel title="Spécialité" dense>
        <form
          className="mission-form"
          onSubmit={(e) => {
            e.preventDefault();
            setSpecialty(specialtyInput.trim());
          }}
        >
          <div className="mission-form__row">
            <label className="mission-form__field">
              <span>Spécialité (ex. « research »)</span>
              <Input value={specialtyInput} onChange={(e) => setSpecialtyInput(e.target.value)} />
            </label>
            <Button type="submit" variant="primary" disabled={!specialtyInput.trim()}>
              Inspecter
            </Button>
          </div>
        </form>
      </Panel>

      {weakness.isLoading ? (
        <LoadingState />
      ) : weaknessError?.status === 422 ? (
        <ErrorState detail={`Spécialité inconnue : ${weaknessError.message}`} />
      ) : weaknessError ? (
        <ErrorState detail={`Erreur API (HTTP ${weaknessError.status}) : ${weaknessError.message}`} />
      ) : weakness.data?.status !== "real" ? (
        <ErrorState detail={weakness.data?.detail ?? "Backend indisponible."} />
      ) : report ? (
        <>
          <Panel
            title={
              <>
                Rapport de faiblesse — {specialty}{" "}
                <StatusBadge status={CATEGORY_BADGE[report.category] ?? "unknown"} />
              </>
            }
          >
            <p>{CATEGORY_LABEL[report.category] ?? report.category}</p>
            <p className="page__subtitle">{report.reason}</p>
            <div className="stat-grid">
              <StatCard label="Réussites" value={report.pass_count} />
              <StatCard label="Échecs" value={report.fail_count} />
              <StatCard label="Erreurs infra" value={report.error_count} />
              <StatCard label="Timeouts" value={report.timeout_count} />
              <StatCard label="Inconnus" value={report.unknown_count} />
              <StatCard
                label="Taux d'échec"
                value={report.failure_rate !== null ? `${(report.failure_rate * 100).toFixed(1)}%` : "—"}
              />
              <StatCard label="EvalRuns cités" value={report.evidence_run_ids.length} />
            </div>
          </Panel>

          <Panel title="Confiance calibrée et coût (via Self-Model)" dense>
            {selfModel.isLoading ? (
              <LoadingState />
            ) : !knowledge ? (
              <ErrorState detail={selfModel.data?.detail ?? "Indisponible."} />
            ) : (
              <div className="stat-grid">
                <StatCard label="Statut calibration" value={knowledge.calibrated_confidence.status} />
                <StatCard
                  label="Estimation calibrée"
                  value={
                    knowledge.calibrated_confidence.calibrated_point_estimate !== null
                      ? `${(knowledge.calibrated_confidence.calibrated_point_estimate * 100).toFixed(1)}%`
                      : "—"
                  }
                  hint={
                    knowledge.calibrated_confidence.calibrated_lower_bound !== null &&
                    knowledge.calibrated_confidence.calibrated_upper_bound !== null
                      ? `IC 95% [${(knowledge.calibrated_confidence.calibrated_lower_bound * 100).toFixed(0)}%, ${(knowledge.calibrated_confidence.calibrated_upper_bound * 100).toFixed(0)}%]`
                      : undefined
                  }
                />
                <StatCard label="EvalRuns (spécialité)" value={knowledge.eval_run_count} />
                <StatCard label="Méthodes observées" value={knowledge.methods_used.join(", ") || "—"} />
                <div className="stat-card">
                  <div className="stat-card__label">Coût temps réel</div>
                  <MeasurementValue measurement={knowledge.cost.elapsed_s} unit="s" />
                </div>
                <div className="stat-card">
                  <div className="stat-card__label">Pic mémoire</div>
                  <MeasurementValue measurement={knowledge.cost.memory_peak_mb} unit=" Mo" />
                </div>
              </div>
            )}
          </Panel>
        </>
      ) : (
        <ErrorState detail="Aucune donnée." />
      )}
    </div>
  );
}
