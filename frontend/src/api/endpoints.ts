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
  CatalogModel,
  ConfiguredAgent,
  ConfiguredAgentCreateInput,
  ConfiguredAgentUpdateInput,
  Conversation,
  ConversationCreateInput,
  DataEnvelope,
  Gate,
  Health,
  Message,
  Mission,
  MissionCreateInput,
  Project,
  ProjectCreateInput,
  ProviderInfo,
  Registry,
  RunAgentExecutionInput,
  SendMessageInput,
  SendMessageResult,
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
