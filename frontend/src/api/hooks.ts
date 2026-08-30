/**
 * Un hook par endpoint, tous construits sur `usePolling` (voir
 * src/realtime/usePolling.ts) — le seul point qui connaît le mécanisme de
 * transport réel (aujourd'hui : HTTP polling via React Query).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { usePolling } from "../realtime/usePolling";
import {
  cancelMission,
  createMission,
  getAgent,
  getAuditLog,
  getEvents,
  getGate,
  getHealth,
  getMission,
  getRegistry,
  listAgents,
  listMissions,
  rejectMission,
  validateMission,
} from "./endpoints";
import type { MissionCreateInput } from "./types";

const REFRESH_MS = 10_000;

export function useHealth() {
  return usePolling(["health"], getHealth, REFRESH_MS);
}

export function useGate() {
  return usePolling(["gate"], getGate, 60_000);
}

export function useRegistry() {
  return useQuery({ queryKey: ["registry"], queryFn: getRegistry, staleTime: 60_000 });
}

export function useEvents(limit = 100) {
  return usePolling(["events", limit], () => getEvents(limit), REFRESH_MS);
}

export function useAuditLog(limit = 100) {
  return usePolling(["audit", limit], () => getAuditLog(limit), REFRESH_MS);
}

export function useAgents() {
  return usePolling(["agents"], listAgents, REFRESH_MS);
}

export function useAgent(cloneId: string | undefined) {
  return useQuery({
    queryKey: ["agents", cloneId],
    queryFn: () => getAgent(cloneId as string),
    enabled: Boolean(cloneId),
    refetchInterval: REFRESH_MS,
  });
}

// -- missions --------------------------------------------------------------
// Mission Engine (apex.missions.service) : create/list/get/cancel sont
// réellement câblés sur PostgreSQL. validate/reject exigent l'état REVIEW,
// atteignable seulement par MissionService.mark_running/mark_review — aucune
// route API ni déclenchement automatique ne les appelle encore (voir
// apex/missions/service.py). Une mutation ici représente donc une action
// RÉELLE quand elle réussit ; un échec (ex. transition interdite) revient en
// DataEnvelope status="unknown", jamais masqué.

export function useMissions() {
  return usePolling(["missions"], listMissions, REFRESH_MS);
}

export function useMission(missionId: string | undefined) {
  return useQuery({
    queryKey: ["missions", missionId],
    queryFn: () => getMission(missionId as string),
    enabled: Boolean(missionId),
    refetchInterval: REFRESH_MS,
  });
}

export function useCreateMission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: MissionCreateInput) => createMission(input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["missions"] }),
  });
}

export function useCancelMission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (missionId: string) => cancelMission(missionId),
    onSuccess: (_, missionId) => {
      queryClient.invalidateQueries({ queryKey: ["missions"] });
      queryClient.invalidateQueries({ queryKey: ["missions", missionId] });
    },
  });
}

export function useValidateMission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (missionId: string) => validateMission(missionId),
    onSuccess: (_, missionId) => {
      queryClient.invalidateQueries({ queryKey: ["missions"] });
      queryClient.invalidateQueries({ queryKey: ["missions", missionId] });
    },
  });
}

export function useRejectMission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ missionId, reason }: { missionId: string; reason?: string }) => rejectMission(missionId, reason),
    onSuccess: (_, { missionId }) => {
      queryClient.invalidateQueries({ queryKey: ["missions"] });
      queryClient.invalidateQueries({ queryKey: ["missions", missionId] });
    },
  });
}
