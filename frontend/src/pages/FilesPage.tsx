import { PlannedBanner, PlannedSectionGrid } from "../components/common/PlannedSections";
import "./pages.css";

const SECTIONS = [
  { icon: "📄", title: "Texte", desc: "Import/collage de documents texte pour une conversation ou une mission." },
  { icon: "🖼️", title: "Images", desc: "Réception d'images — nécessite un modèle multimodal réel (aucun aujourd'hui, voir Capability.VISION)." },
  { icon: "🖥️", title: "Captures d'écran", desc: "Compréhension de capture d'écran — même dépendance : Capability.SCREEN, aucun signal réel dans le schéma actuel." },
  { icon: "🎙️", title: "Voix", desc: "Message vocal en entrée, réponse vocale à la demande — Capability.AUDIO/VOICE, PLANNED." },
  { icon: "🎬", title: "Vidéo", desc: "Compréhension puis génération vidéo — Capability.VIDEO, PLANNED, la dernière étape de la feuille de route multimodale." },
  { icon: "🗂️", title: "Bibliothèque", desc: "Fichiers déjà envoyés, organisés par conversation/projet." },
];

/**
 * Structure UI seulement — `apex.models.catalog`/`apex.routing.model.
 * Capability` sont déjà prêts pour VISION/AUDIO/VOICE/VIDEO/SCREEN (passe
 * « Model/Provider Catalog »), mais AUCUN modèle réel ne les déclare
 * aujourd'hui (aucun signal dans le schéma `ModelEndpoint`) — donc rien
 * n'est cliquable ici au-delà de la navigation, jamais une simulation
 * présentée comme fonctionnelle.
 */
export function FilesPage() {
  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Files</h1>
          <p className="page__subtitle">Réception et gestion de fichiers pour le Chat/les Missions.</p>
        </div>
      </div>

      <PlannedBanner note="Aucun backend Files n'existe encore. L'architecture (Capability.VISION/AUDIO/VOICE/VIDEO/SCREEN) est prête à recevoir un modèle réel qui les déclare — rien n'est simulé en attendant." />

      <PlannedSectionGrid sections={SECTIONS} />
    </div>
  );
}
