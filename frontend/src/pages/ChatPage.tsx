import { useEffect, useMemo, useRef, useState } from "react";

import {
  useCatalogModels,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
  useMessages,
  useRenameConversation,
  useSendMessage,
} from "../api/hooks";
import type { Message } from "../api/types";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import "./pages.css";
import "./chat.css";

/** Étapes SYNTHÉTIQUES affichées pendant qu'une génération réelle est en
 * cours (`useSendMessage().isPending`) — jamais une chaîne de pensée privée
 * du modèle, seulement un déroulé d'ÉTAPES SYSTÈME connu par construction
 * (c'est le pipeline réel que `ConversationService.send_message` traverse :
 * sélection via `select_model`, appel `generate_with_fallback`). La
 * temporisation ci-dessous est un simple indicateur visuel de progression
 * pendant l'attente d'UNE requête HTTP réelle — elle ne prétend mesurer
 * aucune étape individuellement. */
const GENERATION_STEPS = [
  "Analyse de la demande",
  "Sélection du modèle",
  "Génération",
  "Évaluation",
  "Réponse",
];

function useGenerationStepper(active: boolean): number {
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (!active) {
      setStep(0);
      return;
    }
    const id = window.setInterval(() => {
      setStep((s) => Math.min(s + 1, GENERATION_STEPS.length - 2));
    }, 700);
    return () => window.clearInterval(id);
  }, [active]);
  return step;
}

function MessageBubble({ message }: { message: Message }) {
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
  const [sendError, setSendError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const messages = useMessages(selectedId ?? undefined);
  const sendMessage = useSendMessage();
  const stepIndex = useGenerationStepper(sendMessage.isPending);

  const conversationList = conversations.data?.status === "real" ? conversations.data.data ?? [] : [];
  const messageList = messages.data?.status === "real" ? messages.data.data ?? [] : [];

  const roles = useMemo(() => {
    const models = catalog.data?.status === "real" ? catalog.data.data ?? [] : [];
    return Array.from(new Set(models.map((m) => m.role))).sort();
  }, [catalog.data]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messageList.length]);

  useEffect(() => {
    if (!selectedId && conversationList.length > 0) {
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
      input: { content, model_role: modelRole || null },
    });
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

      <div className="chat-layout">
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
              </div>

              <div className="chat-messages">
                {messages.isLoading ? (
                  <LoadingState />
                ) : messageList.length === 0 ? (
                  <div className="chat-empty">
                    <p>Aucun message — écrivez le premier ci-dessous.</p>
                  </div>
                ) : (
                  messageList.map((m) => <MessageBubble key={m.id} message={m} />)
                )}

                {sendMessage.isPending && (
                  <div className="chat-generating">
                    <span className="chat-generating__dot" aria-hidden="true" />
                    APEX · {GENERATION_STEPS[stepIndex]}
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
