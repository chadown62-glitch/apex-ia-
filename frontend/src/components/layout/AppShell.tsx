import type { ReactNode } from "react";

import { SPECIALTIES } from "../../specialties/registry";
import { Sidebar } from "./Sidebar";
import { SidebarProvider } from "./SidebarContext";
import { Topbar } from "./Topbar";
import "./layout.css";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SidebarProvider>
      <div className="app-shell">
        <Sidebar specialties={SPECIALTIES} />
        <div className="app-shell__main">
          <Topbar />
          <main className="app-shell__content">{children}</main>
        </div>
      </div>
    </SidebarProvider>
  );
}
