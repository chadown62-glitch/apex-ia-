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
import { MemoryPage } from "../pages/MemoryPage";
import { TaskRunsPage } from "../pages/TaskRunsPage";
import { WeaknessesPage } from "../pages/WeaknessesPage";
import { ResearchOnly } from "../components/common/ResearchOnly";
import { SPECIALTY_DEFINITIONS } from "./data";
import type { Specialty } from "./types";

function researchOnly(Page: () => JSX.Element, feature: string): Specialty["component"] {
  return function ResearchPage() {
    return (
      <ResearchOnly feature={feature}>
        <Page />
      </ResearchOnly>
    );
  };
}

const COMPONENTS: Partial<Record<string, Specialty["component"]>> = {
  dashboard: DashboardPage,
  chat: ChatPage,
  "task-runs": TaskRunsPage,
  memory: MemoryPage,
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
  // Pages de l'extension RECHERCHE : absentes du Runtime packagé, elles le disent (ResearchOnly).
  skills: researchOnly(SkillsPage, "Skills"),
  weaknesses: researchOnly(WeaknessesPage, "Faiblesses"),
  "experiment-lab": researchOnly(ExperimentLabPage, "Experiment Lab"),
  "brain-live": researchOnly(BrainLivePage, "Brain Live"),
};

export const SPECIALTIES: Specialty[] = SPECIALTY_DEFINITIONS.map((def) => ({
  ...def,
  component: COMPONENTS[def.id],
}));
