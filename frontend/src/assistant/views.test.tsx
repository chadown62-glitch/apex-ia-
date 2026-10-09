import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { resetAppearanceFromCache } from "../theme/appearance";
import { AssistantSidebar } from "./AssistantSidebar";
import { ConversationView } from "./ConversationView";
import { HomeView } from "./HomeView";
import { FinancePanel, DesignPanel } from "./ModePanels";
import { ProjectView } from "./ProjectView";
import { SettingsView } from "./SettingsView";
import { MODES, SETTINGS, conversation, installFakeApi, message, real, renderAt, turn, type FakeApi } from "./testing";

const originalFetch = globalThis.fetch;
let api: FakeApi;

beforeEach(() => {
  localStorage.clear();
  resetAppearanceFromCache();
  api = installFakeApi();
  api.on("GET", "/assistant/modes", MODES);
  api.on("GET", "/assistant/settings", SETTINGS);
  api.on("GET", "/assistant/models", [{ id: "coder-1.5b", runtime: "transformers", served_model: "q", available: true, detail: null }]);
  api.on("GET", "/assistant/projects", []);
  api.on("GET", "/assistant/conversations", []);
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  localStorage.clear();
  resetAppearanceFromCache();
});

describe("HomeView", () => {
  it("creates the conversation in the chosen mode, submits the first message and opens it", async () => {
    api.on("POST", "/assistant/conversations", (body) => real(conversation({ id: "new1", ...(body as object) })));
    api.on("POST", "/assistant/conversations/new1/turns", () => ({ status: 202, body: { status: "real", data: { user_message: message(), turn: turn() } } }));
    renderAt("/", [
      { path: "/", element: <HomeView /> },
      { path: "/c/:conversationId", element: <div data-testid="opened">ouverte</div> },
    ]);
    fireEvent.click(await screen.findByRole("radio", { name: /finance/i }));
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Mensualité pour 200 000 € sur 20 ans à 3,5 % ?" } });
    fireEvent.click(screen.getByRole("button", { name: "Démarrer la conversation" }));
    expect(await screen.findByTestId("opened")).toBeInTheDocument();
    const create = api.calls.find((c) => c.method === "POST" && c.path === "/assistant/conversations");
    expect(create?.body).toEqual({ title: "Mensualité pour 200 000 € sur 20 ans à 3,5 % ?", project_id: null, mode: "finance" });
    expect(api.calls.find((c) => c.path === "/assistant/conversations/new1/turns")?.body).toEqual({
      content: "Mensualité pour 200 000 € sur 20 ans à 3,5 % ?",
    });
  });

  it("warns before sending when no model is available, without inventing one", async () => {
    api.on("GET", "/assistant/models", [{ id: "coder-1.5b", runtime: "transformers", served_model: "q", available: false, detail: "poids absents" }]);
    renderAt("/", [{ path: "/", element: <HomeView /> }]);
    expect(await screen.findByText("Aucun modèle disponible")).toBeInTheDocument();
    // Le détail mesuré de chaque modèle déclaré reste accessible, replié.
    const details = screen.getByText(/Détails techniques \(1 modèle déclaré\)/).closest("details")!;
    expect(details.querySelector("li")?.textContent).toBe("coder-1.5b : poids absents");
  });

  it("keeps the draft and shows the backend's reason when the conversation cannot be created", async () => {
    api.on("POST", "/assistant/conversations", () => ({ status: 422, body: { detail: "mode « x » inconnu" } }));
    renderAt("/", [{ path: "/", element: <HomeView /> }]);
    const box = await screen.findByRole("textbox", { name: "Message" });
    fireEvent.change(box, { target: { value: "Bonjour" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(await screen.findByText("mode « x » inconnu")).toBeInTheDocument();
    expect(box).toHaveValue("Bonjour");
  });
});

describe("ConversationView", () => {
  it("shows the stored history, the live turn with its tool steps, and cancels it on request", async () => {
    api.on("GET", "/assistant/conversations/c1", conversation({ mode: "finance" }));
    api.on("GET", "/assistant/conversations/c1/messages", [message()]);
    const running = turn({
      steps: [{ step: 1, tool: "finance.loan", arguments: { principal: 200000 }, ok: true, duration_s: 0.01, observation: { ok: true, output: { monthly_payment: 1159.92, total_paid: 278380.8, total_interest: 78380.8, rows: [{ year: 1, interest: 6870.1, remaining: 193951 }], assumptions: { principal: 200000, annual_rate_percent: 3.5, years: 20 } } } }],
    });
    api.on("GET", "/assistant/conversations/c1/turns", [running]);
    api.on("POST", "/assistant/turns/t1/cancel", { cancel_requested: true });
    renderAt("/c/c1", [{ path: "/c/:conversationId", element: <ConversationView /> }]);
    expect(await screen.findByText("Bonjour")).toBeInTheDocument();
    expect(await screen.findByText(/Outil finance.loan terminé/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" }).closest("form")).toHaveClass("is-disabled");
    // La vue Finance montre le résultat réel de l'outil.
    expect(screen.getAllByText("1 159,92 €").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /arrêter/i }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === "/assistant/turns/t1/cancel")).toBe(true));
  });

  it("renders a failed answer as an explicit error with a retry that resends the user's message", async () => {
    api.on("GET", "/assistant/conversations/c1", conversation());
    api.on("GET", "/assistant/conversations/c1/messages", [
      message(),
      message({ id: "m2", role: "assistant", content: "", status: "error", error: "aucun modèle disponible : poids absents" }),
    ]);
    api.on("GET", "/assistant/conversations/c1/turns", [turn({ status: "failed" })]);
    api.on("POST", "/assistant/conversations/c1/turns", () => ({ status: 202, body: { status: "real", data: { user_message: message(), turn: turn() } } }));
    renderAt("/c/c1", [{ path: "/c/:conversationId", element: <ConversationView /> }]);
    expect(await screen.findByText("La réponse a échoué")).toBeInTheDocument();
    expect(screen.getByText(/aucun modèle disponible : poids absents/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /réessayer/i }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "POST" && c.path === "/assistant/conversations/c1/turns")?.body).toEqual({ content: "Bonjour" }),
    );
  });

  it("renames the conversation and changes its mode through the API", async () => {
    api.on("GET", "/assistant/conversations/c1", conversation());
    api.on("GET", "/assistant/conversations/c1/messages", []);
    api.on("GET", "/assistant/conversations/c1/turns", []);
    api.on("PATCH", "/assistant/conversations/c1", (body) => real(conversation(body as object)));
    renderAt("/c/c1", [{ path: "/c/:conversationId", element: <ConversationView /> }]);
    fireEvent.click(await screen.findByRole("button", { name: "Renommer la conversation" }));
    const input = screen.getByRole("textbox", { name: "Titre de la conversation" });
    fireEvent.change(input, { target: { value: "Budget 2027" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByRole("heading", { name: /Budget 2027/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox")).not.toBeDisabled());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "code" } });
    await waitFor(() => expect(api.calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([{ title: "Budget 2027" }, { mode: "code" }]));
  });

  it("refuses deletion during a running turn and shows the backend's reason", async () => {
    api.on("GET", "/assistant/conversations/c1", conversation());
    api.on("GET", "/assistant/conversations/c1/messages", [message()]);
    api.on("GET", "/assistant/conversations/c1/turns", [turn()]);
    api.on("DELETE", "/assistant/conversations/c1", () => ({ status: 409, body: { detail: "une réponse est en cours dans cette conversation" } }));
    renderAt("/c/c1", [{ path: "/c/:conversationId", element: <ConversationView /> }]);
    fireEvent.click(await screen.findByRole("button", { name: "Supprimer la conversation" }));
    const dialog = screen.getByRole("dialog", { name: "Supprimer la conversation" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Supprimer" }));
    expect(await within(dialog).findByText("une réponse est en cours dans cette conversation")).toBeInTheDocument();
  });

  it("says so when the conversation no longer exists", async () => {
    api.on("GET", "/assistant/conversations/zz", () => ({ status: 404, body: { detail: "conversation « zz » introuvable" } }));
    api.on("GET", "/assistant/conversations/zz/messages", () => ({ status: 404, body: { detail: "introuvable" } }));
    api.on("GET", "/assistant/conversations/zz/turns", []);
    renderAt("/c/zz", [{ path: "/c/:conversationId", element: <ConversationView /> }]);
    expect(await screen.findByRole("heading", { name: "Conversation introuvable" })).toBeInTheDocument();
  });
});

describe("AssistantSidebar", () => {
  function renderSidebar() {
    return renderAt("/", [
      { path: "/", element: <AssistantSidebar collapsed={false} onToggleCollapsed={() => {}} mobileOpen={false} onCloseMobile={() => {}} /> },
      { path: "/p/:projectId", element: <div data-testid="project-page">projet</div> },
    ]);
  }

  it("groups real conversations under their project and lists standalone ones", async () => {
    api.on("GET", "/assistant/projects", [{ id: "p1", name: "Maison", description: "", instructions: "", created_by: "human", created_at: "2026-10-01T00:00:00Z", updated_at: null }]);
    api.on("GET", "/assistant/conversations", [conversation({ id: "a", title: "Travaux cuisine", project_id: "p1" }), conversation({ id: "b", title: "Question libre" })]);
    renderSidebar();
    expect(await screen.findByRole("link", { name: /Maison/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Question libre/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Travaux cuisine/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /déplier le projet maison/i }));
    expect(screen.getByRole("link", { name: /Travaux cuisine/ })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "libre" } });
    expect(screen.queryByRole("link", { name: /Maison/ })).not.toBeInTheDocument();
  });

  it("shows empty and error states instead of example data", async () => {
    api.on("GET", "/assistant/projects", () => ({ status: 200, body: { status: "unknown", data: null, detail: "base indisponible" } }));
    renderSidebar();
    expect(await screen.findByText(/Projets indisponibles : base indisponible/)).toBeInTheDocument();
    expect(await screen.findByText("Aucune conversation hors projet.")).toBeInTheDocument();
  });

  it("creates a project through the API and opens it", async () => {
    api.on("POST", "/assistant/projects", (body) => real({ id: "p9", description: "", instructions: "", created_by: "human", created_at: "2026-10-09T00:00:00Z", updated_at: null, ...(body as object) }));
    renderSidebar();
    fireEvent.click(await screen.findByRole("button", { name: "Créer un projet" }));
    const dialog = screen.getByRole("dialog", { name: "Nouveau projet" });
    fireEvent.change(within(dialog).getByPlaceholderText(/Rénovation/), { target: { value: "Vacances" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Créer le projet" }));
    expect(await screen.findByTestId("project-page")).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Vacances", description: "", instructions: "" });
  });
});

describe("ProjectView", () => {
  it("reports each attachment accepted or refused by the backend, with the reason", async () => {
    api.on("GET", "/assistant/projects/p1", { id: "p1", name: "Maison", description: "Travaux", instructions: "Montants TTC.", created_by: "human", created_at: "2026-10-01T00:00:00Z", updated_at: null });
    api.on("GET", "/assistant/projects/p1/attachments", []);
    api.on("POST", "/assistant/projects/p1/attachments", (body) => {
      const name = (body as { filename: string }).filename;
      return name.endsWith(".pdf")
        ? { status: 422, body: { detail: "format « .pdf » non pris en charge : APEX ne lit que des fichiers texte" } }
        : real({ id: "a1", project_id: "p1", filename: name, media_type: "text/plain", size_bytes: 5, sha256: "ab".repeat(32), created_at: "2026-10-09T00:00:00Z" });
    });
    const { container } = renderAt("/p/p1", [{ path: "/p/:projectId", element: <ProjectView /> }]);
    expect(await screen.findByText("Montants TTC.")).toBeInTheDocument();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [new File(["notes"], "notes.md", { type: "text/markdown" }), new File(["%PDF"], "devis.pdf", { type: "application/pdf" })];
    await act(async () => {
      fireEvent.change(input, { target: { files } });
    });
    expect(await screen.findByText("Ajouté : notes.md")).toBeInTheDocument();
    expect(screen.getByText(/devis.pdf : format « .pdf » non pris en charge/)).toBeInTheDocument();
    const upload = api.calls.find((c) => c.method === "POST" && (c.body as { filename: string }).filename === "notes.md");
    expect(atob((upload?.body as { content_base64: string }).content_base64)).toBe("notes");
  });
});

describe("SettingsView", () => {
  it("applies and saves the theme, and saves a section with the backend's validation", async () => {
    api.on("PUT", "/assistant/settings", (body) => {
      const changes = body as Record<string, Record<string, unknown>>;
      if (changes.runtime && Number(changes.runtime.generation_timeout_s) > 3600) {
        return { status: 422, body: { detail: [{ loc: ["body", "runtime", "generation_timeout_s"], msg: "Input should be less than or equal to 3600" }] } };
      }
      return real({ ...SETTINGS, ...changes });
    });
    renderAt("/settings", [{ path: "/settings", element: <SettingsView /> }]);
    fireEvent.click(await screen.findByRole("radio", { name: /APEX Dune/ }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dune");
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ appearance: { theme: "dune" } }));

    const timeout = screen.getByRole("spinbutton", { name: "Délai en secondes" });
    fireEvent.change(timeout, { target: { value: "5000" } });
    const runtime = screen.getByRole("heading", { name: "Runtime" }).closest("section")!;
    fireEvent.click(within(runtime).getByRole("button", { name: "Enregistrer" }));
    expect(await within(runtime).findByText(/runtime.generation_timeout_s : Input should be less than or equal to 3600/)).toBeInTheDocument();
    fireEvent.change(timeout, { target: { value: "1200" } });
    fireEvent.click(within(runtime).getByRole("button", { name: "Enregistrer" }));
    expect(await within(runtime).findByText("Enregistré.")).toBeInTheDocument();
    expect(api.calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({ runtime: { generation_timeout_s: 1200 } });
  });
});

describe("mode panels", () => {
  it("Design: shows the cited palette and computes WCAG contrast", () => {
    const messages = [message({ id: "a", role: "assistant", content: "Fond #0f172a, texte #e2e8f0." })];
    const { container } = renderAt("/", [{ path: "/", element: <DesignPanel messages={messages as never} liveSteps={[]} /> }]);
    const swatches = container.querySelector(".ax-swatches") as HTMLElement;
    expect(within(swatches).getAllByRole("listitem").map((li) => li.querySelector("code")?.textContent)).toEqual(["#0f172a", "#e2e8f0"]);
    expect(screen.getByText(/Ratio/).textContent).toMatch(/14\.4\d : 1/);
  });

  it("Finance: lists only real tool results, newest first", () => {
    const messages = [
      message({
        id: "a",
        role: "assistant",
        content: "…",
        meta: { steps: [{ step: 1, tool: "calc.evaluate", arguments: {}, ok: true, duration_s: 0, observation: { ok: true, output: { expression: "1500*12", result: 18000 } } }] },
      }),
    ];
    renderAt("/", [{ path: "/", element: <FinancePanel messages={messages as never} liveSteps={[]} /> }]);
    expect(screen.getByText("1500*12")).toBeInTheDocument();
    expect(screen.getByText("18 000")).toBeInTheDocument();
  });
});

describe("summarizeError", () => {
  it("keeps the readable head of a routing error and flags a missing local model", async () => {
    const { summarizeError } = await import("./MessageItem");
    const long = "génération impossible — devstral : backend non disponible; qwen2.5-coder : poids absents ou incomplets dans C:/Users/x/AppData/Local/APEX/models";
    expect(summarizeError(long)).toBe("génération impossible (modèle local non installé)");
    expect(summarizeError("délai dépassé")).toBe("délai dépassé");
    expect(summarizeError("x".repeat(400))).toHaveLength(218);
  });
});
