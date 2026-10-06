import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../api/client";
import {
  createFunctionTask,
  createReferenceTask,
  getTaskRun,
  listReferenceTaskKinds,
  listTaskRuns,
  listTasks,
  startTaskRun,
} from "../api/endpoints";
import type { FunctionCaseInput, RetryAdaptation, TaskRunDetail, TaskRunEvent } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { Button } from "../components/ui/Button";
import { Checkbox, Input, Select, Textarea } from "../components/ui/Field";
import "./pages.css";

/**
 * Résolution de tâches — le TaskLoop RÉEL (apex/taskloop) piloté depuis l'interface : créer une tâche
 * (référence ou définie par l'utilisateur), lancer une exécution, suivre sa trace réelle (plan, tentatives,
 * exécution sandbox, verdict du Judge, adaptation entre tentatives, Experience enregistrées). Tout ce qui
 * est affiché vient de l'API, jamais d'une estimation côté client.
 */

const EXAMPLE_CASES = '[\n  {"args": [1, 2], "expected": 3},\n  {"args": [-4, 4], "expected": 0}\n]';

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : String(error);
}

/** Lecture des cas saisis : JSON, tableau d'objets {args: [...], expected: ...} — sinon un message clair. */
export function parseCases(text: string): { cases?: FunctionCaseInput[]; error?: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "Les cas doivent être du JSON valide." };
  }
  if (!Array.isArray(parsed) || parsed.length === 0) return { error: "Les cas doivent être une liste non vide." };
  for (const [i, item] of parsed.entries()) {
    if (typeof item !== "object" || item === null || !Array.isArray((item as { args?: unknown }).args)) {
      return { error: `Cas ${i} : « args » doit être une liste.` };
    }
    if (!("expected" in item)) return { error: `Cas ${i} : « expected » manquant.` };
  }
  return { cases: parsed as FunctionCaseInput[] };
}

function NewTaskPanel({ onCreated }: { onCreated: (taskId: string) => void }) {
  const kinds = useQuery({ queryKey: ["task-reference-kinds"], queryFn: listReferenceTaskKinds });
  const [kind, setKind] = useState("is_palindrome");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [fn, setFn] = useState("");
  const [casesText, setCasesText] = useState(EXAMPLE_CASES);
  const [formError, setFormError] = useState<string | null>(null);

  const reference = useMutation({
    mutationFn: () => createReferenceTask(kind),
    onSuccess: (env) => env.data && onCreated(env.data.id),
  });
  const custom = useMutation({
    mutationFn: (cases: FunctionCaseInput[]) =>
      createFunctionTask({ title: title || undefined, description, function: fn, cases }),
    onSuccess: (env) => env.data && onCreated(env.data.id),
  });

  const submitCustom = () => {
    const { cases, error } = parseCases(casesText);
    if (error || !cases) {
      setFormError(error ?? "cas invalides");
      return;
    }
    setFormError(null);
    custom.mutate(cases);
  };

  return (
    <Panel title="Nouvelle tâche">
      <div className="form-row">
        <Select aria-label="Tâche de référence" value={kind} onChange={(e) => setKind(e.target.value)}>
          {(kinds.data?.data ?? ["is_palindrome"]).map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </Select>
        <Button onClick={() => reference.mutate()} disabled={reference.isPending}>
          Créer la tâche de référence
        </Button>
      </div>
      {reference.isError && <ErrorState detail={errorText(reference.error)} />}

      <h3>Ma tâche (fonction Python)</h3>
      <p className="muted">
        Énoncé, nom de la fonction et cas cachés (arguments et résultat attendu). APEX écrit lui-même le
        harnais de test : les résultats attendus ne sont jamais montrés au modèle ni envoyés dans la sandbox.
      </p>
      <Input aria-label="Titre" placeholder="Titre (facultatif)" value={title} onChange={(e) => setTitle(e.target.value)} />
      <Textarea
        aria-label="Énoncé"
        placeholder="Énoncé du problème"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      <Input aria-label="Nom de la fonction" placeholder="nom_de_fonction" value={fn} onChange={(e) => setFn(e.target.value)} />
      <Textarea aria-label="Cas cachés (JSON)" value={casesText} onChange={(e) => setCasesText(e.target.value)} />
      <Button variant="primary" onClick={submitCustom} disabled={custom.isPending || !description || !fn}>
        Créer ma tâche
      </Button>
      {formError && <ErrorState detail={formError} />}
      {custom.isError && <ErrorState detail={errorText(custom.error)} />}
    </Panel>
  );
}

function RunLauncher({ taskId, onStarted }: { taskId: string; onStarted: (runId: string) => void }) {
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [planFirst, setPlanFirst] = useState(false);
  const [adaptation, setAdaptation] = useState<RetryAdaptation>("failure_facts");
  const [learnFromLatest, setLearnFromLatest] = useState(false);
  const [toolMode, setToolMode] = useState(false);
  const [useMemory, setUseMemory] = useState(false);
  const start = useMutation({
    mutationFn: () =>
      startTaskRun(taskId, {
        max_attempts: maxAttempts,
        plan_first: planFirst,
        retry_adaptation: adaptation,
        use_memory: useMemory,
        ...(learnFromLatest ? { condition_on_run: "latest" } : {}),
        // Permissions EXPLICITES : l'utilisateur les accorde ici, jamais le modèle.
        ...(toolMode ? { tool_permissions: ["workspace.read", "workspace.write", "sandbox.execute"], max_tool_steps: 10 } : {}),
      }),
    onSuccess: (env) => env.data && onStarted(env.data.id),
  });
  return (
    <div className="form-row">
      <label>
        Tentatives max{" "}
        <Input
          aria-label="Tentatives max"
          type="number"
          min={1}
          max={10}
          value={maxAttempts}
          onChange={(e) => setMaxAttempts(Number(e.target.value))}
        />
      </label>
      <Checkbox label="Plan d'abord (analyse par le modèle)" checked={planFirst} onChange={(e) => setPlanFirst(e.target.checked)} />
      <Select aria-label="Entre deux tentatives" value={adaptation} onChange={(e) => setAdaptation(e.target.value as RetryAdaptation)}>
        <option value="failure_facts">Corriger avec les faits d'échec</option>
        <option value="none">Réessayer à l'identique</option>
      </Select>
      <Checkbox
        label="Apprendre de la dernière exécution"
        checked={learnFromLatest}
        onChange={(e) => setLearnFromLatest(e.target.checked)}
      />
      <Checkbox
        label="Mode outillé (fichiers, exécution et tests en sandbox)"
        checked={toolMode}
        onChange={(e) => setToolMode(e.target.checked)}
      />
      <Checkbox
        label="Consulter la mémoire (exécutions passées, documents)"
        checked={useMemory}
        onChange={(e) => setUseMemory(e.target.checked)}
      />
      <Button variant="primary" onClick={() => start.mutate()} disabled={start.isPending}>
        Lancer
      </Button>
      {start.isError && <ErrorState detail={errorText(start.error)} />}
    </div>
  );
}

function eventSummary(event: TaskRunEvent): string {
  switch (event.kind) {
    case "task.plan.generated":
      return String(event.plan ?? "");
    case "task.plan.failed":
      return `plan non obtenu : ${String(event.reason ?? "")}`;
    case "judge.completed":
      return `${String(event.verdict ?? "")} — ${String(event.detail ?? "")}`;
    case "strategy.adapted":
    case "strategy.decided":
      return String(event.description ?? "");
    case "strategy.unchanged":
      return String(event.reason ?? "");
    case "provider.selected":
      return String(event.model_id ?? "");
    case "agent.step":
      return event.submit
        ? `tour ${String(event.step)} : solution soumise`
        : `tour ${String(event.step)} : ${String(event.tool ?? "action illisible")} ${event.ok ? "ok" : `échec (${String(event.error ?? "")})`}`;
    case "memory.retrieved": {
      const items = (event.items as Array<{ source?: string }> | undefined) ?? [];
      return items.length ? `souvenirs : ${items.map((i) => String(i.source ?? "")).join(", ")}` : "aucun souvenir pertinent";
    }
    case "memory.unavailable":
      return `mémoire indisponible : ${String(event.error ?? "")}`;
    case "workspace.created":
      return `espace ${String(event.workspace_id ?? "")} — outils : ${(event.tools as string[] | undefined)?.join(", ") ?? ""}`;
    default:
      return "";
  }
}

export function RunDetail({ runId }: { runId: string }) {
  const run = useQuery({
    queryKey: ["task-run", runId],
    queryFn: () => getTaskRun(runId),
    refetchInterval: (query) => (query.state.data?.data?.status === "running" ? 2000 : false),
  });
  if (run.isLoading) return <LoadingState />;
  if (run.isError) return <ErrorState detail={errorText(run.error)} />;
  const detail: TaskRunDetail | null | undefined = run.data?.data;
  if (!detail) return <ErrorState detail={run.data?.detail} />;
  return (
    <div>
      <p>
        <strong>Statut : {detail.status}</strong>
        {detail.stop_reason ? ` (${detail.stop_reason})` : ""} — {detail.attempts.length} tentative(s)
      </p>
      {detail.failure && <p className="muted">Échec : {detail.failure}</p>}
      <h3>Tentatives</h3>
      <ol>
        {detail.eval_runs.map((ev) => (
          <li key={ev.id}>
            {ev.model_id ?? "modèle inconnu"} — verdict {ev.oracle_verdict} ({ev.failure_category}) —{" "}
            {ev.oracle_detail}
            {ev.generated_code && <pre className="mono">{ev.generated_code}</pre>}
          </li>
        ))}
      </ol>
      <h3>Trace</h3>
      <ol aria-label="Trace de l'exécution">
        {detail.events.map((event, i) => (
          <li key={`${event.ts}-${i}`}>
            <span className="mono">{event.kind}</span> {eventSummary(event)}
          </li>
        ))}
      </ol>
    </div>
  );
}

function TaskRunsPanel({ taskId }: { taskId: string }) {
  const queryClient = useQueryClient();
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const runs = useQuery({ queryKey: ["task-runs", taskId], queryFn: () => listTaskRuns(taskId), refetchInterval: 3000 });
  return (
    <Panel title="Exécutions">
      <RunLauncher
        taskId={taskId}
        onStarted={(runId) => {
          setSelectedRun(runId);
          void queryClient.invalidateQueries({ queryKey: ["task-runs", taskId] });
        }}
      />
      {runs.isError && <ErrorState detail={errorText(runs.error)} />}
      {(runs.data?.data ?? []).length === 0 ? (
        <EmptyState title="Aucune exécution" detail="Lancez une exécution pour voir sa trace réelle." />
      ) : (
        <ul>
          {(runs.data?.data ?? []).map((r) => (
            <li key={r.id}>
              <button type="button" className="link-button" onClick={() => setSelectedRun(r.id)}>
                {r.started_at} — {r.status}
              </button>
            </li>
          ))}
        </ul>
      )}
      {selectedRun && <RunDetail runId={selectedRun} />}
    </Panel>
  );
}

export function TaskRunsPage() {
  const queryClient = useQueryClient();
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const tasks = useQuery({ queryKey: ["tasks", "all"], queryFn: () => listTasks() });
  // Tâches créées par un humain (référence ou définie par l'utilisateur) : celles que le TaskLoop exécute.
  const runnable = (tasks.data?.data ?? []).filter((t) => t.created_by === "human");
  return (
    <div className="page">
      <h1>Résolution de tâches</h1>
      <NewTaskPanel
        onCreated={(taskId) => {
          setSelectedTask(taskId);
          void queryClient.invalidateQueries({ queryKey: ["tasks", "all"] });
        }}
      />
      <Panel title="Tâches">
        {tasks.isLoading && <LoadingState />}
        {tasks.isError && <ErrorState detail={errorText(tasks.error)} />}
        <ul>
          {runnable.map((t) => (
            <li key={t.id}>
              <button type="button" className="link-button" onClick={() => setSelectedTask(t.id)}>
                {t.title}
              </button>
            </li>
          ))}
        </ul>
      </Panel>
      {selectedTask && <TaskRunsPanel taskId={selectedTask} />}
    </div>
  );
}
