import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CatalogModel, Conversation, DataEnvelope, Message } from "../api/types";
import { ChatPage } from "./ChatPage";

function makeConversation(overrides: Partial<Conversation>): Conversation {
  return {
    id: "c-1",
    title: "Une conversation",
    project_id: null,
    created_by: "human",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPage />
    </QueryClientProvider>,
  );
}

function mockFetchRouter(handlers: Record<string, (init?: RequestInit) => unknown>) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace(/^.*\/api\/v1/, "");
    for (const [pattern, handler] of Object.entries(handlers)) {
      if (path === pattern || path.startsWith(pattern)) {
        return { ok: true, status: 200, json: async () => handler(init) };
      }
    }
    return { ok: true, status: 200, json: async () => ({ status: "unknown", data: null, detail: "no handler" }) };
  }) as unknown as typeof fetch;
}

describe("ChatPage", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("auto-selects the first real conversation on load", async () => {
    const conv = makeConversation({ id: "c-1", title: "Debug d'un test" });
    globalThis.fetch = mockFetchRouter({
      "/conversations": () => ({ status: "real", data: [conv], detail: null } satisfies DataEnvelope<Conversation[]>),
      "/conversations/c-1/messages": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Message[]>),
      "/models": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<CatalogModel[]>),
    });

    renderPage();

    await waitFor(() => expect(screen.getByPlaceholderText("Écrire un message…")).toBeInTheDocument());
    expect(document.querySelector(".chat-layout")).toHaveAttribute("data-has-selection", "true");
  });

  it("the mobile back button clears the selection and it stays cleared — never re-auto-selected", async () => {
    const conv = makeConversation({ id: "c-1", title: "Debug d'un test" });
    globalThis.fetch = mockFetchRouter({
      "/conversations": () => ({ status: "real", data: [conv], detail: null } satisfies DataEnvelope<Conversation[]>),
      "/conversations/c-1/messages": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Message[]>),
      "/models": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<CatalogModel[]>),
    });

    renderPage();

    const backButton = await screen.findByRole("button", { name: /retour aux conversations/i });
    fireEvent.click(backButton);

    await waitFor(() => expect(document.querySelector(".chat-layout")).toHaveAttribute("data-has-selection", "false"));
    // Le bug corrigé : l'effet d'auto-sélection ne doit JAMAIS re-déclencher
    // après un retour explicite à la liste — sinon "Retour" serait un no-op.
    expect(screen.queryByRole("button", { name: /retour aux conversations/i })).not.toBeInTheDocument();
    expect(screen.getByText(/sélectionnez une conversation/i)).toBeInTheDocument();
  });

  it("clicking a conversation from the list restores the conversation view", async () => {
    const conv = makeConversation({ id: "c-1", title: "Debug d'un test" });
    globalThis.fetch = mockFetchRouter({
      "/conversations": () => ({ status: "real", data: [conv], detail: null } satisfies DataEnvelope<Conversation[]>),
      "/conversations/c-1/messages": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<Message[]>),
      "/models": () => ({ status: "real", data: [], detail: null } satisfies DataEnvelope<CatalogModel[]>),
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /retour aux conversations/i }));
    await waitFor(() => expect(document.querySelector(".chat-layout")).toHaveAttribute("data-has-selection", "false"));

    fireEvent.click(screen.getByText("Debug d'un test"));

    await waitFor(() => expect(document.querySelector(".chat-layout")).toHaveAttribute("data-has-selection", "true"));
    expect(screen.getByPlaceholderText("Écrire un message…")).toBeInTheDocument();
  });

  it("shows the memories really used for a reply and sends the memory choice", async () => {
    const conv = makeConversation({ id: "c-1", title: "Projet" });
    const reply: Message = {
      id: "m-2", conversation_id: "c-1", role: "assistant", content: "Zéphyr.", model_id: "qwen", provider_id: "p",
      status: "real", error: null, created_at: "2026-01-01T00:00:01Z",
    } as Message;
    const calls: Array<{ url: string; body?: string }> = [];
    globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string | undefined });
      const path = url.replace(/^.*\/api\/v1/, "");
      const env = (data: unknown) => ({ ok: true, status: 200, json: async () => ({ status: "real", data, detail: null }) });
      if (path === "/conversations/c-1/recalls") return env({ "m-2": [{ id: "r1", source: "conversation:c-0:m-1", score: 1.2, category: "conversation" }] });
      if (path === "/conversations/c-1/messages" && init?.method === "POST") return env({ user_message: reply, assistant_message: reply, memory: [] });
      if (path === "/conversations/c-1/messages") return env([reply]);
      if (path.startsWith("/conversations")) return env([conv]);
      return env([]);
    }) as unknown as typeof fetch;

    renderPage();
    expect(await screen.findByLabelText("Souvenirs utilisés")).toHaveTextContent("conversation · conversation:c-0:m-1");
    fireEvent.click(screen.getByLabelText("Mémoire"));
    fireEvent.change(screen.getByPlaceholderText("Écrire un message…"), { target: { value: "Et alors ?" } });
    fireEvent.click(screen.getByText("Envoyer"));
    await waitFor(() => expect(calls.some((c) => c.body?.includes('"use_memory":false'))).toBe(true));
  });
});
