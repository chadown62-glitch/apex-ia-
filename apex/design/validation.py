"""Validation structurelle d'un `DesignPackage` (étape VALIDATE) — QA
architecturale prête pour une future validation par modèle de vision
(jamais simulée aujourd'hui : aucun modèle de vision réel n'est connecté
dans ce dépôt, voir `apex.design.visual_provider`).

`validate_package` vérifie STRUCTURE, cohérence, provenance et
reproductibilité — jamais la qualité esthétique d'un résultat visuel, hors
périmètre de cette version. Chaque vérification exécutée est consignée
dans `checks`, qu'elle passe ou échoue — un `DesignValidation.passed=True`
n'est jamais silencieux sur ce qui a été réellement vérifié."""

from __future__ import annotations

from datetime import datetime, timezone

from apex.design.model import DesignPackage, DesignValidation
from apex.design.states import DESIGN_STEP_ORDER


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_package(package: DesignPackage) -> DesignValidation:
    checks: list[str] = []
    failures: list[str] = []

    def check(description: str, passed: bool) -> None:
        checks.append(description)
        if not passed:
            failures.append(description)

    check("brief présent et résumé non vide", package.brief is not None and bool(package.brief.summary.strip()))
    check("au moins un concept produit", len(package.concepts) >= 1)
    check(
        "chaque concept porte une narration non vide",
        all(bool(c.narrative.strip()) for c in package.concepts),
    )
    if package.concepts:
        check("au moins un prompt visuel construit", len(package.visual_prompts) >= 1)
        check(
            "chaque prompt visuel référence un concept existant",
            all(p.concept_id in {c.id for c in package.concepts} for p in package.visual_prompts),
        )
    if package.visual_prompts:
        check(
            "un résultat visuel enregistré pour chaque prompt (généré ou honnêtement indisponible)",
            len(package.visual_results) == len(package.visual_prompts),
        )
    check(
        "provenance renseignée pour le brief, les concepts et les résultats de recherche",
        (package.brief is None or bool(package.brief.provenance))
        and all(bool(c.provenance) for c in package.concepts)
        and all(bool(r.provenance) for r in package.research),
    )
    executed_order = tuple(s.step for s in package.steps)
    expected_prefix = DESIGN_STEP_ORDER[: len(executed_order)]
    check("les étapes exécutées suivent l'ordre fixe du pipeline, sans trou ni réordonnancement", executed_order == expected_prefix)
    check(
        "aucune étape en échec parmi celles déjà exécutées",
        all(s.state.value != "failed" for s in package.steps),
    )

    return DesignValidation(
        design_id=package.design_id,
        passed=not failures,
        checks=tuple(checks),
        failures=tuple(failures),
        validated_at=_utcnow(),
    )
