import { useEffect, useState } from "react";

import type { Message, ToolStep, Turn } from "./api";
import { Markdown } from "./Markdown";
import { TOOL_LABELS } from "./ModePicker";
import { IconRefresh, IconStop } from "./Icons";

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds % 60)} s`;
}

/** Résumé lisible d'une erreur du backend : sa première partie (avant « — ») ou ses 220 premiers caractères ;
 * le texte complet reste accessible dans « Détails techniques », jamais tronqué silencieusement. */
export function summarizeError(error: string): string {
  const notInstalled = /poids absents|aucun modèle/i.test(error);
  const head = error.split(" — ")[0].trim();
  const summary = head.length > 0 && head.length < error.length ? head : error;
  const short = summary.length > 220 ? `${summary.slice(0, 217).trimEnd()}…` : summary;
  return notInstalled && !/aucun modèle/i.test(short) ? `${short} (modèle local non installé)` : short;
}

export function ToolSteps({ steps, open }: { steps: ToolStep[]; open?: boolean }) {
  if (steps.length === 0) return null;
  return (
    <details className="ax-steps" open={open}>
      <summary>
        {steps.length} étape{steps.length > 1 ? "s" : ""} d'outil{steps.length > 1 ? "s" : ""}
        {steps.some((s) => !s.ok) && <span className="ax-steps__warn"> · {steps.filter((s) => !s.ok).length} en échec</span>}
      </summary>
      <ol>
        {steps.map((step) => (
          <li key={step.step} className={step.ok ? "is-ok" : "is-failed"}>
            <div className="ax-step__head">
              <span className="ax-step__status" aria-label={step.ok ? "réussie" : "échouée"}>
                {step.ok ? "✓" : "✕"}
              </span>
              <code>{step.tool}</code>
              <span className="ax-step__label">{TOOL_LABELS[step.tool] ?? ""}</span>
              <span className="ax-step__time">{formatDuration(step.duration_s)}</span>
            </div>
            <details className="ax-step__body">
              <summary>Arguments et résultat</summary>
              <pre>{JSON.stringify(step.arguments, null, 2)}</pre>
              <pre>
                {step.observation.error
                  ? `${step.observation.error.code} : ${step.observation.error.message}`
                  : JSON.stringify(step.observation.output ?? step.observation.summary ?? null, null, 2)}
              </pre>
            </details>
          </li>
        ))}
      </ol>
    </details>
  );
}

function MetaLine({ message }: { message: Message }) {
  const meta = message.meta;
  const parts: string[] = [];
  if (message.model_id) parts.push(message.model_id);
  if (meta?.runtime?.dtype) parts.push(meta.runtime.dtype);
  if (typeof meta?.elapsed_s === "number") parts.push(formatDuration(meta.elapsed_s));
  if (typeof meta?.tokens_completion === "number") parts.push(`${meta.tokens_completion} tokens`);
  if (typeof meta?.runtime?.tokens_per_s === "number") parts.push(`${meta.runtime.tokens_per_s.toFixed(1)} tokens/s`);
  if (meta?.memory && meta.memory.length > 0) parts.push(`${meta.memory.length} souvenir${meta.memory.length > 1 ? "s" : ""} rappelé${meta.memory.length > 1 ? "s" : ""}`);
  if (meta?.attachments && meta.attachments.length > 0) parts.push(`pièces jointes : ${meta.attachments.join(", ")}`);
  const delegation = meta?.delegation;
  if (delegation) {
    const confidence = typeof delegation.confidence === "number" ? delegation.confidence.toFixed(2) : "?";
    const why = delegation.reason === "cannot_know" ? "il dit ne pas pouvoir le savoir" : `confiance ${confidence}`;
    parts.push(delegation.status === "delegated"
      ? `confié par le Cerveau APEX (${why})`
      : `Cerveau APEX peu sûr (${why}), aucun autre moteur disponible`);
  }
  if (parts.length === 0) return null;
  return <div className="ax-msg__meta">{parts.join(" · ")}</div>;
}

export function MessageItem({ message, onRetry }: { message: Message; onRetry?: () => void }) {
  if (message.role === "user") {
    return (
      <div className="ax-msg ax-msg--user">
        <div className="ax-msg__bubble">{message.content}</div>
      </div>
    );
  }
  const steps = message.meta?.steps ?? [];
  if (message.status === "error" || message.status === "cancelled") {
    const cancelled = message.status === "cancelled";
    return (
      <div className={`ax-msg ax-msg--assistant ax-msg--${message.status}`}>
        <div className="ax-msg__card">
          <p className="ax-msg__status">
            <strong>{cancelled ? "Réponse annulée" : "La réponse a échoué"}</strong>
            {message.error && <span> — {summarizeError(message.error)}</span>}
          </p>
          {message.error && summarizeError(message.error) !== message.error && (
            <details className="ax-msg__details">
              <summary>Détails techniques</summary>
              <pre>{message.error}</pre>
            </details>
          )}
          <ToolSteps steps={steps} />
          {message.meta?.tools_notes && message.meta.tools_notes.length > 0 && (
            <ul className="ax-msg__notes">
              {message.meta.tools_notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          {onRetry && (
            <button type="button" className="ax-chip-btn" onClick={onRetry}>
              <IconRefresh size={14} /> Réessayer
            </button>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="ax-msg ax-msg--assistant">
      <div className="ax-msg__card">
        <ToolSteps steps={steps} />
        <Markdown text={message.content} />
        <MetaLine message={message} />
      </div>
    </div>
  );
}

/** Tour en file ou en cours : durée réelle écoulée, étapes d'outils déjà exécutées, arrêt possible. */
export function TurnProgress({ turn, onCancel, cancelling }: { turn: Turn; onCancel: () => void; cancelling: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const since = turn.started_at ?? turn.created_at;
  const elapsed = Math.max(0, (now - new Date(since).getTime()) / 1000);
  const lastStep = turn.steps[turn.steps.length - 1];
  return (
    <div className="ax-msg ax-msg--assistant ax-msg--pending" aria-live="polite">
      <div className="ax-msg__card">
        <div className="ax-progress">
          <span className="ax-progress__orb" aria-hidden="true" />
          <span className="ax-progress__label">
            {turn.status === "queued"
              ? "En file d'attente…"
              : lastStep
                ? `Outil ${lastStep.tool} terminé — le modèle poursuit…`
                : "Le modèle génère la réponse…"}
          </span>
          <span className="ax-progress__time">{formatDuration(elapsed)}</span>
          <button type="button" className="ax-chip-btn" onClick={onCancel} disabled={cancelling}>
            <IconStop size={14} /> {cancelling ? "Arrêt demandé…" : "Arrêter"}
          </button>
        </div>
        <ToolSteps steps={turn.steps} open />
        {turn.status === "running" && elapsed > 45 && turn.steps.length === 0 && (
          <p className="ax-progress__hint">
            Sur processeur, un modèle local produit quelques mots par seconde : une réponse longue peut prendre plusieurs minutes.
          </p>
        )}
      </div>
    </div>
  );
}
