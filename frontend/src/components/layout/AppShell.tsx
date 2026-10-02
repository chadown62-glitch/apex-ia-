import type { ReactNode } from "react";

import { SPECIALTIES } from "../../specialties/registry";
import { useSystemVerdict } from "../status/SystemVerdict";
import { Sidebar } from "./Sidebar";
import { SidebarProvider } from "./SidebarContext";
import { Topbar } from "./Topbar";
import "./layout.css";

export function AppShell({ children }: { children: ReactNode }) {
  const { view } = useSystemVerdict();
  return (
    <SidebarProvider>
      <div className="app-shell">
        <Sidebar specialties={SPECIALTIES} />
        <div className="app-shell__main">
          <Topbar verdict={view} />
          {(view.kind === "session" || view.kind === "unreachable") && (
            <div className="app-shell__alert" role="alert">
              <strong>{view.label}</strong> — {view.kind === "session" ? view.detail : `${view.detail}. Vérifiez dans le Panel APEX que le backend tourne, puis rouvrez APEX depuis le Panel.`}
            </div>
          )}
          <main className="app-shell__content">{children}</main>
        </div>
      </div>
    </SidebarProvider>
  );
}
