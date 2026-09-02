"""Vocabulaire de `SecretBroker` — dataclasses pures, aucune dépendance
réseau/DB (même discipline que `apex.models.model`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecretRef:
    """Référence LOGIQUE à un secret — jamais la valeur elle-même. `key`
    est un identifiant stable (`"openai_api_key"`), résolu par
    `SecretBroker.resolve()` au moment de l'appel, jamais mis en cache par
    l'appelant — un secret révoqué/tourné doit être vu au prochain appel."""

    key: str
