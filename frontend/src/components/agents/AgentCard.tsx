import { Link } from "react-router-dom";
import type { Agent } from "../../api/types";
import "./agents.css";

const STATE_LABEL: Record<Agent["state"], string> = {
  spawning: "Spawning",
  ready: "Ready",
  working: "Working",
  reporting: "Reporting",
  terminating: "Terminating",
  terminated: "Terminated",
  crashed: "Crashed",
  recovering: "Recovering",
  failed: "Failed",
};

const STATE_CLASS: Record<Agent["state"], string> = {
  spawning: "pending",
  ready: "pending",
  working: "active",
  reporting: "active",
  terminating: "pending",
  terminated: "done",
  crashed: "error",
  recovering: "pending",
  failed: "error",
};

export function AgentStatePill({ state }: { state: Agent["state"] }) {
  return <span className={`agent-state agent-state--${STATE_CLASS[state]}`}>{STATE_LABEL[state]}</span>;
}

export function AgentCard({ agent }: { agent: Agent }) {
  return (
    <Link to={`/agents/${agent.clone_id}`} className="agent-card">
      <div className="agent-card__top">
        <span className="agent-card__type">{agent.type}</span>
        <AgentStatePill state={agent.state} />
      </div>
      <div className="agent-card__id mono">{agent.clone_id.slice(0, 8)}</div>
      <div className="agent-card__mission">{agent.mission_id ?? "— sans mission —"}</div>
      <div className="agent-card__footer">
        <span className="mono">{agent.genome_ref.slice(0, 16)}</span>
        {agent.retries_used > 0 && (
          <span className="agent-card__retries">
            {agent.retries_used}/{agent.max_retries} retries
          </span>
        )}
      </div>
    </Link>
  );
}
