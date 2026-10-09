import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useGate, useHealth, useMissions } from "../../api/hooks";
import { IconButton } from "../ui/Button";
import { ApexStatusBadge, deriveApexStatus } from "../status/ApexStatus";
import { SystemVerdictBadge, type SystemVerdictView } from "../status/SystemVerdict";
import { GateStatusBadge, HealthStatusBadge } from "../status/StatusBadge";
import { ThemeToggle } from "../../theme/ThemeToggle";
import { GlobalSearch } from "./GlobalSearch";
import { useSidebar } from "./SidebarContext";
import "./layout.css";

export function Topbar({ verdict }: { verdict: SystemVerdictView }) {
  const health = useHealth();
  const gate = useGate();
  const missions = useMissions();
  const { setMobileOpen } = useSidebar();

  const missionsData = missions.data?.status === "real" ? missions.data.data ?? [] : [];
  const systemStatus = deriveApexStatus(health.data?.data?.status ?? null, health.data?.status === "real", missionsData);

  return (
    <header className="topbar">
      <div className="topbar__left">
        <IconButton
          className="topbar__hamburger"
          aria-label="Ouvrir la navigation"
          onClick={() => setMobileOpen(true)}
        >
          ☰
        </IconButton>
        <div className="topbar__title">APEX Control Center</div>
        <Link to="/" className="topbar__assistant-link">
          ← Assistant
        </Link>
      </div>
      <GlobalSearch />
      <div className="topbar__status">
        <StatusGroup label="État">
          <SystemVerdictBadge view={verdict} />
        </StatusGroup>
        <StatusGroup label="Activité">
          <ApexStatusBadge status={systemStatus} />
        </StatusGroup>
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
        <ThemeToggle />
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
