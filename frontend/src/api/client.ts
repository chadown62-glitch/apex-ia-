/**
 * Client HTTP unique — TOUTE requête vers le backend passe par ici.
 *
 * Règle de sécurité absolue (demandée explicitement) : le frontend n'accède
 * JAMAIS à PostgreSQL ni au Core directement. Ce fichier est le seul point
 * qui parle réseau ; les composants ne connaissent que des fonctions
 * typées (voir endpoints.ts), jamais une URL.
 *
 * Authentification (passe « Frontend API Integration ») : `apex.api.auth.
 * require_api_token` exige désormais un jeton sur CHAQUE route, `/health`
 * inclus — ce module est le SEUL endroit qui lit `VITE_APEX_API_TOKEN` et
 * pose l'en-tête `Authorization`, jamais un composant individuel. Le jeton
 * n'est jamais journalisé, jamais renvoyé dans un message d'erreur, jamais
 * affiché — seule sa PRÉSENCE/ABSENCE est observable.
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

/** `true` seulement si un jeton non vide est configuré — jamais le jeton
 * lui-même (voir `getApiToken`, non exporté, seul point de lecture). */
export function isApiTokenConfigured(): boolean {
  return Boolean(getApiToken());
}

function getApiToken(): string | undefined {
  const token = import.meta.env.VITE_APEX_API_TOKEN as string | undefined;
  return token && token.length > 0 ? token : undefined;
}

/** `body` a la forme d'un `DataEnvelope` (real/simulated/unknown) — produite
 * par `apex.api.app`'s gestionnaire d'exception générique (bugs réels, 500)
 * ou par chaque route en cas de dégradation (DB absente). Distinct du corps
 * `{"detail": ...}` que FastAPI produit lui-même pour un `HTTPException`
 * (401/404/422) — voir `hasEnvelopeShape` ci-dessous. */
function hasEnvelopeShape(body: unknown): body is DataEnvelope<unknown> {
  return typeof body === "object" && body !== null && "status" in body;
}

function extractDetail(body: unknown, status: number): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return `HTTP ${status}`;
}

/**
 * Appelle un endpoint et retourne son `DataEnvelope<T>` tel quel — le
 * statut real/simulated/unknown n'est JAMAIS traduit ou perdu ici, il
 * remonte inchangé jusqu'au composant qui l'affiche.
 *
 * Une erreur réseau (backend injoignable) produit elle-même une enveloppe
 * "unknown" plutôt qu'une exception qui obligerait chaque appelant à
 * gérer deux mécanismes d'échec différents.
 *
 * Un `HTTPException` FastAPI (401 authentification, 404 ressource absente,
 * 422 entrée invalide) n'a PAS la forme d'un `DataEnvelope` — ce sont des
 * échecs distincts d'une infrastructure absente, jamais confondus avec
 * `status: "unknown"` : ils lèvent `ApiError(detail, httpStatus)`, à charge
 * de l'appelant (page/hook) de distinguer authentification/validation/
 * ressource-absente/inattendu via `error.status`.
 */
export async function fetchEnvelope<T>(path: string, init?: RequestInit): Promise<DataEnvelope<T>> {
  const token = getApiToken();
  if (!token) {
    return {
      status: "unknown",
      data: null,
      detail: "VITE_APEX_API_TOKEN non configuré — voir frontend/.env.example.",
    };
  }
  try {
    const response = await fetch(`${BASE_URL}${API_PREFIX}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...init?.headers,
      },
    });
    if (!response.ok && response.status !== 200) {
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        throw new ApiError(`HTTP ${response.status}`, response.status);
      }
      // Le backend renvoie déjà une DataEnvelope pour ses erreurs 500
      // (gestionnaire global, apex/api/app.py) — on la lit telle quelle ;
      // sinon (401/404/422, forme `{"detail": ...}`) : ApiError typée.
      if (hasEnvelopeShape(body)) return body as DataEnvelope<T>;
      throw new ApiError(extractDetail(body, response.status), response.status);
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
