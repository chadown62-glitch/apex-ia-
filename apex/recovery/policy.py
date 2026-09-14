"""`RecoveryPolicy.decide` — la seule fonction qui décide RETRY/REASSIGN/
BLOCK/ESCALATE à partir d'une `FailureCategory`. Pure, déterministe,
testable sans DB ni sandbox réelle — même discipline que
`apex.metalearner.adaptive_solution_loop._mutate_from_experience` (une
décision reproductible à partir de ses seuls arguments, jamais un état
caché).

Implémente exactement la table de §5 de la mission N+26 :

    SANDBOX_FAILURE        -> BLOCK (jamais de retry — structurel)
    TIMEOUT                -> RETRY borné
    CANCELLED               -> BLOCK (jamais de retry automatique)
    INVALID_CANDIDATE       -> RETRY borné, stratégie différente
    EXECUTION_FAILURE       -> RETRY borné
    INCORRECT_SOLUTION      -> RETRY borné (feedback du judge) puis ESCALATE
    PROVIDER_FAILURE        -> REASSIGN si alternative réelle, sinon RETRY borné
    PROVIDER_UNAVAILABLE    -> REASSIGN si alternative réelle, sinon BLOCK
    RESOURCE_EXHAUSTED      -> RETRY borné PLUS STRICTEMENT (policy.resource_exhausted_max_retries)

`max_attempts` (toutes catégories confondues) et
`resource_exhausted_max_retries` (RESOURCE_EXHAUSTED spécifiquement)
bornent TOUJOURS le nombre de tentatives — aucune catégorie ne peut
produire une boucle infinie, quelle que soit la logique ci-dessus."""

from __future__ import annotations

from apex.config.schema import RecoveryPolicy as RecoveryPolicyConfig
from apex.recovery.model import FailureCategory, RecoveryAction, RecoveryDecision


def decide(
    category: FailureCategory,
    *,
    attempt_number: int,
    policy: RecoveryPolicyConfig,
    has_alternate_target: bool = False,
    previous_failure: str | None = None,
) -> RecoveryDecision:
    """`attempt_number` est le numéro (1-based) de la tentative qui vient
    d'ÉCHOUER — la décision porte sur l'opportunité d'une tentative
    `attempt_number + 1`."""
    if attempt_number < 1:
        raise ValueError("attempt_number doit être >= 1")

    def _decision(
        action: RecoveryAction, reason: str, *, strategy_change: str | None = None,
    ) -> RecoveryDecision:
        return RecoveryDecision(
            action=action, category=category, reason=reason,
            attempt_number=attempt_number, max_attempts=policy.max_attempts,
            strategy_change=strategy_change, previous_failure=previous_failure,
        )

    budget_exhausted = attempt_number >= policy.max_attempts

    if category is FailureCategory.CANCELLED:
        return _decision(
            RecoveryAction.BLOCK,
            "annulation explicitement demandée — jamais relancée automatiquement",
        )

    if category is FailureCategory.SANDBOX_FAILURE:
        return _decision(
            RecoveryAction.BLOCK,
            "isolation indisponible — problème structurel, réessayer ne change pas l'hôte",
        )

    if category is FailureCategory.RESOURCE_EXHAUSTED:
        if attempt_number >= policy.resource_exhausted_max_retries + 1:
            return _decision(
                RecoveryAction.BLOCK,
                f"limite de ressources atteinte {attempt_number} fois — "
                f"{policy.resource_exhausted_max_retries} nouvelle(s) tentative(s) déjà épuisée(s), "
                "réessayer avec les mêmes limites n'aiderait pas",
            )
        return _decision(
            RecoveryAction.RETRY,
            "limite de ressources atteinte — nouvelle tentative bornée (charge possiblement transitoire)",
            strategy_change="nouveau clone, mêmes limites de ressources",
        )

    if category is FailureCategory.PROVIDER_UNAVAILABLE:
        if has_alternate_target:
            return _decision(
                RecoveryAction.REASSIGN,
                "provider injoignable — un provider alternatif réellement éligible existe",
                strategy_change="basculement vers le provider/pool alternatif",
            )
        return _decision(
            RecoveryAction.BLOCK,
            "provider injoignable — aucun provider alternatif réellement éligible",
        )

    if budget_exhausted:
        if category is FailureCategory.INCORRECT_SOLUTION:
            return _decision(
                RecoveryAction.ESCALATE,
                f"solution incorrecte après {attempt_number} tentative(s) — "
                "budget de tentatives épuisé, attention humaine requise",
            )
        return _decision(
            RecoveryAction.BLOCK,
            f"budget de tentatives épuisé ({attempt_number}/{policy.max_attempts})",
        )

    if category is FailureCategory.PROVIDER_FAILURE:
        if has_alternate_target:
            return _decision(
                RecoveryAction.REASSIGN,
                "provider en échec — un provider alternatif réellement éligible existe",
                strategy_change="basculement vers le provider/pool alternatif",
            )
        return _decision(
            RecoveryAction.RETRY,
            "provider en échec — nouvelle tentative bornée, aucune alternative éligible",
            strategy_change="nouveau clone, même pool",
        )

    if category is FailureCategory.INVALID_CANDIDATE:
        return _decision(
            RecoveryAction.RETRY,
            "candidat structurellement invalide — nouvelle tentative avec une stratégie différente",
            strategy_change="stratégie de génération/commande différente",
        )

    if category is FailureCategory.INCORRECT_SOLUTION:
        return _decision(
            RecoveryAction.RETRY,
            "solution jugée incorrecte — nouvelle tentative avec le feedback du judge",
            strategy_change="feedback du judge incorporé au contexte de la tentative suivante",
        )

    if category is FailureCategory.TIMEOUT:
        return _decision(
            RecoveryAction.RETRY,
            "délai dépassé — nouvelle tentative bornée",
            strategy_change="nouveau clone, même pool",
        )

    if category is FailureCategory.EXECUTION_FAILURE:
        return _decision(
            RecoveryAction.RETRY,
            "échec d'exécution générique — nouvelle tentative bornée",
            strategy_change="nouveau clone, même pool",
        )

    msg = f"FailureCategory « {category.value} » sans règle de recovery connue"
    raise ValueError(msg)  # pragma: no cover
