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
    # -- TaskBank (phase 5) ---------------------------------------------------
    # HUMAN seul, même motif que CREATE_MISSION : un clone ne doit jamais
    # pouvoir écrire sa propre tâche d'évaluation (contournement trivial du
    # split HELDOUT sinon — un clone qui écrirait ses propres tâches pourrait
    # s'auto-attribuer des tâches faciles).
    CREATE_TASK = "create_task"
    # -- SkillRegistry (phase 14) ----------------------------------------------
    # PROPOSE_SKILL : CORE (synthèse automatique de preuve depuis EvalRunner)
    # ET HUMAN (saisie manuelle) — même motif que CANCEL_MISSION, accordée
    # aux deux sans être un pouvoir de décision. PROMOTE_SKILL/REJECT_SKILL :
    # HUMAN SEUL, jamais CORE ni un clone — une compétence ne peut jamais
    # s'auto-promouvoir (§8 anti-auto-illusion : aucune auto-déclaration de
    # réussite comme preuve suffisante). Distinct de PromotionGovernor
    # (PLANNED, phase 16, future porte UNIQUE de promotion vers le Core) :
    # ceci ne promeut qu'une entrée SkillRegistry, jamais le Core lui-même.
    PROPOSE_SKILL = "propose_skill"
    PROMOTE_SKILL = "promote_skill"
    REJECT_SKILL = "reject_skill"
    # -- GenomeStore (phase 17) -------------------------------------------
    # Même motif exact que SkillRegistry (phase 14) : PROPOSE_GENOME_VERSION
    # accordée à CORE (synthèse depuis une mutation/un croisement réel) ET
    # HUMAIN (saisie manuelle) ; PROMOTE/REJECT_GENOME_VERSION HUMAIN SEUL,
    # jamais CORE ni un clone — invariant absolu (ADR-0001,
    # docs/COGNITIVE_ARCHITECTURE.md §1 : « aucun clone, aucune boucle
    # d'apprentissage... ne modifie le Core »). Cycle HUMAIN provisoire en
    # attendant PromotionGovernor (PLANNED, phase 16, périmètre jugé
    # insuffisamment défini pour l'instant, voir apex/interfaces/registry.py).
    PROPOSE_GENOME_VERSION = "propose_genome_version"
    PROMOTE_GENOME_VERSION = "promote_genome_version"
    REJECT_GENOME_VERSION = "reject_genome_version"
    # -- Sandbox (étape 7) ----------------------------------------------------
    # Réservée à CORE, jamais à HUMAN ni à un clone : un clone ne doit jamais
    # pouvoir déclencher lui-même sa propre exécution en sandbox (contournement
    # direct de la chaîne CORE -> POLICY -> AGENT RUNTIME -> SANDBOX).
    EXECUTE_IN_SANDBOX = "execute_in_sandbox"
    # -- Chat / Conversations (passe « Première interface utilisable ») ------
    # HUMAIN seul, même motif que CREATE_MISSION/CREATE_TASK : un tour de
    # conversation est une action humaine directe, jamais celle d'un clone
    # ni du CORE en autonomie (aucune automatisation de Chat n'existe).
    CREATE_CONVERSATION = "create_conversation"
    UPDATE_CONVERSATION = "update_conversation"      # renommer un fil existant
    DELETE_CONVERSATION = "delete_conversation"
    SEND_MESSAGE = "send_message"
    # -- Projects (passe « Première interface utilisable ») ------------------
    CREATE_PROJECT = "create_project"
    # -- AgentTemplate (passe « Première interface utilisable ») -------------
    # Une DÉFINITION d'agent, jamais un spawn réel (SPAWN_CLONE, inchangé) —
    # même distinction que documentée dans `apex.db.models.AgentTemplateRow`.
    CREATE_AGENT_TEMPLATE = "create_agent_template"
    DELETE_AGENT_TEMPLATE = "delete_agent_template"
    # -- Agent (passe « Agents V1 ») ------------------------------------------
    # Distinct d'AgentTemplate (une définition, jamais exécutable) : un
    # `Agent` est une entité réelle, assignable à une Mission, exécutable.
    # CREATE/UPDATE/DELETE_AGENT : HUMAIN seul, même motif que
    # CREATE_CONVERSATION — création/édition d'une entité humaine directe.
    # RUN_AGENT_EXECUTION : CORE seul, même motif exact que LAUNCH_MISSION —
    # jamais HUMAIN ni un clone ne déclenche directement une exécution ;
    # HUMAIN crée/assigne, CORE lance (même chaîne de gouvernance).
    CREATE_AGENT = "create_agent"
    UPDATE_AGENT = "update_agent"
    DELETE_AGENT = "delete_agent"
    RUN_AGENT_EXECUTION = "run_agent_execution"
    CREATE_AGENT_TEAM = "create_agent_team"
    # -- Operator (N+16) — contrôle du lifecycle d'APEX lui-même -------------
    # Les quatre réservées à HUMAIN seul, même motif que CREATE_MISSION :
    # démarrer/arrêter/mettre en pause/reprendre APEX est une décision
    # humaine explicite, jamais un clone (aucune allow-list de clone ne les
    # contient — refus par défaut, pas une liste noire séparée), jamais CORE
    # non plus (CORE exécute déjà les missions une fois lancées ; il ne
    # décide pas lui-même de démarrer ou d'arrêter tout APEX). Distinctes de
    # LAUNCH_MISSION/CANCEL_MISSION (déjà existantes, réutilisées telles
    # quelles par `apex.operator.service.OperatorService` pour
    # SubmitTask/CancelTask — jamais dupliquées ici).
    START_OPERATOR = "start_operator"
    STOP_OPERATOR = "stop_operator"
    PAUSE_OPERATOR = "pause_operator"
    RESUME_OPERATOR = "resume_operator"

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
