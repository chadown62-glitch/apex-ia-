import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { listModelBackends } from "../api/endpoints";

import { useCatalogModels, useDiscoverModels, useProviders } from "../api/hooks";
import type { CatalogAvailability } from "../api/types";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import "./pages.css";
import "./models.css";

const AVAILABILITY_LABEL: Record<CatalogAvailability, string> = {
  available: "Disponible",
  unavailable: "Indisponible",
  unknown: "Non sondé",
  disabled: "Désactivé",
};

const AVAILABILITY_CLASS: Record<CatalogAvailability, string> = {
  available: "real",
  unavailable: "fail",
  unknown: "unknown",
  disabled: "unknown",
};

/**
 * Models / Providers — projection directe de `build_model_catalog()`/
 * `build_provider_catalog()` (`apex.models.catalog`), déjà réel. Aucune
 * seconde registry côté frontend : cette page n'affiche que ce que l'API
 * renvoie. "Vérifier la connexion" est la SEULE action qui touche
 * réellement le réseau (`POST /models/discover`) — jamais déclenchée au
 * chargement de la page.
 */
export function ModelsPage() {
  const models = useCatalogModels();
  const providers = useProviders();
  const discover = useDiscoverModels();
  const [lastCheckedAt, setLastCheckedAt] = useState<Date | null>(null);

  const modelList = models.data?.status === "real" ? models.data.data ?? [] : [];
  const providerList = providers.data?.status === "real" ? providers.data.data ?? [] : [];

  async function handleDiscover() {
    await discover.mutateAsync();
    setLastCheckedAt(new Date());
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Models / Providers</h1>
          <p className="page__subtitle">
            Catalogue réel dérivé de `config.yaml` — disponibilité `UNKNOWN` tant qu'aucune sonde réelle n'a été lancée.
          </p>
        </div>
        <button className="action-button action-button--live" onClick={handleDiscover} disabled={discover.isPending}>
          {discover.isPending ? "Vérification…" : "Vérifier la connexion"}
        </button>
      </div>

      {lastCheckedAt && (
        <p className="models-page__checked-at">Dernière vérification réelle : {lastCheckedAt.toLocaleTimeString("fr-FR")}</p>
      )}

      <Panel title="Providers" dense>
        {providers.isLoading ? (
          <LoadingState />
        ) : providers.data?.status !== "real" ? (
          <ErrorState detail={providers.data?.detail} />
        ) : providerList.length === 0 ? (
          <ErrorState detail="Aucun provider déclaré dans config.yaml." />
        ) : (
          <div className="kv-table-wrap">
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Runtime</th>
                  <th>Local/Externe</th>
                  <th>Secret</th>
                  <th>Activé</th>
                  <th>Modèles servis</th>
                </tr>
              </thead>
              <tbody>
                {providerList.map((p) => (
                  <tr key={p.id}>
                    <td>{p.display_name}</td>
                    <td className="mono">{p.runtime}</td>
                    <td>{p.local ? "Local" : "Externe"}</td>
                    <td className="mono">{p.secret_key ?? "— aucun —"}</td>
                    <td>{p.enabled ? "Oui" : "Non"}</td>
                    <td>{p.model_ids.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Modèles" dense>
        {models.isLoading ? (
          <LoadingState />
        ) : models.data?.status !== "real" ? (
          <ErrorState detail={models.data?.detail} />
        ) : modelList.length === 0 ? (
          <ErrorState detail="Aucun modèle déclaré dans config.yaml." />
        ) : (
          <div className="kv-table-wrap">
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Modèle</th>
                  <th>Rôle</th>
                  <th>Local/Externe</th>
                  <th>Runtime</th>
                  <th>Contexte</th>
                  <th>Capacités</th>
                  <th>Disponibilité</th>
                  <th>Historique réel</th>
                </tr>
              </thead>
              <tbody>
                {modelList.map((m) => (
                  // `model_id` seul n'est pas unique : le même modèle peut être
                  // candidat de plusieurs rôles (ex. qwen3.6 en coder_main ET
                  // generalist, config.yaml) — chaque ligne du catalogue est
                  // une entrée (rôle, modèle) distincte, jamais un doublon visuel.
                  <tr key={`${m.role}:${m.model_id}`}>
                    <td>
                      <div className="models-page__model-name">{m.display_name}</div>
                      <div className="models-page__model-id mono">{m.model_id}</div>
                    </td>
                    <td>{m.role}</td>
                    <td>{m.is_local ? "Local" : "Externe"}</td>
                    <td className="mono">{m.runtime ?? "— non configuré —"}</td>
                    <td>{m.max_context ? m.max_context.toLocaleString("fr-FR") : "—"}</td>
                    <td>
                      <div className="models-page__caps">
                        {m.capabilities.map((c) => (
                          <span key={c} className="models-page__cap-chip">
                            {c}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <span className={`status-badge status-badge--${AVAILABILITY_CLASS[m.availability]}`}>
                        <span className="status-badge__dot" aria-hidden="true" />
                        {AVAILABILITY_LABEL[m.availability]}
                      </span>
                      {m.availability_detail && (
                        <div className="models-page__model-id">{m.availability_detail}</div>
                      )}
                    </td>
                    <td>
                      {m.history.usage_count === 0 ? (
                        <span className="models-page__history-empty">Aucune exécution connue</span>
                      ) : (
                        <div className="models-page__history">
                          <span>
                            {m.history.success_count}/{m.history.usage_count} réussie(s)
                          </span>
                          {m.history.last_failure_reason && (
                            <span className="models-page__history-last-failure mono">
                              dernier échec : {m.history.last_failure_reason}
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {discover.data && discover.data.status !== "real" && <ErrorState detail={discover.data.detail} />}

      <BackendsPanel />
    </div>
  );
}

/** Catalogue FERMÉ des runtimes d'inférence (`GET /models/backends`) : réels ou PLANNED. Un runtime PLANNED
 * n'a aucun fournisseur actif — l'interface le dit au lieu de le laisser croire disponible. */
function BackendsPanel() {
  const backends = useQuery({ queryKey: ["model-backends"], queryFn: listModelBackends });
  const list = backends.data?.status === "real" ? backends.data.data ?? [] : [];
  return (
    <Panel title="Backends d'inférence">
      {backends.isError && <ErrorState detail={String(backends.error)} />}
      <table className="models-page__table" aria-label="Backends d'inférence">
        <thead>
          <tr>
            <th>Runtime</th>
            <th>État</th>
            <th>Description</th>
            <th>LoRA</th>
          </tr>
        </thead>
        <tbody>
          {list
            .filter((b) => b.adapter !== "openai_compatible" || b.runtime === "openai_compatible")
            .map((b) => (
              <tr key={b.runtime}>
                <td className="mono">{b.runtime}</td>
                <td>{b.status === "implemented" ? "implémenté" : "PLANNED — non disponible"}</td>
                <td>{b.description}</td>
                <td>{b.lora}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </Panel>
  );
}
