"""Vocabulaire fermé de `TaskGenerator` — `GenerationMode`, `GeneratedTaskSpec`.

Registre : `TaskGenerator`, « self-play, bug farm, adversarial »
(`phase_defined` 5, `phase_implemented` 15) — le point central de génération
de tâches (directive Phase 15). `GenerationMode` nomme les onze intentions
listées par la directive ; seul un sous-ensemble a un backend RÉEL
aujourd'hui (`apex.taskgen.deterministic.DeterministicTaskGenerator`) — les
autres lèvent `apex.errors.NotImplementedYet`, jamais un contenu inventé
pour combler l'absence de dépendance réelle (ex. `CodeIntelligence`,
`HypothesisGenerator`, tous deux encore `PLANNED`).

**`EDGE_CASE`/`ADVERSARIAL` (passe « Adversarial Curriculum + Capability
Benchmarking », N+10) — RÉELS depuis cette passe, mais bornés au SEUL
domaine de tâche existant (le script diagnostic déterministe de la
spécialité `research`) : `EdgeCaseKind`/`AdversarialKind` énumèrent des
variations dérivées de propriétés RÉELLES de ce domaine (nombre de
contrôles exécutés, répétition, proximité de `timeout_s`/`memory_mb`
RÉELLEMENT résolus) — jamais un cas arbitraire sans rapport avec la tâche
(§ mission N+10, Phase 3 : « chaque edge case doit avoir une provenance
traçable »). Un adversarial N'A JAMAIS le droit de demander plus que la
politique administrateur déjà résolue (`apex.genome.clone_config.
resolve_resource_limits`, inchangé) — il ne fait que s'en approcher, ou
tenter une action que le sandbox est censé bloquer (ex. écriture hors
`cwd`, egress réseau), auquel cas l'ÉCHEC de cette tentative EST le
comportement correct attendu (§ Phase 4/5 : « ne doit jamais modifier les
limites de sécurité de l'exécution »). `BUG_FARM` reste `PLANNED` comme
MODE de génération (nécessite toujours `CodeIntelligence`) — le suivi de
bug réel de cette passe (`apex.metalearner.bug_farm_model.BugRecord`) ne
génère aucune tâche lui-même, il réutilise `REGRESSION` (déjà réel) pour
vérifier qu'un bug corrigé ne revient pas.

`GeneratedTaskSpec` n'est PAS un `apex.tasks.model.Task` : c'est une
proposition en mémoire, jamais persistée par ce module — la persistance
reste `apex.tasks.service.TaskBankService.create()` (HUMAN seul,
`Action.CREATE_TASK`), réutilisée telle quelle par l'appelant, jamais
dupliquée ici. Aucun raccordement automatique TaskGenerator -> TaskBank
n'existe : un générateur qui écrirait lui-même sa production contournerait
le contrôle HUMAIN déjà établi en Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from apex.tasks.model import TaskSplit


class GenerationMode(str, Enum):
    #: Tâche de référence, sans variation particulière.
    NORMAL = "normal"
    #: Progression paramétrée par un niveau entier (composable avec
    #: `apex.curriculum.CurriculumStrategy`).
    INCREASING_DIFFICULTY = "increasing_difficulty"
    #: Même mécanisme qu'`INCREASING_DIFFICULTY` — étiquette distincte pour
    #: une tâche explicitement demandée par `CurriculumStrategy`.
    CURRICULUM_INTERMEDIATE = "curriculum_intermediate"
    #: Niveau dérivé d'un `apex.weakness.model.WeaknessReport` — cible la
    #: sévérité détectée, PAS un sous-contrôle spécifique (la granularité
    #: nécessaire n'existe pas encore dans les données disponibles).
    TARGETED_WEAKNESS = "targeted_weakness"
    #: Reproduit exactement la commande d'une tâche existante — jamais un
    #: contenu réinventé, une régression teste l'identique.
    REGRESSION = "regression"
    #: RÉEL depuis N+10 (`EdgeCaseKind`) — voir la docstring du module.
    EDGE_CASE = "edge_case"
    #: Nécessite `CodeIntelligence` (`PLANNED`, phase 12) — aucun domaine de
    #: code réel n'existe encore pour fabriquer un bug légitime.
    BUG_FARM = "bug_farm"
    #: RÉEL depuis N+10 (`AdversarialKind`) — voir la docstring du module.
    ADVERSARIAL = "adversarial"
    #: Nécessite une seconde spécialité réellement supportée — seule
    #: `research` l'est aujourd'hui (`apex.specialties.registry`).
    TRANSFER = "transfer"
    #: Nécessite une orchestration multi-clones réelle (population, phase
    #: 17) — absente aujourd'hui.
    SELF_PLAY = "self_play"
    #: Nécessite `HypothesisGenerator` (`PLANNED`, phase 16).
    FROM_HYPOTHESIS = "from_hypothesis"


class EdgeCaseKind(str, Enum):
    """Variations RÉELLES du script diagnostic (`apex.taskgen.
    deterministic`), dérivées de propriétés RÉELLES du domaine — jamais un
    cas arbitraire (§ Phase 3 de la mission N+10)."""

    #: Aucun contrôle au-delà de la version Python — analogue réel d'un
    #: « empty input » pour ce domaine (le plus petit script valide).
    EMPTY = "empty"
    #: Un seul contrôle (`_MIN_LEVEL`) — déjà réel via `level=1`, exposé ici
    #: sous un nom explicite pour la traçabilité du challenge.
    MINIMAL = "minimal"
    #: Tous les contrôles (`_MAX_LEVEL`) — déjà réel via `level=3`, même
    #: raison que `MINIMAL`.
    MAXIMAL = "maximal"
    #: Exécute le bloc de contrôle plusieurs fois DANS LE MÊME processus et
    #: exige un JSON strictement identique à chaque répétition — un « appel
    #: répété »/« ordonnancement inattendu » RÉEL : détecte une
    #: non-déterminisme du candidat, pas un cas inventé.
    REPEATED_CALLS = "repeated_calls"
    #: Dort jusqu'à une fraction RÉELLE (documentée) de `timeout_s`
    #: RÉELLEMENT résolu pour CE candidat puis complète normalement — une
    #: « valeur limite »/« entrée maximale autorisée » RÉELLE, jamais un
    #: délai arbitraire.
    BOUNDARY_TIMEOUT = "boundary_timeout"


class AdversarialKind(str, Enum):
    """Challenges RÉELS qui cherchent à casser la solution ou vérifier une
    frontière de sécurité — ne demandent JAMAIS plus que la politique
    administrateur déjà résolue (§ Phase 4 de la mission N+10 : jamais de
    privilège/réseau/ressource au-delà des policies, jamais de secrets)."""

    #: Dort à une fraction RÉELLE (documentée, > `BOUNDARY_TIMEOUT`, encore
    #: strictement sous `timeout_s`) — pression réelle sur le budget temps
    #: résolu, jamais une demande de délai supplémentaire.
    TIMEOUT_PRESSURE = "timeout_pressure"
    #: Répète le bloc de contrôle un nombre RÉEL de fois plus élevé que
    #: `REPEATED_CALLS` — pression réelle sur la répétition/l'ordonnancement.
    REPEATED_EXECUTION_STRESS = "repeated_execution_stress"
    #: Alloue une mémoire s'approchant de `memory_mb` RÉELLEMENT résolu,
    #: SANS jamais le dépasser (le script lui-même borne son allocation à
    #: la valeur résolue, transmise par l'appelant — §7 : jamais au-delà des
    #: policies).
    RESOURCE_PRESSURE = "resource_pressure"
    #: Tente une connexion réseau sortante — le sandbox est censé la
    #: refuser (`network="none"`) ; l'ÉCHEC de cette tentative EST le
    #: comportement correct (frontière de sécurité RÉELLE, déjà sondée par
    #: `_diagnostic_script(level>=2)`, ici exposée comme challenge dédié).
    NETWORK_BOUNDARY_PROBE = "network_boundary_probe"
    #: Tente une écriture hors du répertoire de travail courant — le
    #: sandbox est censé la refuser ; même principe que
    #: `NETWORK_BOUNDARY_PROBE`, jamais une demande de privilège élevé.
    FILESYSTEM_BOUNDARY_PROBE = "filesystem_boundary_probe"


class GeneralizationVariant(str, Enum):
    """§ Phase 6 de la mission N+10 : une variation STRUCTURELLEMENT
    distincte du script diagnostic (même propriété réelle testée —
    l'isolation réseau — mais une cible de sonde différente), pour qu'un
    challenge HELDOUT ne soit jamais un doublon byte-à-byte d'un challenge
    TRAIN. Frontière honnête : ceci varie un PARAMÈTRE réel de la sonde
    existante, pas un domaine de généralisation sémantique riche (absent de
    ce dépôt — voir les limitations documentées de `CurriculumEngineService`,
    N+9)."""

    #: Cible de sonde réseau par défaut, utilisée par tout script TRAIN.
    DEFAULT_PROBE = "default_probe"
    #: Cible de sonde réseau ALTERNATIVE, réservée aux challenges HELDOUT
    #: du benchmark de capacité — jamais utilisée pour une tâche TRAIN.
    ALTERNATE_PROBE = "alternate_probe"


@dataclass(frozen=True)
class GeneratedTaskSpec:
    """Proposition de tâche en mémoire — jamais persistée par ce module.
    Mêmes champs que `apex.tasks.service.TaskBankService.create()` accepte,
    pour que l'appelant puisse la persister sans traduction."""

    title: str
    description: str
    command: tuple[str, ...]
    specialty: str
    split: TaskSplit
    mode: GenerationMode
    dataset_ref: str | None = None
