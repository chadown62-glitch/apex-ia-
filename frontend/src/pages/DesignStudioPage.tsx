import { useState } from "react";

import { useDesign, useRecentDesigns, useResumeDesign, useSubmitDesign } from "../api/hooks";
import type { DesignPackage } from "../api/types";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import "./pages.css";
import "./design-studio.css";

const STATE_LABEL: Record<string, string> = {
  request: "Requête", analyzing: "Analyse", researching: "Recherche", synthesizing: "Synthèse",
  designing: "Conception", visualizing: "Visualisation", validating: "Validation", packaging: "Empaquetage",
  completed: "Terminée", failed: "Échouée",
};

const STEP_LABEL: Record<string, string> = {
  analyze: "Analyser", research: "Rechercher", synthesize: "Synthétiser", design: "Concevoir",
  visualize: "Visualiser", validate: "Valider", package: "Empaqueter",
};

const STEP_STATE_LABEL: Record<string, string> = {
  pending: "en attente", running: "en cours", completed: "terminée", skipped: "ignorée", failed: "échouée",
};

/** Vocabulaire d'état d'un DesignPackage (apex.design.states.DesignPipelineState)
 * — même patron que MissionStatePill, jamais réutilisé tel quel : deux
 * vocabulaires distincts (missions vs conceptions). */
function DesignStatePill({ state }: { state: string }) {
  return <span className={`design-state design-state--${state}`}>{STATE_LABEL[state] ?? state}</span>;
}

function StepTimeline({ steps }: { steps: DesignPackage["steps"] }) {
  if (steps.length === 0) return <p className="design-empty-note">Aucune étape encore exécutée.</p>;
  return (
    <ol className="step-timeline">
      {steps.map((s) => (
        <li key={s.step} className={`step-timeline__item step-timeline__item--${s.state}`}>
          <div className="step-timeline__row">
            <strong>{STEP_LABEL[s.step] ?? s.step}</strong>
            <span className="step-timeline__state">{STEP_STATE_LABEL[s.state] ?? s.state}</span>
            {s.duration_s !== null && <span className="step-timeline__duration">{s.duration_s.toFixed(2)}s</span>}
          </div>
          <p className="step-timeline__summary">{s.output_summary || s.input_summary}</p>
          <p className="step-timeline__provenance mono">{s.provenance}</p>
          {s.error && <ErrorState detail={s.error} />}
        </li>
      ))}
    </ol>
  );
}

function DesignDetail({
  design,
  onResume,
  resuming,
}: {
  design: DesignPackage;
  onResume: () => void;
  resuming: boolean;
}) {
  const unavailableVisuals = design.visual_results.filter((v) => v.state === "unavailable");
  return (
    <div className="design-detail">
      <div className="design-detail__row">
        <strong>{design.request.text}</strong>
        <DesignStatePill state={design.state} />
      </div>

      {design.state === "failed" && (
        <div className="design-detail__actions">
          <button className="action-button action-button--live" disabled={resuming} onClick={onResume}>
            {resuming ? "Reprise…" : "Reprendre la conception"}
          </button>
        </div>
      )}
      {design.error && <ErrorState detail={design.error} />}

      <Panel title="Étapes du pipeline" dense>
        <StepTimeline steps={design.steps} />
      </Panel>

      {design.brief && (
        <Panel title="Brief" dense>
          <div className="kv-table-wrap">
            <table className="kv-table">
              <tbody>
                <tr><th>Résumé</th><td>{design.brief.summary}</td></tr>
                <tr><th>Public</th><td>{design.brief.audience}</td></tr>
                <tr><th>Mots-clés de style</th><td>{design.brief.style_keywords.join(", ") || "—"}</td></tr>
                <tr><th>Objectifs</th><td>{design.brief.goals.join(", ") || "—"}</td></tr>
                <tr><th>Contraintes</th><td>{design.brief.constraints.join(", ") || "—"}</td></tr>
                <tr><th>Provenance</th><td className="mono">{design.brief.provenance}</td></tr>
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {design.concepts.length > 0 && (
        <Panel title={`Concepts (${design.concepts.length})`} dense>
          <div className="design-concepts">
            {design.concepts.map((c) => (
              <div key={c.id} className="design-concept-card">
                <strong>{c.title}</strong>
                <p>{c.narrative}</p>
                {c.key_elements.length > 0 && <p className="mono">{c.key_elements.join(" · ")}</p>}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {design.visual_prompts.length > 0 && (
        <Panel
          title={`Visuels (${design.visual_prompts.length} prompt${design.visual_prompts.length > 1 ? "s" : ""})`}
          dense
        >
          {unavailableVisuals.length === design.visual_results.length && unavailableVisuals.length > 0 && (
            <ErrorState detail={unavailableVisuals[0].detail} />
          )}
          <div className="kv-table-wrap">
            <table className="kv-table">
              <thead>
                <tr><th>Sujet</th><th>Style</th><th>Résultat</th></tr>
              </thead>
              <tbody>
                {design.visual_prompts.map((p) => {
                  const result = design.visual_results.find((v) => v.prompt_id === p.id);
                  return (
                    <tr key={p.id}>
                      <td>{p.subject}</td>
                      <td>{p.style}</td>
                      <td>{result ? (result.state === "completed" ? "généré" : result.state) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {design.validation && (
        <Panel title="Validation" dense>
          <p>
            <span className={`design-validation ${design.validation.passed ? "design-validation--pass" : "design-validation--fail"}`}>
              {design.validation.passed ? "Conforme" : "Non conforme"}
            </span>
            {" — "}
            {design.validation.checks.length} vérification{design.validation.checks.length > 1 ? "s" : ""}
            {design.validation.failures.length > 0 && `, ${design.validation.failures.length} échec(s)`}
          </p>
          {design.validation.failures.length > 0 && (
            <ul className="design-validation-failures">
              {design.validation.failures.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      {design.artifacts.length > 0 && (
        <Panel title={`Artefacts (${design.artifacts.length})`} dense>
          <div className="kv-table-wrap">
            <table className="kv-table">
              <tbody>
                {design.artifacts.map((a) => (
                  <tr key={a.content_hash}>
                    <th>{a.kind}</th>
                    <td className="mono">{a.content_hash}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

/**
 * Design Engine (apex.design.service, N+17) — réellement câblé via
 * `POST/GET /operator/designs*`. Le pipeline ANALYZE -> RESEARCH ->
 * SYNTHESIZE -> DESIGN -> VISUALIZE -> VALIDATE -> PACKAGE s'exécute pour de
 * vrai (voir `apex.interfaces.registry`, entrée "DesignEngine") ; deux
 * étapes restent honnêtement indisponibles faute de backend connecté à ce
 * dépôt — recherche web (RESEARCH) et génération d'image (VISUALIZE) — ni
 * simulées ni masquées, visibles ci-dessous exactement comme le backend les
 * rapporte.
 */
export function DesignStudioPage() {
  const recent = useRecentDesigns();
  const submitDesign = useSubmitDesign();
  const resumeDesign = useResumeDesign();

  const [requestText, setRequestText] = useState("");
  const [selectedDesignId, setSelectedDesignId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const selected = useDesign(selectedDesignId ?? undefined);
  const selectedDesign = selected.data?.status === "real" ? selected.data.data : null;
  const recentDesigns = recent.data?.status === "real" ? recent.data.data ?? [] : [];

  async function handleSubmit() {
    setSubmitError(null);
    const envelope = await submitDesign.mutateAsync({ request: requestText });
    if (envelope.status === "real" && envelope.data) {
      setSelectedDesignId(envelope.data.design_id);
      setRequestText("");
    } else {
      setSubmitError(envelope.detail ?? "Soumission refusée par le backend.");
    }
  }

  function handleResume() {
    if (selectedDesign) resumeDesign.mutate(selectedDesign.design_id);
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Design Studio</h1>
          <p className="page__subtitle">Design Engine — pipeline de conception réel (brief → concepts → visuels → paquet).</p>
        </div>
      </div>

      <div className="two-col">
        <Panel title="Nouvelle conception">
          <div className="mission-form">
            <label className="mission-form__field">
              <span>Requête</span>
              <textarea
                rows={4}
                value={requestText}
                onChange={(e) => setRequestText(e.target.value)}
                placeholder="Décrivez ce que vous voulez concevoir…"
              />
            </label>
            <p className="design-form-hint">
              Recherche web et génération d'image ne sont connectées à aucun backend réel dans ce dépôt : ces étapes
              s'exécutent et le déclarent honnêtement plutôt que de fabriquer un résultat.
            </p>
            <button
              className="action-button action-button--live"
              disabled={!requestText.trim() || submitDesign.isPending}
              onClick={handleSubmit}
            >
              {submitDesign.isPending ? "Conception en cours…" : "Lancer la conception"}
            </button>
            {submitError && <ErrorState detail={submitError} />}
          </div>
        </Panel>

        <Panel title={selectedDesign ? "Conception sélectionnée" : "Statut"}>
          {!selectedDesignId ? (
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-muted)" }}>
              Aucune conception sélectionnée — lancez-en une ou choisissez-en une dans l'historique ci-dessous.
            </p>
          ) : selected.isLoading ? (
            <LoadingState />
          ) : !selectedDesign ? (
            <ErrorState detail={selected.data?.detail} />
          ) : (
            <DesignDetail design={selectedDesign} onResume={handleResume} resuming={resumeDesign.isPending} />
          )}
        </Panel>
      </div>

      <Panel title="Conceptions récentes" dense>
        {recent.isLoading ? (
          <LoadingState />
        ) : recentDesigns.length > 0 ? (
          <div className="kv-table-wrap">
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Requête</th>
                  <th>État</th>
                  <th>Créée</th>
                </tr>
              </thead>
              <tbody>
                {recentDesigns.map((d) => (
                  <tr
                    key={d.design_id}
                    className="mission-row"
                    onClick={() => setSelectedDesignId(d.design_id)}
                    style={{ cursor: "pointer", background: d.design_id === selectedDesignId ? "var(--bg-hover)" : undefined }}
                  >
                    <td>{d.request.text}</td>
                    <td><DesignStatePill state={d.state} /></td>
                    <td className="mono">{new Date(d.created_at).toLocaleString("fr-FR")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ErrorState detail={recent.data?.detail ?? "Aucune conception lancée pour l'instant."} />
        )}
      </Panel>
    </div>
  );
}
