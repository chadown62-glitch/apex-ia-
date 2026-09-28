/**
 * Flux INCRÉMENTAL d'un journal append-only (curseur `seq`) : chaque interrogation ne demande que
 * les événements postérieurs au dernier reçu et les ajoute. Même transport que `usePolling`
 * (HTTP) ; aucun événement n'est fabriqué ici — seuls ceux renvoyés par le backend sont gardés.
 */

import { useEffect, useRef, useState } from "react";

import type { DataEnvelope } from "../api/types";

export interface EventStream<E> {
  events: E[];
  status: DataEnvelope<unknown>["status"] | "loading";
  detail: string | null;
}

export function useEventStream<E extends { seq: number }>(
  fetchAfter: (after: number) => Promise<DataEnvelope<E[]>>,
  intervalMs: number,
): EventStream<E> {
  const [state, setState] = useState<EventStream<E>>({ events: [], status: "loading", detail: null });
  const cursor = useRef(-1); // le journal numérote à partir de 0

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const envelope = await fetchAfter(cursor.current);
        if (cancelled) return;
        const fresh = (envelope.data ?? []).filter((e) => e.seq > cursor.current);
        if (fresh.length > 0) cursor.current = fresh[fresh.length - 1].seq;
        setState((prev) => ({
          events: fresh.length > 0 ? [...prev.events, ...fresh] : prev.events,
          status: envelope.status,
          detail: envelope.detail ?? null,
        }));
      } catch (error) {
        if (!cancelled) setState((prev) => ({ ...prev, status: "unknown", detail: String(error) }));
      }
    }
    void poll();
    const timer = setInterval(() => void poll(), intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [fetchAfter, intervalMs]);

  return state;
}
