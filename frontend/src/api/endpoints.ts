/**
 * Un point d'entrée typé par endpoint réellement disponible. Ajouter un
 * futur endpoint (ex. /learning) = ajouter une fonction ici, jamais
 * modifier un composant existant.
 */

import { fetchEnvelope } from "./client";
import type {
  Agent,
  ApexEvent,
  AuditLogEntry,
  DataEnvelope,
  Gate,
  Health,
  Mission,
  MissionCreateInput,
  Registry,
} from "./types";

export function getHealth(): Promise<DataEnvelope<Health>> {
  return fetchEnvelope<Health>("/health");
}

export function getGate(): Promise<DataEnvelope<Gate>> {
  return fetchEnvelope<Gate>("/gate");
}

export function getRegistry(): Promise<DataEnvelope<Registry>> {
  return fetchEnvelope<Registry>("/registry");
}

export function getEvents(limit = 100): Promise<DataEnvelope<ApexEvent[]>> {
  return fetchEnvelope<ApexEvent[]>(`/events?limit=${limit}`);
}

export function getAuditLog(limit = 100): Promise<DataEnvelope<AuditLogEntry[]>> {
  return fetchEnvelope<AuditLogEntry[]>(`/audit?limit=${limit}`);
}

export function listAgents(): Promise<DataEnvelope<Agent[]>> {
  return fetchEnvelope<Agent[]>("/agents");
}

export function getAgent(cloneId: string): Promise<DataEnvelope<Agent>> {
  return fetchEnvelope<Agent>(`/agents/${encodeURIComponent(cloneId)}`);
}

export function listMissions(): Promise<DataEnvelope<Mission[]>> {
  return fetchEnvelope<Mission[]>("/missions");
}

export function getMission(missionId: string): Promise<DataEnvelope<Mission>> {
  return fetchEnvelope<Mission>(`/missions/${encodeURIComponent(missionId)}`);
}

export function createMission(input: MissionCreateInput): Promise<DataEnvelope<Mission>> {
  return fetchEnvelope<Mission>("/missions", { method: "POST", body: JSON.stringify(input) });
}

export function cancelMission(missionId: string): Promise<DataEnvelope<Mission>> {
  return fetchEnvelope<Mission>(`/missions/${encodeURIComponent(missionId)}/cancel`, { method: "POST" });
}

export function validateMission(missionId: string): Promise<DataEnvelope<Mission>> {
  return fetchEnvelope<Mission>(`/missions/${encodeURIComponent(missionId)}/validate`, { method: "POST" });
}

export function rejectMission(missionId: string, reason?: string): Promise<DataEnvelope<Mission>> {
  return fetchEnvelope<Mission>(`/missions/${encodeURIComponent(missionId)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}
