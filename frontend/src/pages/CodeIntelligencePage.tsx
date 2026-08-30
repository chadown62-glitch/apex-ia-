import { PlannedBanner, PlannedSectionGrid } from "../components/common/PlannedSections";
import "./pages.css";

const SECTIONS = [
  { icon: "📁", title: "Workspace", desc: "Arborescence du dépôt ciblé par la mission — lecture seule au départ." },
  { icon: "📄", title: "Fichiers", desc: "Liste des fichiers modifiés/proposés par un clone DEVELOPER." },
  { icon: "✍️", title: "Éditeur", desc: "Diff et édition assistée, jamais un commit automatique sans validation humaine." },
  { icon: "🔍", title: "Analyse", desc: "Revue statique par un clone CRITIC — style, complexité, conventions." },
  { icon: "🧪", title: "Tests", desc: "Exécution des tests existants — jamais inventés ni simulés." },
  { icon: "🩺", title: "Diagnostics", desc: "Erreurs de typage, lint, avertissements de build." },
  { icon: "🔗", title: "Dépendances", desc: "Graphe des dépendances du module concerné par la mission." },
  { icon: "🕘", title: "Historique", desc: "Missions de code passées et leur résultat (audit_log)." },
  { icon: "📊", title: "Résultats", desc: "Comparaison avant/après, mesurée — jamais estimée." },
];

/**
 * Maquette structurelle — aucun endpoint /code-intelligence n'existe côté
 * backend. Cette page prépare l'UX (voir docs/COGNITIVE_ARCHITECTURE.md) sans
 * simuler la moindre action : rien ici n'est cliquable au-delà de la
 * navigation elle-même.
 */
export function CodeIntelligencePage() {
  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Code Intelligence</h1>
          <p className="page__subtitle">Analyse, revue et génération de code assistées par clones DEVELOPER/CRITIC.</p>
        </div>
      </div>

      <PlannedBanner note="Aucun backend Code Intelligence n'existe. Cette page montre la disposition prévue des écrans — aucune donnée, aucune action réelle." />

      <PlannedSectionGrid sections={SECTIONS} />
    </div>
  );
}
