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
import { BrainLivePage } from "../pages/BrainLivePage";
import { ChatPage } from "../pages/ChatPage";
import { CodeIntelligencePage } from "../pages/CodeIntelligencePage";
import { DashboardPage } from "../pages/DashboardPage";
import { DesignStudioPage } from "../pages/DesignStudioPage";
import { EvolutionTimelinePage } from "../pages/EvolutionTimelinePage";
import { ExperimentLabPage } from "../pages/ExperimentLabPage";
import { FilesPage } from "../pages/FilesPage";
import { HardwarePage } from "../pages/HardwarePage";
import { MissionCenterPage } from "../pages/MissionCenterPage";
import { ModelsPage } from "../pages/ModelsPage";
import { ProjectManagerPage } from "../pages/ProjectManagerPage";
import { ResourcesPage } from "../pages/ResourcesPage";
import { SecurityGatePage } from "../pages/SecurityGatePage";
import { SkillsPage } from "../pages/SkillsPage";
import { SystemPage } from "../pages/SystemPage";
import { WeaknessesPage } from "../pages/WeaknessesPage";
import { SPECIALTY_DEFINITIONS } from "./data";
import type { Specialty } from "./types";

const COMPONENTS: Partial<Record<string, Specialty["component"]>> = {
  dashboard: DashboardPage,
  chat: ChatPage,
  agents: AgentsPage,
  models: ModelsPage,
  "code-intelligence": CodeIntelligencePage,
  "design-studio": DesignStudioPage,
  "project-manager": ProjectManagerPage,
  "security-gate": SecurityGatePage,
  resources: ResourcesPage,
  hardware: HardwarePage,
  files: FilesPage,
  "evolution-timeline": EvolutionTimelinePage,
  "mission-center": MissionCenterPage,
  system: SystemPage,
  skills: SkillsPage,
  weaknesses: WeaknessesPage,
  "experiment-lab": ExperimentLabPage,
  "brain-live": BrainLivePage,
};

export const SPECIALTIES: Specialty[] = SPECIALTY_DEFINITIONS.map((def) => ({
  ...def,
  component: COMPONENTS[def.id],
}));
