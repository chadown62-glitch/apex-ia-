/**
 * Vues propres à chaque mode, à côté de la conversation — uniquement des données RÉELLES :
 * - Code : fichiers de l'espace de travail (API), lus à la demande ;
 * - Design : couleurs citées dans les réponses, contrastes calculés ici avec la formule WCAG (theme/contrast.ts)
 *   et contrastes vérifiés par l'outil `color.contrast` ;
 * - Finance : résultats des outils de calcul exécutés pendant les tours (prêt, projection, calculatrice) ;
 * - 3D : modèles de l'espace de travail, et missions confiées à Game Studio (lues par ses outils).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { contrast, extractHexColors } from "../theme/contrast";
import { assistantApi, errorMessage, type GameStudioAvailability, type Message, type ToolStep } from "./api";
import { keys } from "./hooks";
import { explainRefusal } from "./MessageItem";
import { IconFile, IconRefresh } from "./Icons";

const euro = (value: unknown) =>
  typeof value === "number" ? value.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
  return `${(bytes / 1024 / 1024).toFixed(1)} Mo`;
}

export function CodePanel({ conversationId, notes }: { conversationId: string; notes: string[] }) {
  const workspace = useQuery({ queryKey: keys.workspace(conversationId), queryFn: () => assistantApi.workspace(conversationId) });
  const [selected, setSelected] = useState<string | null>(null);
  const file = useQuery({
    queryKey: [...keys.workspace(conversationId), "file", selected],
    queryFn: () => assistantApi.workspaceFile(conversationId, selected!),
    enabled: selected !== null,
  });

  return (
    <div className="ax-panel">
      <div className="ax-panel__head">
        <h2>Espace de travail</h2>
        <button type="button" className="ax-icon-btn ax-icon-btn--sm" onClick={() => workspace.refetch()} aria-label="Actualiser les fichiers" title="Actualiser">
          <IconRefresh size={15} />
        </button>
      </div>
      {notes.length > 0 && (
        <ul className="ax-panel__notes">
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {workspace.isLoading ? (
        <p className="ax-muted">Lecture de l'espace de travail…</p>
      ) : workspace.isError ? (
        <p className="ax-inline-error" role="alert">
          {errorMessage(workspace.error)}
        </p>
      ) : workspace.data && workspace.data.files.length > 0 ? (
        <>
          <p className="ax-muted">
            {workspace.data.scope === "project" ? "Espace partagé par les conversations du projet." : "Espace propre à cette conversation."}
          </p>
          <ul className="ax-files">
            {workspace.data.files.map((f) => (
              <li key={f.path}>
                <button type="button" className={`ax-file ${selected === f.path ? "is-active" : ""}`} onClick={() => setSelected(f.path)}>
                  <IconFile size={14} />
                  <span className="ax-file__path">{f.path}</span>
                  <span className="ax-file__size">{formatSize(f.size)}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="ax-muted">
          Vide pour l'instant. En mode Code, APEX y écrit les fichiers qu'il crée ; les pièces jointes du projet y sont déposées
          (dossier pieces-jointes).
        </p>
      )}
      {selected && (
        <div className="ax-viewer">
          <div className="ax-viewer__bar">
            <code>{selected}</code>
            <button type="button" className="ax-chip-btn" onClick={() => setSelected(null)}>
              Fermer
            </button>
          </div>
          {file.isLoading ? (
            <p className="ax-muted">Lecture…</p>
          ) : file.isError ? (
            <p className="ax-inline-error">{errorMessage(file.error)}</p>
          ) : (
            <pre className="ax-viewer__content">
              <code>{file.data?.content}</code>
            </pre>
          )}
          {file.data?.truncated && <p className="ax-muted">Fichier tronqué à 400 000 octets pour l'affichage.</p>}
        </div>
      )}
    </div>
  );
}

function stepsOf(messages: Message[], live: ToolStep[]): ToolStep[] {
  return [...messages.flatMap((m) => m.meta?.steps ?? []), ...live];
}

export function DesignPanel({ messages, liveSteps }: { messages: Message[]; liveSteps: ToolStep[] }) {
  const palette = useMemo(
    () =>
      extractHexColors(
        messages
          .filter((m) => m.role === "assistant" && m.status === "real")
          .slice()
          .reverse()
          .map((m) => m.content)
          .join("\n"),
      ),
    [messages],
  );
  const checked = stepsOf(messages, liveSteps).filter((s) => s.tool === "color.contrast" && s.ok && s.observation.output);
  const choices = [...palette, "#ffffff", "#000000"].filter((c, i, all) => all.indexOf(c) === i);
  const [fg, setFg] = useState<string | null>(null);
  const [bg, setBg] = useState<string | null>(null);
  const foreground = fg ?? palette[1] ?? "#000000";
  const background = bg ?? palette[0] ?? "#ffffff";
  const result = contrast(foreground, background);

  return (
    <div className="ax-panel">
      <div className="ax-panel__head">
        <h2>Nuancier</h2>
      </div>
      {palette.length === 0 ? (
        <p className="ax-muted">Les couleurs #RRGGBB citées dans les réponses s'afficheront ici, avec leurs contrastes.</p>
      ) : (
        <ul className="ax-swatches">
          {palette.map((color) => {
            const onWhite = contrast(color, "#ffffff")!;
            const onBlack = contrast(color, "#000000")!;
            return (
              <li key={color} className="ax-swatch">
                <span className="ax-swatch__chip" style={{ background: color }} aria-hidden="true" />
                <code>{color}</code>
                <span className="ax-swatch__ratios" title="Ratio de contraste WCAG avec le blanc et avec le noir">
                  □ {onWhite.ratio} · ■ {onBlack.ratio}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <div className="ax-contrast">
        <h3>Vérifier un couple</h3>
        <div className="ax-contrast__pickers">
          <label>
            Texte
            <select value={foreground} onChange={(e) => setFg(e.target.value)}>
              {choices.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label>
            Fond
            <select value={background} onChange={(e) => setBg(e.target.value)}>
              {choices.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="ax-contrast__preview" style={{ color: foreground, background }}>
          Aa — Texte d'exemple
        </div>
        {result && (
          <p className="ax-contrast__result">
            Ratio <strong>{result.ratio} : 1</strong> — AA texte normal {result.aaNormal ? "✓" : "✕"} · AA grand texte{" "}
            {result.aaLarge ? "✓" : "✕"} · AAA {result.aaaNormal ? "✓" : "✕"}
          </p>
        )}
        <p className="ax-muted">Calculé dans l'interface avec la formule WCAG 2.x (la même que l'outil d'APEX).</p>
      </div>
      {checked.length > 0 && (
        <div className="ax-contrast">
          <h3>Contrastes vérifiés par l'outil</h3>
          <ul className="ax-checked">
            {checked.map((s, i) => {
              const out = s.observation.output as { foreground: string; background: string; ratio: number; aa_normal_text: boolean };
              return (
                <li key={`${s.step}-${i}`}>
                  <span className="ax-swatch__chip ax-swatch__chip--sm" style={{ background: out.background }} aria-hidden="true">
                    <span style={{ color: out.foreground }}>A</span>
                  </span>
                  <code>
                    {out.foreground} / {out.background}
                  </code>
                  <span>
                    {out.ratio} : 1 {out.aa_normal_text ? "AA ✓" : "AA ✕"}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

interface LoanOutput {
  monthly_payment: number;
  total_paid: number;
  total_interest: number;
  rows: { year: number; interest: number; remaining: number }[];
  assumptions: { principal: number; annual_rate_percent: number; years: number };
}

interface ProjectionOutput {
  monthly_rate_percent: number;
  rows: { year: number; contributed: number; balance: number; gains: number; balance_real: number }[];
  final: { year: number; contributed: number; balance: number; gains: number; balance_real: number };
  assumptions: { initial: number; monthly_contribution: number; annual_rate_percent: number; years: number; annual_inflation_percent: number };
}

export function FinancePanel({ messages, liveSteps }: { messages: Message[]; liveSteps: ToolStep[] }) {
  const steps = stepsOf(messages, liveSteps).filter(
    (s) => s.ok && s.observation.output && ["finance.loan", "finance.projection", "calc.evaluate"].includes(s.tool),
  );
  return (
    <div className="ax-panel">
      <div className="ax-panel__head">
        <h2>Calculs</h2>
      </div>
      {steps.length === 0 ? (
        <p className="ax-muted">
          Les calculs exécutés par les outils (prêt, projection d'épargne, calculatrice) s'afficheront ici avec leurs tableaux. Exemple :
          « Mensualité d'un prêt de 200 000 € sur 20 ans à 3,5 % ? »
        </p>
      ) : (
        steps
          .slice()
          .reverse()
          .map((step, i) => {
            const key = `${step.tool}-${step.step}-${i}`;
            if (step.tool === "finance.loan") {
              const out = step.observation.output as unknown as LoanOutput;
              return (
                <section key={key} className="ax-fin">
                  <h3>Prêt de {euro(out.assumptions.principal)} € à {out.assumptions.annual_rate_percent} % sur {out.assumptions.years} ans</h3>
                  <dl className="ax-fin__kpis">
                    <div>
                      <dt>Mensualité</dt>
                      <dd>{euro(out.monthly_payment)} €</dd>
                    </div>
                    <div>
                      <dt>Total remboursé</dt>
                      <dd>{euro(out.total_paid)} €</dd>
                    </div>
                    <div>
                      <dt>Coût des intérêts</dt>
                      <dd>{euro(out.total_interest)} €</dd>
                    </div>
                  </dl>
                  <div className="md-table">
                    <table>
                      <thead>
                        <tr>
                          <th>Année</th>
                          <th>Intérêts payés</th>
                          <th>Restant dû</th>
                        </tr>
                      </thead>
                      <tbody>
                        {out.rows.map((r) => (
                          <tr key={r.year}>
                            <td>{r.year}</td>
                            <td>{euro(r.interest)}</td>
                            <td>{euro(r.remaining)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              );
            }
            if (step.tool === "finance.projection") {
              const out = step.observation.output as unknown as ProjectionOutput;
              const inflation = out.assumptions.annual_inflation_percent > 0;
              return (
                <section key={key} className="ax-fin">
                  <h3>
                    Projection : {euro(out.assumptions.initial)} € + {euro(out.assumptions.monthly_contribution)} €/mois à{" "}
                    {out.assumptions.annual_rate_percent} % sur {out.assumptions.years} ans
                  </h3>
                  <dl className="ax-fin__kpis">
                    <div>
                      <dt>Solde final</dt>
                      <dd>{euro(out.final.balance)} €</dd>
                    </div>
                    <div>
                      <dt>Versé</dt>
                      <dd>{euro(out.final.contributed)} €</dd>
                    </div>
                    <div>
                      <dt>Gains</dt>
                      <dd>{euro(out.final.gains)} €</dd>
                    </div>
                    {inflation && (
                      <div>
                        <dt>Solde réel ({out.assumptions.annual_inflation_percent} % d'inflation)</dt>
                        <dd>{euro(out.final.balance_real)} €</dd>
                      </div>
                    )}
                  </dl>
                  <div className="md-table">
                    <table>
                      <thead>
                        <tr>
                          <th>Année</th>
                          <th>Versé</th>
                          <th>Solde</th>
                          <th>Gains</th>
                          {inflation && <th>Solde réel</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {out.rows.map((r) => (
                          <tr key={r.year}>
                            <td>{r.year}</td>
                            <td>{euro(r.contributed)}</td>
                            <td>{euro(r.balance)}</td>
                            <td>{euro(r.gains)}</td>
                            {inflation && <td>{euro(r.balance_real)}</td>}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="ax-muted">Scénario à taux constant, pas une prévision.</p>
                </section>
              );
            }
            const out = step.observation.output as { expression: string; result: number };
            return (
              <section key={key} className="ax-fin ax-fin--calc">
                <code>{out.expression}</code>
                <span>=</span>
                <strong>{typeof out.result === "number" ? out.result.toLocaleString("fr-FR", { maximumFractionDigits: 10 }) : String(out.result)}</strong>
              </section>
            );
          })
      )}
    </div>
  );
}

/** Un modèle 3D de l'espace de travail : ses fichiers `modeles3d/<nom>.*` et `modeles3d/Apex*.{cs,h,cpp}`. */
interface Model3D {
  name: string;
  preview: string | null;
  files: { path: string; size: number }[];
  summary: string | null;
}

const MODELS_DIR = "modeles3d/";

function models3d(files: { path: string; size: number }[], steps: ToolStep[]): Model3D[] {
  const inDir = files.filter((f) => f.path.startsWith(MODELS_DIR));
  const summaries = new Map<string, { summary: string; files: string[] }>();
  for (const step of steps) {
    const output = step.tool === "model3d.create" && step.ok ? step.observation.output : null;
    if (output && typeof output.name === "string" && typeof output.summary === "string") {
      const written = Array.isArray(output.files) ? (output.files as { path: string }[]).map((f) => f.path) : [];
      summaries.set(output.name, { summary: output.summary, files: written });
    }
  }
  return inDir
    .filter((f) => f.path.endsWith(".glb"))
    .map((glb) => {
      const name = glb.path.slice(MODELS_DIR.length, -".glb".length);
      const known = summaries.get(name);
      const own = inDir.filter(
        (f) => f.path.slice(MODELS_DIR.length).startsWith(`${name}.`) || f.path.slice(MODELS_DIR.length).startsWith(`${name}_`) ||
          (known?.files.includes(f.path) ?? false),
      );
      const preview = own.find((f) => f.path === `${MODELS_DIR}${name}.png`)?.path ?? null;
      return { name, preview, files: own, summary: known?.summary ?? null };
    });
}

function PreviewImage({ conversationId, path, alt }: { conversationId: string; path: string; alt: string }) {
  const image = useQuery({
    queryKey: ["assistant", "workspace", conversationId, "base64", path],
    queryFn: () => assistantApi.workspaceFileBase64(conversationId, path),
    staleTime: Infinity,
  });
  if (image.isLoading) return <div className="ax-model3d__preview ax-model3d__preview--loading" aria-label="Chargement de l'aperçu" />;
  if (image.isError || !image.data) return <p className="ax-inline-error">{errorMessage(image.error)}</p>;
  return <img className="ax-model3d__preview" src={`data:image/png;base64,${image.data.content_base64}`} alt={alt} />;
}

async function download(conversationId: string, path: string): Promise<void> {
  const file = await assistantApi.workspaceFileBase64(conversationId, path);
  const bytes = Uint8Array.from(atob(file.content_base64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes]));
  const link = document.createElement("a");
  link.href = url;
  link.download = path.slice(path.lastIndexOf("/") + 1);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** États après lesquels une mission Game Studio n'évolue plus (ses résultats sont alors lisibles). */
const GAME_STUDIO_FINISHED = new Set(["review", "failed", "cancelled"]);
const GAME_STUDIO_STATES: Record<string, string> = {
  queued: "en file d'attente",
  running: "en cours",
  review: "terminée, à revoir",
  technical_review: "revue technique",
  failed: "échouée",
  cancelled: "annulée",
  blocked: "bloquée",
};
const stateLabel = (state: string) => GAME_STUDIO_STATES[state] ?? state;

/** Missions lancées dans cette conversation : chaque appel de `game_studio.create`, réussi ou refusé. */
function gameStudioLaunches(steps: ToolStep[]): { key: string; projectId: string | null; refusal: string | null }[] {
  return steps
    .filter((s) => s.tool === "game_studio.create")
    .map((s, i) => {
      const id = s.ok ? s.observation.output?.project_id : null;
      const refusal = s.ok ? null : explainRefusal(s.observation.error?.message);
      return { key: typeof id === "string" ? id : `refus-${i}`, projectId: typeof id === "string" ? id : null, refusal };
    });
}

/** Missions que le Cerveau a PROPOSÉES et qui n'ont pas encore été lancées (une seule fois chacune). */
function gameStudioProposals(messages: Message[]): { message: Message; request: string; reason: string }[] {
  const launched = new Set(messages.map((m) => m.meta?.game_studio?.proposal_of).filter((id): id is string => !!id));
  return messages
    .filter((m) => m.meta?.game_studio?.state === "proposed" && !launched.has(m.id))
    .map((m) => ({
      message: m,
      request: messages.find((u) => u.id === m.meta?.game_studio?.request_message_id)?.content ?? "",
      reason: m.meta?.game_studio?.reason ?? "",
    }));
}

function GameStudioPreview({ conversationId, projectId, digest, name }: { conversationId: string; projectId: string; digest: string; name: string }) {
  const image = useQuery({
    queryKey: ["assistant", "game-studio", conversationId, projectId, "preview", digest],
    queryFn: () => assistantApi.gameStudioPreview(conversationId, projectId, digest),
    staleTime: Infinity,
  });
  if (image.isLoading) return <div className="ax-model3d__preview ax-model3d__preview--loading" aria-label="Chargement du rendu" />;
  if (image.isError || !image.data) return <p className="ax-inline-error">{errorMessage(image.error)}</p>;
  return <img className="ax-model3d__preview" src={`data:${image.data.content_type};base64,${image.data.content_base64}`} alt={`Rendu ${name}`} />;
}

function GameStudioMissionCard({ conversationId, projectId }: { conversationId: string; projectId: string }) {
  const client = useQueryClient();
  const key = ["assistant", "game-studio", conversationId, projectId];
  const mission = useQuery({
    queryKey: key,
    queryFn: () => assistantApi.gameStudioMission(conversationId, projectId),
    refetchInterval: (query) => (query.state.data && GAME_STUDIO_FINISHED.has(query.state.data.status.status) ? false : 5000),
  });
  const cancel = useMutation({
    mutationFn: () => assistantApi.cancelGameStudioMission(conversationId, projectId),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  const data = mission.data;
  const views = (data?.results.artifacts ?? []).filter((a) => a.role === "view").slice(0, 4);
  return (
    <li className="ax-model3d ax-gs-mission">
      <div className="ax-model3d__body">
        <h3>Mission {projectId.slice(0, 8)}</h3>
        {mission.isLoading ? (
          <p className="ax-muted">Lecture de la mission…</p>
        ) : mission.isError || !data ? (
          <p className="ax-inline-error" role="alert">{errorMessage(mission.error)}</p>
        ) : (
          <>
            <p className="ax-model3d__summary">
              État : <strong>{stateLabel(data.status.status)}</strong>
              {data.status.delivery_status ? ` · livraison : ${data.status.delivery_status}` : ""}
            </p>
            {data.status.steps.length > 0 && (
              <ul className="ax-gs-steps">
                {data.status.steps.map((step) => (
                  <li key={step.key}>
                    {step.key}
                    {step.kind ? ` (${step.kind})` : ""} : {stateLabel(step.status)}
                  </li>
                ))}
              </ul>
            )}
            {data.results.blocked && data.results.blocked.length > 0 && (
              <ul className="ax-panel__notes">
                {data.results.blocked.map((b) => (
                  <li key={b.key}>{`${b.key} bloquée${b.reason ? ` : ${b.reason}` : ""}`}</li>
                ))}
              </ul>
            )}
            {views.map((v) => (
              <GameStudioPreview key={v.hash} conversationId={conversationId} projectId={projectId} digest={v.hash} name={v.name} />
            ))}
            {data.results.ready ? (
              <p className="ax-muted">
                {data.results.artifact_count} fichier(s) produit(s){data.results.truncated ? " (liste tronquée)" : ""} :{" "}
                {(data.results.artifacts ?? []).map((a) => `${a.name} (${a.role})`).join(", ")}.{" "}
                <Link to="/game-studio">Ouvrir dans Game Studio</Link> pour les télécharger.
              </p>
            ) : (
              GAME_STUDIO_FINISHED.has(data.status.status) && <p className="ax-muted">Aucun fichier produit.</p>
            )}
            {!GAME_STUDIO_FINISHED.has(data.status.status) && (
              <button type="button" className="ax-chip-btn" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
                Annuler la mission
              </button>
            )}
            {cancel.isError && <p className="ax-inline-error" role="alert">{errorMessage(cancel.error)}</p>}
          </>
        )}
      </div>
    </li>
  );
}

function GameStudioProposal({ conversationId, message, request, reason, availability }: {
  conversationId: string; message: Message; request: string; reason: string; availability: GameStudioAvailability | undefined;
}) {
  const client = useQueryClient();
  const launch = useMutation({
    mutationFn: () => assistantApi.launchGameStudio(conversationId, message.id),
    onSettled: () => client.invalidateQueries({ queryKey: keys.messages(conversationId) }),
  });
  const cpu = availability?.compute === "cpu";
  return (
    <li className="ax-model3d ax-gs-mission">
      <div className="ax-model3d__body">
        <h3>Mission proposée</h3>
        <p className="ax-model3d__summary">
          Le Cerveau propose de confier à Game Studio : « {request.length > 160 ? `${request.slice(0, 160)}…` : request} »
        </p>
        {reason && <p className="ax-muted">{reason}</p>}
        <p className="ax-muted">
          Une mission dure plusieurs minutes et utilise {cpu ? "le processeur (choix explicite)" : "le GPU"}. Le calcul se règle dans
          Paramètres &gt; Outils.
        </p>
        {availability && availability.installed && !availability.ready && (
          <ul className="ax-panel__notes">
            {availability.missing.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        )}
        <button type="button" className="ax-chip-btn" disabled={launch.isPending} onClick={() => launch.mutate()}>
          Lancer la mission
        </button>
        {launch.isError && <p className="ax-inline-error" role="alert">{errorMessage(launch.error)}</p>}
      </div>
    </li>
  );
}

function GameStudioMissions({ conversationId, messages, steps }: { conversationId: string; messages: Message[]; steps: ToolStep[] }) {
  const launches = useMemo(() => gameStudioLaunches(steps), [steps]);
  const proposals = useMemo(() => gameStudioProposals(messages), [messages]);
  const availability = useQuery({ queryKey: ["assistant", "game-studio", "availability"], queryFn: assistantApi.gameStudioAvailability, staleTime: 30_000 });
  const report = availability.data;
  if (launches.length === 0 && proposals.length === 0 && !report?.installed) return null;
  return (
    <section aria-label="Missions Game Studio">
      <h3 className="ax-gs-title">Game Studio</h3>
      {report && (
        <p className="ax-muted">
          {report.ready ? "Prêt sur ce PC." : "Pas encore prêt sur ce PC."}{" "}
          {report.auto_launch ? "Le Cerveau lance lui-même les demandes de création claires." : "Le Cerveau propose les missions ; vous les lancez."}
        </p>
      )}
      {report && !report.ready && proposals.length === 0 && report.missing.length > 0 && (
        <ul className="ax-panel__notes">
          {report.missing.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
      <ul className="ax-model3d-list">
        {proposals.map((p) => (
          <GameStudioProposal key={p.message.id} conversationId={conversationId} message={p.message} request={p.request} reason={p.reason}
            availability={report} />
        ))}
        {launches.map((launch) =>
          launch.projectId ? (
            <GameStudioMissionCard key={launch.key} conversationId={conversationId} projectId={launch.projectId} />
          ) : (
            <li key={launch.key} className="ax-model3d ax-gs-mission">
              <p className="ax-inline-error">Game Studio a refusé la mission : {launch.refusal}</p>
            </li>
          ),
        )}
      </ul>
    </section>
  );
}

export function Studio3DPanel({ conversationId, messages, liveSteps, notes }: {
  conversationId: string; messages: Message[]; liveSteps: ToolStep[]; notes: string[];
}) {
  const workspace = useQuery({ queryKey: keys.workspace(conversationId), queryFn: () => assistantApi.workspace(conversationId) });
  const steps = useMemo(() => stepsOf(messages, liveSteps), [messages, liveSteps]);
  const models = useMemo(() => models3d(workspace.data?.files ?? [], steps), [workspace.data, steps]);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="ax-panel">
      <div className="ax-panel__head">
        <h2>Atelier 3D</h2>
        <button type="button" className="ax-icon-btn ax-icon-btn--sm" onClick={() => workspace.refetch()} aria-label="Actualiser les modèles" title="Actualiser">
          <IconRefresh size={15} />
        </button>
      </div>
      {notes.length > 0 && (
        <ul className="ax-panel__notes">
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {error && (
        <p className="ax-inline-error" role="alert">
          {error}
        </p>
      )}
      <GameStudioMissions conversationId={conversationId} messages={messages} steps={steps} />
      {workspace.isLoading ? (
        <p className="ax-muted">Lecture de l'espace de travail…</p>
      ) : workspace.isError ? (
        <p className="ax-inline-error" role="alert">
          {errorMessage(workspace.error)}
        </p>
      ) : models.length === 0 ? (
        <p className="ax-muted">
          Aucun modèle pour l'instant. Demandez par exemple « un char de 7 m kaki » ou « une épée de 1,2 m » : APEX construit le
          modèle et écrit ses fichiers pour Blender, Unity et Unreal.
        </p>
      ) : (
        <>
          {workspace.data?.root && (
            <p className="ax-muted ax-model3d__root">
              Dossier sur ce PC :{" "}
              <code>{`${workspace.data.root}${workspace.data.root.includes("\\") ? "\\" : "/"}${MODELS_DIR.slice(0, -1)}`}</code>
            </p>
          )}
          <ul className="ax-model3d-list">
            {models.map((model) => (
              <li key={model.name} className="ax-model3d">
                {model.preview && <PreviewImage conversationId={conversationId} path={model.preview} alt={`Aperçu de ${model.name}`} />}
                <div className="ax-model3d__body">
                  <h3>{model.name}</h3>
                  {model.summary && <p className="ax-model3d__summary">{model.summary}</p>}
                  <ul className="ax-files">
                    {model.files.map((f) => (
                      <li key={f.path}>
                        <button
                          type="button"
                          className="ax-file"
                          title="Télécharger"
                          onClick={() => {
                            setError(null);
                            download(conversationId, f.path).catch((e) => setError(errorMessage(e)));
                          }}
                        >
                          <IconFile size={14} />
                          <span className="ax-file__path">{f.path.slice(MODELS_DIR.length)}</span>
                          <span className="ax-file__size">{formatSize(f.size)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              </li>
            ))}
          </ul>
          <p className="ax-muted">
            .glb : Blender, Unreal (et Unity avec glTFast) · .obj : Unity · _blender.py : reconstruit le modèle dans Blender · .cs : Unity ·
            .h/.cpp : Unreal. Scripts Unity et Unreal vérifiés pour la syntaxe seulement.
          </p>
        </>
      )}
    </div>
  );
}
