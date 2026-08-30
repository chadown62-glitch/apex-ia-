"""Registre des abstractions APEX.

Ce module est la réponse structurelle aux principes verrouillés 12 et 15 : il
est la source unique de vérité sur **ce qui existe réellement** par opposition
à ce qui est prévu. Il est lu par `apex doctor`, par le rapport de fin de phase
et, plus tard, par le Dashboard.

Un état n'est pas une intention : il est vérifié par un test de contrat
(`tests/test_interfaces_registry.py`) qui échoue si une abstraction déclarée
IMPLEMENTED n'a pas d'implémentation importable, ou si une abstraction déclarée
PLANNED en possède une. Le registre ne peut donc pas mentir sur la durée.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import Enum


class ImplStatus(str, Enum):
    #: Interface définie ET implémentation réelle utilisable dès maintenant.
    IMPLEMENTED = "implemented"
    #: Interface définie ; implémentation triviale ou partielle explicitement signalée.
    STUB = "stub"
    #: Interface définie, aucune implémentation ; lève NotImplementedYet.
    DEFINED = "defined"
    #: Rien encore, phase de définition annoncée.
    PLANNED = "planned"


@dataclass(frozen=True)
class Abstraction:
    name: str
    purpose: str
    phase_defined: int
    phase_implemented: int
    status: ImplStatus
    fallback: str
    module: str | None = None
    notes: str = ""

    @property
    def is_available(self) -> bool:
        return self.status in (ImplStatus.IMPLEMENTED, ImplStatus.STUB)


#: Les 23 abstractions arrêtées en Phase 0 (addendum, §6).
#: `status` reflète l'état RÉEL à la fin de la phase 1 — il sera mis à jour
#: phase par phase, et jamais par anticipation.
ABSTRACTIONS: tuple[Abstraction, ...] = (
    Abstraction(
        "ModelProvider", "génération, streaming, comptage de tokens", 4, 6,
        ImplStatus.PLANNED, "Ollama local",
    ),
    Abstraction(
        "EmbeddingProvider", "vectorisation pour la mémoire", 4, 8,
        ImplStatus.PLANNED, "bge-m3 local",
    ),
    Abstraction(
        "ArtifactStore", "workspaces adressés par contenu", 3, 3,
        ImplStatus.STUB, "MinIO local",
        module="apex.artifacts.provider",
        notes="STUB et non IMPLEMENTED délibérément (étape 10) : principe 5 "
              "(identité par hash SHA-256, format canonique `sha256:<64 hex>`, "
              "jamais par chemin) réel et testé pour "
              "`LocalFilesystemArtifactStore` — création paresseuse du "
              "répertoire, écriture atomique (fichier temporaire + "
              "`os.replace`), vérification d'intégrité systématique à la "
              "lecture, chemin filesystem TOUJOURS dérivé d'un hash déjà "
              "validé (path traversal structurellement impossible, pas "
              "seulement filtré). Aucun quota — limitation assumée et "
              "documentée, pas un oubli. `MinioArtifactStore` est "
              "architecturé (même contrat `ArtifactStoreProvider`, "
              "`detect()` sonde une vraie connexion TCP) mais ses "
              "opérations de stockage lèvent `NotImplementedYet` : MinIO "
              "n'est pas joignable sur cette machine de développement "
              "(`CapabilityStatus.UNAVAILABLE_ON_DEV`, jamais "
              "`UNSUPPORTED` — voir `apex.obs.capability`), donc jamais "
              "prétendu validé. Aucun couplage avec SandboxProvider/"
              "Scheduler/AgentRuntime/MissionEngine/MemoryProvider dans "
              "cette étape — `ArtifactMetadata.memory_record_id` est le "
              "seul point d'association prévu, jamais une duplication de "
              "la provenance déjà portée par `MemoryRecord`.",
    ),
    Abstraction(
        "SandboxProvider", "exécution isolée de code généré", 3, 3,
        ImplStatus.STUB, "aucun — refuse l'exécution si l'isolation n'est pas vérifiée",
        module="apex.sandbox.provider",
        notes="STUB et non IMPLEMENTED délibérément : niveau NAMESPACED atteint et "
              "vérifié empiriquement (namespaces Linux mount/pid/net/ipc/uts + cgroups "
              "v1 cpu/memory/pids + abaissement de privilège réel via setpriv, pas un "
              "simple remap de namespace + racine remontée en lecture seule + réseau "
              "coupé, y compris la boucle locale vers PostgreSQL — tout testé par "
              "intégration réelle sur cette machine, jamais supposé). JAMAIS "
              "« container-grade » ni « fully isolated » : aucun jail de filesystem "
              "complet (pas de pivot_root vers un rootfs minimal, un fichier "
              "monde-lisible de l'hôte reste lisible) et aucun seccomp-bpf (libseccomp.so "
              "présente sur cette machine mais aucun binding Python — non exploitable par "
              "ce code). `LinuxNamespaceSandbox` est le seul backend "
              "implémenté ; `ContainerSandbox` reste PLANNED (démon Docker injoignable "
              "ici). `detect_capabilities()` sonde empiriquement à chaque appel — sur une "
              "machine différente (moins capable OU plus capable), le niveau réellement "
              "obtenu peut différer ; ce résultat ne doit jamais être extrapolé à une "
              "autre machine, voir apex.config.schema.PoolStatus (CURRENT vs TARGET).",
    ),
    Abstraction(
        "ComputePool", "où s'exécute un run (GPU/CPU/local/distant/cloud)", 1, 6,
        ImplStatus.DEFINED, "pool unique « local »",
        module="apex.config.schema",
        notes="Principe 6 : déclaré dès la phase 1 dans la configuration.",
    ),
    Abstraction(
        "Placement", "assignation run -> pool", 6, 6,
        ImplStatus.PLANNED, "pool unique",
    ),
    Abstraction(
        "ToolProvider", "outils à effet de bord", 7, 7,
        ImplStatus.PLANNED, "fs, git, shell",
    ),
    Abstraction(
        "ToolRegistry", "catalogue et métadonnées d'outils", 7, 7,
        ImplStatus.PLANNED, "aucun",
    ),
    Abstraction(
        "PolicyEngine", "autorisation avant tout effet de bord", 2, 7,
        ImplStatus.IMPLEMENTED, "allow-list statique",
        module="apex.policy.engine",
        notes="Deux couches : interdits absolus (jamais accordables, à aucun "
              "sujet) puis allow-list par sujet fermée, refus par défaut. "
              "Sans dépendance (pas de DB) — la décision est journalisée par "
              "l'appelant, pas par le moteur. Pas encore de consommateur réel "
              "(AgentRuntime, phase 7) : le moteur est utilisable et testé "
              "isolément, comme EventSink avant son branchement.",
    ),
    Abstraction(
        "SecretBroker", "injection de secrets hors contexte modèle", 2, 7,
        ImplStatus.PLANNED, "fichier chiffré local",
    ),
    Abstraction(
        "MemoryProvider", "écriture/récupération avec provenance", 5, 8,
        ImplStatus.STUB, "PostgreSQL + pgvector",
        module="apex.memory.service",
        notes="STUB et non IMPLEMENTED délibérément — tranche minimale de "
              "l'étape 9 (docs/COGNITIVE_ARCHITECTURE.md §6) : "
              "`MemoryRecord` (provenance, source, confiance `[0,1]`, statut "
              "de vérification `VerificationStatus` — VERIFIED/LIKELY/"
              "UNCERTAIN/DISPUTED/FALSE, distinct du `confidence` numérique "
              "et de `apex.obs.status.DataStatus`, jamais fusionnés) et "
              "`MemoryRelation` (SUPPORTS/CONTRADICTS entre deux "
              "`MemoryRecord` EXISTANTS) sont réels, testés contre "
              "PostgreSQL réel, jamais mutés en place (une révision est une "
              "nouvelle ligne via `supersedes_id`, chaîne strictement "
              "linéaire ; la seule mutation en place est la visibilité "
              "OWN -> SHARED via `promote_to_shared`, un acte explicite et "
              "audité, accordé à personne par défaut). Réutilise "
              "intégralement PolicyEngine existant (WRITE_OWN_MEMORY/"
              "READ_SHARED_MEMORY/PROMOTE_MEMORY_TO_SHARED), aucune action "
              "ajoutée. Ce qui reste PLANNED : `EmbeddingProvider` (RAG, "
              "recherche sémantique — abstraction séparée, non touchée), "
              "aucun moteur de traversée de graphe ni de visualisation "
              "(les arêtes sont une structure de données, pas un graphe "
              "interrogeable), aucun raccordement automatique depuis "
              "`MissionEngine`/`AgentRuntime` (un futur pas, pas celui-ci).",
    ),
    Abstraction(
        "GenomeStore", "configuration mutable versionnée", 4, 17,
        ImplStatus.PLANNED, "génome unique figé",
    ),
    Abstraction(
        "MutationOperator", "opérateurs d'évolution sur le Genome", 4, 17,
        ImplStatus.PLANNED, "aucun",
    ),
    Abstraction(
        "TaskBank", "banque de tâches et splits", 5, 5,
        ImplStatus.PLANNED, "banque statique",
    ),
    Abstraction(
        "OracleProvider", "vérité terrain d'une tâche", 5, 5,
        ImplStatus.PLANNED, "oracle « tests »",
    ),
    Abstraction(
        "EvalRunner", "exécution appariée, seeds, budgets", 5, 5,
        ImplStatus.PLANNED, "séquentiel local",
    ),
    Abstraction(
        "StatTest", "décision statistique, preuve cumulable", 5, 16,
        ImplStatus.PLANNED, "bootstrap apparié",
        notes="Principe 10 : validité continue, accumulation multi-nuits.",
    ),
    Abstraction(
        "JudgeTier", "notation hiérarchisée tier 0/1/2", 5, 9,
        ImplStatus.PLANNED, "tier 0 objectif seul",
    ),
    Abstraction(
        "LanguageToolchain", "build/test/lint/bench par langage", 3, 6,
        ImplStatus.DEFINED, "Python d'abord",
        module="apex.config.schema",
        notes="C++ en vague 2 (phase 15), métriques séparées.",
    ),
    Abstraction(
        "SearchProvider", "recherche web pour le Research Agent", 4, 13,
        ImplStatus.PLANNED, "désactivé",
    ),
    Abstraction(
        "WeaknessDetector", "détection d'écarts de compétence", 5, 12,
        ImplStatus.PLANNED, "requête SQL simple",
    ),
    Abstraction(
        "CurriculumStrategy", "progression de difficulté", 5, 12,
        ImplStatus.PLANNED, "difficulté fixe",
    ),
    Abstraction(
        "TaskGenerator", "self-play, bug farm, adversarial", 5, 15,
        ImplStatus.PLANNED, "banque statique",
    ),
    Abstraction(
        "SelectionStrategy", "successive halving, front de Pareto", 4, 17,
        ImplStatus.PLANNED, "candidat unique",
    ),
    Abstraction(
        "MetaLearner", "apprendre comment apprendre", 5, 22,
        ImplStatus.PLANNED, "journalisation seule",
    ),
    Abstraction(
        "EventSink", "journal d'événements append-only", 1, 2,
        ImplStatus.IMPLEMENTED, "logging structuré JSON (sink additionnel, pas un remplacement)",
        module="apex.db.event_sink",
        notes="Persistance PostgreSQL (phase 2, apex.db.event_sink.DatabaseEventSink). "
              "Validé contre PostgreSQL réel (tests/test_db_security.py) ; le sink JSON "
              "structuré (apex.obs.logging) reste actif en parallèle, inchangé.",
    ),
    Abstraction(
        "MetricSink", "métriques de tout composant", 1, 1,
        ImplStatus.IMPLEMENTED, "registre en processus",
        module="apex.obs.metrics",
    ),
    Abstraction(
        "HealthCheckable", "contrat de santé de tout composant", 1, 1,
        ImplStatus.IMPLEMENTED, "aucun",
        module="apex.obs.health",
    ),
    Abstraction(
        "CheckpointStore", "reprise d'expérience longue durée", 2, 19,
        ImplStatus.PLANNED, "état en base",
        notes="Principe 8 : une expérience interrompue reprend à son état sauvegardé.",
    ),
    # -- Ajouts issus de la Master Architecture Specification -----------------
    # Chacune a été confrontée au registre existant (§58) : elles sont ajoutées
    # parce qu'aucune abstraction en place ne les couvre sans dénaturer son rôle.
    Abstraction(
        "SkillRegistry", "compétences versionnées, graphe de prérequis, promotion", 11, 14,
        ImplStatus.PLANNED, "aucune compétence — capacités portées par le Genome",
        notes="Manquait au registre initial. Distinct du Genome : une compétence a "
              "son propre cycle de promotion, ses benchmarks et sa généalogie.",
    ),
    Abstraction(
        "PromotionGovernor", "autorité de promotion indépendante du système évalué", 16, 16,
        ImplStatus.PLANNED, "décision par l'ExperimentEngine, journalisée",
        notes="APEX ne doit pas être seul juge de sa propre promotion. Hors de "
              "portée de toute mutation, au même titre que le Policy Engine.",
    ),
    Abstraction(
        "HypothesisGenerator", "observation -> hypothèse falsifiable", 16, 16,
        ImplStatus.PLANNED, "hypothèses saisies par l'humain",
        notes="Distinct de l'ExperimentEngine, qui exécute et décide mais ne "
              "propose pas. Seul composant où un modèle externe apporte un gain net.",
    ),
    Abstraction(
        "CostGainEstimator", "gain attendu / coût de calcul, puis calibration sur le réel", 5, 18,
        ImplStatus.PLANNED, "coût réel mesuré, gain non estimé",
        notes="Couvre à la fois la conception d'expériences, l'estimation de "
              "ressources et le rendement d'apprentissage — une seule boucle "
              "estimer/mesurer/corriger, donc une seule abstraction.",
    ),
    Abstraction(
        "ConfidenceCalibrator", "incertitude calibrée contre les résultats réels", 9, 12,
        ImplStatus.PLANNED, "seuils fixes dans le Genome",
        notes="Pilote l'escalade de raisonnement et de modèle. Une confiance non "
              "calibrée contre les issues observées est un nombre décoratif.",
    ),
    # -- Ajouts issus de docs/COGNITIVE_ARCHITECTURE.md (ADR-0011) -------------
    Abstraction(
        "AgentRuntime", "instanciation, cycle de vie et isolation d'un clone spécialisé", 3, 7,
        ImplStatus.STUB, "exécution directe dans le Core (interactif uniquement)",
        module="apex.agents.runtime",
        notes="STUB et non IMPLEMENTED délibérément : cycle de vie, permissions "
              "(PolicyEngine), persistance (events/audit_log) et un type de "
              "clone (RESEARCH) validés de bout en bout et testés. Deux chemins "
              "d'exécution coexistent : run() reste sans isolation OS (thread "
              "intra-processus, budget détecté mais pas interrompu de force) ; "
              "execute_in_sandbox() (étape 7) délègue réellement à SandboxProvider "
              "(NAMESPACED, lui-même STUB — voir cette entrée) pour les missions "
              "lancées via apex.missions.execution.launch_mission. Le niveau "
              "NAMESPACED n'est ni container-grade ni seccomp — voir l'entrée "
              "SandboxProvider pour le détail exact des garanties tenues/non tenues.",
    ),
    Abstraction(
        "CodeIntelligence",
        "compréhension du dépôt APEX par lui-même : carte, dépendances, "
        "code↔test↔doc, détection d'incohérences et de lacunes", 7, 12,
        ImplStatus.PLANNED, "analyse manuelle par un humain",
        notes="Lecture et proposition seulement — ne modifie jamais le dépôt. "
              "Toute modification proposée passe par un Developer Clone "
              "(AgentRuntime) sous supervision complète : GATE, PromotionGovernor "
              "humain. Voir docs/COGNITIVE_ARCHITECTURE.md §8.",
    ),
    # -- Ajouts étape 6 — Mission Engine + Specialty Engine backend ------------
    Abstraction(
        "MissionEngine",
        "cycle de vie d'une mission — création, file d'attente, exécution, "
        "revue, décision humaine (valider/rejeter/réviser), annulation", 3, 7,
        ImplStatus.STUB, "aucune mission — coordination manuelle",
        module="apex.missions.execution",
        notes="STUB et non IMPLEMENTED délibérément : la machine à états "
              "(DRAFT/QUEUED/RUNNING/REVIEW/COMPLETED/FAILED/CANCELLED), "
              "PolicyEngine (aucun clone ne peut créer/valider/rejeter/lancer "
              "sa propre mission), la persistance PostgreSQL et la "
              "journalisation events/audit_log sont testées de bout en bout "
              "(y compris contre PostgreSQL réel). Depuis l'étape 7, "
              "apex.missions.execution.launch_mission relie réellement CORE -> "
              "POLICY -> AGENT RUNTIME -> SANDBOX -> MISSION -> RESULT -> REVIEW "
              "pour UNE seule charge de travail (diagnostic RESEARCH, strictement "
              "déterministe, apex.missions.workloads) — testé de bout en bout "
              "avec une vraie isolation NAMESPACED, y compris via "
              "POST /missions/{id}/launch (apex/api/routes/missions.py). "
              "TaskBank/EvalRunner, PLANNED, n'existent toujours pas pour "
              "dériver un « travail » arbitraire depuis l'objectif texte "
              "d'une mission quelconque — seul le diagnostic RESEARCH, câblé "
              "en dur, existe ; lancer toute autre spécialité échoue proprement.",
    ),
    Abstraction(
        "SpecialtyEngine",
        "registre backend des Specialty — capacités, outils, permissions "
        "requises, types de clones compatibles, état de support réel", 6, 6,
        ImplStatus.IMPLEMENTED, "aucun",
        module="apex.specialties.registry",
        notes="Registre statique honnête : une seule Specialty (« research ») "
              "est déclarée supportée (STUB, comme AgentRuntime), toutes les "
              "autres restent PLANNED — aucune des 15 spécialités du frontend "
              "n'a de moteur backend inventé pour la circonstance. Distinct de "
              "Domain (apprentissage/connaissance, docs/COGNITIVE_ARCHITECTURE.md "
              "§3, toujours PLANNED) : les deux ne sont jamais fusionnés.",
    ),
    # -- Ajouts étape 8 — Scheduler (orchestration multi-missions/clones) -----
    Abstraction(
        "Scheduler",
        "décide QUAND lancer une mission QUEUED — priorité/FIFO, "
        "dépendances entre missions, capacité/backpressure, annulation "
        "réelle, heartbeat, récupération après redémarrage", 8, 11,
        ImplStatus.STUB, "pas de boucle de fond ; capacité non partagée entre instances",
        module="apex.scheduler.service",
        notes="STUB et non IMPLEMENTED délibérément — limitations réelles, "
              "pas un manque d'intégration : depuis l'étape 11, "
              "`POST /missions/{id}/launch` (apex.api.routes.missions) est le "
              "SEUL déclencheur réel de l'application et passe RÉELLEMENT par "
              "ce Scheduler (singleton applicatif paresseux, "
              "apex.api.dependencies.get_scheduler_service — jamais recréé par "
              "requête, jamais hébergé comme runtime par FastAPI) ; "
              "launch_mission() n'est plus exposée comme point d'orchestration "
              "HTTP, seul le Scheduler l'appelle encore. Arbitrage humain "
              "étape 11 : AUCUNE boucle de fond autonome — tick() n'est "
              "déclenché qu'à la demande (par /launch), jamais par un thread "
              "périodique ; une boucle permanente reste PLANNED, pourra être "
              "ajoutée sans modifier ce module. HeartbeatService (existant "
              "depuis l'étape 8) n'est délibérément PAS démarré : "
              "`GET /scheduler` rapporte heartbeat=UNKNOWN, jamais une valeur "
              "fabriquée. N'appelle jamais AgentRuntime/SandboxProvider "
              "directement — uniquement apex.missions.execution.launch_mission "
              "(déjà STUB, étape 7), jamais de contournement ni de duplication "
              "de PolicyEngine. Réclamation QUEUED -> RUNNING atomique en base "
              "(un seul UPDATE conditionnel) testée sous concurrence réelle "
              "(PostgreSQL, plusieurs workers ET plusieurs instances de "
              "SchedulerService, y compris via l'API désormais) : aucune "
              "mission ne peut être lancée deux fois. max_concurrent_missions "
              "borne RÉELLEMENT le pool de threads d'UNE instance (aucune "
              "croissance illimitée) mais n'est PAS un plafond partagé entre "
              "plusieurs instances simultanées — chacune applique sa propre "
              "borne locale, limite documentée, pas résolue ici. Annulation "
              "réelle (os.killpg, vérifiée par constat direct, pas supposée) "
              "testée contre de vraies exécutions sandboxées. RecoveryService "
              "ne reprend JAMAIS une mission interrompue : toute ligne "
              "RUNNING/non-terminale trouvée à la première construction du "
              "singleton dans ce processus est marquée orpheline, jamais "
              "résumée. PAS de retry automatique (reste PLANNED, décision "
              "explicite) et PAS de résolution automatique d'agent (une "
              "mission sans assigned_agent reste QUEUED). Distinct de "
              "Placement (PLANNED, sélection entre plusieurs ComputePool) : ce "
              "Scheduler compose les ComputePool status=ACTIVE déclarés en "
              "configuration mais ne choisit pas encore ENTRE plusieurs pools "
              "réels — un seul nœud existe aujourd'hui, l'abstraction "
              "(apex.scheduler.capacity) ne suppose pas que ça restera le cas.",
    ),
    # -- Ajouts étape 9 — sécurité des données non fiables (architecture et
    # documentation seules, voir docs/UNTRUSTED_DATA_SECURITY.md ; aucun code
    # n'existe pour cette entrée, PLANNED au sens strict) ----------------------
    Abstraction(
        "FileScanner",
        "analyse de sécurité d'un contenu non fiable (fichier, page web, "
        "image, vidéo) avant toute extraction ou promotion en mémoire", 9, 9,
        ImplStatus.PLANNED,
        "aucune analyse — tout contenu externe reste UNTRUSTED indéfiniment "
        "tant qu'aucun scanner n'existe",
        notes="Seul ajout réel du pipeline UNTRUSTED DATA -> QUARANTINE -> "
              "SECURITY ANALYSIS -> RISK ASSESSMENT -> SANDBOX -> EXTRACTION "
              "-> PROVENANCE -> MEMORY documenté dans "
              "docs/UNTRUSTED_DATA_SECURITY.md — chaque autre étage compose "
              "des abstractions déjà existantes (QUARANTINE : `TrustLevel` "
              "déjà existant, apex.config.schema, + `ArtifactStore` PLANNED ; "
              "SANDBOX : `SandboxProvider`, STUB réel depuis l'étape 7 ; "
              "SECRETS : `SecretBroker`, PLANNED ; PROVENANCE/MEMORY : "
              "`MemoryProvider`, STUB depuis l'étape 9). N'exécute jamais de "
              "contenu lui-même — un scan qui a besoin d'exécuter délègue à "
              "`SandboxProvider`, jamais un chemin d'exécution parallèle.",
    ),
)



def by_name(name: str) -> Abstraction:
    for item in ABSTRACTIONS:
        if item.name == name:
            return item
    raise KeyError(f"abstraction inconnue : {name}")


def module_importable(abstraction: Abstraction) -> bool:
    """Vérifie qu'un module déclaré est réellement importable."""
    if not abstraction.module:
        return False
    try:
        importlib.import_module(abstraction.module)
    except ImportError:
        return False
    return True


def summary() -> dict[str, int]:
    counts = {status.value: 0 for status in ImplStatus}
    for item in ABSTRACTIONS:
        counts[item.status.value] += 1
    return counts


def available_at_phase(phase: int) -> tuple[Abstraction, ...]:
    """Abstractions dont l'implémentation est attendue au plus tard à `phase`."""
    return tuple(a for a in ABSTRACTIONS if a.phase_implemented <= phase)
