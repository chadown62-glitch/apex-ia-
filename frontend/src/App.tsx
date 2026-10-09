import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";

import { AppearanceSync } from "./assistant/AppearanceSync";
import { AssistantLayout } from "./assistant/AssistantLayout";
import { ConversationView } from "./assistant/ConversationView";
import { HomeView } from "./assistant/HomeView";
import { ProjectView } from "./assistant/ProjectView";
import { SettingsView } from "./assistant/SettingsView";
import { AppShell } from "./components/layout/AppShell";
import { ToastProvider } from "./components/ui/Toast";
import { AgentDetailPage } from "./pages/AgentDetailPage";
import { PlannedSpecialtyPage } from "./pages/PlannedSpecialtyPage";
import { SPECIALTIES } from "./specialties/registry";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
    },
  },
});

function ControlCenterLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

/**
 * Deux espaces, une seule application :
 * - l'assistant (accueil `/`, conversations `/c/:id`, projets `/p/:id`, paramètres `/settings`) ;
 * - le centre de contrôle (`/control` et les routes des Specialty, issues du registre — ajouter une
 *   Specialty dans registry.tsx suffit à lui donner une route, réelle ou PLANNED).
 * L'apparence (Night/Dune) est commune et alignée sur les paramètres enregistrés (AppearanceSync).
 */
export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AppearanceSync />
        <Routes>
          <Route element={<AssistantLayout />}>
            <Route path="/" element={<HomeView />} />
            <Route path="/c/:conversationId" element={<ConversationView />} />
            <Route path="/p/:projectId" element={<ProjectView />} />
            <Route path="/settings" element={<SettingsView />} />
          </Route>
          <Route element={<ControlCenterLayout />}>
            {SPECIALTIES.map((specialty) => {
              const Component = specialty.component;
              return (
                <Route
                  key={specialty.id}
                  path={specialty.path}
                  element={Component ? <Component /> : <PlannedSpecialtyPage specialty={specialty} />}
                />
              );
            })}
            <Route path="/agents/:cloneId" element={<AgentDetailPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ToastProvider>
    </QueryClientProvider>
  );
}
