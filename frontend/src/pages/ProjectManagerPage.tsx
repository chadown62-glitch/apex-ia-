import { useState } from "react";

import { useConversations, useCreateProject, useProjects } from "../api/hooks";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { PlannedBanner, PlannedSectionGrid } from "../components/common/PlannedSections";
import "./pages.css";
import "./mission-center.css";

const PLANNED_SECTIONS = [
  { icon: "✅", title: "Tâches", desc: "Décomposition d'un projet en tâches assignables à des clones." },
  { icon: "🗂️", title: "Kanban", desc: "Tableau visuel — à faire / en cours / terminé, par tâche." },
  { icon: "🔗", title: "Dépendances", desc: "Ordre d'exécution imposé entre tâches." },
  { icon: "🎯", title: "Objectifs", desc: "Critères de réussite déclarés pour le projet." },
  { icon: "📈", title: "Progression", desc: "Avancement réellement mesuré, jamais estimé." },
  { icon: "🤖", title: "Agents associés", desc: "Clones actuellement affectés au projet — lien vers Agent Center." },
  { icon: "📜", title: "Timeline", desc: "Chronologie des décisions et jalons du projet." },
  { icon: "⚠️", title: "Risques", desc: "Points de blocage identifiés, avec leur statut." },
];

/**
 * Structure MINIMALE réelle (`apex.projects`) : créer/lister un projet et
 * voir les conversations qui s'y rattachent (`Conversation.project_id`).
 * Tout le reste (tâches/kanban/dépendances/timeline/risques) reste
 * `PLANNED` — délibérément, pour ne pas ralentir le MVP (voir directive
 * « Première interface utilisable », §4).
 */
export function ProjectManagerPage() {
  const projects = useProjects();
  const createProject = useCreateProject();
  const conversations = useConversations();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const projectList = projects.data?.status === "real" ? projects.data.data ?? [] : [];
  const conversationList = conversations.data?.status === "real" ? conversations.data.data ?? [] : [];

  async function handleCreate() {
    setCreateError(null);
    const envelope = await createProject.mutateAsync({ name, description });
    if (envelope.status === "real") {
      setName("");
      setDescription("");
    } else {
      setCreateError(envelope.detail ?? "Création refusée par le backend.");
    }
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Project Manager</h1>
          <p className="page__subtitle">Regroupement réel de conversations par projet — le reste (tâches, kanban…) reste planifié.</p>
        </div>
      </div>

      <div className="two-col">
        <Panel title="Nouveau projet">
          <div className="mission-form">
            <label className="mission-form__field">
              <span>Nom</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nom du projet" />
            </label>
            <label className="mission-form__field">
              <span>Description</span>
              <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
            </label>
            <button
              className="action-button action-button--live"
              disabled={!name.trim() || createProject.isPending}
              onClick={handleCreate}
            >
              {createProject.isPending ? "Création…" : "Créer le projet"}
            </button>
            {createError && <ErrorState detail={createError} />}
          </div>
        </Panel>

        <Panel title="Projets" dense>
          {projects.isLoading ? (
            <LoadingState />
          ) : projects.data?.status !== "real" ? (
            <ErrorState detail={projects.data?.detail} />
          ) : projectList.length === 0 ? (
            <ErrorState detail="Aucun projet créé pour l'instant." />
          ) : (
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Nom</th>
                  <th>Conversations</th>
                  <th>Créé le</th>
                </tr>
              </thead>
              <tbody>
                {projectList.map((p) => (
                  <tr key={p.id}>
                    <td>{p.name}</td>
                    <td>{conversationList.filter((c) => c.project_id === p.id).length}</td>
                    <td className="mono">{new Date(p.created_at).toLocaleDateString("fr-FR")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      <PlannedBanner note="Tâches/kanban/dépendances/timeline/risques : aucun backend n'existe encore pour ces aspects. Seule l'association conversation ↔ projet est réelle ci-dessus." />

      <PlannedSectionGrid sections={PLANNED_SECTIONS} />
    </div>
  );
}
