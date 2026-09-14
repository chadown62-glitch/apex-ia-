"""Classification EMPIRIQUE d'un échec d'exécution — jamais devinée depuis un
texte libre. Même discipline que `apex.errors.category_from_http_status` (un
point unique, jamais dupliqué entre appelants) et
`apex.metalearner.failure_taxonomy.classify_failure` (preuve structurelle
d'abord, jamais un défaut optimiste).

Deux sources de preuve réelles aujourd'hui dans APEX :
- `apex.sandbox.model.SandboxResult`/`apex.errors.IsolationUnavailable` —
  exécution en sandbox (`apex.missions.execution.launch_mission`).
- `apex.errors.ModelProviderError` — appel à un `ModelProvider`
  (`apex.missions.execution.generate_mission_response*`).

Voir la docstring de `apex.recovery.model.FailureCategory` pour la
distinction avec `apex.metalearner.failure_taxonomy.FailureCategory` — ce
module ne lit JAMAIS un `EvalRun`.
"""

from __future__ import annotations

from apex.errors import IsolationUnavailable, ModelErrorCategory, ModelProviderError
from apex.recovery.model import FailureCategory
from apex.sandbox.model import ExecutionStatus
from apex.sandbox.result import SandboxResult

#: Mapping FERMÉ — chaque `ExecutionStatus` (vocabulaire fermé lui-même,
#: voir `apex.sandbox.model`) a EXACTEMENT une catégorie de recovery.
#: `SUCCESS` est volontairement absent : `classify_sandbox_outcome` refuse
#: de classifier un succès (voir sa docstring).
_EXECUTION_STATUS_TO_CATEGORY: dict[ExecutionStatus, FailureCategory] = {
    ExecutionStatus.TIMEOUT: FailureCategory.TIMEOUT,
    ExecutionStatus.CANCELLED: FailureCategory.CANCELLED,
    ExecutionStatus.OOM_KILLED: FailureCategory.RESOURCE_EXHAUSTED,
    ExecutionStatus.RESOURCE_LIMIT_EXCEEDED: FailureCategory.RESOURCE_EXHAUSTED,
    ExecutionStatus.PROCESS_LIMIT_EXCEEDED: FailureCategory.RESOURCE_EXHAUSTED,
    ExecutionStatus.DISK_LIMIT_EXCEEDED: FailureCategory.RESOURCE_EXHAUSTED,
    # PERMISSION_DENIED couvre à la fois « création de sandbox impossible »
    # (LinuxNamespaceSandbox/ContainerSandbox, voir leurs `_creation_failure_
    # result`) — un problème d'INFRASTRUCTURE, pas du candidat exécuté.
    ExecutionStatus.PERMISSION_DENIED: FailureCategory.SANDBOX_FAILURE,
    # Le workload a tenté une opération réseau alors que network=none — un
    # comportement du CANDIDAT incompatible avec la sandbox, pas une panne
    # d'infrastructure : reclassé comme échec d'exécution générique, jamais
    # une catégorie « réseau » dédiée qui suggérerait une action de recovery
    # spécifique qu'aucune preuve ne justifie.
    ExecutionStatus.NETWORK_DENIED: FailureCategory.EXECUTION_FAILURE,
    ExecutionStatus.FAILED: FailureCategory.EXECUTION_FAILURE,
}

#: Mapping FERMÉ — voir `apex.errors.ModelErrorCategory` pour le vocabulaire
#: source (N+18, dérivé UNIQUEMENT d'un signal structurel réel).
_MODEL_ERROR_TO_CATEGORY: dict[ModelErrorCategory, FailureCategory] = {
    ModelErrorCategory.OFFLINE: FailureCategory.PROVIDER_UNAVAILABLE,
    ModelErrorCategory.RATE_LIMITED: FailureCategory.PROVIDER_FAILURE,
    ModelErrorCategory.AUTH_ERROR: FailureCategory.PROVIDER_FAILURE,
    ModelErrorCategory.CONTEXT_TOO_LONG: FailureCategory.PROVIDER_FAILURE,
    ModelErrorCategory.PROVIDER_ERROR: FailureCategory.PROVIDER_FAILURE,
    ModelErrorCategory.TIMEOUT: FailureCategory.TIMEOUT,
    ModelErrorCategory.UNKNOWN: FailureCategory.PROVIDER_FAILURE,
}


def classify_sandbox_outcome(
    *, result: SandboxResult | None = None, isolation_unavailable: IsolationUnavailable | None = None,
) -> FailureCategory:
    """Classifie l'échec d'UNE exécution sandboxée. Exactement un des deux
    arguments doit être fourni — jamais les deux (deux preuves distinctes,
    jamais fusionnées), jamais aucun (rien à classifier).

    Lève `ValueError` sur `result.status is ExecutionStatus.SUCCESS` : un
    succès n'est pas un échec à catégoriser — l'appelant qui invoque ceci sur
    un succès a un bug, jamais une catégorie `None` silencieuse."""
    if (result is None) == (isolation_unavailable is None):
        raise ValueError(
            "classify_sandbox_outcome exige exactement un de `result`/`isolation_unavailable`"
        )
    if isolation_unavailable is not None:
        return FailureCategory.SANDBOX_FAILURE
    assert result is not None
    if result.status is ExecutionStatus.SUCCESS:
        raise ValueError("un SandboxResult SUCCESS n'est pas un échec à classifier")
    category = _EXECUTION_STATUS_TO_CATEGORY.get(result.status)
    if category is None:  # pragma: no cover - vocabulaire ExecutionStatus fermé, exhaustif ci-dessus
        raise ValueError(f"ExecutionStatus « {result.status.value} » sans catégorie de recovery connue")
    return category


def classify_model_error_category(category: ModelErrorCategory) -> FailureCategory:
    """Traduit un `ModelErrorCategory` DÉJÀ connu (extrait d'un
    `ModelProviderError.category` réel, ou reconstruit depuis une preuve
    structurée équivalente persistée ailleurs — voir `apex.taskloop.loop`,
    qui appelle cette fonction sur une catégorie relue depuis `EvalRun.facts`,
    jamais depuis un texte libre) en `FailureCategory`. Extrait de
    `classify_provider_error` (N+27) pour être réutilisable par un appelant
    qui n'a pas l'exception d'origine sous la main — SEULE et même table de
    correspondance, jamais dupliquée."""
    category_mapped = _MODEL_ERROR_TO_CATEGORY.get(category)
    if category_mapped is None:  # pragma: no cover - vocabulaire fermé, exhaustif ci-dessus
        raise ValueError(f"ModelErrorCategory « {category.value} » sans catégorie de recovery connue")
    return category_mapped


def classify_provider_error(exc: ModelProviderError) -> FailureCategory:
    """Classifie l'échec d'UN appel `ModelProvider` — dérivé uniquement de
    `exc.category` (déjà un signal structurel réel, N+18), jamais du texte
    de `exc.detail`."""
    return classify_model_error_category(exc.category)
