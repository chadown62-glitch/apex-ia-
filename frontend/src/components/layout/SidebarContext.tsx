import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const COLLAPSE_STORAGE_KEY = "apex-sidebar-collapsed";

interface SidebarContextValue {
  /** Réduction persistante (desktop) — icônes seules, largeur fixe réduite. */
  collapsed: boolean;
  toggleCollapsed: () => void;
  /** Ouverture éphémère (mobile/tablette) — panneau hors-écran, jamais persistée
   * entre sessions : un état d'affichage temporaire, pas une préférence. */
  mobileOpen: boolean;
  setMobileOpen: (open: boolean) => void;
}

const SidebarContext = createContext<SidebarContextValue | null>(null);

function readStoredCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(() => readStoredCollapsed());
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? "1" : "0");
    } catch {
      /* stockage indisponible — la préférence reste active pour cette session. */
    }
  }, [collapsed]);

  return (
    <SidebarContext.Provider
      value={{
        collapsed,
        toggleCollapsed: () => setCollapsed((v) => !v),
        mobileOpen,
        setMobileOpen,
      }}
    >
      {children}
    </SidebarContext.Provider>
  );
}

export function useSidebar(): SidebarContextValue {
  const ctx = useContext(SidebarContext);
  if (!ctx) throw new Error("useSidebar doit être utilisé sous <SidebarProvider>");
  return ctx;
}
