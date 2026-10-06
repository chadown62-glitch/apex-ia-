"""PolicyEngine — autorisation avant tout effet de bord.

Registre : `PolicyEngine`, repli documenté « allow-list statique ». C'est
exactement ce que ce module implémente, en deux couches strictement
ordonnées :

1. **Interdits absolus** (`_ABSOLUTE_DENY`) : vérifiés en premier, ne
   dépendent d'aucune allow-list. Aucun sujet — pas même `CORE` ou `HUMAN` —
   ne peut jamais obtenir l'une de ces actions. C'est la traduction en code
   des « Limites absolues de sécurité » de
   `docs/COGNITIVE_ARCHITECTURE.md` §2.11 : un clone (ou même un bug dans une
   future allow-list) ne peut pas, par construction, obtenir un droit
   d'écriture sur le Core, le Gate, le registre, un ADR, ou `audit_log`.
2. **Allow-list par sujet**, fermée, refus par défaut : une action absente de
   la liste d'un sujet est refusée, jamais accordée par omission.

Volontairement **sans dépendance** (pas de DB, pas de réseau) : une décision
d'autorisation doit pouvoir être prise même si tout le reste est en panne —
même exigence que `apex.doctor`. Le moteur ne journalise rien lui-même ;
c'est à l'appelant (le futur `AgentRuntime`) d'écrire la décision dans
`events`/`audit_log` s'il le juge utile — même séparation diagnostiquer/agir
que `apex.doctor` et `apex.gate`.

L'allow-list par défaut n'est **pas pilotée par `config.yaml`** ni par le
Genome, contrairement à `ExternalPolicy.allowed_contexts`. C'est délibéré :
`ExternalPolicy` gouverne un choix opérationnel (utiliser un modèle externe),
alors que `PolicyEngine` protège la frontière de sécurité elle-même — elle ne
doit pas pouvoir s'élargir par une simple édition de fichier versionné sans
revue de code dédiée.
"""

from __future__ import annotations

from collections.abc import Mapping

from apex.errors import PolicyDenied
from apex.policy.model import Action, PolicyDecision, Subject

#: Jamais accordable, à aucun sujet, sous aucune allow-list.
_ABSOLUTE_DENY: frozenset[Action] = frozenset(
    {
        Action.MODIFY_CORE,
        Action.MODIFY_GATE,
        Action.MODIFY_REGISTRY,
        Action.MODIFY_ADR,
        Action.MODIFY_AUDIT_LOG,
        Action.GIT_PUSH_ORIGIN,
        Action.MERGE_TO_MAIN,
        Action.ESCALATE_OWN_PRIVILEGE,
        Action.DISABLE_GATE,
    }
)

#: Allow-list par défaut — traduction directe de la table de permissions
#: docs/COGNITIVE_ARCHITECTURE.md §2.7. `PROMOTE_MEMORY_TO_SHARED` n'apparaît
#: dans aucune entrée : promouvoir un souvenir de clone vers la mémoire
#: partagée reste un acte explicite et audité, jamais un droit par défaut
#: (§2.5 du même document).
DEFAULT_ALLOWLIST: Mapping[Subject, frozenset[Action]] = {
    Subject.CORE: frozenset(
        {
            Action.SPAWN_CLONE, Action.TERMINATE_CLONE, Action.WRITE_AUDIT_LOG,
            Action.WRITE_EVENT, Action.READ_SHARED_MEMORY, Action.READ_REPO_SNAPSHOT,
            # CORE est l'orchestrateur système : il peut lancer une mission déjà
            # validée par un HUMAN (QUEUED -> RUNNING) et l'annuler (ex. budget
            # épuisé) — jamais la créer, la valider ni la rejeter à la place d'un HUMAN.
            Action.LAUNCH_MISSION, Action.CANCEL_MISSION,
            # Seul CORE déclenche une exécution en sandbox — jamais HUMAN
            # directement (chaîne obligatoire : HUMAN valide/rejette la
            # REVIEW, mais ne lance ni n'exécute lui-même).
            Action.EXECUTE_IN_SANDBOX,
            # SkillRegistry (phase 14) : CORE peut proposer une compétence à
            # partir d'une synthèse automatique de preuve (EvalRunner) —
            # jamais la promouvoir ni la rejeter (décision HUMAN seule).
            Action.PROPOSE_SKILL,
            # GenomeStore (phase 17) : même motif — CORE peut proposer une
            # version (issue d'une mutation/d'un croisement réel), jamais
            # la promouvoir ni la rejeter.
            Action.PROPOSE_GENOME_VERSION,
            # Agents V1 : CORE lance une exécution déjà créée/assignée par
            # HUMAIN — même chaîne que LAUNCH_MISSION, jamais CREATE_AGENT.
            Action.RUN_AGENT_EXECUTION,
            # Mémoire système (06/10/2026) : CORE indexe les exécutions et y puise pour décider.
            Action.WRITE_SYSTEM_MEMORY, Action.READ_SYSTEM_MEMORY,
        }
    ),
    Subject.HUMAN: frozenset(
        {
            Action.SPAWN_CLONE, Action.TERMINATE_CLONE, Action.PROPOSE_INTEGRATION,
            Action.WRITE_AUDIT_LOG, Action.READ_SHARED_MEMORY, Action.READ_REPO_SNAPSHOT,
            # Mémoire système (06/10/2026) : l'utilisateur ajoute ses documents et lit la mémoire.
            Action.WRITE_SYSTEM_MEMORY, Action.READ_SYSTEM_MEMORY,
            # Le cycle de vie humain d'une mission au complet : créer, annuler,
            # et la décision de revue (valider/rejeter/renvoyer en révision).
            # Jamais LAUNCH_MISSION : le lancement effectif reste au CORE.
            Action.CREATE_MISSION, Action.CANCEL_MISSION, Action.VALIDATE_MISSION,
            Action.REJECT_MISSION, Action.REQUEST_MISSION_REVISION,
            # TaskBank (phase 5) : même motif que CREATE_MISSION — HUMAN
            # seul, jamais CORE ni un clone.
            Action.CREATE_TASK,
            # SkillRegistry (phase 14) : HUMAN peut aussi proposer (saisie
            # manuelle) ET seul HUMAN décide de la promotion/du rejet —
            # jamais une compétence qui s'auto-promeut.
            Action.PROPOSE_SKILL, Action.PROMOTE_SKILL, Action.REJECT_SKILL,
            # GenomeStore (phase 17) : même motif — seul HUMAIN décide de
            # promouvoir/rejeter une version de Genome, invariant absolu
            # (aucun clone, aucun CORE ne modifie le Core via le Genome).
            Action.PROPOSE_GENOME_VERSION, Action.PROMOTE_GENOME_VERSION, Action.REJECT_GENOME_VERSION,
            # Chat/Conversations/Projects/AgentTemplate (passe « Première
            # interface utilisable ») : HUMAIN seul, même motif que
            # CREATE_MISSION — jamais un clone, jamais le CORE en autonomie.
            Action.CREATE_CONVERSATION, Action.UPDATE_CONVERSATION, Action.DELETE_CONVERSATION,
            Action.SEND_MESSAGE, Action.CREATE_PROJECT,
            Action.CREATE_AGENT_TEMPLATE, Action.DELETE_AGENT_TEMPLATE,
            # Agents V1 : HUMAIN crée/édite/supprime un Agent et une Team —
            # jamais RUN_AGENT_EXECUTION (réservé à CORE, même motif que
            # LAUNCH_MISSION jamais accordé à HUMAIN directement).
            Action.CREATE_AGENT, Action.UPDATE_AGENT, Action.DELETE_AGENT, Action.CREATE_AGENT_TEAM,
            # Operator (N+16) : HUMAIN seul contrôle le lifecycle d'APEX —
            # jamais CORE, jamais un clone (absentes de leurs allow-lists).
            Action.START_OPERATOR, Action.STOP_OPERATOR, Action.PAUSE_OPERATOR, Action.RESUME_OPERATOR,
            # Design Engine (N+17) : HUMAIN seul soumet une demande de
            # conception — même motif que CREATE_MISSION/CREATE_TASK.
            Action.SUBMIT_DESIGN,
        }
    ),
    Subject.RESEARCH: frozenset(
        {Action.NETWORK_EGRESS, Action.WRITE_OWN_MEMORY, Action.WRITE_EVENT, Action.READ_SHARED_MEMORY}
    ),
    Subject.LEARNING: frozenset(
        {Action.WRITE_OWN_MEMORY, Action.WRITE_EVENT, Action.READ_SHARED_MEMORY}
    ),
    Subject.DEVELOPER: frozenset(
        {Action.WRITE_OWN_WORKTREE, Action.WRITE_OWN_MEMORY, Action.WRITE_EVENT, Action.READ_REPO_SNAPSHOT}
    ),
    Subject.EXPERIMENT: frozenset(
        {Action.WRITE_OWN_MEMORY, Action.WRITE_EVENT, Action.READ_SHARED_MEMORY}
    ),
    Subject.CRITIC: frozenset(
        {Action.WRITE_EVENT, Action.READ_SHARED_MEMORY, Action.READ_REPO_SNAPSHOT}
    ),
    Subject.SECURITY: frozenset(
        {Action.WRITE_AUDIT_LOG, Action.WRITE_EVENT, Action.READ_REPO_SNAPSHOT, Action.READ_SHARED_MEMORY}
    ),
    Subject.BENCHMARK: frozenset({Action.WRITE_EVENT, Action.READ_SHARED_MEMORY}),
    Subject.TEACHER: frozenset({Action.WRITE_EVENT, Action.READ_SHARED_MEMORY}),
    Subject.INTEGRATOR: frozenset(
        {Action.PROPOSE_INTEGRATION, Action.WRITE_EVENT, Action.READ_REPO_SNAPSHOT}
    ),
}


class PolicyEngine:
    """Autorise ou refuse une action pour un sujet. Refus par défaut."""

    def __init__(self, allowlist: Mapping[Subject, frozenset[Action]] | None = None) -> None:
        self._allowlist: Mapping[Subject, frozenset[Action]] = (
            DEFAULT_ALLOWLIST if allowlist is None else allowlist
        )

    def check(self, subject: Subject, action: Action) -> PolicyDecision:
        """Décide sans jamais lever. `authorize` est la variante qui lève."""
        if action in _ABSOLUTE_DENY:
            return PolicyDecision(
                allowed=False, subject=subject, action=action,
                reason=f"« {action.value} » interdit à tout sujet, sans exception",
            )
        allowed_actions = self._allowlist.get(subject, frozenset())
        if action in allowed_actions:
            return PolicyDecision(
                allowed=True, subject=subject, action=action, reason="autorisé par l'allow-list",
            )
        return PolicyDecision(
            allowed=False, subject=subject, action=action,
            reason=f"« {action.value} » non autorisé pour « {subject.value} »",
        )

    def authorize(self, subject: Subject, action: Action) -> None:
        """Lève `PolicyDenied` si `check` refuse. Ne retourne rien si autorisé."""
        decision = self.check(subject, action)
        if not decision.allowed:
            raise PolicyDenied(subject.value, action.value, reason=decision.reason)
