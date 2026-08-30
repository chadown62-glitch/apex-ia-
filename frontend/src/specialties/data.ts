/**
 * Métadonnées PURES des Specialty — aucune référence à un composant React
 * ici, pour que ce module reste importable par n'importe quelle page (ex.
 * MissionCenterPage a besoin de la liste des spécialités pour son
 * formulaire) SANS créer de cycle d'import avec specialties/registry.tsx
 * (qui, lui, importe les pages ET ce fichier pour construire la liste
 * finale utilisée par la navigation).
 */

import type { CloneType } from "../api/types";
import type { DevStage, SpecialtyStatus, SpecialtyWorkspaceMeta } from "./types";

export interface SpecialtyDefinition {
  id: string;
  label: string;
  icon: string;
  description: string;
  path: string;
  status: SpecialtyStatus;
  devStage: DevStage;
  workspace?: SpecialtyWorkspaceMeta;
}

const dev = (types: CloneType[]): CloneType[] => types;

/** Spécialité associée à un type de clone — dérivation pure, jamais une
 * mesure : sert à afficher/filtrer "spécialité" dans Agent Center sans
 * inventer un champ que le backend ne fournit pas. */
export function specialtyForAgentType(type: CloneType): SpecialtyDefinition | undefined {
  return SPECIALTY_DEFINITIONS.find((s) => s.id !== "agents" && s.workspace?.compatibleAgentTypes?.includes(type));
}

export const SPECIALTY_DEFINITIONS: SpecialtyDefinition[] = [
  {
    id: "dashboard",
    label: "Dashboard",
    icon: "🏠",
    description: "Vue d'ensemble : santé système, Gate, agents, événements récents.",
    path: "/",
    status: "real",
    devStage: "connected",
  },
  {
    id: "agents",
    label: "Agents / Clones",
    icon: "🤖",
    description: "AgentRuntime — recherche, filtres, détail complet par clone.",
    path: "/agents",
    status: "real",
    devStage: "connected",
    workspace: {
      compatibleAgentTypes: dev([
        "research", "learning", "developer", "experiment",
        "critic", "security", "benchmark", "teacher", "integrator",
      ]),
      requiredPermissions: ["voir les clones (lecture seule, tous rôles)"],
    },
  },
  {
    id: "code-intelligence",
    label: "Code Intelligence",
    icon: "💻",
    description: "Analyse, revue et génération de code assistées par clones DEVELOPER.",
    path: "/code-intelligence",
    status: "planned",
    devStage: "ui-scaffold",
    workspace: {
      capabilities: ["analyse statique", "revue de diff", "exécution de tests existants"],
      tools: ["éditeur diff", "linter", "runner de tests"],
      compatibleAgentTypes: dev(["developer", "critic"]),
      requiredPermissions: ["MODIFY_CORE refusé par défaut (PolicyEngine, absolute-deny)"],
    },
  },
  {
    id: "design-studio",
    label: "Design Studio",
    icon: "🎨",
    description: "Ateliers de conception d'interfaces et d'expériences.",
    path: "/design-studio",
    status: "planned",
    devStage: "ui-scaffold",
    workspace: {
      capabilities: ["composition visuelle", "export d'artefacts"],
      tools: ["canvas", "bibliothèque de composants"],
    },
  },
  {
    id: "project-manager",
    label: "Project Manager",
    icon: "📁",
    description: "Suivi de missions, jalons et livrables multi-clones.",
    path: "/project-manager",
    status: "planned",
    devStage: "ui-scaffold",
    workspace: {
      capabilities: ["suivi de tâches", "kanban", "timeline de projet"],
      compatibleAgentTypes: dev(["integrator"]),
    },
  },
  {
    id: "research",
    label: "Research",
    icon: "🔬",
    description: "Missions d'exploration confiées aux clones RESEARCH.",
    path: "/research",
    status: "planned",
    devStage: "concept",
    workspace: { compatibleAgentTypes: dev(["research"]) },
  },
  {
    id: "learning-center",
    label: "Learning Center",
    icon: "🧠",
    description: "Apprentissage par Domain — voir docs/COGNITIVE_ARCHITECTURE.md §3.",
    path: "/learning-center",
    status: "planned",
    devStage: "concept",
    workspace: { compatibleAgentTypes: dev(["learning", "teacher"]) },
  },
  {
    id: "experiment-lab",
    label: "Experiment Lab",
    icon: "🧪",
    description: "Experiment Engine — hypothèses, protocoles, résultats.",
    path: "/experiment-lab",
    status: "planned",
    devStage: "concept",
    workspace: { compatibleAgentTypes: dev(["experiment"]) },
  },
  {
    id: "benchmark-center",
    label: "Benchmark Center",
    icon: "📊",
    description: "Comparaisons de performance entre génomes et versions.",
    path: "/benchmark-center",
    status: "planned",
    devStage: "concept",
    workspace: { compatibleAgentTypes: dev(["benchmark"]) },
  },
  {
    id: "genome-evolution",
    label: "Genome / Evolution",
    icon: "🧬",
    description: "Boucle d'auto-amélioration — proposition, validation, promotion.",
    path: "/genome-evolution",
    status: "planned",
    devStage: "concept",
  },
  {
    id: "memory-knowledge-graph",
    label: "Memory / Knowledge Graph",
    icon: "🧠",
    description: "Graphe de connaissances partagé entre clones et domaines.",
    path: "/memory-knowledge-graph",
    status: "planned",
    devStage: "concept",
  },
  {
    id: "security-gate",
    label: "Security / Gate",
    icon: "🛡️",
    description: "apex.gate — mesures matérielles/environnement réelles.",
    path: "/security-gate",
    status: "real",
    devStage: "connected",
  },
  {
    id: "resources",
    label: "Resources",
    icon: "⚡",
    description: "Consommation CPU/RAM/GPU réellement mesurée (jamais estimée).",
    path: "/resources",
    status: "real",
    devStage: "connected",
  },
  {
    id: "evolution-timeline",
    label: "Evolution Timeline",
    icon: "📜",
    description: "audit_log — chaque décision de gouvernance, en lecture seule.",
    path: "/evolution-timeline",
    status: "real",
    devStage: "connected",
  },
  {
    id: "mission-center",
    label: "Mission Center",
    icon: "🎯",
    description: "Mission Engine — création, liste, consultation et annulation réelles (PostgreSQL).",
    path: "/mission-center",
    status: "real",
    devStage: "connected",
    workspace: {
      capabilities: ["composition de mission", "sélection de clone compatible", "annulation"],
      requiredPermissions: ["validation humaine requise avant COMPLETED (ADR-0011) — validate/reject pas encore atteignables depuis l'UI"],
    },
  },
  {
    id: "system",
    label: "System / Configuration",
    icon: "⚙️",
    description: "Registre des abstractions APEX et détail de santé des composants.",
    path: "/system",
    status: "real",
    devStage: "connected",
  },
];
