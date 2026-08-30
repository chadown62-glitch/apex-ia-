/**
 * Chokepoint unique pour toute donnée "vivante" de l'interface.
 *
 * Aujourd'hui `apex/api` n'expose aucun canal temps réel (pas de
 * WebSocket/SSE) — seul HTTP polling est réellement disponible. Ce hook fait
 * donc du polling proprement via React Query. Le jour où un canal réel
 * existera côté backend, seule l'implémentation de CE fichier changera
 * (probablement vers `useQuery` alimenté par un flux, ou un
 * `queryClient.setQueryData` piloté par un WebSocket) — aucune page ni
 * aucun composant appelant n'aura à changer, puisqu'ils ne connaissent que
 * la forme `{ data, isLoading, ... }` renvoyée ici, pas le mécanisme de
 * transport.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

export type RealtimeMode = "poll"; // "socket" viendra plus tard — pas avant qu'un canal existe réellement.

export function usePolling<T>(queryKey: unknown[], queryFn: () => Promise<T>, intervalMs: number): UseQueryResult<T> {
  return useQuery({ queryKey, queryFn, refetchInterval: intervalMs });
}
