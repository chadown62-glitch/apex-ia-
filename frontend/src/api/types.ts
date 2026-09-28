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

/**
 * Historique réel d'exécution pour un modèle — passe « Routing & Execution
 * Observability Hardening » (N+18), projection directe d'`apex.models.
 * catalog.PerformanceHistory`. `usage_count === 0` signifie « aucune
 * AgentExecution réelle connue », jamais un défaut masqué.
 */
export interface ModelHistory {
  usage_count: number;
  success_count: number;
  failure_count: number;
  last_failure_reason: string | null;
  failure_reasons: Record<string, number>;
}

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
  history: ModelHistory;
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

// -- évaluation / apprentissage (passe « API Évaluation/Apprentissage » +
// « Frontend API Integration ») — miroir de apex/api/schemas.py -----------

export type TaskSplit = "train" | "heldout";

export interface Task {
  id: string;
  split: TaskSplit;
  specialty: string;
  title: string;
  description: string;
  command: string[];
  dataset_ref: string | null;
  created_by: string;
  expected_answer: string | null;
  created_at: string;
}

export type OracleVerdict = "pass" | "fail" | "error" | "timeout" | "unknown";

export interface EvalRun {
  id: string;
  task_id: string;
  method: string;
  oracle_verdict: OracleVerdict;
  oracle_detail: string | null;
  exit_code: number | null;
  elapsed_s: number | null;
  facts: Record<string, unknown>;
  created_by: string;
  clone_id: string | null;
  sandbox_status: string | null;
  model_id: string | null;
  seed: number | null;
  dataset_ref: string | null;
  experiment_ref: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export type WeaknessCategory =
  | "insufficient_data"
  | "no_weakness"
  | "isolated_failure"
  | "repeated_weakness"
  | "statistically_credible_weakness";

export interface WeaknessReport {
  category: WeaknessCategory;
  pass_count: number;
  fail_count: number;
  error_count: number;
  timeout_count: number;
  unknown_count: number;
  failure_rate: number | null;
  evidence_run_ids: string[];
  reason: string;
}

export type SkillStatus = "proposed" | "acquired" | "rejected" | "superseded";

export interface Skill {
  id: string;
  name: string;
  specialty: string;
  description: string;
  status: SkillStatus;
  prerequisite_skill_ids: string[];
  evidence_run_ids: string[];
  confidence: number | null;
  supersedes_id: string | null;
  created_by: string;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
}

export type CalibrationStatus = "insufficient_data" | "calibrated";

export interface CalibratedConfidence {
  status: CalibrationStatus;
  sample_size: number;
  observed_success_rate: number | null;
  calibrated_point_estimate: number | null;
  calibrated_lower_bound: number | null;
  calibrated_upper_bound: number | null;
  declared_confidence: number | null;
  calibration_gap: number | null;
  reason: string;
}

export interface ResourceCost {
  elapsed_s: Measurement<number>;
  cpu_time_s: Measurement<number>;
  memory_peak_mb: Measurement<number>;
  token_count: Measurement<number>;
  experiment_count: number;
}

export interface SelfModelSpecialty {
  specialty: string;
  acquired_skills: Skill[];
  proposed_skills: Skill[];
  weakness_report: WeaknessReport;
  calibrated_confidence: CalibratedConfidence;
  eval_run_count: number;
  methods_used: string[];
  genome_refs_used: string[];
  cost: ResourceCost;
  last_skill_validated_at: string | null;
}

/** Champs EXACTS de `apex.api.schemas.ExperimentCycleCreateIn` — aucun champ
 * d'identité/sujet ici : le backend force `Subject.HUMAN` lui-même, jamais
 * transmis par l'appelant (voir apex/api/routes/evals.py). */
export interface ExperimentCycleCreateInput {
  specialty: string;
  current_level: number;
  clone_id: string;
  method?: string;
  split?: string;
}

export interface CurriculumDecision {
  current_level: number;
  recommended_level: number;
  direction: string;
  reason: string;
}

export interface StrategyDecision {
  specialty: string;
  mode: string | null;
  level: number;
  curriculum_decision: CurriculumDecision;
  reason: string;
}

export interface ExperimentCycle {
  decision: StrategyDecision;
  task: Task;
  eval_run: EvalRun;
  weakness_before: WeaknessReport;
  weakness_after: WeaknessReport;
}

/** Champs EXACTS de `apex.api.schemas.LearningCycleStartIn` — aucun champ
 * d'identité/sujet ici, même discipline qu'`ExperimentCycleCreateInput` : le
 * backend force `Subject.HUMAN` dans `AutonomousLearningService.start_cycle`,
 * qui n'accepte d'ailleurs même pas ce paramètre (voir apex/metalearner/
 * autonomy.py). `clone_id` reste optionnel ici (contrairement à
 * `ExperimentCycleCreateInput`) : un clone est auto-provisionné si omis. */
export interface LearningCycleStartInput {
  specialty: string;
  current_level?: number;
  method?: string;
  split?: string;
  clone_id?: string | null;
}

export type LearningCycleState =
  | "idle"
  | "running"
  | "completed"
  | "failed"
  | "cancel_requested"
  | "cancelled";

export interface LearningCycleStatus {
  state: LearningCycleState;
  cycle_id: string | null;
  specialty: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  eval_run_id: string | null;
  task_id: string | null;
  oracle_verdict: string | null;
  /** Toujours PROPOSED, jamais promu par ce chemin — voir
   * apex/metalearner/feedback.py. `null` = aucun candidat pour ce cycle. */
  skill_candidate_id: string | null;
  genome_candidate_id: string | null;
}

export interface LearningCycleHistoryEntry {
  cycle_id: string;
  specialty: string;
  state: LearningCycleState;
  eval_run_id: string | null;
  task_id: string | null;
  oracle_verdict: string | null;
  error: string | null;
  completed_at: string;
  skill_candidate_id: string | null;
  genome_candidate_id: string | null;
}

export interface AutonomousLearningStatus {
  enabled: boolean;
  scheduler_running: boolean;
  interval_s: number;
  cycle: LearningCycleStatus;
}

/** Étiquette de bookkeeping interne à la population — jamais une décision
 * de promotion Genome/Skill (voir apex/population/service.py). */
export type PopulationMemberStatus =
  | "experimental"
  | "elite"
  | "champion"
  | "hibernating"
  | "retired";

export interface PopulationMember {
  id: string;
  specialty: string;
  generation: number;
  clone_id: string;
  genome_version_id: string;
  parent_member_id: string | null;
  status: PopulationMemberStatus;
  fitness: number | null;
  fitness_components: Record<string, unknown> | null;
  skill_candidate_id: string | null;
  genome_candidate_id: string | null;
  created_at: string;
}

export interface Population {
  specialty: string;
  latest_generation: number;
  champion: PopulationMember | null;
  elite: PopulationMember[];
  members: PopulationMember[];
}

export interface EvolutionGeneration {
  specialty: string;
  generation: number;
  members: PopulationMember[];
  champion: PopulationMember | null;
  elite: PopulationMember[];
  /** Un membre en échec n'empêche jamais les autres (§18) — mais la raison
   * reste observable ici, jamais silencieuse. */
  failures: string[];
}

// -- design engine (N+17, apex.design) ------------------------------------
// Miroir de apex/api/schemas.py::DesignPackageOut et ses sous-objets.
// `DesignPipelineState`/`DesignStep`/`DesignStepState` restent des `string`
// ici plutôt que des unions littérales : le backend (apex.design.states)
// est la seule source de vérité fermée, ce fichier n'en duplique jamais
// l'énumération exacte pour ne jamais désynchroniser silencieusement.

export interface DesignRequest {
  id: string;
  text: string;
  requested_by: string;
  created_at: string;
}

export interface DesignStepRecord {
  step: string;
  state: string;
  input_summary: string;
  output_summary: string;
  provenance: string;
  logs: string[];
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_s: number | null;
}

export interface DesignBrief {
  design_id: string;
  summary: string;
  goals: string[];
  constraints: string[];
  audience: string;
  style_keywords: string[];
  source_request: string;
  provenance: string;
}

export interface ResearchResult {
  source: string;
  title: string;
  url: string | null;
  excerpt: string;
  relevance: number;
  timestamp: string;
  provenance: string;
}

export interface DesignConcept {
  id: string;
  design_id: string;
  version: number;
  title: string;
  narrative: string;
  key_elements: string[];
  based_on_research: number[];
  provenance: string;
}

export interface VisualPrompt {
  id: string;
  concept_id: string;
  variant: number;
  subject: string;
  environment: string;
  composition: string;
  architecture_design: string;
  materials: string;
  colors: string;
  light: string;
  camera: string;
  style: string;
  constraints: string;
  elements_to_avoid: string;
  rendered: string;
}

/** `state`: "completed" (succès réel) | "unavailable" (aucun backend visuel
 * connecté — le cas systématique dans ce dépôt) | "failed" (backend
 * présent mais l'appel a échoué). Jamais un 4e état fabriqué côté client. */
export interface VisualResult {
  id: string;
  prompt_id: string;
  state: string;
  provider: string;
  detail: string;
  artifact_content_hash: string | null;
}

export interface DesignArtifactRef {
  content_hash: string;
  kind: string;
  created_at: string;
}

export interface DesignValidation {
  design_id: string;
  passed: boolean;
  checks: string[];
  failures: string[];
  validated_at: string;
}

export interface DesignPackage {
  design_id: string;
  request: DesignRequest;
  state: string;
  steps: DesignStepRecord[];
  brief: DesignBrief | null;
  research: ResearchResult[];
  concepts: DesignConcept[];
  visual_prompts: VisualPrompt[];
  visual_results: VisualResult[];
  validation: DesignValidation | null;
  artifacts: DesignArtifactRef[];
  error: string | null;
  created_at: string;
  completed_at: string | null;
  ready: boolean;
}

export interface DesignSubmitInput {
  request: string;
}

// -- cerveau natif (GET /brain) : lecture des fichiers du moteur, jamais simulée -----------

export interface NotAvailable {
  status: "not_available";
  reason: string;
}

export interface BrainEvent {
  seq: number;
  at: string;
  kind: "loop" | "experiment";
  event: string;
  experiment_id: string | null;
  label: string;
  summary: string;
  target?: string;
  rationale?: string[];
  regressions?: { group: string; suite: string; before: number; after: number; gain_ci: [number, number]; tolerance: number }[];
}

export interface BrainChampion {
  status: "real";
  name: string;
  index: number;
  kind: string;
  artifact: string | null;
  at: string | null;
  attested: boolean;
  attestation_problem: string | null;
  reports: Record<string, number>;
  metrics: Record<string, string>;
  versions: number;
  promotions: number;
}

export interface BrainProtection {
  group: string;
  suites: string[];
  anchor_index: number;
  anchor_name: string;
  anchor_values: Record<string, number>;
}

export interface BrainTelemetry {
  t: number;
  phase: string;
  step?: number;
  of?: number;
  loss?: number;
  lr?: number;
  run?: string;
  [key: string]: unknown;
}

export interface BrainCurrent {
  experiment_id: string;
  journal_status: string;
  phase: string | null;
  phase_at: number | null;
  progress: BrainTelemetry | null;
  telemetry: BrainTelemetry[];
}

export interface BrainExperiment {
  experiment_id: string;
  status: string;
  finished: boolean;
  strategy: string | null;
  modification: string | null;
  target: string | null;
  target_before: number | null;
  target_after: number | null;
  target_verdict: string | null;
  decision: string | null;
  promoted: boolean;
  cause: string | null;
  forgotten: string[];
  reasons: string[];
  started_at: string | null;
  ended_at: string | null;
}

export interface BrainLoop {
  status: "real" | "not_available";
  state?: "running" | "idle";
  pid?: number | null;
  reason?: string;
  last_event: BrainEvent | null;
  resources?: { status: "real"; rss_mb: number | null; cpu_seconds: number; loadavg: string[] } | NotAvailable;
}

export interface BrainWorld {
  status: "real";
  fingerprint: string;
  parent: string | null;
  capabilities: string[];
  domains: string[];
  architecture: Record<string, number | null>;
  train_documents: number;
}

export interface BrainState {
  status: "real";
  workdir: string;
  world: BrainWorld | NotAvailable;
  loop: BrainLoop;
  champion: BrainChampion | NotAvailable;
  protections: BrainProtection[] | NotAvailable;
  current: BrainCurrent | null;
  experiments: BrainExperiment[];
}
