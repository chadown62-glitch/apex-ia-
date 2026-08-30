import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useAgents } from "../api/hooks";
import type { Agent, CloneState, CloneType } from "../api/types";
import { AgentCard, AgentStatePill } from "../components/agents/AgentCard";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { MeasurementValue } from "../components/status/MeasurementValue";
import { specialtyForAgentType, SPECIALTY_DEFINITIONS } from "../specialties/data";
import "./pages.css";
import "./agent-center.css";

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
