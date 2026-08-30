import type { ReactNode } from "react";

import { useGate, useHealth } from "../../api/hooks";
import { GateStatusBadge, HealthStatusBadge } from "../status/StatusBadge";
import { GlobalSearch } from "./GlobalSearch";
import "./layout.css";

export function Topbar() {
  const health = useHealth();
  const gate = useGate();

  return (
    <header className="topbar">
      <div className="topbar__title">APEX Control Center</div>
      <GlobalSearch />
      <div className="topbar__status">
        <StatusGroup label="Système">
          {health.data?.data ? (
            <HealthStatusBadge status={health.data.data.status} />
          ) : (
            <span className="topbar__pending">…</span>
          )}
        </StatusGroup>
        <StatusGroup label="Gate">
          {gate.data?.data ? (
            <GateStatusBadge status={gate.data.data.gate_status} />
          ) : gate.data ? (
            <GateStatusBadge status="UNKNOWN" />
          ) : (
            <span className="topbar__pending">…</span>
          )}
        </StatusGroup>
      </div>
    </header>
  );
}

function StatusGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="topbar__group">
      <span className="topbar__group-label">{label}</span>
      {children}
    </div>
  );
}
