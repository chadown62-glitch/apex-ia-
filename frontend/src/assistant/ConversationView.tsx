/**
 * Conversation — historique persistant (API), tour en cours suivi en direct (interrogation 1 s), arrêt,
 * nouvelle tentative, renommage, changement de mode, suppression confirmée, et la vue propre au mode.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { Button } from "../components/ui/Button";
import { Modal } from "../components/ui/Modal";
import { assistantApi, errorMessage, type Message, type ModeId } from "./api";
import { Composer } from "./Composer";
import { isActive, keys, useModes, useProjects, useTurns } from "./hooks";
import { IconEdit, IconFolder, IconTrash } from "./Icons";
import { MessageItem, TurnProgress } from "./MessageItem";
import { EngineNotice, EnginePicker } from "./EnginePicker";
import { CodePanel, DesignPanel, FinancePanel } from "./ModePanels";

interface LocationState {
  draft?: string;
  error?: string;
}

export function ConversationView() {
  const { conversationId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const handoff = (location.state as LocationState | null) ?? {};

  const conversation = useQuery({
    queryKey: keys.conversation(conversationId),
    queryFn: () => assistantApi.conversation(conversationId),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
  });
  const messages = useQuery({ queryKey: keys.messages(conversationId), queryFn: () => assistantApi.messages(conversationId) });
  const turns = useTurns(conversationId);
  const modes = useModes();
  const projects = useProjects();

  const [sendError, setSendError] = useState<string | null>(handoff.error ?? null);
  const [cancelling, setCancelling] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [headerError, setHeaderError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const activeTurn = (turns.data ?? []).find(isActive) ?? null;
  const activeId = activeTurn?.id ?? null;
  const previousActive = useRef<string | null>(null);

  // Fin d'un tour : la réponse est enregistrée -> messages, liste (activité) et espace de travail rechargés.
  useEffect(() => {
    if (previousActive.current && !activeId) {
      void queryClient.invalidateQueries({ queryKey: keys.messages(conversationId) });
      void queryClient.invalidateQueries({ queryKey: keys.conversations });
      void queryClient.invalidateQueries({ queryKey: keys.workspace(conversationId) });
      setCancelling(false);
    }
    previousActive.current = activeId;
  }, [activeId, conversationId, queryClient]);

  useEffect(() => {
    setSendError(handoff.error ?? null);
    setEditingTitle(false);
    setPanelOpen(false);
  }, [conversationId, handoff.error]);

  // Défilement vers le bas à chaque nouveau contenu, seulement si l'on était déjà en bas.
  const messageCount = messages.data?.length ?? 0;
  const stepCount = activeTurn?.steps.length ?? 0;
  useEffect(() => {
    const list = listRef.current;
    const nearBottom = !list || list.scrollHeight - list.scrollTop - list.clientHeight < 240;
    if (nearBottom) endRef.current?.scrollIntoView({ block: "end" });
  }, [messageCount, stepCount, activeId]);

  const data = conversation.data;
  const mode: ModeId = data?.mode ?? "normal";
  const project = data?.project_id ? projects.data?.find((p) => p.id === data.project_id) : undefined;
  const lastAssistant = useMemo(() => [...(messages.data ?? [])].reverse().find((m) => m.role === "assistant"), [messages.data]);

  async function send(content: string) {
    setSendError(null);
    try {
      await assistantApi.submitTurn(conversationId, content);
    } catch (e) {
      setSendError(errorMessage(e));
      throw e;
    } finally {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.messages(conversationId) }),
        queryClient.invalidateQueries({ queryKey: keys.turns(conversationId) }),
        queryClient.invalidateQueries({ queryKey: keys.conversations }),
      ]);
    }
    if (handoff.draft) navigate(location.pathname, { replace: true, state: null });
  }

  async function cancel() {
    if (!activeTurn) return;
    setCancelling(true);
    try {
      const { cancel_requested } = await assistantApi.cancelTurn(activeTurn.id);
      if (!cancel_requested) setCancelling(false);
    } catch (e) {
      setCancelling(false);
      setSendError(errorMessage(e));
    }
    void queryClient.invalidateQueries({ queryKey: keys.turns(conversationId) });
  }

  function retryContent(message: Message): string | null {
    const all = messages.data ?? [];
    const index = all.findIndex((m) => m.id === message.id);
    for (let i = index - 1; i >= 0; i -= 1) if (all[i].role === "user") return all[i].content;
    return null;
  }

  async function saveTitle() {
    const title = titleDraft.trim();
    if (!title || title === data?.title) {
      setEditingTitle(false);
      return;
    }
    setHeaderError(null);
    try {
      const updated = await assistantApi.updateConversation(conversationId, { title });
      queryClient.setQueryData(keys.conversation(conversationId), updated);
      await queryClient.invalidateQueries({ queryKey: keys.conversations });
      setEditingTitle(false);
    } catch (e) {
      setHeaderError(errorMessage(e));
    }
  }

  async function changeMode(next: ModeId) {
    setHeaderError(null);
    try {
      const updated = await assistantApi.updateConversation(conversationId, { mode: next });
      queryClient.setQueryData(keys.conversation(conversationId), updated);
      await queryClient.invalidateQueries({ queryKey: keys.conversations });
    } catch (e) {
      setHeaderError(errorMessage(e));
    }
  }

  async function remove() {
    setDeleteError(null);
    try {
      await assistantApi.deleteConversation(conversationId);
      await queryClient.invalidateQueries({ queryKey: keys.conversations });
      setConfirmDelete(false);
      navigate(data?.project_id ? `/p/${data.project_id}` : "/", { replace: true });
    } catch (e) {
      setDeleteError(errorMessage(e));
    }
  }

  if (conversation.isError) {
    const notFound = conversation.error instanceof ApiError && conversation.error.status === 404;
    return (
      <div className="ax-page ax-empty">
        <h1>{notFound ? "Conversation introuvable" : "Conversation indisponible"}</h1>
        <p className="ax-muted">{notFound ? "Elle a peut-être été supprimée." : errorMessage(conversation.error)}</p>
        <div className="ax-empty__actions">
          {!notFound && <Button onClick={() => conversation.refetch()}>Réessayer</Button>}
          <Button variant="primary" onClick={() => navigate("/")}>
            Nouvelle conversation
          </Button>
        </div>
      </div>
    );
  }

  const liveSteps = activeTurn?.steps ?? [];
  const hasPanel = mode !== "normal";
  const last = messages.data?.[messages.data.length - 1];

  return (
    <div className={`ax-conversation ${hasPanel ? "has-panel" : ""} ${panelOpen ? "is-panel-open" : ""}`}>
      <div className="ax-conversation__main">
        <header className="ax-conv-head glass">
          <div className="ax-conv-head__title">
            {project && (
              <Link to={`/p/${project.id}`} className="ax-crumb">
                <IconFolder size={14} /> {project.name}
              </Link>
            )}
            {editingTitle ? (
              <input
                className="ax-title-input"
                value={titleDraft}
                maxLength={200}
                autoFocus
                aria-label="Titre de la conversation"
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => void saveTitle()}
                onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
                  if (e.key === "Enter") void saveTitle();
                  if (e.key === "Escape") setEditingTitle(false);
                }}
              />
            ) : (
              <h1>
                {data?.title ?? "…"}
                {data && (
                  <button
                    type="button"
                    className="ax-icon-btn ax-icon-btn--sm"
                    aria-label="Renommer la conversation"
                    title="Renommer"
                    onClick={() => {
                      setTitleDraft(data.title);
                      setEditingTitle(true);
                    }}
                  >
                    <IconEdit size={14} />
                  </button>
                )}
              </h1>
            )}
          </div>
          <div className="ax-conv-head__actions">
            <label className="ax-mode-select">
              <span className="ax-sr">Mode</span>
              <select value={mode} onChange={(e) => void changeMode(e.target.value as ModeId)} disabled={!data || !modes.data}>
                {(modes.data ?? []).map((m) => (
                  <option key={m.id} value={m.id}>
                    Mode {m.label}
                  </option>
                ))}
              </select>
            </label>
            {hasPanel && (
              <button type="button" className="ax-chip-btn ax-panel-toggle" onClick={() => setPanelOpen((v) => !v)} aria-expanded={panelOpen}>
                {{ code: "Fichiers", design: "Nuancier", finance: "Calculs", normal: "" }[mode]}
              </button>
            )}
            <button type="button" className="ax-icon-btn" aria-label="Supprimer la conversation" title="Supprimer" onClick={() => setConfirmDelete(true)}>
              <IconTrash />
            </button>
          </div>
          {headerError && (
            <p className="ax-inline-error ax-conv-head__error" role="alert">
              {headerError}
            </p>
          )}
        </header>

        <div className="ax-thread" ref={listRef}>
          <div className="ax-thread__inner">
            {messages.isLoading ? (
              <div className="ax-skeleton-thread" aria-label="Chargement des messages">
                <span />
                <span />
              </div>
            ) : messages.isError ? (
              <div className="ax-banner ax-banner--error" role="alert">
                <span>Messages indisponibles : {errorMessage(messages.error)}</span>
                <button type="button" className="ax-chip-btn" onClick={() => messages.refetch()}>
                  Réessayer
                </button>
              </div>
            ) : messages.data && messages.data.length === 0 && !activeTurn ? (
              <div className="ax-thread__empty">
                <p>Conversation vide. Écrivez le premier message ci-dessous.</p>
              </div>
            ) : (
              (messages.data ?? []).map((message) => {
                const content = message === last && message.role === "assistant" && message.status !== "real" ? retryContent(message) : null;
                return (
                  <MessageItem
                    key={message.id}
                    message={message}
                    onRetry={content && !activeTurn ? () => void send(content).catch(() => undefined) : undefined}
                  />
                );
              })
            )}
            {activeTurn && <TurnProgress turn={activeTurn} onCancel={() => void cancel()} cancelling={cancelling} />}
            <div ref={endRef} />
          </div>
        </div>

        <div className="ax-conversation__composer">
          {!activeTurn && <EngineNotice />}
          <Composer
            key={conversationId}
            onSubmit={send}
            disabled={Boolean(activeTurn) || !data}
            disabledReason={activeTurn ? "Une réponse est en cours : attendez-la ou arrêtez-la." : undefined}
            placeholder="Écrire un message…"
            error={sendError}
            initialDraft={handoff.draft}
            autoFocus
            toolbar={<EnginePicker />}
          />
        </div>
      </div>

      {hasPanel && (
        <aside className="ax-conversation__panel glass" aria-label="Vue du mode">
          {mode === "code" && <CodePanel conversationId={conversationId} notes={lastAssistant?.meta?.tools_notes ?? []} />}
          {mode === "design" && <DesignPanel messages={messages.data ?? []} liveSteps={liveSteps} />}
          {mode === "finance" && <FinancePanel messages={messages.data ?? []} liveSteps={liveSteps} />}
        </aside>
      )}

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Supprimer la conversation">
        <p>
          « {data?.title} » et tous ses messages seront supprimés définitivement
          {data?.project_id ? "." : ", ainsi que son espace de travail."}
        </p>
        {deleteError && (
          <p className="ax-inline-error" role="alert">
            {deleteError}
          </p>
        )}
        <div className="ax-form__actions">
          <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
            Annuler
          </Button>
          <Button variant="danger" onClick={() => void remove()}>
            Supprimer
          </Button>
        </div>
      </Modal>
    </div>
  );
}
