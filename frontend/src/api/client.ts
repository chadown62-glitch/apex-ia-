/**
 * Client HTTP unique — TOUTE requête vers le backend passe par ici.
 *
 * Règle de sécurité absolue (demandée explicitement) : le frontend n'accède
 * JAMAIS à PostgreSQL ni au Core directement. Ce fichier est le seul point
 * qui parle réseau ; les composants ne connaissent que des fonctions
 * typées (voir endpoints.ts), jamais une URL.
 */

import type { DataEnvelope } from "./types";

const BASE_URL = (import.meta.env.VITE_APEX_API_BASE_URL as string | undefined) ?? "";
const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Appelle un endpoint et retourne son `DataEnvelope<T>` tel quel — le
 * statut real/simulated/unknown n'est JAMAIS traduit ou perdu ici, il
 * remonte inchangé jusqu'au composant qui l'affiche.
 *
 * Une erreur réseau (backend injoignable) produit elle-même une enveloppe
 * "unknown" plutôt qu'une exception qui obligerait chaque appelant à
 * gérer deux mécanismes d'échec différents.
 */
export async function fetchEnvelope<T>(path: string, init?: RequestInit): Promise<DataEnvelope<T>> {
  try {
    const response = await fetch(`${BASE_URL}${API_PREFIX}${path}`, {
      ...init,
      headers: { Accept: "application/json", "Content-Type": "application/json", ...init?.headers },
    });
    if (!response.ok && response.status !== 200) {
      // Le backend renvoie déjà une DataEnvelope même sur ses erreurs 500
      // (gestionnaire global, apex/api/app.py) — on la lit quand même.
      try {
        return (await response.json()) as DataEnvelope<T>;
      } catch {
        throw new ApiError(`HTTP ${response.status}`, response.status);
      }
    }
    return (await response.json()) as DataEnvelope<T>;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    return {
      status: "unknown",
      data: null,
      detail: `API injoignable : ${error instanceof Error ? error.message : String(error)}`,
    };
  }
}
