/**
 * Accueil de l'assistant — choisir un mode, écrire le premier message : la conversation est CRÉÉE puis le
 * tour est SOUMIS au backend (deux appels réels), et l'on bascule sur la conversation qui suit la réponse.
 * `?project=<id>` crée la conversation dans ce projet.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { assistantApi, errorMessage, type ModeId } from "./api";
import { Composer } from "./Composer";
import { keys, useConversations, useModels, useModes, useProjects, useSettings } from "./hooks";
import { IconFolder } from "./Icons";
import { ModePicker } from "./ModePicker";
import { ModelNotice } from "./ModelNotice";

function titleFrom(content: string): string {
  const firstLine = content.trim().split("\n")[0].trim();
  return firstLine.length > 60 ? `${firstLine.slice(0, 57).trimEnd()}…` : firstLine || "Nouvelle conversation";
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5 || hour >= 22) return "Bonsoir";
  if (hour < 18) return "Bonjour";
  return "Bonsoir";
}

export function HomeView() {
  const [params, setParams] = useSearchParams();
  const projectId = params.get("project");
  const projects = useProjects();
  const conversations = useConversations();
  const modes = useModes();
  const settings = useSettings();
  const models = useModels();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [mode, setMode] = useState<ModeId | null>(null);
  const [error, setError] = useState<string | null>(null);

  const project = projectId ? projects.data?.find((p) => p.id === projectId) : undefined;
  const selectedMode: ModeId = mode ?? settings.data?.behavior.default_mode ?? "normal";
  const recent = (conversations.data ?? []).filter((c) => (projectId ? c.project_id === projectId : true)).slice(0, 4);

  useEffect(() => setError(null), [projectId]);

  async function start(content: string) {
    setError(null);
    let conversationId: string | null = null;
    try {
      const conversation = await assistantApi.createConversation({
        title: titleFrom(content),
        project_id: projectId,
        mode: selectedMode,
      });
      conversationId = conversation.id;
      await assistantApi.submitTurn(conversation.id, content);
      await queryClient.invalidateQueries({ queryKey: keys.conversations });
      navigate(`/c/${conversation.id}`);
    } catch (e) {
      if (conversationId) {
        // La conversation existe mais le message n'a pas été accepté : on l'ouvre avec le brouillon et l'erreur.
        await queryClient.invalidateQueries({ queryKey: keys.conversations });
        navigate(`/c/${conversationId}`, { state: { draft: content, error: errorMessage(e) } });
        return;
      }
      setError(errorMessage(e));
      throw e;
    }
  }

  return (
    <div className="ax-home">
      <div className="ax-home__hero">
        <h1 className="ax-home__title">
          {greeting()}. <span className="ax-home__title-soft">Que voulez-vous faire ?</span>
        </h1>
        {projectId && (
          <div className="ax-home__context">
            {project ? (
              <>
                <IconFolder size={15} />
                <span>
                  Nouvelle conversation dans <Link to={`/p/${project.id}`}>{project.name}</Link>
                </span>
              </>
            ) : projects.isLoading ? (
              <span>Chargement du projet…</span>
            ) : (
              <span className="ax-inline-error">Projet introuvable : la conversation sera créée hors projet si vous le retirez.</span>
            )}
            <button type="button" className="ax-chip-btn" onClick={() => setParams({})}>
              Hors projet
            </button>
          </div>
        )}
      </div>

      <ModelNotice models={models} />

      <ModePicker modes={modes} value={selectedMode} onChange={setMode} />

      <Composer
        autoFocus
        placeholder={
          {
            normal: "Posez une question, demandez une explication ou un texte…",
            code: "Décrivez le programme à écrire, le bug à corriger ou le code à analyser…",
            design: "Décrivez l'interface, la marque ou l'ambiance visuelle recherchée…",
            finance: "Budget, épargne, prêt, comparaison de scénarios…",
          }[selectedMode]
        }
        onSubmit={start}
        error={error}
        submitLabel="Démarrer la conversation"
      />

      {recent.length > 0 && (
        <section className="ax-home__recent" aria-labelledby="ax-recent-label">
          <h2 id="ax-recent-label">Reprendre</h2>
          <ul>
            {recent.map((c) => (
              <li key={c.id}>
                <Link to={`/c/${c.id}`} className="ax-recent glass">
                  <span className={`ax-mode-tag ax-mode-tag--${c.mode}`}>{c.mode}</span>
                  <span className="ax-recent__title">{c.title}</span>
                  <time dateTime={c.updated_at}>{new Date(c.updated_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}</time>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
