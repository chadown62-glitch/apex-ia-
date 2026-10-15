/**
 * Paramètres centralisés — sept sections, toutes ENREGISTRÉES par le backend (`/assistant/settings`, validées
 * et bornées côté serveur). L'apparence s'applique immédiatement ; les autres sections s'enregistrent par un
 * bouton, avec l'erreur exacte du backend en cas de refus.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { Switch } from "../components/ui/Field";
import { setAppearance, useAppearance, type Appearance } from "../theme/appearance";
import { saveAppearance } from "../theme/useTheme";
import { assistantApi, errorMessage, type AppSettings, type EngineId } from "./api";
import { keys, useEngines, useModes, useSettings } from "./hooks";

type SectionKey = Exclude<keyof AppSettings, "problems" | "appearance">;

const SECTIONS: { id: string; label: string }[] = [
  { id: "apparence", label: "Apparence" },
  { id: "modeles", label: "Modèles" },
  { id: "runtime", label: "Runtime" },
  { id: "comportement", label: "Comportement" },
  { id: "outils", label: "Outils" },
  { id: "confidentialite", label: "Confidentialité" },
  { id: "ressources", label: "Ressources" },
];

function Section({ id, title, lead, children }: { id: string; title: string; lead?: string; children: ReactNode }) {
  return (
    <section id={id} className="ax-card glass ax-settings__section" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>{title}</h2>
      {lead && <p className="ax-muted">{lead}</p>}
      {children}
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="ax-setting">
      <div className="ax-setting__text">
        <span className="ax-setting__label">{label}</span>
        {hint && <span className="ax-setting__hint">{hint}</span>}
      </div>
      <div className="ax-setting__control">{children}</div>
    </div>
  );
}

/** Brouillon d'une section : édité localement, enregistré d'un bloc (tout ou rien côté serveur). */
function useSectionDraft<K extends SectionKey>(settings: AppSettings | undefined, key: K) {
  const queryClient = useQueryClient();
  const server = settings?.[key];
  const [draft, setDraft] = useState<AppSettings[K] | undefined>(server);
  const [state, setState] = useState<{ saving: boolean; error: string | null; saved: boolean }>({ saving: false, error: null, saved: false });

  useEffect(() => setDraft(server), [server]);

  const dirty = JSON.stringify(draft) !== JSON.stringify(server);
  const update = (changes: Partial<AppSettings[K]>) => {
    setDraft((prev) => ({ ...(prev as AppSettings[K]), ...changes }));
    setState((s) => ({ ...s, saved: false, error: null }));
  };
  async function save() {
    if (!draft) return;
    setState({ saving: true, error: null, saved: false });
    try {
      const updated = await assistantApi.updateSettings({ [key]: draft as unknown as Record<string, unknown> });
      queryClient.setQueryData(keys.settings, updated);
      setState({ saving: false, error: null, saved: true });
    } catch (e) {
      setState({ saving: false, error: errorMessage(e), saved: false });
    }
  }
  return { draft, update, dirty, save, state, reset: () => setDraft(server) };
}

function SaveBar({ section }: { section: { dirty: boolean; save: () => Promise<void>; reset: () => void; state: { saving: boolean; error: string | null; saved: boolean } } }) {
  return (
    <div className="ax-savebar">
      {section.state.error && (
        <p className="ax-inline-error" role="alert">
          {section.state.error}
        </p>
      )}
      {section.state.saved && !section.dirty && (
        <p className="ax-ok" role="status">
          Enregistré.
        </p>
      )}
      <div className="ax-form__actions">
        <Button variant="ghost" size="sm" onClick={section.reset} disabled={!section.dirty || section.state.saving}>
          Annuler les changements
        </Button>
        <Button variant="primary" size="sm" onClick={() => void section.save()} disabled={!section.dirty || section.state.saving}>
          {section.state.saving ? "Enregistrement…" : "Enregistrer"}
        </Button>
      </div>
    </div>
  );
}

function NumberField({ value, min, max, step = 1, onChange, label }: { value: number; min: number; max: number; step?: number; onChange: (n: number) => void; label: string }) {
  return (
    <input
      className="ax-number"
      type="number"
      aria-label={label}
      value={Number.isFinite(value) ? value : ""}
      min={min}
      max={max}
      step={step}
      onChange={(e) => onChange(e.target.valueAsNumber)}
    />
  );
}

function ThemeCard({ theme, active, onSelect }: { theme: Appearance["theme"]; active: boolean; onSelect: () => void }) {
  return (
    <button type="button" role="radio" aria-checked={active} className={`ax-theme-card ax-theme-card--${theme} ${active ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="ax-theme-card__preview" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="ax-theme-card__name">{theme === "night" ? "APEX Night" : "APEX Dune"}</span>
      <span className="ax-theme-card__desc">{theme === "night" ? "Noir nocturne, ciel étoilé discret, touches violettes, verre fumé." : "Ivoire, crème et sable, poussière légère, verre dépoli."}</span>
    </button>
  );
}

export function SettingsView() {
  const settings = useSettings();
  const engines = useEngines();
  const modes = useModes();
  const appearance = useAppearance();
  const queryClient = useQueryClient();
  const location = useLocation();
  const [appearanceError, setAppearanceError] = useState<string | null>(null);

  const modelSection = useSectionDraft(settings.data, "models");
  const runtimeSection = useSectionDraft(settings.data, "runtime");
  const behaviorSection = useSectionDraft(settings.data, "behavior");
  const toolsSection = useSectionDraft(settings.data, "tools");
  const privacySection = useSectionDraft(settings.data, "privacy");
  const resourcesSection = useSectionDraft(settings.data, "resources");

  useEffect(() => {
    if (!location.hash || !settings.data) return;
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [location.hash, settings.data]);

  async function changeAppearance(changes: Partial<Appearance>) {
    setAppearanceError(null);
    const error = await saveAppearance(changes);
    if (error) {
      setAppearanceError(`Appliqué sur cet appareil, mais non enregistré : ${error}`);
    } else {
      queryClient.setQueryData<AppSettings>(keys.settings, (prev) => (prev ? { ...prev, appearance: { ...prev.appearance, ...changes } } : prev));
    }
  }

  if (settings.isLoading) {
    return (
      <div className="ax-page">
        <h1>Paramètres</h1>
        <p className="ax-muted">Chargement des paramètres…</p>
      </div>
    );
  }
  if (settings.isError || !settings.data) {
    return (
      <div className="ax-page ax-empty">
        <h1>Paramètres indisponibles</h1>
        <p className="ax-muted">{errorMessage(settings.error)}</p>
        <Button onClick={() => settings.refetch()}>Réessayer</Button>
      </div>
    );
  }

  const m = modelSection.draft;
  const rt = runtimeSection.draft;
  const b = behaviorSection.draft;
  const t = toolsSection.draft;
  const pv = privacySection.draft;
  const rs = resourcesSection.draft;
  const threads = typeof navigator !== "undefined" ? navigator.hardwareConcurrency : undefined;

  return (
    <div className="ax-page ax-settings">
      <header className="ax-page__head">
        <div>
          <p className="ax-eyebrow">APEX</p>
          <h1>Paramètres</h1>
          <p className="ax-page__lead">Enregistrés sur cet ordinateur, dans la base d'APEX ; ils s'appliquent aux prochaines réponses.</p>
        </div>
      </header>
      <nav className="ax-settings__nav glass" aria-label="Sections des paramètres">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`} onClick={(e) => {
            e.preventDefault();
            document.getElementById(s.id)?.scrollIntoView({ block: "start" });
          }}>
            {s.label}
          </a>
        ))}
      </nav>

      {settings.data.problems.length > 0 && (
        <div className="ax-banner ax-banner--warning" role="status">
          {settings.data.problems.map((p) => (
            <span key={p}>{p}</span>
          ))}
        </div>
      )}

      <Section id="apparence" title="Apparence" lead="Appliquée immédiatement et mémorisée.">
        <div className="ax-theme-cards" role="radiogroup" aria-label="Thème">
          <ThemeCard theme="night" active={appearance.theme === "night"} onSelect={() => void changeAppearance({ theme: "night" })} />
          <ThemeCard theme="dune" active={appearance.theme === "dune"} onSelect={() => void changeAppearance({ theme: "dune" })} />
        </div>
        <Row label="Animations" hint="Transitions et mouvements décoratifs.">
          <Switch label="Animations" checked={appearance.animations} onChange={(v) => void changeAppearance({ animations: v })} />
        </Row>
        <Row label="Particules" hint="Étoiles (Night) ou poussière (Dune) en arrière-plan.">
          <Switch label="Particules" checked={appearance.particles} onChange={(v) => void changeAppearance({ particles: v })} />
        </Row>
        <Row label="Réduire les animations" hint="« Système » suit le réglage d'accessibilité de Windows.">
          <select className="ax-select" value={appearance.reduce_motion} onChange={(e) => void changeAppearance({ reduce_motion: e.target.value as Appearance["reduce_motion"] })}>
            <option value="system">Comme le système</option>
            <option value="always">Toujours</option>
            <option value="never">Jamais (ignore le réglage du système)</option>
          </select>
        </Row>
        {appearanceError && (
          <p className="ax-inline-error" role="alert">
            {appearanceError}{" "}
            <button type="button" className="ax-chip-btn" onClick={() => setAppearance(settings.data!.appearance)}>
              Revenir à l'apparence enregistrée
            </button>
          </p>
        )}
      </Section>

      {m && (
        <Section id="modeles" title="Modèles" lead="Le moteur qui répond, et sa disponibilité mesurée maintenant par APEX (rien n'est chargé ni téléchargé ici).">
          {engines.isLoading ? (
            <p className="ax-muted">Détection des moteurs…</p>
          ) : engines.isError ? (
            <p className="ax-inline-error">{errorMessage(engines.error)}</p>
          ) : (
            <div className="ax-engine-cards" role="radiogroup" aria-label="Moteur de réponse">
              {(engines.data ?? []).map((engine) => (
                <button
                  key={engine.id}
                  type="button"
                  role="radio"
                  aria-checked={m.engine === engine.id}
                  className={`ax-engine-card ${m.engine === engine.id ? "is-selected" : ""} ${engine.available ? "is-available" : "is-missing"}`}
                  onClick={() => modelSection.update({ engine: engine.id as EngineId, preferred_model: "auto" })}
                >
                  <span className="ax-engine-card__head">
                    <span className="ax-engine-card__name">{engine.label}</span>
                    {engine.experimental && <span className="ax-engine-card__tag">expérimental</span>}
                  </span>
                  <span className="ax-engine-card__desc">{engine.description}</span>
                  <span className="ax-engine-card__state">
                    <span className="ax-models__dot" aria-hidden="true" />
                    {engine.available ? "disponible" : "indisponible"} — {engine.detail}
                  </span>
                </button>
              ))}
            </div>
          )}
          {(() => {
            const selected = engines.data?.find((e) => e.id === m.engine);
            const choices = selected?.models ?? [];
            return (
              <Row label="Modèle" hint="« Automatique » : APEX choisit parmi les modèles disponibles de ce moteur, avec repli.">
                <select className="ax-select" value={m.preferred_model} onChange={(e) => modelSection.update({ preferred_model: e.target.value })}>
                  <option value="auto">Automatique</option>
                  {choices.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.served_model ?? model.id}
                      {model.available ? "" : " (indisponible)"}
                    </option>
                  ))}
                  {m.preferred_model !== "auto" && !choices.some((x) => x.id === m.preferred_model) && (
                    <option value={m.preferred_model}>{m.preferred_model} (hors de ce moteur)</option>
                  )}
                </select>
              </Row>
            );
          })()}
          <Row label={`Température : ${m.temperature.toFixed(2)}`} hint="Basse : réponses plus constantes ; haute : plus variées.">
            <input type="range" min={0} max={1.5} step={0.05} value={m.temperature} onChange={(e) => modelSection.update({ temperature: e.target.valueAsNumber })} aria-label="Température" />
          </Row>
          <Row label="Longueur maximale d'une réponse" hint="En tokens (64 à 4096). Plus long = plus lent sur processeur.">
            <NumberField label="Tokens maximum" value={m.max_tokens} min={64} max={4096} step={64} onChange={(n) => modelSection.update({ max_tokens: n })} />
          </Row>
          <Row
            label="Le Cerveau passe la main sous cette confiance"
            hint="Mode automatique : une réponse du Cerveau APEX moins sûre que ce seuil (0 à 1, mesuré sur ses propres mots) est confiée au moteur suivant. 0 : jamais."
          >
            <NumberField label="Seuil de confiance" value={m.brain_delegate_below} min={0} max={1} step={0.01} onChange={(n) => modelSection.update({ brain_delegate_below: n })} />
          </Row>
          <SaveBar section={modelSection} />
        </Section>
      )}

      {rt && (
        <Section id="runtime" title="Runtime" lead="Exécution des réponses par le modèle local.">
          <Row label="Délai maximal d'une réponse (secondes)" hint="30 à 3600. Sur processeur, mesuré : 3 à 4 tokens/s ; une réponse de 700 tokens prend 3 à 4 minutes.">
            <NumberField label="Délai en secondes" value={rt.generation_timeout_s} min={30} max={3600} step={30} onChange={(n) => runtimeSection.update({ generation_timeout_s: n })} />
          </Row>
          <SaveBar section={runtimeSection} />
        </Section>
      )}

      {b && (
        <Section id="comportement" title="Comportement">
          <Row label="Mode par défaut" hint="Proposé à l'accueil pour une nouvelle conversation.">
            <select className="ax-select" value={b.default_mode} onChange={(e) => behaviorSection.update({ default_mode: e.target.value as typeof b.default_mode })}>
              {(modes.data ?? []).map((mode) => (
                <option key={mode.id} value={mode.id}>
                  {mode.label}
                </option>
              ))}
            </select>
          </Row>
          <Row label="Mémoire entre conversations" hint="Rappeler des échanges passés pertinents (mémoire locale d'APEX).">
            <Switch label="Mémoire" checked={b.use_memory} onChange={(v) => behaviorSection.update({ use_memory: v })} />
          </Row>
          <Row label="Historique envoyé au modèle (caractères)" hint="1000 à 24000. Plus long = plus de contexte, réponse plus lente.">
            <NumberField label="Caractères d'historique" value={b.history_chars} min={1000} max={24000} step={500} onChange={(n) => behaviorSection.update({ history_chars: n })} />
          </Row>
          <SaveBar section={behaviorSection} />
        </Section>
      )}

      {t && (
        <Section id="outils" title="Outils" lead="Ce que le modèle peut faire en mode Code (les autres modes n'ont que des calculs sans effet de bord).">
          <Row label="Écrire dans l'espace de travail" hint="Créer et modifier des fichiers, uniquement dans l'espace de la conversation ou du projet.">
            <Switch label="Écriture" checked={t.workspace_write} onChange={(v) => toolsSection.update({ workspace_write: v })} />
          </Row>
          <Row label="Exécuter du code" hint="Python dans la sandbox d'APEX, réseau coupé ; proposé seulement si la sandbox est vérifiée.">
            <Switch label="Exécution" checked={t.code_execution} onChange={(v) => toolsSection.update({ code_execution: v })} />
          </Row>
          <Row label="Étapes d'outils par réponse" hint="1 à 40 : davantage pour un gros projet (lire, écrire, tester, corriger), au prix d'une réponse plus longue.">
            <NumberField label="Étapes maximum" value={t.max_tool_steps} min={1} max={40} onChange={(n) => toolsSection.update({ max_tool_steps: n })} />
          </Row>
          <SaveBar section={toolsSection} />
        </Section>
      )}

      {pv && (
        <Section id="confidentialite" title="Confidentialité" lead="Tout reste sur cet ordinateur : APEX n'envoie rien sur Internet pour répondre.">
          <Row label="Indexer les conversations dans la mémoire" hint="Désactivé : les nouveaux échanges ne sont plus mémorisés pour d'autres conversations.">
            <Switch label="Indexation" checked={pv.index_conversations} onChange={(v) => privacySection.update({ index_conversations: v })} />
          </Row>
          <Row label="Envoyer les pièces jointes au modèle" hint="Désactivé : les fichiers restent dans le projet mais ne sont plus joints aux réponses.">
            <Switch label="Pièces jointes" checked={pv.attachments_enabled} onChange={(v) => privacySection.update({ attachments_enabled: v })} />
          </Row>
          <SaveBar section={privacySection} />
        </Section>
      )}

      {rs && (
        <Section id="ressources" title="Ressources" lead="Usage de la mémoire et du processeur par le modèle local.">
          <Row label="Précision du modèle" hint="Automatique : choisie selon la mémoire libre mesurée (float32 ≈ 7,5 Go, bfloat16 ≈ 4 Go pour le modèle 1,5B). Un réglage trop gourmand est refusé avec la mémoire mesurée.">
            <select className="ax-select" value={rs.model_precision} onChange={(e) => resourcesSection.update({ model_precision: e.target.value as typeof rs.model_precision })}>
              <option value="auto">Automatique</option>
              <option value="float32">float32 (plus rapide, plus de mémoire)</option>
              <option value="bfloat16">bfloat16 (moitié moins de mémoire)</option>
            </select>
          </Row>
          <Row label="Threads processeur" hint={`0 = automatique.${threads ? ` Ce processeur expose ${threads} threads logiques.` : ""}`}>
            <NumberField label="Threads" value={rs.cpu_threads} min={0} max={64} onChange={(n) => resourcesSection.update({ cpu_threads: n })} />
          </Row>
          <SaveBar section={resourcesSection} />
        </Section>
      )}
    </div>
  );
}
