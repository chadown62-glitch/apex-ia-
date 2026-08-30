import { PlannedBanner, PlannedSectionGrid } from "../components/common/PlannedSections";
import "./pages.css";

const SECTIONS = [
  { icon: "📁", title: "Projets", desc: "Vue d'ensemble des projets actifs et de leurs objectifs." },
  { icon: "✅", title: "Tâches", desc: "Décomposition d'un projet en tâches assignables à des clones." },
  { icon: "🗂️", title: "Kanban", desc: "Tableau visuel — à faire / en cours / terminé, par tâche." },
  { icon: "🔗", title: "Dépendances", desc: "Ordre d'exécution imposé entre tâches." },
  { icon: "🎯", title: "Objectifs", desc: "Critères de réussite déclarés pour le projet." },
  { icon: "📈", title: "Progression", desc: "Avancement réellement mesuré, jamais estimé." },
  { icon: "🤖", title: "Agents associés", desc: "Clones actuellement affectés au projet — lien vers Agent Center." },
  { icon: "📜", title: "Timeline", desc: "Chronologie des décisions et jalons du projet." },
  { icon: "⚠️", title: "Risques", desc: "Points de blocage identifiés, avec leur statut." },
  { icon: "📊", title: "Résultats", desc: "Livrables produits et leur validation." },
];

/**
 * Maquette structurelle — aucun backend Project Manager n'existe.
 * Les "Agents associés" pointeront un jour vers de vrais clones (Agent
 * Center, déjà connecté à /agents) ; ici, aucune association n'est encore
 * possible tant que le concept de "projet" n'existe pas côté backend.
 */
export function ProjectManagerPage() {
  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Project Manager</h1>
          <p className="page__subtitle">Suivi de missions, jalons et livrables multi-clones.</p>
        </div>
      </div>

      <PlannedBanner note="Aucun backend Project Manager n'existe. Cette page montre la disposition prévue des écrans — aucune donnée, aucune action réelle." />

      <PlannedSectionGrid sections={SECTIONS} />
    </div>
  );
}
