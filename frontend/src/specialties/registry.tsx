/**
 * Registre final des Specialty consommé par la navigation (Sidebar, App).
 * Compose les métadonnées pures de data.ts avec les composants de page —
 * séparé exprès pour que data.ts reste importable par une page (ex.
 * MissionCenterPage) sans créer de cycle d'import.
 *
 * Ajouter une nouvelle Specialty = une entrée dans data.ts (+ sa page ici
 * si elle en a une) ; Sidebar/App n'ont rien d'autre à changer.
 */

import { AgentsPage } from "../pages/AgentsPage";
import { CodeIntelligencePage } from "../pages/CodeIntelligencePage";
import { DashboardPage } from "../pages/DashboardPage";
import { DesignStudioPage } from "../pages/DesignStudioPage";
import { EvolutionTimelinePage } from "../pages/EvolutionTimelinePage";
import { MissionCenterPage } from "../pages/MissionCenterPage";
import { ProjectManagerPage } from "../pages/ProjectManagerPage";
import { ResourcesPage } from "../pages/ResourcesPage";
import { SecurityGatePage } from "../pages/SecurityGatePage";
import { SystemPage } from "../pages/SystemPage";
import { SPECIALTY_DEFINITIONS } from "./data";
import type { Specialty } from "./types";

const COMPONENTS: Partial<Record<string, Specialty["component"]>> = {
  dashboard: DashboardPage,
  agents: AgentsPage,
  "code-intelligence": CodeIntelligencePage,
  "design-studio": DesignStudioPage,
  "project-manager": ProjectManagerPage,
  "security-gate": SecurityGatePage,
  resources: ResourcesPage,
  "evolution-timeline": EvolutionTimelinePage,
  "mission-center": MissionCenterPage,
  system: SystemPage,
};

export const SPECIALTIES: Specialty[] = SPECIALTY_DEFINITIONS.map((def) => ({
  ...def,
  component: COMPONENTS[def.id],
}));
