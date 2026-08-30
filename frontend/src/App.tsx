import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/layout/AppShell";
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

/**
 * Toutes les routes de premier niveau viennent du registre des Specialty —
 * ajouter une Specialty (registry.tsx) suffit à lui donner une route,
 * réelle ou PLANNED. `/agents/:cloneId` est une sous-route de la Specialty
 * "agents", pas une Specialty à part entière.
 */
export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell>
        <Routes>
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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell>
    </QueryClientProvider>
  );
}
