/**
 * Un point d'entrée typé par endpoint réellement disponible. Ajouter un
 * futur endpoint (ex. /learning) = ajouter une fonction ici, jamais
 * modifier un composant existant.
 */

import { fetchEnvelope } from "./client";
import type {
  Agent,
  AgentExecution,
  AgentTeam,
  AgentTeamCreateInput,
  AgentTemplate,
  AgentTemplateCreateInput,
  ApexEvent,
  AuditLogEntry,
  AutonomousLearningStatus,
  CatalogModel,
  ConfiguredAgent,
  ConfiguredAgentCreateInput,
  ConfiguredAgentUpdateInput,
  Conversation,
  ConversationCreateInput,
  DataEnvelope,
  EvalRun,
  EvolutionGeneration,
  ExperimentCycle,
  ExperimentCycleCreateInput,
  Gate,
  Health,
  LearningCycleHistoryEntry,
  LearningCycleStartInput,
  LearningCycleStatus,
  Message,
  Mission,
  MissionCreateInput,
  Population,
  Project,
  ProjectCreateInput,
  ProviderInfo,
  Registry,
  RunAgentExecutionInput,
  SelfModelSpecialty,
  SendMessageInput,
  SendMessageResult,
  Task,
  WeaknessReport,
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

export function launchMission(missionId: string): Promise<DataEnvelope<Mission>> {
  return fetchEnvelope<Mission>(`/missions/${encodeURIComponent(missionId)}/launch`, { method: "POST" });
}

// -- projects ------------------------------------------------------------------

export function listProjects(): Promise<DataEnvelope<Project[]>> {
  return fetchEnvelope<Project[]>("/projects");
}

export function createProject(input: ProjectCreateInput): Promise<DataEnvelope<Project>> {
  return fetchEnvelope<Project>("/projects", { method: "POST", body: JSON.stringify(input) });
}

// -- conversations / messages ---------------------------------------------------

export function listConversations(): Promise<DataEnvelope<Conversation[]>> {
  return fetchEnvelope<Conversation[]>("/conversations");
}

export function getConversation(conversationId: string): Promise<DataEnvelope<Conversation>> {
  return fetchEnvelope<Conversation>(`/conversations/${encodeURIComponent(conversationId)}`);
}

export function createConversation(input: ConversationCreateInput): Promise<DataEnvelope<Conversation>> {
  return fetchEnvelope<Conversation>("/conversations", { method: "POST", body: JSON.stringify(input) });
}

export function renameConversation(conversationId: string, title: string): Promise<DataEnvelope<Conversation>> {
  return fetchEnvelope<Conversation>(`/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteConversation(conversationId: string): Promise<DataEnvelope<boolean>> {
  return fetchEnvelope<boolean>(`/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

export function listMessages(conversationId: string): Promise<DataEnvelope<Message[]>> {
  return fetchEnvelope<Message[]>(`/conversations/${encodeURIComponent(conversationId)}/messages`);
}

export function sendMessage(
  conversationId: string,
  input: SendMessageInput,
): Promise<DataEnvelope<SendMessageResult>> {
  return fetchEnvelope<SendMessageResult>(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// -- agent templates -------------------------------------------------------------

export function listAgentTemplates(): Promise<DataEnvelope<AgentTemplate[]>> {
  return fetchEnvelope<AgentTemplate[]>("/agent-templates");
}

export function createAgentTemplate(input: AgentTemplateCreateInput): Promise<DataEnvelope<AgentTemplate>> {
  return fetchEnvelope<AgentTemplate>("/agent-templates", { method: "POST", body: JSON.stringify(input) });
}

export function deleteAgentTemplate(templateId: string): Promise<DataEnvelope<boolean>> {
  return fetchEnvelope<boolean>(`/agent-templates/${encodeURIComponent(templateId)}`, { method: "DELETE" });
}

// -- model / provider catalog ------------------------------------------------

export function listCatalogModels(): Promise<DataEnvelope<CatalogModel[]>> {
  return fetchEnvelope<CatalogModel[]>("/models");
}

export function listProviders(): Promise<DataEnvelope<ProviderInfo[]>> {
  return fetchEnvelope<ProviderInfo[]>("/providers");
}

export function discoverModels(): Promise<DataEnvelope<CatalogModel[]>> {
  return fetchEnvelope<CatalogModel[]>("/models/discover", { method: "POST" });
}

// -- configured agents (passe « Agents V1 ») ----------------------------------

export function listConfiguredAgents(): Promise<DataEnvelope<ConfiguredAgent[]>> {
  return fetchEnvelope<ConfiguredAgent[]>("/configured-agents");
}

export function getConfiguredAgent(agentId: string): Promise<DataEnvelope<ConfiguredAgent>> {
  return fetchEnvelope<ConfiguredAgent>(`/configured-agents/${encodeURIComponent(agentId)}`);
}

export function createConfiguredAgent(input: ConfiguredAgentCreateInput): Promise<DataEnvelope<ConfiguredAgent>> {
  return fetchEnvelope<ConfiguredAgent>("/configured-agents", { method: "POST", body: JSON.stringify(input) });
}

export function updateConfiguredAgent(
  agentId: string,
  input: ConfiguredAgentUpdateInput,
): Promise<DataEnvelope<ConfiguredAgent>> {
  return fetchEnvelope<ConfiguredAgent>(`/configured-agents/${encodeURIComponent(agentId)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteConfiguredAgent(agentId: string): Promise<DataEnvelope<boolean>> {
  return fetchEnvelope<boolean>(`/configured-agents/${encodeURIComponent(agentId)}`, { method: "DELETE" });
}

export function listAgentExecutions(agentId: string): Promise<DataEnvelope<AgentExecution[]>> {
  return fetchEnvelope<AgentExecution[]>(`/configured-agents/${encodeURIComponent(agentId)}/executions`);
}

export function listMissionExecutions(missionId: string): Promise<DataEnvelope<AgentExecution[]>> {
  return fetchEnvelope<AgentExecution[]>(`/missions/${encodeURIComponent(missionId)}/executions`);
}

export function runMissionViaAgent(
  missionId: string,
  input: RunAgentExecutionInput = {},
): Promise<DataEnvelope<AgentExecution>> {
  return fetchEnvelope<AgentExecution>(`/missions/${encodeURIComponent(missionId)}/run`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listAgentTeams(): Promise<DataEnvelope<AgentTeam[]>> {
  return fetchEnvelope<AgentTeam[]>("/agent-teams");
}

export function createAgentTeam(input: AgentTeamCreateInput): Promise<DataEnvelope<AgentTeam>> {
  return fetchEnvelope<AgentTeam>("/agent-teams", { method: "POST", body: JSON.stringify(input) });
}

// -- évaluation / apprentissage (apex/api/routes/evals.py) -------------------
// `getTask`/`getEvalRun` peuvent lever `ApiError(404)` (ressource absente) ;
// `getWeakness`/`getSelfModel`/`createExperimentCycle` peuvent lever
// `ApiError(422)` (spécialité/entrée invalide) — voir client.ts. Ce ne sont
// jamais des DataEnvelope "unknown" : l'appelant les distingue via
// `error.status` (voir hooks.ts / les pages Skills/Weaknesses/Experiment Lab).

export function listTasks(params?: { specialty?: string; split?: string }): Promise<DataEnvelope<Task[]>> {
  const query = new URLSearchParams();
  if (params?.specialty) query.set("specialty", params.specialty);
  if (params?.split) query.set("split", params.split);
  const qs = query.toString();
  return fetchEnvelope<Task[]>(`/tasks${qs ? `?${qs}` : ""}`);
}

export function getTask(taskId: string): Promise<DataEnvelope<Task>> {
  return fetchEnvelope<Task>(`/tasks/${encodeURIComponent(taskId)}`);
}

export function listEvalRuns(params?: { taskId?: string; cloneId?: string }): Promise<DataEnvelope<EvalRun[]>> {
  const query = new URLSearchParams();
  if (params?.taskId) query.set("task_id", params.taskId);
  if (params?.cloneId) query.set("clone_id", params.cloneId);
  const qs = query.toString();
  return fetchEnvelope<EvalRun[]>(`/eval-runs${qs ? `?${qs}` : ""}`);
}

export function getEvalRun(evalRunId: string): Promise<DataEnvelope<EvalRun>> {
  return fetchEnvelope<EvalRun>(`/eval-runs/${encodeURIComponent(evalRunId)}`);
}

export function getWeakness(specialty: string): Promise<DataEnvelope<WeaknessReport>> {
  return fetchEnvelope<WeaknessReport>(`/weakness/${encodeURIComponent(specialty)}`);
}

export function getSelfModel(specialty: string): Promise<DataEnvelope<SelfModelSpecialty>> {
  return fetchEnvelope<SelfModelSpecialty>(`/self-model/${encodeURIComponent(specialty)}`);
}

export function createExperimentCycle(
  input: ExperimentCycleCreateInput,
): Promise<DataEnvelope<ExperimentCycle>> {
  return fetchEnvelope<ExperimentCycle>("/experiment-cycles", { method: "POST", body: JSON.stringify(input) });
}

// -- Autonomous Learning Trigger ------------------------------------------
// aucun champ d'identité dans `LearningCycleStartInput` : le backend force
// Subject.HUMAN lui-même (voir apex/api/routes/learning.py).

export function getLearningStatus(): Promise<DataEnvelope<AutonomousLearningStatus>> {
  return fetchEnvelope<AutonomousLearningStatus>("/learning/status");
}

export function getLearningHistory(limit?: number): Promise<DataEnvelope<LearningCycleHistoryEntry[]>> {
  const qs = limit ? `?limit=${encodeURIComponent(String(limit))}` : "";
  return fetchEnvelope<LearningCycleHistoryEntry[]>(`/learning/history${qs}`);
}

export function startLearningCycle(
  input: LearningCycleStartInput,
): Promise<DataEnvelope<LearningCycleStatus>> {
  return fetchEnvelope<LearningCycleStatus>("/learning/cycles", { method: "POST", body: JSON.stringify(input) });
}

export function cancelLearningCycle(): Promise<DataEnvelope<LearningCycleStatus>> {
  return fetchEnvelope<LearningCycleStatus>("/learning/cycles/cancel", { method: "POST" });
}

// -- Real Clone Population + Evolution Loop -------------------------------
// aucun champ d'identité dans `evolvePopulation` : le backend force
// Subject.HUMAN/Subject.CORE lui-même (voir apex/api/routes/population.py).

export function getPopulation(specialty: string): Promise<DataEnvelope<Population>> {
  return fetchEnvelope<Population>(`/population/${encodeURIComponent(specialty)}`);
}

export function evolvePopulation(specialty: string): Promise<DataEnvelope<EvolutionGeneration>> {
  return fetchEnvelope<EvolutionGeneration>(`/population/${encodeURIComponent(specialty)}/evolve`, {
    method: "POST",
  });
}
