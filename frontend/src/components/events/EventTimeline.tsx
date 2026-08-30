import type { ApexEvent, AuditLogEntry } from "../../api/types";
import { EmptyState } from "../common/EmptyState";
import "./events.css";

function formatTs(ts: string): string {
  try {
    return new Date(ts).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "medium" });
  } catch {
    return ts;
  }
}

export function EventTimeline({ events }: { events: ApexEvent[] }) {
  if (events.length === 0) return <EmptyState title="Aucun événement" />;
  return (
    <ol className="timeline">
      {events.map((event) => (
        <li key={event.id} className={`timeline__item timeline__item--${event.level.toLowerCase()}`}>
          <div className="timeline__marker" aria-hidden="true" />
          <div className="timeline__content">
            <div className="timeline__row">
              <span className="timeline__msg">{event.msg}</span>
              <span className="timeline__ts">{formatTs(event.ts)}</span>
            </div>
            <div className="timeline__meta mono">{event.logger}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Timeline de gouvernance — audit_log, INSERT-only (ADR-0010). Distincte
 * d'EventTimeline (routine) : chaque entrée porte un acteur et une action,
 * pas seulement un message. */
export function AuditTimeline({ entries }: { entries: AuditLogEntry[] }) {
  if (entries.length === 0) return <EmptyState title="Aucune entrée d'audit" />;
  return (
    <ol className="timeline">
      {entries.map((entry) => (
        <li key={entry.id} className="timeline__item timeline__item--info">
          <div className="timeline__marker" aria-hidden="true" />
          <div className="timeline__content">
            <div className="timeline__row">
              <span className="timeline__msg">
                <strong>{entry.actor}</strong> · {entry.action}
              </span>
              <span className="timeline__ts">{formatTs(entry.ts)}</span>
            </div>
            {entry.target && <div className="timeline__meta mono">{entry.target}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}
