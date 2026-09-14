"""`detect_no_progress` — garde-fou NO_PROGRESS de la mission N+27 : « si
APEX reproduit substantiellement le même échec plusieurs fois sans
changement utile, arrêter ». Fonction PURE sur `apex.recovery.model.
AttemptHistory`, DÉJÀ réel (N+26) — aucun second modèle de tentative,
aucune empreinte/fingerprint réinventée : la « signature » d'un échec est
simplement `(catégorie, provider, stratégie)`, les trois champs déjà portés
par `AttemptRecord`."""

from __future__ import annotations

from apex.recovery import AttemptHistory, AttemptRecord


def _signature(record: AttemptRecord) -> tuple[str | None, str, str | None]:
    return (
        record.category.value if record.category is not None else None,
        record.provider_name,
        record.strategy,
    )


def detect_no_progress(history: AttemptHistory, *, patience: int = 2) -> bool:
    """`True` si les `patience` DERNIÈRES tentatives ont TOUTES échoué
    (`category is not None`) avec EXACTEMENT la même signature — jamais sur
    moins de `patience` tentatives réelles (pas assez de preuve), jamais si
    l'une d'elles est un succès (une réussite interrompt toujours la
    boucle avant `detect_no_progress`, mais cette fonction reste sûre même
    appelée directement sur un historique partiel)."""
    if patience < 1:
        raise ValueError("patience doit être >= 1")
    if len(history.records) < patience:
        return False
    tail = history.records[-patience:]
    if any(record.category is None for record in tail):
        return False
    signatures = {_signature(record) for record in tail}
    return len(signatures) == 1
