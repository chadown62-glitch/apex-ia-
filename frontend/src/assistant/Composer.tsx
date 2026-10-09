import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { IconSend } from "./Icons";

const MAX_CHARS = 20_000;

/**
 * Zone de saisie — Entrée envoie, Maj+Entrée va à la ligne. Le brouillon n'est vidé qu'après acceptation
 * par le backend : un envoi refusé garde le texte et affiche la raison.
 */
export function Composer({
  onSubmit,
  disabled,
  disabledReason,
  placeholder,
  error,
  initialDraft = "",
  autoFocus,
  submitLabel = "Envoyer",
}: {
  onSubmit: (content: string) => Promise<void>;
  disabled?: boolean;
  disabledReason?: string;
  placeholder: string;
  error?: string | null;
  initialDraft?: string;
  autoFocus?: boolean;
  submitLabel?: string;
}) {
  const [draft, setDraft] = useState(initialDraft);
  const [pending, setPending] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, Math.round(window.innerHeight * 0.4))}px`;
  }, [draft]);

  useEffect(() => {
    if (initialDraft) setDraft(initialDraft);
  }, [initialDraft]);

  const content = draft.trim();
  const tooLong = draft.length > MAX_CHARS;
  const blocked = disabled || pending || !content || tooLong;

  async function send(event?: FormEvent) {
    event?.preventDefault();
    if (blocked) return;
    setPending(true);
    try {
      await onSubmit(content);
      setDraft("");
    } catch {
      /* l'appelant affiche l'erreur ; le brouillon est conservé. */
    } finally {
      setPending(false);
      textarea.current?.focus();
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  }

  return (
    <form className={`ax-composer glass ${disabled ? "is-disabled" : ""}`} onSubmit={send}>
      <textarea
        ref={textarea}
        className="ax-composer__input"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        rows={2}
        autoFocus={autoFocus}
        aria-label="Message"
        aria-describedby="ax-composer-hint"
      />
      <div className="ax-composer__bar">
        <span id="ax-composer-hint" className={`ax-composer__hint ${tooLong ? "is-error" : ""}`}>
          {disabled && disabledReason
            ? disabledReason
            : tooLong
              ? `${draft.length.toLocaleString("fr-FR")} / ${MAX_CHARS.toLocaleString("fr-FR")} caractères : message trop long`
              : "Entrée pour envoyer · Maj+Entrée pour aller à la ligne"}
        </span>
        <button type="submit" className="ax-send" disabled={blocked} aria-label={submitLabel} title={submitLabel}>
          {pending ? <span className="ax-spinner" aria-hidden="true" /> : <IconSend />}
        </button>
      </div>
      {error && (
        <p className="ax-inline-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
