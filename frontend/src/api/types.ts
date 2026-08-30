/**
 * Types miroir de apex/api/schemas.py — AUCUN champ ajouté, AUCUN renommé.
 * Si le backend change de forme, ce fichier doit changer en même temps,
 * jamais l'un sans l'autre.
 */

/** Statut d'une donnée — apex.obs.status.DataStatus, jamais un 4e état ici :
 * "planned" n'existe QUE côté frontend, pour un module entier sans backend
 * (voir src/specialties/types.ts). Une valeur de donnée reste real/simulated/unknown. */
export type DataStatus = "real" | "simulated" | "unknown";

export interface Measurement<T = unknown> {
  value: T | null;
  status: DataStatus;
}

export interface DataEnvelope<T> {
  status: DataStatus;
  data: T | null;
  detail: string | null;
}

// -- health --------------------------------------------------------------

export type ComponentStatus = "ok" | "degraded" | "failed";

export interface HealthComponent {
  component: string;
  status: ComponentStatus;
  detail: string;
  version: string | null;
  fallback_active: boolean;
  latency_ms: number | null;
}

export interface Health {
  status: ComponentStatus;
  components: HealthComponent[];
}

// -- gate ------------------------------------------------------------------

export interface GateCheck {
  check_id: string;
  title: string;
  status: string;
  message: string;
}

export interface Gate {
  gate_status: string;
  phase_2_authorized: boolean;
  machine_label: string | null;
  fingerprint: string;
  measured_on_target_machine: boolean;
  checks: GateCheck[];
}

// -- registry ----------------------------------------------------------------

export type AbstractionStatus = "implemented" | "stub" | "defined" | "planned";

export interface Abstraction {
  name: string;
  purpose: string;
  status: AbstractionStatus;
  phase_defined: number;
  phase_implemented: number;
  fallback: string;
  module: string | null;
  notes: string;
}

export interface Registry {
  summary: Record<string, number>;
  abstractions: Abstraction[];
}

// -- events / audit ----------------------------------------------------------

export interface ApexEvent {
  id: number;
  ts: string;
  level: string;
  logger: string;
  msg: string;
  context: Record<string, unknown>;
}

export interface AuditLogEntry {
  id: number;
  ts: string;
  actor: string;
  action: string;
  target: string | null;
  details: Record<string, unknown>;
}

// -- agents ------------------------------------------------------------------

export type CloneState =
  | "spawning"
  | "ready"
  | "working"
  | "reporting"
  | "terminating"
  | "terminated"
  | "crashed"
  | "recovering"
  | "failed";

export type CloneType =
  | "research"
  | "learning"
  | "developer"
  | "experiment"
  | "critic"
  | "security"
  | "benchmark"
  | "teacher"
  | "integrator";

export interface Agent {
  clone_id: string;
  type: CloneType;
  state: CloneState;
  mission_id: string | null;
  genome_ref: string;
  pool_id: string;
  isolation_level: Measurement<string>;
  created_at: string;
  started_at: string | null;
  terminated_at: string | null;
  elapsed_s: Measurement<number>;
  cpu_time_s: Measurement<number>;
  ram_mb: Measurement<number>;
  gpu: Measurement<number>;
  retries_used: number;
  max_retries: number;
  last_error: string | null;
  available_actions: string[];
}

// -- missions ------------------------------------------------------------

export type MissionState = "draft" | "queued" | "running" | "review" | "completed" | "failed" | "cancelled";

export type MissionPriority = "low" | "normal" | "high" | "critical";

export type ValidationStatus = "validated" | "rejected" | "revision_requested";

export interface Mission {
  id: string;
  title: string;
  description: string;
  specialty: string;
  objective: string;
  priority: MissionPriority;
  constraints: string | null;
  assigned_agent: string | null;
  state: MissionState;
  result: string | null;
  error: string | null;
  validation_status: ValidationStatus | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface MissionCreateInput {
  title: string;
  description?: string;
  specialty: string;
  objective: string;
  priority?: MissionPriority;
  constraints?: string | null;
  assigned_agent?: string | null;
}
