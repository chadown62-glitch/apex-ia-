import { PlannedBanner, PlannedSectionGrid } from "../components/common/PlannedSections";
import "./pages.css";

const SECTIONS = [
  { icon: "🗂️", title: "Workspace", desc: "Espace de travail dédié à un projet de design." },
  { icon: "📁", title: "Projets", desc: "Liste des projets de conception en cours." },
  { icon: "🖼️", title: "Assets", desc: "Images, icônes et ressources versionnées par projet." },
  { icon: "🖌️", title: "Canvas", desc: "Surface de composition visuelle interactive." },
  { icon: "🕘", title: "Versions", desc: "Historique des itérations d'une même maquette." },
  { icon: "🧩", title: "Composants", desc: "Bibliothèque de composants réutilisables entre projets." },
  { icon: "📤", title: "Exports", desc: "Génération d'artefacts (images, code) à partir du canvas." },
];

/**
 * Maquette structurelle — aucun backend Design Studio n'existe. Layout
 * préparé pour ne pas devoir refaire l'interface le jour où il existera.
 */
export function DesignStudioPage() {
  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Design Studio</h1>
          <p className="page__subtitle">Ateliers de conception d'interfaces et d'expériences.</p>
        </div>
      </div>

      <PlannedBanner note="Aucun backend Design Studio n'existe. Cette page montre la disposition prévue des écrans — aucune donnée, aucune action réelle." />

      <PlannedSectionGrid sections={SECTIONS} />
    </div>
  );
}
