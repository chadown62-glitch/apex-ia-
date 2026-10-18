/**
 * Barre latérale de l'assistant — coulissante (mobile) et repliable (bureau, choix mémorisé). Projets et
 * conversations viennent de l'API (aucune liste d'exemple) ; chaque état est montré : chargement, erreur
 * avec nouvelle tentative, liste vide.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import { Modal } from "../components/ui/Modal";
import { assistantApi, errorMessage, type Conversation, type Project } from "./api";
import { keys, useConversations, useEngines, useProjects, useSettings } from "./hooks";
import {
  IconChevron,
  IconFolder,
  IconGauge,
  IconPlus,
  IconSettings,
  IconSidebar,
} from "./Icons";
import { ProjectForm } from "./ProjectForm";

const MODE_LABEL: Record<string, string> = { normal: "Normal", code: "Code", design: "Design", finance: "Finance", "3d": "3D" };

function ConversationLink({ conversation, onNavigate }: { conversation: Conversation; onNavigate: () => void }) {
  return (
    <NavLink
      to={`/c/${conversation.id}`}
      className={({ isActive }) => `ax-nav__item ax-nav__item--conversation ${isActive ? "is-active" : ""}`}
      onClick={onNavigate}
      title={conversation.title}
    >
      <span className="ax-nav__text">{conversation.title}</span>
      {conversation.mode !== "normal" && <span className={`ax-mode-dot ax-mode-dot--${conversation.mode}`} title={`Mode ${MODE_LABEL[conversation.mode]}`} />}
    </NavLink>
  );
}

function ProjectGroup({
  project,
  conversations,
  expanded,
  onToggle,
  onNavigate,
}: {
  project: Project;
  conversations: Conversation[];
  expanded: boolean;
  onToggle: () => void;
  onNavigate: () => void;
}) {
  return (
    <li className="ax-project">
      <div className="ax-project__row">
        <button
          type="button"
          className={`ax-project__toggle ${expanded ? "is-open" : ""}`}
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Replier" : "Déplier"} le projet ${project.name}`}
        >
          <IconChevron size={14} />
        </button>
        <NavLink
          to={`/p/${project.id}`}
          className={({ isActive }) => `ax-nav__item ax-nav__item--project ${isActive ? "is-active" : ""}`}
          onClick={onNavigate}
          title={project.name}
        >
          <IconFolder size={16} />
          <span className="ax-nav__text">{project.name}</span>
          <span className="ax-nav__count">{conversations.length}</span>
        </NavLink>
      </div>
      {expanded && (
        <ul className="ax-project__children">
          {conversations.map((c) => (
            <li key={c.id}>
              <ConversationLink conversation={c} onNavigate={onNavigate} />
            </li>
          ))}
          <li>
            <Link to={`/?project=${encodeURIComponent(project.id)}`} className="ax-nav__item ax-nav__item--add" onClick={onNavigate}>
              <IconPlus size={14} />
              <span className="ax-nav__text">Nouvelle conversation</span>
            </Link>
          </li>
        </ul>
      )}
    </li>
  );
}

export function AssistantSidebar({
  collapsed,
  onToggleCollapsed,
  mobileOpen,
  onCloseMobile,
}: {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}) {
  const projects = useProjects();
  const conversations = useConversations();
  const engines = useEngines();
  const settings = useSettings();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const needle = filter.trim().toLowerCase();
  const visible = useMemo(
    () => (conversations.data ?? []).filter((c) => !needle || c.title.toLowerCase().includes(needle)),
    [conversations.data, needle],
  );
  const standalone = visible.filter((c) => !c.project_id);
  const byProject = (projectId: string) => visible.filter((c) => c.project_id === projectId);
  const visibleProjects = (projects.data ?? []).filter(
    (p) => !needle || p.name.toLowerCase().includes(needle) || byProject(p.id).length > 0,
  );
  const activeProjectId = location.pathname.startsWith("/p/") ? location.pathname.slice(3) : null;
  const activeConversation = (conversations.data ?? []).find((c) => location.pathname === `/c/${c.id}`);

  // État du moteur CHOISI (Automatique, Ollama, APEX intégré, Cerveau APEX), tel que le backend vient de le mesurer.
  const chosenId = settings.data?.models.engine ?? "auto";
  const chosen = engines.data?.find((e) => e.id === chosenId);
  const modelState = engines.isLoading || settings.isLoading ? "pending" : engines.isError || !chosen ? "unknown"
    : !chosen.available ? "missing" : chosen.experimental ? "experimental" : "ready";
  const modelLabel = {
    pending: "Vérification du moteur…",
    unknown: "État du moteur inconnu",
    ready: chosen?.id === "auto" ? "Modèle prêt (automatique)" : `${chosen?.label ?? "Moteur"} prêt`,
    experimental: `${chosen?.label ?? "Moteur"} (expérimental)`,
    missing: chosen?.id === "auto" ? "Aucun modèle disponible" : `${chosen?.label ?? "Moteur"} indisponible`,
  }[modelState];

  function isExpanded(project: Project) {
    return (
      expanded[project.id] ?? (project.id === activeProjectId || project.id === activeConversation?.project_id || Boolean(needle))
    );
  }

  return (
    <>
      {mobileOpen && <div className="ax-scrim" onClick={onCloseMobile} aria-hidden="true" />}
      <aside
        className={`ax-sidebar glass ${collapsed ? "is-collapsed" : ""} ${mobileOpen ? "is-mobile-open" : ""}`}
        aria-label="Projets et conversations"
      >
        <div className="ax-sidebar__head">
          <Link to="/" className="ax-brand" onClick={onCloseMobile} aria-label="APEX — accueil">
            <span className="ax-brand__mark" aria-hidden="true" />
            {!collapsed && <span className="ax-brand__name">APEX</span>}
          </Link>
          <button
            type="button"
            className="ax-icon-btn ax-sidebar__collapse"
            onClick={onToggleCollapsed}
            aria-label={collapsed ? "Déplier la barre latérale" : "Replier la barre latérale"}
            title={collapsed ? "Déplier" : "Replier"}
          >
            <IconSidebar />
          </button>
        </div>

        <Link to="/" className="ax-new" onClick={onCloseMobile} title="Nouvelle conversation">
          <IconPlus />
          {!collapsed && <span>Nouvelle conversation</span>}
        </Link>

        {!collapsed && (
          <div className="ax-sidebar__scroll">
            <input
              className="ax-search"
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filtrer projets et conversations"
              aria-label="Filtrer projets et conversations"
            />

            <section className="ax-nav-section" aria-labelledby="ax-projects-label">
              <div className="ax-nav-section__head">
                <h2 id="ax-projects-label">Projets</h2>
                <button type="button" className="ax-icon-btn ax-icon-btn--sm" onClick={() => setCreating(true)} aria-label="Créer un projet" title="Créer un projet">
                  <IconPlus size={15} />
                </button>
              </div>
              {projects.isLoading ? (
                <div className="ax-skeleton-list" aria-label="Chargement des projets">
                  <span />
                  <span />
                </div>
              ) : projects.isError ? (
                <div className="ax-nav-error" role="alert">
                  <span>Projets indisponibles : {errorMessage(projects.error)}</span>
                  <button type="button" onClick={() => projects.refetch()}>
                    Réessayer
                  </button>
                </div>
              ) : visibleProjects.length === 0 ? (
                <p className="ax-nav-empty">
                  {needle ? "Aucun projet ne correspond." : "Aucun projet. Un projet regroupe des conversations, des consignes et des fichiers."}
                </p>
              ) : (
                <ul className="ax-nav-list">
                  {visibleProjects.map((project) => (
                    <ProjectGroup
                      key={project.id}
                      project={project}
                      conversations={byProject(project.id)}
                      expanded={isExpanded(project)}
                      onToggle={() => setExpanded((prev) => ({ ...prev, [project.id]: !isExpanded(project) }))}
                      onNavigate={onCloseMobile}
                    />
                  ))}
                </ul>
              )}
            </section>

            <section className="ax-nav-section" aria-labelledby="ax-conversations-label">
              <div className="ax-nav-section__head">
                <h2 id="ax-conversations-label">Conversations</h2>
              </div>
              {conversations.isLoading ? (
                <div className="ax-skeleton-list" aria-label="Chargement des conversations">
                  <span />
                  <span />
                  <span />
                </div>
              ) : conversations.isError ? (
                <div className="ax-nav-error" role="alert">
                  <span>Conversations indisponibles : {errorMessage(conversations.error)}</span>
                  <button type="button" onClick={() => conversations.refetch()}>
                    Réessayer
                  </button>
                </div>
              ) : standalone.length === 0 ? (
                <p className="ax-nav-empty">{needle ? "Aucune conversation ne correspond." : "Aucune conversation hors projet."}</p>
              ) : (
                <ul className="ax-nav-list">
                  {standalone.map((c) => (
                    <li key={c.id}>
                      <ConversationLink conversation={c} onNavigate={onCloseMobile} />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}

        <div className="ax-sidebar__foot">
          <NavLink to="/settings" className={({ isActive }) => `ax-nav__item ${isActive ? "is-active" : ""}`} onClick={onCloseMobile} title="Paramètres">
            <IconSettings size={17} />
            {!collapsed && <span className="ax-nav__text">Paramètres</span>}
          </NavLink>
          <Link to="/control" className="ax-nav__item" onClick={onCloseMobile} title="Centre de contrôle (état du système, tâches, mémoire)">
            <IconGauge size={17} />
            {!collapsed && <span className="ax-nav__text">Centre de contrôle</span>}
          </Link>
          <Link
            to="/settings#modeles"
            className={`ax-model-state ax-model-state--${modelState}`}
            onClick={onCloseMobile}
            title={modelState === "missing" ? `${modelLabel} : ${chosen?.detail ?? ""}` : chosen?.description ?? modelLabel}
          >
            <span className="ax-model-state__dot" aria-hidden="true" />
            {!collapsed && <span>{modelLabel}</span>}
          </Link>
        </div>
      </aside>

      <Modal open={creating} onClose={() => setCreating(false)} title="Nouveau projet">
        <ProjectForm
          submitLabel="Créer le projet"
          onCancel={() => setCreating(false)}
          onSubmit={async (draft) => {
            const project = await assistantApi.createProject(draft);
            await queryClient.invalidateQueries({ queryKey: keys.projects });
            setCreating(false);
            onCloseMobile();
            navigate(`/p/${project.id}`);
          }}
        />
      </Modal>
    </>
  );
}
