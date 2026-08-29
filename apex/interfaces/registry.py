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
        ImplStatus.PLANNED, "MinIO local",
        notes="Principe 5 : identité par hash, jamais par chemin.",
    ),
    Abstraction(
        "SandboxProvider", "exécution isolée de code généré", 3, 3,
        ImplStatus.PLANNED, "Docker Engine WSL2 durci",
        notes="Go/no-go runsc à mesurer en phase 3, pas à supposer.",
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
        ImplStatus.PLANNED, "allow-list statique",
    ),
    Abstraction(
        "SecretBroker", "injection de secrets hors contexte modèle", 2, 7,
        ImplStatus.PLANNED, "fichier chiffré local",
    ),
    Abstraction(
        "MemoryProvider", "écriture/récupération avec provenance", 5, 8,
        ImplStatus.PLANNED, "PostgreSQL + pgvector",
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
        ImplStatus.STUB, "logging structuré JSON",
        module="apex.obs.logging",
        notes="Persistance PostgreSQL en phase 2.",
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
