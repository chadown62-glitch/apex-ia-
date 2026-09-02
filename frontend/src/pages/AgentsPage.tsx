import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  useAgentExecutions,
  useAgents,
  useAgentTemplates,
  useConfiguredAgents,
  useCreateAgentTemplate,
  useCreateConfiguredAgent,
  useDeleteAgentTemplate,
  useDeleteConfiguredAgent,
} from "../api/hooks";
import type {
  Agent,
  AgentRole,
  CloneState,
  CloneType,
  ConfiguredAgentRole,
  ConfiguredAgentStatus,
  Permission,
  ToolName,
} from "../api/types";
import { AgentCard, AgentStatePill } from "../components/agents/AgentCard";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { MeasurementValue } from "../components/status/MeasurementValue";
import { StatusBadge } from "../components/status/StatusBadge";
import { specialtyForAgentType, SPECIALTY_DEFINITIONS } from "../specialties/data";
import "./pages.css";
import "./agent-center.css";
import "./mission-center.css";
import "./agent-registry.css";

const AGENT_ROLES: AgentRole[] = ["coder", "researcher", "tester", "analyst", "designer", "security", "evaluator"];

const CONFIGURED_AGENT_ROLES: ConfiguredAgentRole[] = [
  "coder", "researcher", "tester", "analyst", "designer", "security", "evaluator", "project_manager", "generalist",
];

const ALL_TOOLS: ToolName[] = [
  "files", "terminal", "tests", "git-read", "sandbox", "memory", "static-analysis", "web-research",
];

const ALL_PERMISSIONS: Permission[] = [
  "read_only", "workspace_write", "execute", "network", "git_read", "git_write",
];

const STATUS_LABEL: Record<ConfiguredAgentStatus, string> = {
  idle: "IDLE", queued: "QUEUED", running: "RUNNING", waiting: "WAITING",
  completed: "COMPLETED", failed: "FAILED", disabled: "DISABLED",
};

const STATUS_DOT: Record<ConfiguredAgentStatus, string> = {
  idle: "🟢", queued: "🟡", running: "🔵", waiting: "🟡",
  completed: "🟢", failed: "🔴", disabled: "⚪",
};

function ConfiguredAgentStatusPill({ status }: { status: ConfiguredAgentStatus }) {
  return (
    <span className={`configured-agent-status configured-agent-status--${status}`}>
      <span aria-hidden="true">{STATUS_DOT[status]}</span>
      {STATUS_LABEL[status]}
    </span>
  );
}

/**
 * `Agent` réel (`apex.agent_registry`) — une configuration réutilisable,
 * réellement exécutable via le Model Router, assignable à des Missions.
 * DISTINCT des Templates (blueprints, jamais exécutables, panneau
 * ci-dessous) et des Clones (`AgentRuntime`, instances OS-level, liste plus
 * bas). Aucun outil/permission n'est jamais accordé par défaut.
 */
function ConfiguredAgentsPanel() {
  const agents = useConfiguredAgents();
  const createAgent = useCreateConfiguredAgent();
  const deleteAgent = useDeleteConfiguredAgent();

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState<ConfiguredAgentRole>("coder");
  const [description, setDescription] = useState("");
  const [modelRole, setModelRole] = useState("");
  const [instructions, setInstructions] = useState("");
  const [selectedTools, setSelectedTools] = useState<Set<ToolName>>(new Set());
  const [selectedPermissions, setSelectedPermissions] = useState<Set<Permission>>(new Set());
  const [createError, setCreateError] = useState<string | null>(null);

  const list = agents.data?.status === "real" ? agents.data.data ?? [] : [];

  function toggle<T>(set: Set<T>, value: T, setter: (s: Set<T>) => void) {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    setter(next);
  }

  async function handleCreate() {
    setCreateError(null);
    const envelope = await createAgent.mutateAsync({
      name, role, description, model_role: modelRole || null,
      tools: Array.from(selectedTools), permissions: Array.from(selectedPermissions),
      instructions: instructions || null,
    });
    if (envelope.status === "real") {
      setName("");
      setDescription("");
      setInstructions("");
      setSelectedTools(new Set());
      setSelectedPermissions(new Set());
    } else {
      setCreateError(envelope.detail ?? "Création refusée par le backend.");
    }
  }

  return (
    <Panel title="Mes Agents (réels, exécutables via le Model Router)">
      <div className="two-col">
        <div className="mission-form">
          <label className="mission-form__field">
            <span>Nom</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="ex. Mon développeur Python" />
          </label>
          <div className="mission-form__row">
            <label className="mission-form__field">
              <span>Rôle</span>
              <select value={role} onChange={(e) => setRole(e.target.value as ConfiguredAgentRole)}>
                {CONFIGURED_AGENT_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
            <label className="mission-form__field">
              <span>Modèle préféré (optionnel)</span>
              <input value={modelRole} onChange={(e) => setModelRole(e.target.value)} placeholder="ex. fast, coder_main" />
            </label>
          </div>
          <label className="mission-form__field">
            <span>Description</span>
            <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <label className="mission-form__field">
            <span>Instructions (comportement de mission)</span>
            <textarea rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="ex. Tu es spécialisé dans le développement Python…" />
          </label>

          <div className="agent-registry-chips">
            <span className="agent-registry-chips__label">Outils autorisés (aucun par défaut)</span>
            <div>
              {ALL_TOOLS.map((tool) => (
                <button
                  key={tool}
                  type="button"
                  className={`chip ${selectedTools.has(tool) ? "chip--active" : ""}`}
                  onClick={() => toggle(selectedTools, tool, setSelectedTools)}
                >
                  {tool}
                </button>
              ))}
            </div>
          </div>

          <div className="agent-registry-chips">
            <span className="agent-registry-chips__label">Permissions (aucune par défaut)</span>
            <div>
              {ALL_PERMISSIONS.map((perm) => (
                <button
                  key={perm}
                  type="button"
                  className={`chip ${selectedPermissions.has(perm) ? "chip--active" : ""}`}
                  onClick={() => toggle(selectedPermissions, perm, setSelectedPermissions)}
                >
                  {perm}
                </button>
              ))}
            </div>
          </div>

          <button
            className="action-button action-button--live"
            disabled={!name.trim() || createAgent.isPending}
            onClick={handleCreate}
          >
            {createAgent.isPending ? "Création…" : "Créer l'agent"}
          </button>
          {createError && <ErrorState detail={createError} />}
        </div>

        <div>
          {agents.isLoading ? (
            <LoadingState />
          ) : agents.data?.status !== "real" ? (
            <ErrorState detail={agents.data?.detail} />
          ) : list.length === 0 ? (
            <ErrorState detail="Aucun agent créé pour l'instant." />
          ) : (
            <ul className="configured-agent-list">
              {list.map((a) => (
                <li key={a.id} className="configured-agent-list__item">
                  <button className="configured-agent-list__header" onClick={() => setExpandedId(expandedId === a.id ? null : a.id)}>
                    <div>
                      <strong>{a.name}</strong>
                      <span className="mono" style={{ color: "var(--text-faint)" }}> · {a.role}</span>
                      {a.model_role && <span className="mono" style={{ color: "var(--text-faint)" }}> · {a.model_role}</span>}
                    </div>
                    <ConfiguredAgentStatusPill status={a.status} />
                  </button>
                  {expandedId === a.id && <ConfiguredAgentDetail agentId={a.id} onDelete={() => deleteAgent.mutate(a.id)} />}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

function ConfiguredAgentDetail({ agentId, onDelete }: { agentId: string; onDelete: () => void }) {
  const executions = useAgentExecutions(agentId);
  const list = executions.data?.status === "real" ? executions.data.data ?? [] : [];

  return (
    <div className="configured-agent-detail">
      <div className="configured-agent-detail__section">
        <h4>Exécutions récentes</h4>
        {executions.isLoading ? (
          <LoadingState />
        ) : list.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--text-faint)" }}>Aucune exécution pour l'instant.</p>
        ) : (
          <table className="kv-table">
            <thead>
              <tr>
                <th>Mission</th>
                <th>Statut</th>
                <th>Modèle</th>
                <th>Raison</th>
              </tr>
            </thead>
            <tbody>
              {list.map((e) => (
                <tr key={e.id}>
                  <td className="mono">{e.mission_id.slice(0, 8)}</td>
                  <td>{e.status}</td>
                  <td className="mono">{e.model_id ?? "—"}</td>
                  <td>{e.fallback_reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <button className="action-button action-button--live" onClick={onDelete}>
        Supprimer l'agent
      </button>
    </div>
  );
}

/**
 * DÉFINITIONS d'agent (`apex.agent_templates`) — distinctes des instances
 * réelles listées plus bas (`AgentRuntime`, `/agents`). Créer/lister/
 * supprimer un template est réel ; chaque carte porte explicitement
 * `DEFINED` — jamais présentée comme un agent en cours d'exécution.
 */
function AgentTemplatesPanel() {
  const templates = useAgentTemplates();
  const createTemplate = useCreateAgentTemplate();
  const deleteTemplate = useDeleteAgentTemplate();

  const [name, setName] = useState("");
  const [role, setRole] = useState<AgentRole>("coder");
  const [description, setDescription] = useState("");
  const [modelRole, setModelRole] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const list = templates.data?.status === "real" ? templates.data.data ?? [] : [];

  async function handleCreate() {
    setCreateError(null);
    const envelope = await createTemplate.mutateAsync({
      name, role, description, model_role: modelRole || null,
    });
    if (envelope.status === "real") {
      setName("");
      setDescription("");
    } else {
      setCreateError(envelope.detail ?? "Création refusée par le backend.");
    }
  }

  return (
    <Panel title="Templates d'agent (définitions)">
      <div className="two-col">
        <div className="mission-form">
          <label className="mission-form__field">
            <span>Nom</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="ex. Codeur Python" />
          </label>
          <div className="mission-form__row">
            <label className="mission-form__field">
              <span>Rôle</span>
              <select value={role} onChange={(e) => setRole(e.target.value as AgentRole)}>
                {AGENT_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
            <label className="mission-form__field">
              <span>Rôle modèle (optionnel)</span>
              <input value={modelRole} onChange={(e) => setModelRole(e.target.value)} placeholder="ex. fast, coder_main" />
            </label>
          </div>
          <label className="mission-form__field">
            <span>Description</span>
            <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <button
            className="action-button action-button--live"
            disabled={!name.trim() || createTemplate.isPending}
            onClick={handleCreate}
          >
            {createTemplate.isPending ? "Création…" : "Créer le template"}
          </button>
          {createError && <ErrorState detail={createError} />}
        </div>

        <div>
          {templates.isLoading ? (
            <LoadingState />
          ) : templates.data?.status !== "real" ? (
            <ErrorState detail={templates.data?.detail} />
          ) : list.length === 0 ? (
            <ErrorState detail="Aucun template créé pour l'instant." />
          ) : (
            <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
              {list.map((t) => (
                <li
                  key={t.id}
                  style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "8px 12px", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                  }}
                >
                  <div>
                    <strong>{t.name}</strong> <span className="mono" style={{ color: "var(--text-faint)" }}>· {t.role}</span>
                    <div style={{ marginTop: 4 }}>
                      <StatusBadge status="planned" title="Définition seule — spawn réel non câblé" />
                    </div>
                  </div>
                  <button
                    className="action-button action-button--live"
                    onClick={() => deleteTemplate.mutate(t.id)}
                    disabled={deleteTemplate.isPending}
                  >
                    Supprimer
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

const ALL_STATES: CloneState[] = [
  "spawning", "ready", "working", "reporting", "terminating", "terminated", "crashed", "recovering", "failed",
];
const ALL_TYPES: CloneType[] = [
  "research", "learning", "developer", "experiment", "critic", "security", "benchmark", "teacher", "integrator",
];

const SPECIALTY_FILTER_OPTIONS = SPECIALTY_DEFINITIONS.filter(
  (s) => s.id !== "agents" && (s.workspace?.compatibleAgentTypes?.length ?? 0) > 0,
);

type SortKey = "created_desc" | "created_asc" | "elapsed_desc" | "state" | "type";

function toggle<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

function sortAgents(agents: Agent[], key: SortKey): Agent[] {
  const list = [...agents];
  switch (key) {
    case "created_desc":
      return list.sort((a, b) => b.created_at.localeCompare(a.created_at));
    case "created_asc":
      return list.sort((a, b) => a.created_at.localeCompare(b.created_at));
    case "state":
      return list.sort((a, b) => a.state.localeCompare(b.state));
    case "type":
      return list.sort((a, b) => a.type.localeCompare(b.type));
    case "elapsed_desc":
      // Seules les mesures REAL participent au tri ; le reste (UNKNOWN/SIMULATED)
      // reste en fin de liste plutôt que de recevoir une valeur inventée pour trier.
      return list.sort((a, b) => {
        const av = a.elapsed_s.status === "real" ? (a.elapsed_s.value as number) : null;
        const bv = b.elapsed_s.status === "real" ? (b.elapsed_s.value as number) : null;
        if (av === null && bv === null) return 0;
        if (av === null) return 1;
        if (bv === null) return -1;
        return bv - av;
      });
  }
}

/**
 * Agent Center — recherche, filtres (état/type/spécialité), tri, et deux
 * vues (cartes/tableau) sur les données déjà chargées par /agents. Aucun
 * filtre ni tri n'invente de donnée : "spécialité" est une dérivation
 * statique (specialtyForAgentType), pas un champ renvoyé par l'API.
 */
export function AgentsPage() {
  const agents = useAgents();
  const data = agents.data?.data;

  const [search, setSearch] = useState("");
  const [states, setStates] = useState<Set<CloneState>>(new Set());
  const [types, setTypes] = useState<Set<CloneType>>(new Set());
  const [specialtyId, setSpecialtyId] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("created_desc");
  const [view, setView] = useState<"cards" | "table">("cards");

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    let list = data.filter((a) => {
      if (q) {
        const haystack = `${a.clone_id} ${a.mission_id ?? ""} ${a.genome_ref} ${a.type}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (states.size > 0 && !states.has(a.state)) return false;
      if (types.size > 0 && !types.has(a.type)) return false;
      if (specialtyId !== "all") {
        const spec = specialtyForAgentType(a.type);
        if (spec?.id !== specialtyId) return false;
      }
      return true;
    });
    list = sortAgents(list, sortKey);
    return list;
  }, [data, search, states, types, specialtyId, sortKey]);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Agent Center</h1>
          <p className="page__subtitle">AgentRuntime — registre : STUB (cycle de vie réel, isolation OS non garantie)</p>
        </div>
      </div>

      <ConfiguredAgentsPanel />

      <AgentTemplatesPanel />

      <div className="agent-center-toolbar">
        <input
          className="agent-center-search"
          type="search"
          placeholder="Rechercher (id, mission, genome, type)…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />

        <div className="agent-center-filter-group">
          <span className="agent-center-filter-label">État</span>
          {ALL_STATES.map((s) => (
            <button
              key={s}
              className={`chip ${states.has(s) ? "chip--active" : ""}`}
              onClick={() => setStates(toggle(states, s))}
              type="button"
            >
              {s}
            </button>
          ))}
        </div>

        <div className="agent-center-filter-group">
          <span className="agent-center-filter-label">Type</span>
          {ALL_TYPES.map((t) => (
            <button
              key={t}
              className={`chip ${types.has(t) ? "chip--active" : ""}`}
              onClick={() => setTypes(toggle(types, t))}
              type="button"
            >
              {t}
            </button>
          ))}
        </div>

        <label className="agent-center-select">
          <span>Spécialité</span>
          <select value={specialtyId} onChange={(e) => setSpecialtyId(e.target.value)}>
            <option value="all">Toutes</option>
            {SPECIALTY_FILTER_OPTIONS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.icon} {s.label}
              </option>
            ))}
          </select>
        </label>

        <label className="agent-center-select">
          <span>Tri</span>
          <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
            <option value="created_desc">Plus récent</option>
            <option value="created_asc">Plus ancien</option>
            <option value="elapsed_desc">Durée mesurée (desc.)</option>
            <option value="state">État</option>
            <option value="type">Type</option>
          </select>
        </label>

        <div className="agent-center-view-toggle">
          <button className={view === "cards" ? "active" : ""} onClick={() => setView("cards")} type="button">
            Cartes
          </button>
          <button className={view === "table" ? "active" : ""} onClick={() => setView("table")} type="button">
            Tableau
          </button>
        </div>
      </div>

      {agents.isLoading ? (
        <LoadingState />
      ) : agents.data?.status !== "real" ? (
        <ErrorState detail={agents.data?.detail ?? "AgentRuntime indisponible."} />
      ) : !data || data.length === 0 ? (
        <ErrorState detail="Aucun clone n'a encore été spawné sur cette instance." />
      ) : filtered.length === 0 ? (
        <ErrorState detail="Aucun clone ne correspond aux filtres actuels." />
      ) : view === "cards" ? (
        <div className="agent-grid">
          {filtered.map((agent) => (
            <AgentCard key={agent.clone_id} agent={agent} />
          ))}
        </div>
      ) : (
        <div className="agent-table-wrap">
          <table className="kv-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>État</th>
                <th>Spécialité</th>
                <th>Mission</th>
                <th>Durée</th>
                <th>CPU</th>
                <th>Retries</th>
                <th>Dernière erreur</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((a) => {
                const spec = specialtyForAgentType(a.type);
                return (
                  <tr key={a.clone_id}>
                    <td>
                      <Link to={`/agents/${a.clone_id}`} className="mono">
                        {a.type} · {a.clone_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td>
                      <AgentStatePill state={a.state} />
                    </td>
                    <td>{spec ? `${spec.icon} ${spec.label}` : "—"}</td>
                    <td>{a.mission_id ?? "—"}</td>
                    <td>
                      <MeasurementValue measurement={a.elapsed_s} unit=" s" />
                    </td>
                    <td>
                      <MeasurementValue measurement={a.cpu_time_s} unit=" s" />
                    </td>
                    <td>
                      {a.retries_used}/{a.max_retries}
                    </td>
                    <td className="mono">{a.last_error ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
