import { useBrainEvents, useBrainState } from "../api/hooks";
import type {
  BrainChampion,
  BrainCurrent,
  BrainEvent,
  BrainExperiment,
  BrainLoop,
  BrainProtection,
  BrainWorld,
  NotAvailable,
} from "../api/types";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import "./pages.css";
import "./brain-live.css";

function isNA(value: unknown): value is NotAvailable {
  return typeof value === "object" && value !== null && (value as NotAvailable).status === "not_available";
}

function NotAvailableNote({ value }: { value: NotAvailable }) {
  return (
    <p className="brain-na">
      <span className="brain-tag brain-tag--na">NON DISPONIBLE</span> {value.reason}
    </p>
  );
}

function fmt(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

function LoopPanel({ loop, world }: { loop: BrainLoop; world: BrainWorld | NotAvailable }) {
  return (
    <Panel title="Boucle et monde">
      {loop.status === "not_available" ? (
        <NotAvailableNote value={{ status: "not_available", reason: loop.reason ?? "" }} />
      ) : (
        <p>
          <span className={`brain-tag brain-tag--${loop.state}`}>{loop.state === "running" ? "EN COURS" : "À L'ARRÊT"}</span>
          {loop.pid ? ` pid ${loop.pid}` : ""}
          {loop.resources && !isNA(loop.resources)
            ? ` — ${fmt(loop.resources.rss_mb, 0)} Mo, ${fmt(loop.resources.cpu_seconds, 0)} s CPU, charge ${loop.resources.loadavg.join(" ")}`
            : ""}
        </p>
      )}
      {loop.last_event && (
        <p className="brain-muted">
          Dernier événement de boucle : {loop.last_event.label} {loop.last_event.summary} ({loop.last_event.at})
        </p>
      )}
      {isNA(world) ? (
        <NotAvailableNote value={world} />
      ) : (
        <dl className="brain-dl">
          <dt>Monde</dt>
          <dd title={world.fingerprint}>{world.fingerprint.slice(0, 19)}…</dd>
          <dt>Capacités</dt>
          <dd>{world.capabilities.join(", ") || "texte seulement"}</dd>
          <dt>Architecture</dt>
          <dd>
            {Object.entries(world.architecture)
              .map(([k, v]) => `${k} ${v ?? "—"}`)
              .join(" · ")}
          </dd>
          <dt>Documents d'entraînement</dt>
          <dd>{world.train_documents}</dd>
        </dl>
      )}
    </Panel>
  );
}

function ChampionPanel({ champion }: { champion: BrainChampion | NotAvailable }) {
  if (isNA(champion)) {
    return (
      <Panel title="Champion en service">
        <NotAvailableNote value={champion} />
      </Panel>
    );
  }
  return (
    <Panel title={`Champion en service : ${champion.name}`}>
      <p>
        {champion.attested ? (
          <span className="brain-tag brain-tag--ok">ATTESTÉ par le journal</span>
        ) : (
          <span className="brain-tag brain-tag--bad">NON ATTESTÉ : {champion.attestation_problem}</span>
        )}{" "}
        version #{champion.index} ({champion.kind}) — {champion.promotions} promotion(s) sur {champion.versions} version(s)
      </p>
      <table className="brain-table">
        <thead>
          <tr>
            <th>suite</th>
            <th>valeur</th>
            <th>métrique (plus bas = mieux)</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(champion.reports).map(([suite, value]) => (
            <tr key={suite}>
              <td>{suite}</td>
              <td>{fmt(value)}</td>
              <td>{champion.metrics[suite]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function ProtectionsPanel({ protections }: { protections: BrainProtection[] | NotAvailable }) {
  return (
    <Panel title="Capacités protégées (ancres du registre)">
      {isNA(protections) ? (
        <NotAvailableNote value={protections} />
      ) : (
        <ul className="brain-list">
          {protections.map((p) => (
            <li key={p.group}>
              <strong>{p.group}</strong> — ancre #{p.anchor_index} ({p.anchor_name}) :{" "}
              {Object.entries(p.anchor_values)
                .map(([s, v]) => `${s} ${fmt(v)}`)
                .join(" · ")}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

const RUN_LABELS: Record<string, string> = {
  replay: "Rejeu du Judge (provenance du candidat)",
  replica: "Réplique (graine indépendante)",
  replica_replay: "Rejeu du Judge sur la réplique",
};

function CurrentPanel({ current, running }: { current: BrainCurrent | null; running: boolean }) {
  if (current === null) {
    return (
      <Panel title="Expérience en cours">
        <p className="brain-muted">Aucune expérience inachevée au journal.</p>
      </Panel>
    );
  }
  const progress = current.progress;
  const ratio = progress?.step && progress.of ? progress.step / progress.of : null;
  return (
    <Panel title={`${running ? "Expérience en cours" : "Expérience inachevée"} : ${current.experiment_id}`}>
      {!running && (
        <p>
          <span className="brain-tag brain-tag--bad">INTERROMPUE</span> aucun processus ne tient le verrou de la
          boucle : elle reprendra au prochain lancement, depuis le dernier événement du journal.
        </p>
      )}
      <p>
        Étape du journal : <strong>{current.journal_status}</strong> — phase télémétrie :{" "}
        <strong>{current.phase ?? "aucune télémétrie"}</strong>
      </p>
      {progress ? (
        <>
          <div className="brain-progress" role="progressbar" aria-valuenow={progress.step} aria-valuemax={progress.of}>
            <div className="brain-progress__bar" style={{ width: `${Math.round((ratio ?? 0) * 100)}%` }} />
          </div>
          <p>
            {progress.run ? `${RUN_LABELS[progress.run] ?? progress.run} — ` : ""}pas {progress.step} / {progress.of} — perte{" "}
            {fmt(progress.loss, 4)} — lr {progress.lr?.toExponential(2)}
          </p>
        </>
      ) : (
        <p className="brain-muted">Pas encore de point de progression publié.</p>
      )}
    </Panel>
  );
}

function ExperimentsPanel({ experiments }: { experiments: BrainExperiment[] }) {
  return (
    <Panel title={`Expériences (${experiments.length})`}>
      {experiments.length === 0 ? (
        <p className="brain-muted">Aucune expérience au journal.</p>
      ) : (
        <table className="brain-table">
          <thead>
            <tr>
              <th>expérience</th>
              <th>stratégie</th>
              <th>cible</th>
              <th>avant → après</th>
              <th>Judge</th>
              <th>oubli</th>
              <th>apprentissage (Judge)</th>
              <th>cause</th>
            </tr>
          </thead>
          <tbody>
            {[...experiments].reverse().map((e) => (
              <tr key={e.experiment_id}>
                <td>{e.experiment_id}</td>
                <td>{e.strategy}</td>
                <td>{e.target}</td>
                <td>
                  {fmt(e.target_before)} → {fmt(e.target_after)} {e.target_verdict ? `(${e.target_verdict})` : ""}
                </td>
                <td>
                  {e.decision ? e.decision.toUpperCase() : e.status}
                  {e.promoted ? " · PROMU" : ""}
                </td>
                <td>{e.forgotten.join(", ") || "—"}</td>
                <td>
                  {Object.entries(e.learning ?? {})
                    .map(([cap, kinds]) => `${cap} : ${kinds.join("+")}`)
                    .join(" · ") || "—"}
                </td>
                <td>{e.cause ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function EventsPanel({ events, detail }: { events: BrainEvent[]; detail: string | null }) {
  return (
    <Panel title={`Événements du moteur (${events.length})`}>
      {detail && <p className="brain-na">{detail}</p>}
      <ol className="brain-events">
        {[...events].reverse().map((e) => (
          <li key={e.seq}>
            <span className={`brain-label brain-label--${e.label.split("_")[0].toLowerCase()}`}>{e.label}</span>{" "}
            <span className="brain-muted">
              #{e.seq} {e.at} {e.experiment_id ?? ""}
            </span>
            {e.summary && <div>{e.summary}</div>}
            {e.regressions && e.regressions.length > 0 && (
              <ul>
                {e.regressions.map((r) => (
                  <li key={r.suite}>
                    RÉGRESSION {r.group} / {r.suite} : {fmt(r.before)} → {fmt(r.after)} (tolérance {r.tolerance})
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export function BrainLivePage() {
  const state = useBrainState();
  const stream = useBrainEvents();

  if (state.isLoading) return <LoadingState />;
  if (state.isError) return <ErrorState detail={String(state.error)} />;
  const envelope = state.data;
  if (!envelope || envelope.status !== "real" || !envelope.data) {
    return (
      <div className="page">
        <Panel title="Cerveau natif">
          <p className="brain-na">
            <span className="brain-tag brain-tag--na">NON DISPONIBLE</span> {envelope?.detail ?? "réponse vide"}
          </p>
        </Panel>
      </div>
    );
  }
  const brain = envelope.data;
  return (
    <div className="page brain-live">
      <p className="brain-muted">Répertoire observé : {brain.workdir}</p>
      <div className="brain-grid">
        <LoopPanel loop={brain.loop} world={brain.world} />
        <ChampionPanel champion={brain.champion} />
        <CurrentPanel current={brain.current} running={brain.loop.status === "real" && brain.loop.state === "running"} />
        <ProtectionsPanel protections={brain.protections} />
      </div>
      <ExperimentsPanel experiments={brain.experiments} />
      <EventsPanel events={stream.events} detail={stream.status === "real" ? null : stream.detail} />
    </div>
  );
}
