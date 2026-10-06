import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { getRuntimeStatus, listRecalls } from "../api/endpoints";

import {
  useCatalogModels,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
  useMessages,
  useRenameConversation,
  useSendMessage,
} from "../api/hooks";
import type { Message, RecalledMemory } from "../api/types";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import "./pages.css";
import "./chat.css";

/** Temps RÉEL écoulé depuis l'envoi — la seule progression que l'interface connaît d'une requête en cours
 * (avant le 06/10/2026, une suite d'étapes minutées « Analyse, Sélection, Génération, Évaluation » défilait
 * sans aucun lien avec le traitement réel). */
function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!active) {
      setSeconds(0);
      return;
    }
    const started = Date.now();
    const id = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => window.clearInterval(id);
  }, [active]);
  return seconds;
}

function MessageBubble({ message, memory }: { message: Message; memory?: RecalledMemory[] }) {
  const isUser = message.role === "user";
  const isError = message.status === "error";
  return (
    <div className={`chat-bubble-row ${isUser ? "chat-bubble-row--user" : ""}`}>
      <div className={`chat-bubble ${isUser ? "chat-bubble--user" : "chat-bubble--assistant"} ${isError ? "chat-bubble--error" : ""}`}>
        {isError ? (
          <div className="chat-bubble__error">
            <strong>Génération impossible.</strong>
            <p>{message.error}</p>
          </div>
        ) : (
          <p className="chat-bubble__text">{message.content}</p>
        )}
        {!isUser && !isError && (
          <div className="chat-bubble__meta">
            {message.model_id ?? "—"} {message.provider_id ? `· ${message.provider_id}` : ""}
          </div>
        )}
        {!isUser && memory && memory.length > 0 && (
          <div className="chat-bubble__meta" aria-label="Souvenirs utilisés">
            Souvenirs utilisés : {memory.map((m) => `${m.category ?? "?"} · ${m.source}`).join(" ; ")}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Chat — workspace conversationnel réel : UI -> API -> `ConversationService.
 * send_message` -> `apex.routing.select_model` -> `apex.routing.
 * generate_with_fallback` -> `get_default_provider()` -> `OllamaProvider`
 * (ou `OpenAICompatibleProvider`) -> backend réel -> réponse réelle,
 * persistée puis réaffichée ici. Aucune réponse simulée : un échec de
 * génération s'affiche comme tel (`MessageBubble`, `status="error"`),
 * jamais un texte inventé à sa place.
 */
export function ChatPage() {
  const conversations = useConversations();
  const createConversation = useCreateConversation();
  const renameConversation = useRenameConversation();
  const deleteConversation = useDeleteConversation();
  const catalog = useCatalogModels();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [modelRole, setModelRole] = useState<string>("");
  const recalls = useQuery({
    queryKey: ["recalls", selectedId],
    queryFn: () => listRecalls(selectedId as string),
    enabled: Boolean(selectedId),
  });
  const recallMap = recalls.data?.status === "real" ? recalls.data.data ?? {} : {};
  // Statut RÉEL du runtime pour le rôle choisi (ou tous) : quel modèle peut répondre, sinon pourquoi.
  const runtime = useQuery({
    queryKey: ["runtime-status", modelRole],
    queryFn: () => getRuntimeStatus(modelRole || undefined),
    staleTime: 30_000,
  });
  const runtimeRows = runtime.data?.status === "real" ? runtime.data.data ?? [] : [];
  const executable = runtimeRows
    .filter((r) => r.available)
    .filter((r, i, all) => all.findIndex((o) => o.model_id === r.model_id && o.runtime === r.runtime) === i);
  const [sendError, setSendError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const messages = useMessages(selectedId ?? undefined);
  const sendMessage = useSendMessage();
  const elapsed = useElapsedSeconds(sendMessage.isPending);
  const [useMemory, setUseMemory] = useState(true);
  const queryClient = useQueryClient();

  const conversationList = conversations.data?.status === "real" ? conversations.data.data ?? [] : [];
  const messageList = messages.data?.status === "real" ? messages.data.data ?? [] : [];

  const roles = useMemo(() => {
    const models = catalog.data?.status === "real" ? catalog.data.data ?? [] : [];
    return Array.from(new Set(models.map((m) => m.role))).sort();
  }, [catalog.data]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messageList.length]);

  const hasAutoSelected = useRef(false);
  useEffect(() => {
    if (!hasAutoSelected.current && !selectedId && conversationList.length > 0) {
      hasAutoSelected.current = true;
      setSelectedId(conversationList[0].id);
    }
  }, [selectedId, conversationList]);

  async function handleNewConversation() {
    const envelope = await createConversation.mutateAsync({ title: "Nouvelle conversation" });
    if (envelope.status === "real" && envelope.data) {
      setSelectedId(envelope.data.id);
    }
  }

  async function handleSend() {
    if (!selectedId || !draft.trim()) return;
    setSendError(null);
    const content = draft;
    setDraft("");
    const envelope = await sendMessage.mutateAsync({
      conversationId: selectedId,
      input: { content, model_role: modelRole || null, use_memory: useMemory },
    });
    queryClient.invalidateQueries({ queryKey: ["recalls", selectedId] });
    if (envelope.status !== "real") {
      setSendError(envelope.detail ?? "Envoi refusé par le backend.");
    }
  }

  return (
    <div className="page chat-page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Chat</h1>
          <p className="page__subtitle">
            Conversation réelle — `ConversationService.send_message` → Model Router → provider réel.
          </p>
        </div>
      </div>

      <div className="chat-layout" data-has-selection={selectedId ? "true" : "false"}>
        <aside className="chat-sidebar">
          <button className="chat-sidebar__new" onClick={handleNewConversation} disabled={createConversation.isPending}>
            + Nouvelle conversation
          </button>
          {conversations.isLoading ? (
            <LoadingState />
          ) : conversations.data?.status !== "real" ? (
            <ErrorState detail={conversations.data?.detail} />
          ) : conversationList.length === 0 ? (
            <p className="chat-sidebar__empty">Aucune conversation pour l'instant.</p>
          ) : (
            <ul className="chat-sidebar__list">
              {conversationList.map((c) => (
                <li key={c.id}>
                  <button
                    className={`chat-sidebar__item ${c.id === selectedId ? "chat-sidebar__item--active" : ""}`}
                    onClick={() => setSelectedId(c.id)}
                  >
                    <span className="chat-sidebar__item-title">{c.title}</span>
                    <span className="chat-sidebar__item-date">{new Date(c.updated_at).toLocaleDateString("fr-FR")}</span>
                  </button>
                  <div className="chat-sidebar__item-actions">
                    <button
                      title="Renommer"
                      onClick={() => {
                        const title = window.prompt("Nouveau titre", c.title);
                        if (title && title.trim()) renameConversation.mutate({ conversationId: c.id, title });
                      }}
                    >
                      ✎
                    </button>
                    <button
                      title="Supprimer"
                      onClick={() => {
                        if (window.confirm(`Supprimer « ${c.title} » ?`)) {
                          deleteConversation.mutate(c.id);
                          if (selectedId === c.id) setSelectedId(null);
                        }
                      }}
                    >
                      ✕
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="chat-main">
          {!selectedId ? (
            <div className="chat-empty">
              <p>Sélectionnez une conversation, ou créez-en une nouvelle.</p>
            </div>
          ) : (
            <>
              <div className="chat-toolbar">
                <button
                  type="button"
                  className="chat-mobile-back"
                  onClick={() => setSelectedId(null)}
                  aria-label="Retour aux conversations"
                >
                  ← Conversations
                </button>
                <label className="chat-toolbar__model">
                  <span>Modèle</span>
                  <select value={modelRole} onChange={(e) => setModelRole(e.target.value)}>
                    <option value="">— sélection automatique —</option>
                    {roles.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="chat-toolbar__model">
                  <input type="checkbox" checked={useMemory} onChange={(e) => setUseMemory(e.target.checked)} />
                  <span>Mémoire</span>
                </label>
                <span className="chat-toolbar__model" aria-label="Statut du runtime">
                  {runtime.isLoading
                    ? "Vérification des modèles…"
                    : executable.length > 0
                      ? `Exécutable : ${executable.map((r) => `${r.model_id} (${r.runtime})`).join(", ")}`
                      : `Aucun modèle exécutable — ${runtimeRows.map((r) => r.detail).filter(Boolean)[0] ?? "voir la page Modèles"}`}
                </span>
              </div>

              <div className="chat-messages">
                {messages.isLoading ? (
                  <LoadingState />
                ) : messageList.length === 0 ? (
                  <div className="chat-empty">
                    <p>Aucun message — écrivez le premier ci-dessous.</p>
                  </div>
                ) : (
                  messageList.map((m) => <MessageBubble key={m.id} message={m} memory={recallMap[m.id]} />)
                )}

                {sendMessage.isPending && (
                  <div className="chat-generating">
                    <span className="chat-generating__dot" aria-hidden="true" />
                    Génération en cours — {elapsed} s
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              <div className="chat-composer">
                <textarea
                  rows={2}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Écrire un message…"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                    }
                  }}
                />
                <button
                  className="action-button action-button--live"
                  disabled={!draft.trim() || sendMessage.isPending}
                  onClick={handleSend}
                >
                  {sendMessage.isPending ? "Génération…" : "Envoyer"}
                </button>
              </div>
              {sendError && <ErrorState detail={sendError} />}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
