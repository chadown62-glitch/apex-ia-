/**
 * Client typé de l'assistant (API `/api/v1/assistant/*`, apex/api/routes/assistant.py) — passe par
 * `fetchEnvelope`, le SEUL point réseau de l'interface (session ouverte depuis le Panel, jamais de jeton ici).
 * Une erreur de l'appelant (404/409/422) lève `ApiError` avec le message du backend ; une panne rend une
 * enveloppe `unknown` convertie ici en erreur explicite — jamais une donnée par défaut inventée.
 */

import { ApiError, fetchEnvelope } from "../api/client";

export type ModeId = "normal" | "code" | "design" | "finance" | "3d";

export interface ModeInfo {
  id: ModeId;
  label: string;
  summary: string;
  tools: string[];
  view: "chat" | "code" | "design" | "finance" | "studio3d";
  limits: string[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  instructions: string;
  created_by: string;
  created_at: string;
  updated_at: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  project_id: string | null;
  mode: ModeId;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ToolStep {
  step: number;
  tool: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  observation: { ok?: boolean; output?: Record<string, unknown> | null; error?: { code: string; message: string } | null; summary?: string };
  duration_s: number;
}

export interface MessageMeta {
  mode?: ModeId;
  turn_id?: string;
  steps?: ToolStep[];
  tokens_prompt?: number | null;
  tokens_completion?: number | null;
  elapsed_s?: number;
  runtime?: { backend?: string; device?: string; dtype?: string; tokens_per_s?: number; loaded_now?: boolean };
  memory?: { id: string; source: string; score: number }[];
  attachments?: string[];
  tools_offered?: string[];
  tools_notes?: string[];
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  model_id: string | null;
  provider_id: string | null;
  status: "real" | "error" | "cancelled";
  error: string | null;
  created_at: string;
  meta: MessageMeta | null;
}

export type TurnStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Turn {
  id: string;
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string | null;
  mode: ModeId;
  status: TurnStatus;
  steps: ToolStep[];
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Attachment {
  id: string;
  project_id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

export interface AppSettings {
  appearance: { theme: "night" | "dune"; animations: boolean; particles: boolean; reduce_motion: "system" | "always" | "never" };
  models: { engine: EngineId; preferred_model: string; temperature: number; max_tokens: number };
  runtime: { generation_timeout_s: number };
  behavior: { default_mode: ModeId; use_memory: boolean; history_chars: number };
  tools: { workspace_write: boolean; code_execution: boolean; max_tool_steps: number };
  privacy: { index_conversations: boolean; attachments_enabled: boolean };
  resources: { model_precision: "auto" | "float32" | "bfloat16"; cpu_threads: number };
  problems: string[];
}

export interface ModelOption {
  id: string;
  runtime: string | null;
  served_model: string | null;
  available: boolean;
  detail: string | null;
  /** Cerveau APEX : vrai pour un Cerveau instruit à converser (génération 2), mesuré dans ses fichiers. */
  conversational?: boolean;
}

export type EngineId = "auto" | "ollama" | "integrated" | "brain";

/** Moteur de réponse et sa disponibilité MESURÉE par le backend (`GET /assistant/engines`). */
export interface EngineInfo {
  id: EngineId;
  label: string;
  description: string;
  experimental: boolean;
  available: boolean;
  detail: string;
  models: ModelOption[];
}

export interface WorkspaceListing {
  scope: "project" | "conversation";
  /** Dossier réel de l'espace de travail sur ce PC. */
  root?: string;
  files: { path: string; size: number }[];
}

async function data<T>(path: string, init?: RequestInit): Promise<T> {
  const envelope = await fetchEnvelope<T>(path, init);
  if (envelope.status !== "real" || envelope.data === null || envelope.data === undefined) {
    throw new ApiError(envelope.detail ?? "réponse sans données", 503);
  }
  return envelope.data;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const assistantApi = {
  modes: () => data<ModeInfo[]>("/assistant/modes"),
  settings: () => data<AppSettings>("/assistant/settings"),
  updateSettings: (changes: Partial<Record<keyof AppSettings, Record<string, unknown>>>) =>
    data<AppSettings>("/assistant/settings", json("PUT", changes)),
  models: () => data<ModelOption[]>("/assistant/models"),
  engines: () => data<EngineInfo[]>("/assistant/engines"),

  projects: () => data<Project[]>("/assistant/projects"),
  project: (id: string) => data<Project>(`/assistant/projects/${encodeURIComponent(id)}`),
  createProject: (body: { name: string; description?: string; instructions?: string }) =>
    data<Project>("/assistant/projects", json("POST", body)),
  updateProject: (id: string, body: Partial<Pick<Project, "name" | "description" | "instructions">>) =>
    data<Project>(`/assistant/projects/${encodeURIComponent(id)}`, json("PATCH", body)),
  deleteProject: (id: string) =>
    data<{ deleted: boolean; conversations: number; messages: number; attachments: number }>(
      `/assistant/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
  attachments: (projectId: string) => data<Attachment[]>(`/assistant/projects/${encodeURIComponent(projectId)}/attachments`),
  addAttachment: (projectId: string, filename: string, contentBase64: string) =>
    data<Attachment>(`/assistant/projects/${encodeURIComponent(projectId)}/attachments`,
      json("POST", { filename, content_base64: contentBase64 })),
  deleteAttachment: (id: string) => data<boolean>(`/assistant/attachments/${encodeURIComponent(id)}`, { method: "DELETE" }),

  conversations: (filter: { projectId?: string; standalone?: boolean } = {}) => {
    const query = filter.projectId ? `?project_id=${encodeURIComponent(filter.projectId)}`
      : filter.standalone ? "?standalone=true" : "";
    return data<Conversation[]>(`/assistant/conversations${query}`);
  },
  createConversation: (body: { title: string; project_id?: string | null; mode: ModeId }) =>
    data<Conversation>("/assistant/conversations", json("POST", body)),
  updateConversation: (id: string, body: { title?: string; mode?: ModeId }) =>
    data<Conversation>(`/assistant/conversations/${encodeURIComponent(id)}`, json("PATCH", body)),
  conversation: (id: string) => data<Conversation>(`/assistant/conversations/${encodeURIComponent(id)}`),
  deleteConversation: (id: string) =>
    data<boolean>(`/assistant/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  messages: (conversationId: string) => data<Message[]>(`/assistant/conversations/${encodeURIComponent(conversationId)}/messages`),
  turns: (conversationId: string) => data<Turn[]>(`/assistant/conversations/${encodeURIComponent(conversationId)}/turns`),
  submitTurn: (conversationId: string, content: string) =>
    data<{ user_message: Message; turn: Turn }>(`/assistant/conversations/${encodeURIComponent(conversationId)}/turns`,
      json("POST", { content })),
  turn: (id: string) => data<Turn>(`/assistant/turns/${encodeURIComponent(id)}`),
  cancelTurn: (id: string) => data<{ cancel_requested: boolean }>(`/assistant/turns/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  workspace: (conversationId: string) => data<WorkspaceListing>(`/assistant/conversations/${encodeURIComponent(conversationId)}/workspace`),
  workspaceFile: (conversationId: string, path: string) =>
    data<{ path: string; size: number; truncated: boolean; content: string }>(
      `/assistant/conversations/${encodeURIComponent(conversationId)}/workspace/file?path=${encodeURIComponent(path)}`),
  /** Octets exacts d'un fichier (aperçu PNG, modèle .glb à télécharger), en base64. */
  workspaceFileBase64: (conversationId: string, path: string) =>
    data<{ path: string; size: number; content_base64: string }>(
      `/assistant/conversations/${encodeURIComponent(conversationId)}/workspace/file?path=${encodeURIComponent(path)}&encoding=base64`),
};

/** Fichier local -> base64 (pièces jointes) ; la taille est vérifiée par le backend après décodage. */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve(url.slice(url.indexOf(",") + 1)); // « data:<type>;base64,<contenu> »
    };
    reader.onerror = () => reject(reader.error ?? new Error(`lecture de « ${file.name} » impossible`));
    reader.readAsDataURL(file);
  });
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
