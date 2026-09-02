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
  /** Passe « Agents V1 » — référence un `ConfiguredAgent`, DISTINCT
   * d'`assigned_agent` ci-dessus (un clone OS-level réel). */
  agent_id: string | null;
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
  agent_id?: string | null;
}

// -- configured agents (passe « Agents V1 ») ----------------------------------

export type ConfiguredAgentRole =
  | "coder" | "researcher" | "tester" | "analyst" | "designer" | "security"
  | "evaluator" | "project_manager" | "generalist";

export type Permission = "read_only" | "workspace_write" | "execute" | "network" | "git_read" | "git_write";

export type ToolName = "files" | "terminal" | "tests" | "git-read" | "sandbox" | "memory" | "static-analysis" | "web-research";

/** Vocabulaire fermé, JAMAIS stocké côté backend — toujours dérivé de
 * `enabled` + la dernière exécution réelle. `waiting` reste dans le
 * vocabulaire mais n'est jamais produit aujourd'hui (voir `apex.agent_
 * registry.model.derive_agent_status`). */
export type ConfiguredAgentStatus = "idle" | "queued" | "running" | "waiting" | "completed" | "failed" | "disabled";

export interface ConfiguredAgent {
  id: string;
  name: string;
  description: string;
  role: ConfiguredAgentRole;
  specialty: string | null;
  model_role: string | null;
  required_capabilities: string[];
  tools: ToolName[];
  permissions: Permission[];
  instructions: string | null;
  enabled: boolean;
  status: ConfiguredAgentStatus;
  project_id: string | null;
  template_id: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ConfiguredAgentCreateInput {
  name: string;
  role: ConfiguredAgentRole;
  description?: string;
  specialty?: string | null;
  model_role?: string | null;
  required_capabilities?: string[];
  tools?: ToolName[];
  permissions?: Permission[];
  instructions?: string | null;
  enabled?: boolean;
  project_id?: string | null;
  template_id?: string | null;
}

export interface ConfiguredAgentUpdateInput {
  name?: string;
  description?: string;
  model_role?: string | null;
  required_capabilities?: string[];
  tools?: ToolName[];
  permissions?: Permission[];
  instructions?: string | null;
  enabled?: boolean;
}

export type AgentExecutionStatus = "queued" | "running" | "completed" | "failed";

/** Vocabulaire fermé demandé — seules `success`/`timeout`/
 * `capability_missing`/`provider_error` sont réellement produites
 * aujourd'hui (voir `apex.agent_registry.fallback.classify_outcome`) ; les
 * autres restent déclarées, jamais fabriquées faute de signal réel. */
export type FallbackReason =
  | "success" | "quota_exceeded" | "rate_limited" | "timeout" | "offline"
  | "auth_error" | "capability_missing" | "context_too_long" | "provider_error";

export interface AgentExecution {
  id: string;
  agent_id: string;
  mission_id: string;
  status: AgentExecutionStatus;
  model_id: string | null;
  provider_id: string | null;
  result: string | null;
  error: string | null;
  fallback_reason: FallbackReason | null;
  started_at: string;
  completed_at: string | null;
}

export interface RunAgentExecutionInput {
  prompt?: string | null;
}

export interface AgentTeam {
  id: string;
  name: string;
  description: string;
  agent_ids: string[];
  created_by: string;
  created_at: string;
}

export interface AgentTeamCreateInput {
  name: string;
  description?: string;
  agent_ids?: string[];
}

// -- projects (passe « Première interface utilisable ») ----------------------

export interface Project {
  id: string;
  name: string;
  description: string;
  created_by: string;
  created_at: string;
}

export interface ProjectCreateInput {
  name: string;
  description?: string;
}

// -- conversations / messages (Chat réel) -------------------------------------

export interface Conversation {
  id: string;
  title: string;
  project_id: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationCreateInput {
  title: string;
  project_id?: string | null;
}

export type MessageRole = "user" | "assistant" | "system";
export type MessageStatus = "real" | "error";

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  model_id: string | null;
  provider_id: string | null;
  status: MessageStatus;
  error: string | null;
  created_at: string;
}

export interface SendMessageInput {
  content: string;
  model_role?: string | null;
}

export interface SendMessageResult {
  user_message: Message;
  assistant_message: Message;
}

// -- agent templates (passe « Première interface utilisable ») ---------------

export type AgentRole = "coder" | "researcher" | "tester" | "analyst" | "designer" | "security" | "evaluator";

export interface AgentTemplate {
  id: string;
  name: string;
  role: AgentRole;
  model_role: string | null;
  tools: string[];
  description: string;
  status: "defined";
  created_by: string;
  created_at: string;
}

export interface AgentTemplateCreateInput {
  name: string;
  role: AgentRole;
  description?: string;
  model_role?: string | null;
  tools?: string[];
}

// -- model / provider catalog (apex.models.catalog) ---------------------------

export type CatalogAvailability = "available" | "unavailable" | "unknown" | "disabled";
export type DiscoverySource = "configured" | "discovered" | "verified";

export interface CatalogModel {
  model_id: string;
  display_name: string;
  role: string;
  family: string;
  version: string;
  quantization: string;
  capability_class: string;
  is_local: boolean;
  is_declared_available: boolean;
  capabilities: string[];
  availability: CatalogAvailability;
  discovery: DiscoverySource;
  provider_id: string;
  runtime: string | null;
  served_model: string | null;
  max_context: number | null;
  vram_gb: number | null;
}

export interface ProviderInfo {
  id: string;
  display_name: string;
  runtime: string;
  base_url: string;
  secret_key: string | null;
  local: boolean;
  enabled: boolean;
  model_ids: string[];
}
