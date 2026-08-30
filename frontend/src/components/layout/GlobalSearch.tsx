import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useAgents } from "../../api/hooks";
import { SPECIALTIES } from "../../specialties/registry";
import "./global-search.css";

/**
 * Recherche client uniquement — filtre le registre statique des Specialty
 * et les agents déjà chargés par /agents. Aucun nouvel appel réseau,
 * aucune donnée inventée : si /agents n'a rien renvoyé de "real", la
 * section agents reste simplement vide.
 */
export function GlobalSearch() {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const agents = useAgents();
  const containerRef = useRef<HTMLDivElement>(null);

  const q = query.trim().toLowerCase();

  const specialtyMatches = useMemo(() => {
    if (!q) return [];
    return SPECIALTIES.filter((s) => `${s.label} ${s.description}`.toLowerCase().includes(q)).slice(0, 6);
  }, [q]);

  const agentMatches = useMemo(() => {
    if (!q || agents.data?.status !== "real" || !agents.data.data) return [];
    return agents.data.data
      .filter((a) => `${a.clone_id} ${a.type} ${a.mission_id ?? ""}`.toLowerCase().includes(q))
      .slice(0, 6);
  }, [q, agents.data]);

  const hasResults = specialtyMatches.length > 0 || agentMatches.length > 0;

  return (
    <div
      className="global-search"
      ref={containerRef}
      onBlur={(e) => {
        if (!containerRef.current?.contains(e.relatedTarget as Node)) setOpen(false);
      }}
    >
      <input
        type="search"
        className="global-search__input"
        placeholder="Rechercher une spécialité, un agent…"
        value={query}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
      />
      {open && q && (
        <div className="global-search__results">
          {!hasResults ? (
            <div className="global-search__empty">Aucun résultat pour « {query} »</div>
          ) : (
            <>
              {specialtyMatches.length > 0 && (
                <div className="global-search__group">
                  <div className="global-search__group-label">Spécialités</div>
                  {specialtyMatches.map((s) => (
                    <Link key={s.id} to={s.path} className="global-search__item" onClick={() => setOpen(false)}>
                      <span>{s.icon}</span>
                      <span>{s.label}</span>
                      {s.status === "planned" && <span className="global-search__tag">PLANNED</span>}
                    </Link>
                  ))}
                </div>
              )}
              {agentMatches.length > 0 && (
                <div className="global-search__group">
                  <div className="global-search__group-label">Agents</div>
                  {agentMatches.map((a) => (
                    <Link key={a.clone_id} to={`/agents/${a.clone_id}`} className="global-search__item" onClick={() => setOpen(false)}>
                      <span className="mono">{a.clone_id.slice(0, 8)}</span>
                      <span>{a.type}</span>
                      <span className="global-search__tag">{a.state}</span>
                    </Link>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
