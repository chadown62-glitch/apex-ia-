import type { Specialty } from "../specialties/types";
import "./pages.css";

/**
 * Page générique réutilisée par toute Specialty avec status "planned" —
 * jamais de données inventées, jamais de graphique vide qui suggère une
 * mesure. Le contenu vient uniquement de la description statique de la
 * Specialty et, s'il existe, de ses métadonnées de workspace prévues.
 */
export function PlannedSpecialtyPage({ specialty }: { specialty: Specialty }) {
  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{specialty.label}</h1>
          <p className="page__subtitle">{specialty.description}</p>
        </div>
      </div>

      <div className="planned-page">
        <span className="planned-page__icon" aria-hidden="true">
          {specialty.icon}
        </span>
        <h2 className="planned-page__title">Aucun backend pour l'instant</h2>
        <p className="planned-page__desc">
          Cette spécialité n'a pas encore d'endpoint API. Aucune donnée n'est simulée ici : l'espace de travail
          (agents compatibles, outils, permissions, missions, métriques, ressources, historique, paramètres) sera
          branché dès qu'un backend réel existera, sans refonte de l'interface.
        </p>
        {specialty.workspace?.compatibleAgentTypes && (
          <p className="planned-page__meta">
            Types de clones prévus : {specialty.workspace.compatibleAgentTypes.join(", ")}
          </p>
        )}
      </div>
    </div>
  );
}
