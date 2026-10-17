import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { resetAppearanceFromCache } from "../theme/appearance";
import { AssistantSidebar } from "./AssistantSidebar";
import { ConversationView } from "./ConversationView";
import { HomeView } from "./HomeView";
import { FinancePanel, DesignPanel, Studio3DPanel } from "./ModePanels";
import { ProjectView } from "./ProjectView";
import { SettingsView } from "./SettingsView";
import { MODES, SETTINGS, conversation, engines, installFakeApi, message, real, renderAt, turn, type FakeApi } from "./testing";

const originalFetch = globalThis.fetch;
let api: FakeApi;

beforeEach(() => {
  localStorage.clear();
  resetAppearanceFromCache();
  api = installFakeApi();
  api.on("GET", "/assistant/modes", MODES);
  api.on("GET", "/assistant/settings", SETTINGS);
  api.on("GET", "/assistant/engines", engines());
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
    api.on("GET", "/assistant/engines", engines({ auto: false, integrated: false }));
    renderAt("/", [{ path: "/", element: <HomeView /> }]);
    expect(await screen.findByText("Aucun modèle disponible")).toBeInTheDocument();
    // Le détail mesuré de chaque modèle du moteur choisi reste accessible, replié.
    const details = screen.getByText("Détails techniques").closest("details")!;
    expect(details.querySelector("li")?.textContent).toBe("qwen-1.5b : poids absents");
  });

  it("lets the user choose the engine next to the message box and saves it to APEX", async () => {
    api.on("PUT", "/assistant/settings", (body) => real({ ...SETTINGS, models: { ...SETTINGS.models, ...(body as { models: object }).models } }));
    renderAt("/", [{ path: "/", element: <HomeView /> }]);
    const picker = await screen.findByRole("combobox", { name: "Moteur" });
    await waitFor(() => expect(within(picker).getAllByRole("option")).toHaveLength(4));
    expect(within(picker).getByRole("option", { name: "Ollama — indisponible" })).toBeInTheDocument();
    expect(within(picker).getByRole("option", { name: "Cerveau APEX (expérimental)" })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: "integrated" } });
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PUT" && c.path === "/assistant/settings")?.body).toEqual({
        models: { engine: "integrated", preferred_model: "auto" },
      }),
    );
    await waitFor(() => expect(picker).toHaveValue("integrated"));
  });

  it("says plainly that the APEX brain is experimental and cannot converse yet", async () => {
    api.on("GET", "/assistant/settings", { ...SETTINGS, models: { ...SETTINGS.models, engine: "brain" } });
    renderAt("/", [{ path: "/", element: <HomeView /> }]);
    expect(await screen.findByText("Cerveau APEX — expérimental")).toBeInTheDocument();
    expect(screen.getByText(/pas encore entraîné à\s+converser/)).toBeInTheDocument();
  });

  it("presents a conversational APEX brain as built from scratch, with its measured description", async () => {
    api.on("GET", "/assistant/settings", { ...SETTINGS, models: { ...SETTINGS.models, engine: "brain" } });
    api.on("GET", "/assistant/engines", engines().map((e) => e.id !== "brain" ? e : {
      ...e, experimental: false,
      models: [{ id: "apex-brain:v2:apex-q5", runtime: "apex_brain", served_model: "apex-brain-v2", available: true,
                 detail: "Cerveau APEX « apex-brain-v2 » — 15736192 paramètres", conversational: true }],
    }));
    renderAt("/", [{ path: "/", element: <HomeView /> }]);
    expect(await screen.findByText("Cerveau APEX — construit de zéro par APEX")).toBeInTheDocument();
    expect(screen.getByText(/15736192 paramètres\. C'est un petit modèle/)).toBeInTheDocument();
    expect(screen.queryByText("Cerveau APEX — expérimental")).not.toBeInTheDocument();
  });

  it("names the unavailable engine and what to do instead of a generic error", async () => {
    api.on("GET", "/assistant/settings", { ...SETTINGS, models: { ...SETTINGS.models, engine: "ollama" } });
    renderAt("/", [{ path: "/", element: <HomeView /> }]);
    expect(await screen.findByText("Moteur « Ollama » indisponible")).toBeInTheDocument();
    expect(screen.getByText(/Démarrez Ollama sur ce PC/)).toBeInTheDocument();
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

  it("shows the 3D workshop: real files of each model, the tool's summary and the preview", async () => {
    api.on("GET", "/assistant/conversations/c1", conversation({ mode: "3d" }));
    const step = {
      step: 1, tool: "model3d.create", arguments: { generator: "vehicle", kind: "tank" }, ok: true, duration_s: 0.2,
      observation: { ok: true, output: { name: "char", summary: "char 7 × 3,4 × 2,4 m, 10 galets — 16 pièces, 816 triangles",
        files: [{ path: "modeles3d/char.glb", size: 67384 }, { path: "modeles3d/ApexCharController.cs", size: 4148 }] } },
    };
    api.on("GET", "/assistant/conversations/c1/messages", [
      message(), message({ id: "m2", role: "assistant", content: "Voici le char.", meta: { mode: "3d", steps: [step] } }),
    ]);
    api.on("GET", "/assistant/conversations/c1/turns", []);
    api.on("GET", "/assistant/conversations/c1/workspace", {
      scope: "conversation", root: "C:\\Users\\moi\\APEX\\conversation-c1",
      files: [{ path: "modeles3d/char.glb", size: 67384 }, { path: "modeles3d/char.png", size: 3576 },
        { path: "modeles3d/ApexCharController.cs", size: 4148 }, { path: "notes.txt", size: 5 }],
    });
    api.on("GET", "/assistant/conversations/c1/workspace/file", { path: "modeles3d/char.png", size: 3, content_base64: "iVBO" });
    renderAt("/c/c1", [{ path: "/c/:conversationId", element: <ConversationView /> }]);
    expect(await screen.findByText("ApexCharController.cs")).toBeInTheDocument();
    expect(screen.getByText("char 7 × 3,4 × 2,4 m, 10 galets — 16 pièces, 816 triangles", { selector: "p" })).toBeInTheDocument();
    expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
    expect(screen.getByText("C:\\Users\\moi\\APEX\\conversation-c1\\modeles3d")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Aperçu de char" })).toHaveAttribute("src", "data:image/png;base64,iVBO");
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
    const modeSelect = screen.getByRole("combobox", { name: "Mode" });
    await waitFor(() => expect(modeSelect).not.toBeDisabled());
    fireEvent.change(modeSelect, { target: { value: "code" } });
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

describe("Game Studio in the 3D workshop", () => {
  const PROJECT = "6f1c2a8e-0000-4000-8000-000000000001";
  const created = (ok: boolean, output: Record<string, unknown> | null, error: { code: string; message: string } | null) =>
    message({
      id: "g",
      role: "assistant",
      content: "…",
      meta: { steps: [{ step: 1, tool: "game_studio.create", arguments: { description: "Une scène de forêt." }, ok, duration_s: 0.1, observation: { ok, output, error } }] },
    });

  it("follows a mission the brain handed to Game Studio, through the conversation's route", async () => {
    api.on("GET", "/assistant/conversations/c1/workspace", { scope: "conversation", files: [] });
    api.on("GET", `/assistant/conversations/c1/game-studio/${PROJECT}`, {
      project_id: PROJECT,
      status: { status: "review", mission_id: "m1", steps: [{ key: "scene", kind: "graphics", status: "done", job_id: "j1" }], delivery_status: "delivered", last_event: null },
      results: { status: "review", ready: true, artifacts: [{ name: "scene.glb", hash: "h", role: "exchange", source: "assembly" }], artifact_count: 1, truncated: false, blocked: [] },
    });
    const messages = [created(true, { project_id: PROJECT, mission_id: "m1", status: "queued", poll_tool: "game_studio.status" }, null)];
    renderAt("/", [{ path: "/", element: <Studio3DPanel conversationId="c1" messages={messages as never} liveSteps={[]} notes={[]} /> }]);
    expect(await screen.findByText("Mission 6f1c2a8e")).toBeInTheDocument();
    expect(await screen.findByText(/1 fichier\(s\) produit\(s\) : scene\.glb \(exchange\)/)).toBeInTheDocument();
    expect(screen.getByText(/scene \(graphics\) : done/)).toBeInTheDocument();
    expect(screen.getByText("terminée, à revoir")).toBeInTheDocument();
    // Mission terminée : plus rien à annuler.
    expect(screen.queryByRole("button", { name: "Annuler la mission" })).not.toBeInTheDocument();
  });

  it("shows the rendered previews and lets the user cancel a running mission", async () => {
    api.on("GET", "/assistant/conversations/c1/workspace", { scope: "conversation", files: [] });
    api.on("GET", `/assistant/conversations/c1/game-studio/${PROJECT}`, {
      project_id: PROJECT,
      status: { status: "running", mission_id: "m1", steps: [], delivery_status: null, last_event: null },
      results: { status: "running", ready: true, artifacts: [{ name: "scene.png", hash: "hv", role: "view", source: "assembly" }], artifact_count: 1, truncated: false, blocked: [] },
    });
    api.on("GET", `/assistant/conversations/c1/game-studio/${PROJECT}/preview/hv`, { content_type: "image/png", size: 4, content_base64: "iVBORw==" });
    api.on("POST", `/assistant/conversations/c1/game-studio/${PROJECT}/cancel`, { project_id: PROJECT, status: "cancelled" });
    const messages = [created(true, { project_id: PROJECT, mission_id: "m1", status: "queued" }, null)];
    renderAt("/", [{ path: "/", element: <Studio3DPanel conversationId="c1" messages={messages as never} liveSteps={[]} notes={[]} /> }]);
    expect(await screen.findByAltText("Rendu scene.png")).toHaveAttribute("src", "data:image/png;base64,iVBORw==");
    fireEvent.click(screen.getByRole("button", { name: "Annuler la mission" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path.endsWith("/cancel"))).toBe(true));
  });

  it("asks before launching: the user launches a proposed mission once, with its missing dependencies shown", async () => {
    api.on("GET", "/assistant/conversations/c1/workspace", { scope: "conversation", files: [] });
    api.on("GET", "/assistant/game-studio/availability", {
      installed: true, ready: false, missing: ["Aucun GPU CUDA : choisissez « Processeur »."], notes: [], launch: "human", compute: "auto",
    });
    api.on("POST", "/assistant/conversations/c1/game-studio/launch", (body) => real({ message_id: "l1", content: "Mission lancée.", meta: { echo: body } }));
    const messages = [
      message({ id: "u1", role: "user", content: "Crée une scène de forêt avec une rivière." }),
      message({ id: "a1", role: "assistant", content: "…", meta: { game_studio: { trigger: "cerveau", state: "proposed", launched: false, request_message_id: "u1", reason: "à lancer par vous : une mission n'est jamais lancée sans votre clic" } } }),
    ];
    renderAt("/", [{ path: "/", element: <Studio3DPanel conversationId="c1" messages={messages as never} liveSteps={[]} notes={[]} /> }]);
    expect(await screen.findByText(/Le Cerveau propose de confier à Game Studio : « Crée une scène de forêt avec une rivière\. »/)).toBeInTheDocument();
    expect(await screen.findByText("Aucun GPU CUDA : choisissez « Processeur ».")).toBeInTheDocument();
    expect(screen.getByText(/Le Cerveau propose les missions ; vous seul les lancez ou les annulez\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Lancer la mission" }));
    await waitFor(() => expect(api.calls.find((c) => c.path === "/assistant/conversations/c1/game-studio/launch")?.body).toEqual({ message_id: "a1" }));
  });

  it("never offers a proposal that was already launched", async () => {
    api.on("GET", "/assistant/conversations/c1/workspace", { scope: "conversation", files: [] });
    const messages = [
      message({ id: "u1", role: "user", content: "Crée une scène." }),
      message({ id: "a1", role: "assistant", content: "…", meta: { game_studio: { trigger: "cerveau", state: "proposed", launched: false, request_message_id: "u1" } } }),
      message({ id: "l1", role: "assistant", content: "Mission lancée.", meta: { game_studio: { trigger: "utilisateur", state: "refused", launched: false, proposal_of: "a1", error: { code: "unavailable", message: "local_planner_model_missing" } }, steps: [{ step: 1, tool: "game_studio.create", arguments: {}, ok: false, duration_s: 0, observation: { ok: false, output: {}, error: { code: "unavailable", message: "local_planner_model_missing" } } }] } }),
    ];
    renderAt("/", [{ path: "/", element: <Studio3DPanel conversationId="c1" messages={messages as never} liveSteps={[]} notes={[]} /> }]);
    expect(await screen.findByText(/Game Studio a refusé la mission : le modèle planificateur local/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Lancer la mission" })).not.toBeInTheDocument();
  });

  it("shows Game Studio's refusal as it is, explained, never as a mission", async () => {
    api.on("GET", "/assistant/conversations/c1/workspace", { scope: "conversation", files: [] });
    const messages = [created(false, null, { code: "unavailable", message: "cuda_unavailable_choose_cpu_explicitly" })];
    renderAt("/", [{ path: "/", element: <Studio3DPanel conversationId="c1" messages={messages as never} liveSteps={[]} notes={[]} /> }]);
    expect(await screen.findByText(/Game Studio a refusé la mission : aucun GPU CUDA sur ce PC.*\(cuda_unavailable_choose_cpu_explicitly\)/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.path.includes("/conversations/c1/game-studio/"))).toBe(false);
  });
});

describe("gameStudioLine", () => {
  it("says whether the brain handed the creation to Game Studio, and why not", async () => {
    const { gameStudioLine } = await import("./MessageItem");
    expect(gameStudioLine({ trigger: "cerveau", launched: true, project_id: "6f1c2a8e-0000", status: "queued" }))
      .toBe("confié à Game Studio par le Cerveau APEX : mission 6f1c2a8e (queued)");
    expect(gameStudioLine({ trigger: "cerveau", launched: false, error: { code: "unavailable", message: "local_planner_model_missing" } }))
      .toBe("Game Studio a refusé la mission : le modèle planificateur local de Game Studio n'est pas installé (local_planner_model_missing)");
    expect(gameStudioLine({ trigger: "cerveau", launched: false, reason: "Game Studio ne fait pas partie de cette installation d'APEX" }))
      .toBe("non confié à Game Studio : Game Studio ne fait pas partie de cette installation d'APEX");
    expect(gameStudioLine({ trigger: "cerveau", state: "proposed", launched: false, reason: "demande à confirmer (question générale)" }))
      .toBe("mission Game Studio proposée, à confirmer dans l'Atelier 3D : demande à confirmer (question générale)");
    expect(gameStudioLine({ trigger: "utilisateur", state: "launched", launched: true, project_id: "abcdef12-0000", status: "queued" }))
      .toBe("confié à Game Studio à votre demande : mission abcdef12 (queued)");
    expect(gameStudioLine({ trigger: "cerveau", state: "refused", launched: false, error: { code: "unavailable", message: "x" }, explanation: "le GPU est occupé." }))
      .toBe("Game Studio a refusé la mission : le GPU est occupé.");
  });
});

describe("MessageItem", () => {
  it("says when the APEX Brain handed an answer over, or kept it for lack of another engine", async () => {
    const { MessageItem } = await import("./MessageItem");
    const delegated = message({ id: "d", role: "assistant", content: "…", meta: { delegation: { from: "apex-brain", confidence: 0.623, threshold: 0.97, to: "qwen", status: "delegated" } } });
    const kept = message({ id: "k", role: "assistant", content: "…", meta: { delegation: { from: "apex-brain", confidence: 0.5, threshold: 0.97, to: null, status: "kept" } } });
    const unknown = message({ id: "u", role: "assistant", content: "…", meta: { delegation: { from: "apex-brain", reason: "cannot_know", confidence: 0.99, threshold: 0.97, to: "qwen", status: "delegated" } } });
    const current = message({ id: "c", role: "assistant", content: "…", meta: { delegation: { from: "apex-brain", reason: "current_info_no_tool", confidence: 0.99, threshold: 0.97, to: null, status: "kept" } } });
    renderAt("/", [{ path: "/", element: <><MessageItem message={delegated as never} /><MessageItem message={kept as never} /><MessageItem message={unknown as never} /><MessageItem message={current as never} /></> }]);
    expect(screen.getByText(/question d'actualité : aucun outil connecté, réponse du Cerveau APEX gardée/)).toBeInTheDocument();
    expect(screen.getByText(/confié par le Cerveau APEX \(confiance 0\.62\)/)).toBeInTheDocument();
    expect(screen.getByText(/Cerveau APEX peu sûr \(confiance 0\.50\), aucun autre moteur disponible/)).toBeInTheDocument();
    expect(screen.getByText(/confié par le Cerveau APEX \(il dit ne pas pouvoir le savoir\)/)).toBeInTheDocument();
  });
});

describe("crosscheckLine", () => {
  it("says what the cross-check found, never more", async () => {
    const { crosscheckLine } = await import("./MessageItem");
    expect(crosscheckLine({ language: "go", status: "agree", detail: "", example: null })).toBe("code vérifié : même résultat que sa version Python");
    expect(crosscheckLine({ language: "go", status: "disagree", detail: "", example: { input: [1, 2], answer: 1, python: 2 } }))
      .toBe("code douteux : pour [1,2], il rend 1, sa version Python 2");
    expect(crosscheckLine({ language: "go", status: "unverifiable", detail: "la sandbox de ce PC n'exécute pas go", example: null }))
      .toBe("code non vérifié : la sandbox de ce PC n'exécute pas go");
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
