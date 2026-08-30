"""Vocabulaire fermé du PolicyEngine — sujets, actions, décisions.

`Subject` et `Action` sont des `Enum`, pas des chaînes libres : un sujet ou
une action inconnue du catalogue est refusée par construction plutôt que
silencieusement acceptée (§53, même principe que le Gate). Seul du code
(humain) étend ces catalogues — jamais une donnée de configuration ou de
Genome, contrairement à `ExternalPolicy.allowed_contexts` qui, lui, est
volontairement piloté par `config.yaml`. La frontière de sécurité que le
PolicyEngine protège ne doit pas pouvoir s'élargir par une simple édition de
fichier YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Subject(str, Enum):
    """Qui demande l'autorisation.

    Les neuf types de clone sont ceux de `docs/COGNITIVE_ARCHITECTURE.md` §2.2.
    `CORE` et `HUMAN` couvrent les demandes qui n'émanent pas d'un clone
    (ex. le superviseur qui spawn/termine un clone).
    """

    CORE = "core"
    HUMAN = "human"
    RESEARCH = "research"
    LEARNING = "learning"
    DEVELOPER = "developer"
    EXPERIMENT = "experiment"
    CRITIC = "critic"
    SECURITY = "security"
    BENCHMARK = "benchmark"
    TEACHER = "teacher"
    INTEGRATOR = "integrator"


class Action(str, Enum):
    """Catalogue fermé des actions à effet de bord soumises à autorisation.

    Les neuf premières sont potentiellement accordables selon le sujet. Les
    suivantes (à partir de `MODIFY_CORE`) sont les interdits absolus de
    `docs/COGNITIVE_ARCHITECTURE.md` §2.11 : aucune allow-list ne peut jamais
    les débloquer, pour aucun sujet — voir `PolicyEngine._ABSOLUTE_DENY`.
    """

    # -- Actions ordinaires, accordables selon le sujet ---------------------
    NETWORK_EGRESS = "network_egress"
    WRITE_OWN_MEMORY = "write_own_memory"
    PROMOTE_MEMORY_TO_SHARED = "promote_memory_to_shared"
    WRITE_EVENT = "write_event"
    WRITE_AUDIT_LOG = "write_audit_log"          # INSERT seul — jamais une modification
    WRITE_OWN_WORKTREE = "write_own_worktree"
    PROPOSE_INTEGRATION = "propose_integration"
    SPAWN_CLONE = "spawn_clone"
    TERMINATE_CLONE = "terminate_clone"
    READ_SHARED_MEMORY = "read_shared_memory"
    READ_REPO_SNAPSHOT = "read_repo_snapshot"
    # -- Mission Engine (docs/COGNITIVE_ARCHITECTURE.md, étape 6) ------------
    # Toutes réservées à HUMAN/CORE — jamais à un type de clone : un agent ne
    # doit jamais pouvoir créer, valider, rejeter ou lancer sa propre mission
    # (auto-validation/auto-promotion interdites). L'absence de ces actions
    # dans l'allow-list de chaque Subject de clone est le mécanisme
    # d'interdiction — refus par défaut, pas une liste noire séparée.
    CREATE_MISSION = "create_mission"
    CANCEL_MISSION = "cancel_mission"
    VALIDATE_MISSION = "validate_mission"      # REVIEW -> COMPLETED, HUMAN seul
    REJECT_MISSION = "reject_mission"          # REVIEW -> FAILED, HUMAN seul
    REQUEST_MISSION_REVISION = "request_mission_revision"  # REVIEW -> DRAFT, HUMAN seul
    LAUNCH_MISSION = "launch_mission"          # QUEUED -> RUNNING, CORE seul (jamais HUMAN ni un clone)
    # -- Sandbox (étape 7) ----------------------------------------------------
    # Réservée à CORE, jamais à HUMAN ni à un clone : un clone ne doit jamais
    # pouvoir déclencher lui-même sa propre exécution en sandbox (contournement
    # direct de la chaîne CORE -> POLICY -> AGENT RUNTIME -> SANDBOX).
    EXECUTE_IN_SANDBOX = "execute_in_sandbox"

    # -- Interdits absolus — jamais accordables, à quiconque -----------------
    MODIFY_CORE = "modify_core"
    MODIFY_GATE = "modify_gate"
    MODIFY_REGISTRY = "modify_registry"
    MODIFY_ADR = "modify_adr"
    MODIFY_AUDIT_LOG = "modify_audit_log"        # UPDATE/DELETE/TRUNCATE — distinct de WRITE (INSERT)
    GIT_PUSH_ORIGIN = "git_push_origin"
    MERGE_TO_MAIN = "merge_to_main"
    ESCALATE_OWN_PRIVILEGE = "escalate_own_privilege"
    DISABLE_GATE = "disable_gate"


@dataclass(frozen=True)
class PolicyDecision:
    """Résultat d'une vérification. Toujours produit, jamais d'exception ici
    (`PolicyEngine.check`) — l'exception est réservée à `authorize`."""

    allowed: bool
    subject: Subject
    action: Action
    reason: str
