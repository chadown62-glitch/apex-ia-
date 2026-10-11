import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { assistantApi, errorMessage, type EngineId, type EngineInfo } from "./api";
import { keys, useEngines, useSettings } from "./hooks";

/** Libellé d'un moteur dans une liste : son état mesuré, et « expérimental » quand c'est le cas. */
export function engineOptionLabel(engine: EngineInfo): string {
  return `${engine.label}${engine.experimental ? " (expérimental)" : ""}${engine.available ? "" : " — indisponible"}`;
}

/**
 * Choix du moteur de réponse, à côté de la zone de saisie : Automatique, Ollama, APEX intégré ou Cerveau APEX.
 * Le choix est un paramètre d'APEX (enregistré par le backend, appliqué au prochain message) ; la disponibilité
 * affichée est celle que le backend vient de mesurer, jamais supposée.
 */
export function EnginePicker() {
  const engines = useEngines();
  const settings = useSettings();
  const queryClient = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = settings.data?.models.engine ?? "auto";

  async function choose(engine: EngineId) {
    setSaving(true);
    setError(null);
    try {
      // Le modèle préféré appartient à un moteur : changer de moteur revient au choix automatique dans ce moteur.
      const saved = await assistantApi.updateSettings({ models: { engine, preferred_model: "auto" } });
      queryClient.setQueryData(keys.settings, saved);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  const options = engines.data ?? [];
  return (
    <div className="ax-engine">
      <label className="ax-engine__label" htmlFor="ax-engine-select">
        Moteur
      </label>
      <select
        id="ax-engine-select"
        className="ax-engine__select"
        value={current}
        disabled={saving || settings.isLoading || options.length === 0}
        onChange={(e) => void choose(e.target.value as EngineId)}
        title={options.find((o) => o.id === current)?.description}
      >
        {options.length === 0 && <option value={current}>{engines.isLoading ? "Détection…" : "État inconnu"}</option>}
        {options.map((engine) => (
          <option key={engine.id} value={engine.id}>
            {engineOptionLabel(engine)}
          </option>
        ))}
      </select>
      {error && (
        <span className="ax-inline-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

/**
 * Ce qu'il faut savoir AVANT d'envoyer, selon le moteur choisi : indisponible (et pourquoi, et quoi faire),
 * expérimental (le Cerveau APEX v1 continue le texte, il ne sait pas converser), ou Cerveau APEX v2 (construit de
 * zéro par APEX : petit modèle, réponses à vérifier — description mesurée par le backend). Rien sinon.
 */
export function EngineNotice() {
  const engines = useEngines();
  const settings = useSettings();
  if (engines.isLoading || settings.isLoading) return null;
  if (engines.isError) {
    return (
      <div className="ax-banner ax-banner--warning" role="status">
        <span>État des moteurs inconnu : {errorMessage(engines.error)}</span>
      </div>
    );
  }
  const current = settings.data?.models.engine ?? "auto";
  const engine = engines.data?.find((e) => e.id === current);
  if (!engine) return null;
  if (!engine.available) {
    const remedy = {
      auto: "Dans APEX, « Installer le modèle » (une fois, ≈ 3,1 Go), ou démarrez Ollama.",
      ollama: "Démarrez Ollama sur ce PC, ou choisissez « APEX intégré ».",
      integrated: "Dans APEX, « Installer le modèle » (une fois, ≈ 3,1 Go), ou choisissez Ollama.",
      brain: "Les poids du Cerveau APEX sont absents ou modifiés : réinstallez APEX.",
    }[engine.id];
    return (
      <div className="ax-banner ax-banner--warning" role="status">
        <strong>{engine.id === "auto" ? "Aucun modèle disponible" : `Moteur « ${engine.label} » indisponible`}</strong>
        <span>
          {remedy} <Link to="/settings#modeles">Voir les moteurs</Link>
        </span>
        <details className="ax-banner__detail">
          <summary>Détails techniques</summary>
          <p>{engine.detail}</p>
          {engine.models.length > 0 && (
            <ul>
              {engine.models.map((m) => (
                <li key={m.id}>
                  <code>{m.id}</code> : {m.available ? "disponible" : m.detail ?? "indisponible"}
                </li>
              ))}
            </ul>
          )}
        </details>
      </div>
    );
  }
  if (engine.experimental) {
    return (
      <div className="ax-banner ax-banner--info" role="status">
        <strong>Cerveau APEX — expérimental</strong>
        <span>
          Le réseau propre à APEX (1,9 M paramètres) est pré-entraîné sur des textes techniques mais pas encore entraîné à
          converser : il continue le texte de votre message, et sa réponse n'a souvent pas de sens. Choisissez « APEX
          intégré » ou Ollama pour une vraie réponse.
        </span>
      </div>
    );
  }
  if (engine.id === "brain") {
    const served = engine.models.find((m) => m.available);
    return (
      <div className="ax-banner ax-banner--info" role="status">
        <strong>Cerveau APEX — construit de zéro par APEX</strong>
        <span>
          {served?.detail ?? engine.detail}. C'est un petit modèle qui tourne sur ce PC : il peut se tromper, vérifiez
          ses réponses importantes.
        </span>
      </div>
    );
  }
  return null;
}
