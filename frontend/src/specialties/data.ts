/**
 * Métadonnées PURES des Specialty — aucune référence à un composant React
 * ici, pour que ce module reste importable par n'importe quelle page (ex.
 * MissionCenterPage a besoin de la liste des spécialités pour son
 * formulaire) SANS créer de cycle d'import avec specialties/registry.tsx
 * (qui, lui, importe les pages ET ce fichier pour construire la liste
 * finale utilisée par la navigation).
 */

import type { CloneType } from "../api/types";
import type { DevStage, SpecialtyCategory, SpecialtyStatus, SpecialtyWorkspaceMeta } from "./types";

export interface SpecialtyDefinition {
  id: string;
  label: string;
  icon: string;
  description: string;
  path: string;
  status: SpecialtyStatus;
  devStage: DevStage;
  category: SpecialtyCategory;
  workspace?: SpecialtyWorkspaceMeta;
}

/** Libellé + ordre d'affichage de chaque catégorie de Sidebar — la SEULE
 * source de vérité pour le regroupement visuel (Sidebar.tsx ne fait que
 * `Object.entries` dessus, jamais une liste de sections codée en dur
 * ailleurs). */
export const SPECIALTY_CATEGORIES: Record<SpecialtyCategory, string> = {
  workspace: "Workspace",
  intelligence: "Intelligence",
  cognition: "Cognition",
  creative: "Creative",
  data: "Data",
  system: "System",
};

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
    label: "Home",
    icon: "🏠",
    description: "Vue d'ensemble : santé système, Gate, agents, événements récents.",
    path: "/",
    status: "real",
    devStage: "connected",
    category: "workspace",
  },
  {
    id: "chat",
    label: "Chat",
    icon: "💬",
    description: "Conversation réelle avec APEX — Model Router, provider réel, sauvegarde des échanges. La liste des conversations vit dans ce workspace, jamais dans une page séparée.",
    path: "/chat",
    status: "real",
    devStage: "connected",
    category: "workspace",
  },
  {
    id: "agents",
    label: "Agents / Clones",
    icon: "🤖",
    description: "AgentRuntime — recherche, filtres, détail complet par clone.",
    path: "/agents",
    status: "real",
    devStage: "connected",
    category: "intelligence",
    workspace: {
      compatibleAgentTypes: dev([
        "research", "learning", "developer", "experiment",
        "critic", "security", "benchmark", "teacher", "integrator",
      ]),
      requiredPermissions: ["voir les clones (lecture seule, tous rôles)"],
    },
  },
  {
    id: "models",
    label: "Models / Providers",
    icon: "🧩",
    description: "Catalogue réel des modèles/providers déclarés (apex.models.catalog) — capacités, disponibilité.",
    path: "/models",
    status: "real",
    devStage: "connected",
    category: "system",
  },
  {
    id: "code-intelligence",
    label: "Code Intelligence",
    icon: "💻",
    description: "Analyse, revue et génération de code assistées par clones DEVELOPER.",
    path: "/code-intelligence",
    status: "planned",
    devStage: "ui-scaffold",
    category: "intelligence",
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
    category: "creative",
    workspace: {
      capabilities: ["composition visuelle", "export d'artefacts"],
      tools: ["canvas", "bibliothèque de composants"],
    },
  },
  {
    id: "images",
    label: "Images",
    icon: "🖼️",
    description: "Génération/édition d'images — aucun provider image réel connecté à ce jour.",
    path: "/images",
    status: "planned",
    devStage: "concept",
    category: "creative",
  },
  {
    id: "media",
    label: "Media",
    icon: "🎬",
    description: "Composition vidéo/média — aucune capacité réelle à ce jour.",
    path: "/media",
    status: "planned",
    devStage: "concept",
    category: "creative",
  },
  {
    id: "audio",
    label: "Audio",
    icon: "🎙️",
    description: "Synthèse/transcription audio — aucune capacité réelle à ce jour.",
    path: "/audio",
    status: "planned",
    devStage: "concept",
    category: "creative",
  },
  {
    id: "project-manager",
    label: "Projects",
    icon: "📁",
    description: "Regroupement réel de conversations par projet — tâches/kanban/timeline restent planifiés.",
    path: "/project-manager",
    status: "real",
    devStage: "connected",
    category: "workspace",
    workspace: {
      capabilities: ["créer un projet", "associer des conversations"],
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
    category: "intelligence",
    workspace: { compatibleAgentTypes: dev(["research"]) },
  },
  {
    id: "multi-agent-teams",
    label: "Multi-IA / Teams",
    icon: "🕸️",
    description: "AgentTeam (apex.agent_registry) est réel côté backend (créer/lister une équipe d'agents) mais n'a encore aucune interface dédiée — aucune orchestration multi-agent autonome n'existe.",
    path: "/multi-agent-teams",
    status: "planned",
    devStage: "concept",
    category: "intelligence",
  },
  {
    id: "learning-center",
    label: "Learning",
    icon: "🧠",
    description: "Apprentissage par Domain — voir docs/COGNITIVE_ARCHITECTURE.md §3.",
    path: "/learning-center",
    status: "planned",
    devStage: "concept",
    category: "cognition",
    workspace: { compatibleAgentTypes: dev(["learning", "teacher"]) },
  },
  {
    id: "skills",
    label: "Skills",
    icon: "🧩",
    description: "SkillRegistry (apex.skills) est réel côté backend (proposition/promotion/rejet) mais n'a encore aucune interface dédiée.",
    path: "/skills",
    status: "planned",
    devStage: "concept",
    category: "cognition",
  },
  {
    id: "weaknesses",
    label: "Weaknesses",
    icon: "📉",
    description: "WeaknessDetector (apex.weakness) est réel côté backend mais n'a encore aucune interface dédiée.",
    path: "/weaknesses",
    status: "planned",
    devStage: "concept",
    category: "cognition",
  },
  {
    id: "experiment-lab",
    label: "Experiment Lab",
    icon: "🧪",
    description: "Experiment Engine — hypothèses, protocoles, résultats.",
    path: "/experiment-lab",
    status: "planned",
    devStage: "concept",
    category: "cognition",
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
    category: "cognition",
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
    category: "cognition",
  },
  {
    id: "memory-knowledge-graph",
    label: "Memory / Knowledge Graph",
    icon: "🧠",
    description: "Graphe de connaissances partagé entre clones et domaines.",
    path: "/memory-knowledge-graph",
    status: "planned",
    devStage: "concept",
    category: "cognition",
  },
  {
    id: "security-gate",
    label: "Security / Gate",
    icon: "🛡️",
    description: "apex.gate — mesures matérielles/environnement réelles.",
    path: "/security-gate",
    status: "real",
    devStage: "connected",
    category: "system",
  },
  {
    id: "files",
    label: "Files",
    icon: "🗂️",
    description: "Réception de fichiers (texte, images, captures d'écran, voix, vidéo) — aujourd'hui texte seul possible via le Chat.",
    path: "/files",
    status: "planned",
    devStage: "ui-scaffold",
    category: "data",
  },
  {
    id: "rag",
    label: "RAG",
    icon: "📚",
    description: "Retrieval-augmented generation — aucun pipeline réel connecté à ce jour.",
    path: "/rag",
    status: "planned",
    devStage: "concept",
    category: "data",
  },
  {
    id: "datasets",
    label: "Datasets",
    icon: "🗃️",
    description: "Jeux de données pour l'évaluation/l'entraînement — aucune interface dédiée à ce jour.",
    path: "/datasets",
    status: "planned",
    devStage: "concept",
    category: "data",
  },
  {
    id: "evals",
    label: "Evals",
    icon: "🧾",
    description: "Résultats d'évaluation (EvalRunner/JudgeTier) — aucune interface dédiée pour l'instant.",
    path: "/evals",
    status: "planned",
    devStage: "concept",
    category: "cognition",
  },
  {
    id: "hardware",
    label: "Hardware",
    icon: "🖥️",
    description: "GPU/VRAM/CPU/RAM réellement mesurés — sous-ensemble matériel de apex.gate.",
    path: "/hardware",
    status: "real",
    devStage: "connected",
    category: "system",
  },
  {
    id: "resources",
    label: "Resources",
    icon: "⚡",
    description: "Consommation CPU/RAM/GPU réellement mesurée (jamais estimée).",
    path: "/resources",
    status: "real",
    devStage: "connected",
    category: "system",
  },
  {
    id: "evolution-timeline",
    label: "Logs",
    icon: "📜",
    description: "audit_log — chaque décision de gouvernance, en lecture seule.",
    path: "/evolution-timeline",
    status: "real",
    devStage: "connected",
    category: "system",
  },
  {
    id: "mission-center",
    label: "Missions",
    icon: "🎯",
    description: "Mission Engine — création, liste, consultation et annulation réelles (PostgreSQL).",
    path: "/mission-center",
    status: "real",
    devStage: "connected",
    category: "intelligence",
    workspace: {
      capabilities: ["composition de mission", "sélection de clone compatible", "annulation"],
      requiredPermissions: ["validation humaine requise avant COMPLETED (ADR-0011) — validate/reject pas encore atteignables depuis l'UI"],
    },
  },
  {
    id: "system",
    label: "Settings",
    icon: "⚙️",
    description: "Registre des abstractions APEX et détail de santé des composants.",
    path: "/system",
    status: "real",
    devStage: "connected",
    category: "system",
  },
];
