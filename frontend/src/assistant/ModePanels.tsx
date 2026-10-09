/**
 * Vues propres à chaque mode, à côté de la conversation — uniquement des données RÉELLES :
 * - Code : fichiers de l'espace de travail (API), lus à la demande ;
 * - Design : couleurs citées dans les réponses, contrastes calculés ici avec la formule WCAG (theme/contrast.ts)
 *   et contrastes vérifiés par l'outil `color.contrast` ;
 * - Finance : résultats des outils de calcul exécutés pendant les tours (prêt, projection, calculatrice).
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { contrast, extractHexColors } from "../theme/contrast";
import { assistantApi, errorMessage, type Message, type ToolStep } from "./api";
import { keys } from "./hooks";
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
