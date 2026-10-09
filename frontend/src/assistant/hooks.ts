/**
 * Requêtes react-query de l'assistant — clés centralisées pour que chaque mutation invalide exactement ce
 * qu'elle change (barre latérale, projet, conversation). Les tours en cours sont suivis par interrogation
 * (1 s) tant qu'ils sont en file ou en cours, puis plus du tout.
 */

import { useQuery } from "@tanstack/react-query";

import { assistantApi, type Turn } from "./api";

export const keys = {
  modes: ["assistant", "modes"] as const,
  settings: ["assistant", "settings"] as const,
  models: ["assistant", "models"] as const,
  projects: ["assistant", "projects"] as const,
  project: (id: string) => ["assistant", "project", id] as const,
  attachments: (projectId: string) => ["assistant", "attachments", projectId] as const,
  conversations: ["assistant", "conversations"] as const,
  conversation: (id: string) => ["assistant", "conversation", id] as const,
  messages: (id: string) => ["assistant", "messages", id] as const,
  turns: (id: string) => ["assistant", "turns", id] as const,
  workspace: (id: string) => ["assistant", "workspace", id] as const,
};

export const isActive = (turn: Pick<Turn, "status">) => turn.status === "queued" || turn.status === "running";

export function useModes() {
  return useQuery({ queryKey: keys.modes, queryFn: assistantApi.modes, staleTime: Infinity });
}

export function useSettings() {
  // Pas de rechargement au retour sur la fenêtre : il écraserait un brouillon de paramètres en cours d'édition.
  return useQuery({ queryKey: keys.settings, queryFn: assistantApi.settings, refetchOnWindowFocus: false });
}

export function useModels() {
  return useQuery({ queryKey: keys.models, queryFn: assistantApi.models, staleTime: 30_000 });
}

export function useProjects() {
  return useQuery({ queryKey: keys.projects, queryFn: assistantApi.projects });
}

/** Toutes les conversations (triées par activité récente) : la barre latérale les regroupe par projet. */
export function useConversations() {
  return useQuery({ queryKey: keys.conversations, queryFn: () => assistantApi.conversations() });
}

export function useTurns(conversationId: string) {
  return useQuery({
    queryKey: keys.turns(conversationId),
    queryFn: () => assistantApi.turns(conversationId),
    refetchInterval: (query) => ((query.state.data ?? []).some(isActive) ? 1000 : false),
  });
}
