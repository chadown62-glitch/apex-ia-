/**
 * Outils de TEST uniquement (jamais importés par l'application) : un faux `fetch` routé par méthode + chemin,
 * qui enregistre chaque appel, et un rendu avec routeur + react-query isolés.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { ToastProvider } from "../components/ui/Toast";

export type Handler = (body: unknown, url: URL) => { status?: number; body: unknown };

export interface FakeApi {
  calls: { method: string; path: string; body: unknown }[];
  on: (method: string, path: string | RegExp, handler: Handler | Record<string, unknown> | unknown[]) => void;
}

export function installFakeApi(): FakeApi {
  const routes: { method: string; path: string | RegExp; handler: Handler }[] = [];
  const calls: FakeApi["calls"] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://apex.test");
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: path + url.search, body });
    const route = [...routes].reverse().find((r) => r.method === method && (typeof r.path === "string" ? r.path === path : r.path.test(path)));
    const result = route ? route.handler(body, url) : { status: 200, body: { status: "unknown", data: null, detail: `pas de route de test pour ${method} ${path}` } };
    const status = result.status ?? 200;
    return { ok: status < 400, status, json: async () => result.body } as Response;
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return {
    calls,
    on(method, path, handler) {
      const fn: Handler = typeof handler === "function" ? (handler as Handler) : () => ({ body: { status: "real", data: handler } });
      routes.push({ method, path, handler: fn });
    },
  };
}

export const real = (data: unknown) => ({ body: { status: "real", data } });

export function renderAt(path: string, routes: { path: string; element: ReactElement }[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            {routes.map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
            <Route path="*" element={<div data-testid="elsewhere">ailleurs</div>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

export const MODES = [
  { id: "normal", label: "Normal", summary: "Conversation générale.", tools: [], view: "chat", limits: ["Pas d'accès à Internet."] },
  { id: "code", label: "Code", summary: "Programmation.", tools: ["fs.read", "code.run"], view: "code", limits: [] },
  { id: "design", label: "Design", summary: "Interfaces.", tools: ["color.contrast"], view: "design", limits: ["Pas de génération d'images."] },
  { id: "finance", label: "Finance", summary: "Budgets.", tools: ["finance.loan"], view: "finance", limits: [] },
];

export const SETTINGS = {
  appearance: { theme: "night", animations: true, particles: true, reduce_motion: "system" },
  models: { preferred_model: "auto", temperature: 0.4, max_tokens: 768 },
  runtime: { generation_timeout_s: 900 },
  behavior: { default_mode: "normal", use_memory: true, history_chars: 6000 },
  tools: { workspace_write: true, code_execution: true, max_tool_steps: 6 },
  privacy: { index_conversations: true, attachments_enabled: true },
  resources: { model_precision: "auto", cpu_threads: 0 },
  problems: [],
};

export function conversation(over: object = {}) {
  return {
    id: "c1",
    title: "Budget maison",
    project_id: null,
    mode: "normal",
    created_by: "human",
    created_at: "2026-10-09T08:00:00+00:00",
    updated_at: "2026-10-09T08:05:00+00:00",
    ...over,
  };
}

export function message(over: object = {}) {
  return {
    id: "m1",
    conversation_id: "c1",
    role: "user",
    content: "Bonjour",
    model_id: null,
    provider_id: null,
    status: "real",
    error: null,
    created_at: "2026-10-09T08:00:00+00:00",
    meta: null,
    ...over,
  };
}

export function turn(over: object = {}) {
  return {
    id: "t1",
    conversation_id: "c1",
    user_message_id: "m1",
    assistant_message_id: null,
    mode: "normal",
    status: "running",
    steps: [],
    error: null,
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    finished_at: null,
    ...over,
  };
}
