/**
 * Coque de l'assistant APEX : fond (Backdrop), barre latérale coulissante/repliable, zone principale
 * (`<Outlet />` : accueil, conversation, projet, paramètres). Le verdict système est le même que celui du
 * Panel (useSystemVerdict) : une session expirée ou un backend injoignable est annoncé, jamais masqué.
 */

import { useCallback, useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { useSystemVerdict } from "../components/status/SystemVerdict";
import { AssistantSidebar } from "./AssistantSidebar";
import { Backdrop } from "./Backdrop";
import { IconMenu } from "./Icons";
import "./assistant.css";

const COLLAPSED_KEY = "apex-assistant-sidebar-collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function AssistantLayout() {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const { view } = useSystemVerdict();
  const location = useLocation();

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* stockage indisponible : le choix vaut pour cette session. */
      }
      return next;
    });
  }, []);

  useEffect(() => setMobileOpen(false), [location.pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  return (
    <div className={`ax-shell ${collapsed ? "is-collapsed" : ""}`}>
      <Backdrop />
      <a className="ax-skip" href="#ax-main">
        Aller au contenu
      </a>
      <AssistantSidebar
        collapsed={collapsed && !mobileOpen}
        onToggleCollapsed={toggleCollapsed}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />
      <div className="ax-main">
        <header className="ax-mobilebar">
          <button type="button" className="ax-icon-btn" onClick={() => setMobileOpen(true)} aria-label="Ouvrir la barre latérale">
            <IconMenu />
          </button>
          <span className="ax-mobilebar__title">APEX</span>
        </header>
        {(view.kind === "session" || view.kind === "unreachable") && (
          <div className="ax-banner ax-banner--error" role="alert">
            <strong>{view.label}</strong>
            <span>
              {view.kind === "session"
                ? view.detail
                : `${view.detail}. Vérifiez dans le Panel APEX que le moteur est démarré, puis rouvrez APEX depuis le Panel.`}
            </span>
          </div>
        )}
        <main id="ax-main" className="ax-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
