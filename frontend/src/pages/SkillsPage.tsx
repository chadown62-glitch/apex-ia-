import { useState } from "react";

import { ApiError } from "../api/client";
import { useSelfModel } from "../api/hooks";
import type { Skill } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { StatusBadge } from "../components/status/StatusBadge";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Field";
import "./pages.css";

const DEFAULT_SPECIALTY = "research";

function SkillTable({ title, skills }: { title: string; skills: Skill[] }) {
  return (
    <Panel title={title} dense>
      {skills.length === 0 ? (
        <EmptyState title="Aucune compétence" detail="Rien à afficher pour cette spécialité." />
      ) : (
        <div className="kv-table-wrap">
          <table className="kv-table">
            <thead>
              <tr>
                <th>Nom</th>
                <th>Statut</th>
                <th>Confiance</th>
                <th>Preuves</th>
                <th>Créée le</th>
              </tr>
            </thead>
            <tbody>
              {skills.map((s) => (
                <tr key={s.id}>
                  <td>
                    {s.name}
                    <div className="mono" style={{ fontSize: 11, opacity: 0.7 }}>
                      {s.description}
                    </div>
                  </td>
                  <td>{s.status}</td>
                  <td className="mono">{s.confidence !== null ? s.confidence.toFixed(2) : "—"}</td>
                  <td className="mono">{s.evidence_run_ids.length}</td>
                  <td className="mono">{new Date(s.created_at).toLocaleDateString("fr-FR")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/**
 * `apex.skills.SkillRegistryService` est réel côté backend (propose/promote/
 * reject) mais l'API (`apex/api/routes/evals.py`) n'expose aucun endpoint
 * `/skills` dédié — seul `GET /self-model/{specialty}` porte les compétences
 * acquises/proposées d'une spécialité (`SelfModelSpecialty.acquired_skills`/
 * `.proposed_skills`, réel, jamais fabriqué). Cette page reste donc en
 * LECTURE SEULE : aucune action propose/promote/reject n'est possible ici,
 * exactement ce que l'API permet aujourd'hui — jamais un système parallèle.
 */
export function SkillsPage() {
  const [specialtyInput, setSpecialtyInput] = useState(DEFAULT_SPECIALTY);
  const [specialty, setSpecialty] = useState(DEFAULT_SPECIALTY);
  const selfModel = useSelfModel(specialty);

  const error = selfModel.error instanceof ApiError ? selfModel.error : null;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Skills</h1>
          <p className="page__subtitle">
            Compétences acquises/proposées — projetées depuis <code>GET /self-model/&#123;specialty&#125;</code>,
            aucun endpoint <code>/skills</code> dédié n&apos;existe encore. Lecture seule : la proposition/promotion/
            rejet d&apos;une compétence reste hors de cette interface.
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

      {selfModel.isLoading ? (
        <LoadingState />
      ) : error?.status === 422 ? (
        <ErrorState detail={`Spécialité inconnue : ${error.message}`} />
      ) : error ? (
        <ErrorState detail={`Erreur API (HTTP ${error.status}) : ${error.message}`} />
      ) : selfModel.data?.status !== "real" || !selfModel.data.data ? (
        <ErrorState detail={selfModel.data?.detail ?? "Backend indisponible."} />
      ) : (
        <>
          <div className="page__header">
            <StatusBadge status="real" title="Projection réelle de apex.selfmodel + apex.skills" />
            <span className="page__subtitle">
              {selfModel.data.data.acquired_skills.length} acquise(s) ·{" "}
              {selfModel.data.data.proposed_skills.length} proposée(s)
            </span>
          </div>
          <SkillTable title="Compétences acquises" skills={selfModel.data.data.acquired_skills} />
          <SkillTable title="Compétences proposées" skills={selfModel.data.data.proposed_skills} />
        </>
      )}
    </div>
  );
}
