import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { errorMessage, type ModelOption } from "./api";

/**
 * Avertit AVANT l'envoi quand aucun modèle n'est disponible (détection réelle du backend) : la conversation
 * reste possible, mais la réponse sera une erreur explicite, jamais une réponse inventée.
 */
export function ModelNotice({ models }: { models: UseQueryResult<ModelOption[]> }) {
  if (models.isLoading) return null;
  if (models.isError) {
    return (
      <div className="ax-banner ax-banner--warning" role="status">
        <span>État du modèle inconnu : {errorMessage(models.error)}</span>
      </div>
    );
  }
  if (models.data?.some((m) => m.available)) return null;
  const declared = models.data ?? [];
  return (
    <div className="ax-banner ax-banner--warning" role="status">
      <strong>Aucun modèle disponible</strong>
      <span>
        APEX ne peut pas répondre tant qu'aucun modèle n'est installé. Dans le Panel APEX, cliquez sur « Installer le modèle »
        (une fois, ≈ 3,1 Go), puis redémarrez APEX. <Link to="/settings#modeles">Voir les modèles</Link>
      </span>
      {declared.length > 0 && (
        <details className="ax-banner__detail">
          <summary>Détails techniques ({declared.length} modèle{declared.length > 1 ? "s" : ""} déclaré{declared.length > 1 ? "s" : ""})</summary>
          <ul>
            {declared.map((m) => (
              <li key={m.id}>
                <code>{m.id}</code> : {m.detail ?? "indisponible"}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
