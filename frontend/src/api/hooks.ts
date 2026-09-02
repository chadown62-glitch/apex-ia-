/**
 * Un hook par endpoint, tous construits sur `usePolling` (voir
 * src/realtime/usePolling.ts) — le seul point qui connaît le mécanisme de
 * transport réel (aujourd'hui : HTTP polling via React Query).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { usePolling } from "../realtime/usePolling";
import {
  cancelMission,
  createAgentTeam,
  createAgentTemplate,
  createConfiguredAgent,
  createConversation,
  createMission,
  createProject,
  deleteAgentTemplate,
  deleteConfiguredAgent,
  deleteConversation,
  discoverModels,
  getAgent,
  getAuditLog,
  getConfiguredAgent,
  getConversation,
  getEvents,
  getGate,
  getHealth,
  getMission,
  getRegistry,
  launchMission,
  listAgentExecutions,
  listAgents,
  listAgentTeams,
  listAgentTemplates,
  listCatalogModels,
  listConfiguredAgents,
  listConversations,
  listMessages,
  listMissionExecutions,
  listMissions,
  listProjects,
  listProviders,
  rejectMission,
  renameConversation,
  runMissionViaAgent,
  sendMessage,
  updateConfiguredAgent,
  validateMission,
} from "./endpoints";
import type {
  AgentTeamCreateInput,
  AgentTemplateCreateInput,
  ConfiguredAgentCreateInput,
  ConfiguredAgentUpdateInput,
  ConversationCreateInput,
  MissionCreateInput,
  ProjectCreateInput,
  RunAgentExecutionInput,
  SendMessageInput,
} from "./types";

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

export function useLaunchMission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (missionId: string) => launchMission(missionId),
    onSuccess: (_, missionId) => {
      queryClient.invalidateQueries({ queryKey: ["missions"] });
      queryClient.invalidateQueries({ queryKey: ["missions", missionId] });
    },
  });
}

// -- projects ------------------------------------------------------------------

export function useProjects() {
  return usePolling(["projects"], listProjects, REFRESH_MS);
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ProjectCreateInput) => createProject(input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] }),
  });
}

// -- conversations / messages (Chat réel) ---------------------------------------

export function useConversations() {
  return usePolling(["conversations"], listConversations, REFRESH_MS);
}

export function useConversation(conversationId: string | undefined) {
  return useQuery({
    queryKey: ["conversations", conversationId],
    queryFn: () => getConversation(conversationId as string),
    enabled: Boolean(conversationId),
    refetchInterval: REFRESH_MS,
  });
}

export function useMessages(conversationId: string | undefined) {
  return useQuery({
    queryKey: ["conversations", conversationId, "messages"],
    queryFn: () => listMessages(conversationId as string),
    enabled: Boolean(conversationId),
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ConversationCreateInput) => createConversation(input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

export function useRenameConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ conversationId, title }: { conversationId: string; title: string }) =>
      renameConversation(conversationId, title),
    onSuccess: (_, { conversationId }) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["conversations", conversationId] });
    },
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) => deleteConversation(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

/** Le tour de Chat réel : UI -> API -> `ConversationService.send_message` ->
 * `apex.routing.select_model` -> `apex.routing.generate_with_fallback` ->
 * `get_default_provider()` -> `OllamaProvider` -> Ollama réel -> réponse
 * réelle. `onMutate`/`onSettled` gèrent l'état "en cours de génération"
 * (voir ChatPage — jamais une simulation de progression, seulement le cycle
 * de vie réel de cette requête HTTP). */
export function useSendMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ conversationId, input }: { conversationId: string; input: SendMessageInput }) =>
      sendMessage(conversationId, input),
    onSuccess: (_, { conversationId }) => {
      queryClient.invalidateQueries({ queryKey: ["conversations", conversationId, "messages"] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

// -- agent templates ---------------------------------------------------------

export function useAgentTemplates() {
  return usePolling(["agent-templates"], listAgentTemplates, REFRESH_MS);
}

export function useCreateAgentTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: AgentTemplateCreateInput) => createAgentTemplate(input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-templates"] }),
  });
}

export function useDeleteAgentTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (templateId: string) => deleteAgentTemplate(templateId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-templates"] }),
  });
}

// -- model / provider catalog ------------------------------------------------
// `/models`/`/providers` sont des vues STATIQUES (aucun appel réseau) — un
// simple `useQuery`, pas de polling. `useDiscoverModels` est la SEULE action
// qui touche réellement le réseau (bouton explicite, jamais automatique).

export function useCatalogModels() {
  return useQuery({ queryKey: ["models"], queryFn: listCatalogModels, staleTime: 60_000 });
}

export function useProviders() {
  return useQuery({ queryKey: ["providers"], queryFn: listProviders, staleTime: 60_000 });
}

export function useDiscoverModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => discoverModels(),
    onSuccess: (envelope) => {
      queryClient.setQueryData(["models"], envelope);
    },
  });
}

// -- configured agents (passe « Agents V1 ») ----------------------------------

export function useConfiguredAgents() {
  return usePolling(["configured-agents"], listConfiguredAgents, REFRESH_MS);
}

export function useConfiguredAgent(agentId: string | undefined) {
  return useQuery({
    queryKey: ["configured-agents", agentId],
    queryFn: () => getConfiguredAgent(agentId as string),
    enabled: Boolean(agentId),
    refetchInterval: REFRESH_MS,
  });
}

export function useAgentExecutions(agentId: string | undefined) {
  return useQuery({
    queryKey: ["configured-agents", agentId, "executions"],
    queryFn: () => listAgentExecutions(agentId as string),
    enabled: Boolean(agentId),
  });
}

export function useMissionExecutions(missionId: string | undefined) {
  return useQuery({
    queryKey: ["missions", missionId, "executions"],
    queryFn: () => listMissionExecutions(missionId as string),
    enabled: Boolean(missionId),
    refetchInterval: REFRESH_MS,
  });
}

export function useCreateConfiguredAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ConfiguredAgentCreateInput) => createConfiguredAgent(input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["configured-agents"] }),
  });
}

export function useUpdateConfiguredAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ agentId, input }: { agentId: string; input: ConfiguredAgentUpdateInput }) =>
      updateConfiguredAgent(agentId, input),
    onSuccess: (_, { agentId }) => {
      queryClient.invalidateQueries({ queryKey: ["configured-agents"] });
      queryClient.invalidateQueries({ queryKey: ["configured-agents", agentId] });
    },
  });
}

export function useDeleteConfiguredAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) => deleteConfiguredAgent(agentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["configured-agents"] }),
  });
}

/** Le tour d'exécution réel : UI -> API `POST /missions/{id}/run` ->
 * `AgentRegistryService.run_execution` -> `select_model` ->
 * `generate_with_fallback` -> provider réel -> réponse réelle. */
export function useRunMissionViaAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ missionId, input }: { missionId: string; input?: RunAgentExecutionInput }) =>
      runMissionViaAgent(missionId, input),
    onSuccess: (_, { missionId }) => {
      queryClient.invalidateQueries({ queryKey: ["missions"] });
      queryClient.invalidateQueries({ queryKey: ["missions", missionId] });
      queryClient.invalidateQueries({ queryKey: ["missions", missionId, "executions"] });
      queryClient.invalidateQueries({ queryKey: ["configured-agents"] });
    },
  });
}

export function useAgentTeams() {
  return usePolling(["agent-teams"], listAgentTeams, REFRESH_MS);
}

export function useCreateAgentTeam() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: AgentTeamCreateInput) => createAgentTeam(input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-teams"] }),
  });
}
