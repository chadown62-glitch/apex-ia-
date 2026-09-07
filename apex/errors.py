"""Taxonomie d'erreurs APEX.

Principe directeur (§79 du cahier des charges) : aucun composant ne doit
retourner silencieusement une valeur plausible lorsqu'il n'est pas implémenté.
`NotImplementedYet` porte la phase prévue et le repli disponible, de sorte que
l'état réel du système soit toujours lisible depuis le code et depuis
`apex doctor`.
"""

from __future__ import annotations


class ApexError(Exception):
    """Racine de toutes les erreurs APEX."""

    exit_code: int = 1


class ConfigError(ApexError):
    """Configuration absente, illisible ou invalide."""

    exit_code = 2

    def __init__(self, message: str, *, path: str | None = None, field: str | None = None) -> None:
        self.path = path
        self.field = field
        location = " ".join(p for p in (path, field) if p)
        super().__init__(f"{message} ({location})" if location else message)


class DependencyMissing(ApexError):
    """Une dépendance externe requise est absente ou injoignable."""

    exit_code = 3

    def __init__(self, dependency: str, *, remediation: str | None = None) -> None:
        self.dependency = dependency
        self.remediation = remediation
        msg = f"dépendance manquante : {dependency}"
        if remediation:
            msg += f" — remédiation : {remediation}"
        super().__init__(msg)


class TimeoutExceeded(ApexError):
    """Un budget de temps a été dépassé.

    Toute opération à effet de bord dans APEX possède un timeout (principe 13).
    """

    exit_code = 4

    def __init__(self, operation: str, timeout_s: float) -> None:
        self.operation = operation
        self.timeout_s = timeout_s
        super().__init__(f"timeout dépassé sur « {operation} » après {timeout_s:.1f}s")


class NotImplementedYet(ApexError):
    """Interface définie, implémentation prévue pour une phase ultérieure.

    Volontairement distincte de `NotImplementedError` : elle est structurée,
    interrogeable, et son message nomme la phase et le repli. C'est le mécanisme
    qui empêche un composant d'« exister sur le papier ».
    """

    exit_code = 5

    def __init__(self, component: str, *, phase: int, fallback: str | None = None) -> None:
        self.component = component
        self.phase = phase
        self.fallback = fallback
        msg = f"« {component} » n'est pas implémenté — prévu en phase {phase}"
        if fallback:
            msg += f" ; repli actuel : {fallback}"
        super().__init__(msg)


class PolicyDenied(ApexError):
    """Action refusée par le Policy Engine. Jamais rattrapée silencieusement."""

    exit_code = 6

    def __init__(self, subject: str, action: str, *, reason: str = "") -> None:
        self.subject = subject
        self.action = action
        self.reason = reason
        msg = f"action refusée : « {action} » pour « {subject} »"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)


class ArtifactIntegrityError(ApexError):
    """Le contenu relu depuis un `ArtifactStoreProvider` ne correspond plus
    au hash qui l'identifie — l'identité par hash (principe 5) n'est plus
    vérifiée. Ne jamais rattraper cette exception pour renvoyer un contenu
    non vérifié à l'appelant."""

    exit_code = 8

    def __init__(self, expected: str, *, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"intégrité rompue : attendu « {expected} », relu « {actual} »")


class ModelProviderError(ApexError):
    """Un backend `ModelProvider` a échoué pendant un appel réel (connexion
    refusée, statut HTTP d'erreur, réponse malformée) — distinct d'un
    backend simplement absent (`ModelCapabilityReport.status =
    UNAVAILABLE_ON_DEV`, jamais une exception). Le message ne porte JAMAIS le
    corps de la requête/réponse (prompt, sortie du modèle) ni d'en-tête
    d'authentification — seuls l'URL de l'endpoint et un résumé borné de la
    cause sont conservés (§72)."""

    exit_code = 9

    def __init__(self, component: str, *, endpoint_url: str, detail: str) -> None:
        self.component = component
        self.endpoint_url = endpoint_url
        self.detail = detail
        super().__init__(f"« {component} » a échoué sur {endpoint_url} : {detail}")


class SecretNotFound(ApexError):
    """Un secret référencé par sa clé logique n'a pas pu être résolu.

    Ne porte jamais la clé recherchée dans un contexte qui laisserait deviner
    un motif de nommage exploitable au-delà de ce que l'appelant connaît déjà
    (il a fourni la clé) — seule la clé elle-même et la source consultée sont
    rapportées, jamais une valeur candidate ni un secret partiel."""

    exit_code = 10

    def __init__(self, key: str, *, source: str) -> None:
        self.key = key
        self.source = source
        super().__init__(f"secret « {key} » introuvable (source : {source})")


class IsolationUnavailable(ApexError):
    """Le niveau d'isolation exigé n'est pas disponible sur cet hôte.

    Conséquence directe du principe verrouillé n°4 : une fonctionnalité risquée
    est désactivée ou différée plutôt que d'abaisser les garanties de sécurité.
    """

    exit_code = 7

    def __init__(self, feature: str, *, required: str, available: str) -> None:
        self.feature = feature
        self.required = required
        self.available = available
        super().__init__(
            f"« {feature} » exige l'isolation « {required} » ; "
            f"seul « {available} » est disponible — fonctionnalité désactivée"
        )


class CycleAlreadyRunning(ApexError):
    """Un cycle d'apprentissage autonome (`apex.metalearner.autonomy.
    AutonomousLearningService`) est déjà `RUNNING` dans ce processus —
    un second démarrage n'écrase jamais silencieusement le suivi du
    premier. Jamais rattrapée pour lancer un second cycle concurrent."""

    exit_code = 11

    def __init__(self, cycle_id: str) -> None:
        self.cycle_id = cycle_id
        super().__init__(f"un cycle d'apprentissage autonome est déjà en cours ({cycle_id})")


class PopulationCapExceeded(ApexError):
    """Le plafond de population (`apex.config.schema.PopulationPolicy`,
    passe « Real Clone Population + Evolution Loop ») est déjà atteint pour
    cette spécialité — un nouveau membre n'est jamais créé au-delà de cette
    limite, jamais un dépassement silencieusement toléré (§15 de la
    mission : « empêcher l'explosion de population »)."""

    exit_code = 12

    def __init__(self, specialty: str, *, active: int, limit: int) -> None:
        self.specialty = specialty
        self.active = active
        self.limit = limit
        super().__init__(
            f"plafond de population atteint pour « {specialty} » ({active}/{limit} membres actifs)"
        )


class LearningSessionAlreadyRunning(ApexError):
    """Une session d'apprentissage multi-génération (`apex.metalearner.
    learning_orchestrator.LearningOrchestrator`, passe « Autonomous
    Multi-Generation Learning Orchestrator ») est déjà `RUNNING` pour cette
    spécialité — garanti par un index UNIQUE partiel en base
    (`learning_sessions`, une seule ligne `running` par spécialité),
    JAMAIS par un simple verrou mémoire par processus (contrairement à
    `CycleAlreadyRunning`, limite explicitement documentée comme
    par-processus) : le SGBD reste le seul arbitre, correct même avec
    plusieurs processus/workers API concurrents."""

    exit_code = 13

    def __init__(self, specialty: str) -> None:
        self.specialty = specialty
        super().__init__(
            f"une session d'apprentissage est déjà en cours pour « {specialty} »"
        )


class VisualBackendUnavailable(ApexError):
    """Aucun backend de génération visuelle réel n'est connecté
    (`apex.design.visual_provider.VisualGenerationProvider`, N+17) —
    l'étape VISUALIZE du pipeline de conception le capture et le rapporte
    honnêtement (`VisualResult.state = "unavailable"`) plutôt que de
    prétendre qu'une image a été générée (§79 du cahier des charges :
    aucun composant ne retourne silencieusement une valeur plausible
    lorsqu'il n'est pas implémenté)."""

    exit_code = 15

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"VISUAL_BACKEND_UNAVAILABLE : {detail}")
