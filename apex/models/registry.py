"""Requêtes PURES sur les modèles déjà déclarés dans `ApexConfig` —
`ModelRole.candidates` (identité logique, `ModelRef`) et `ApexConfig.
endpoints` (lieu physique, `ModelEndpoint`).

**Ce n'est PAS un `ModelRegistry` au sens d'une nouvelle classe à état** :
`ApexConfig` EST déjà, depuis la phase 1, la déclaration exhaustive de
chaque modèle connu d'APEX (roles + endpoints, réellement validée à chaque
chargement de config). Créer une seconde structure qui la reflète en
mémoire dupliquerait une source de vérité déjà réelle — exactement le
doublon qu'ADR-0007/le test en 4 points interdit. Ce module fournit
uniquement les fonctions de lecture qui manquaient (filtrage par
capacité/contexte/localité), rien de plus.

**Appariement `ModelRef` ↔ `ModelEndpoint`** : aucune clé étrangère
stricte n'existe entre les deux (vérifié — `ApexConfig.validate()` ne
relie les deux que par une heuristique de préfixe,
`served_model.startswith(ref.family)`, utilisée pour la seule vérification
VRAM résidente). Ce module RÉUTILISE cette même heuristique déjà admise
par le code validé, plutôt que d'en inventer une seconde — mais ne prétend
jamais qu'elle est une garantie stricte : un `ModelCandidate` sans
`endpoint` résolu est une intention déclarée (`ModelRef` seul), pas encore
une capacité réellement joignable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from apex.config.schema import ApexConfig, ModelEndpoint, ModelRef


@dataclass(frozen=True)
class ModelCandidate:
    """Un `ModelRef` (intention logique) apparié — quand c'est possible —
    à un `ModelEndpoint` réel (lieu physique). `endpoint is None` signifie
    « déclaré comme candidat de rôle, aucun point de service configuré » —
    jamais une capacité à considérer comme joignable."""

    role: str
    ref: ModelRef
    resident: bool
    endpoint: ModelEndpoint | None

    @property
    def is_available(self) -> bool:
        """Un candidat n'est réellement utilisable que s'il a un
        `ModelEndpoint` — jamais déduit d'autre chose."""
        return self.endpoint is not None

    @property
    def is_local(self) -> bool:
        """`quantization == "external"` est déjà, par construction
        (`docs/EXTERNAL_INTELLIGENCE.md` §2), la marque d'un modèle non
        local — réutilisée telle quelle, jamais réinventée."""
        return self.ref.quantization != "external"


def serves(endpoint: Any, ref: Any) -> bool:
    """L'endpoint sert-il CE modèle ? Même famille ET même version dans le nom servi (06/10/2026 : la famille
    seule appariait une référence 7B à un endpoint 1.5B de la même famille — un modèle déclaré pour un autre)."""
    served, family = endpoint.served_model.lower(), ref.family.lower()
    if served == family:
        return True  # nom servi = famille (API externe : la version n'apparaît pas dans le nom)
    return served.startswith(family) and ref.version.lower() in served


def resolve_candidates(config: ApexConfig) -> tuple[ModelCandidate, ...]:
    """Un `ModelCandidate` par (rôle, `ModelRef` candidat) déclaré dans
    `config.model_roles` — apparié à son `ModelEndpoint` si un endpoint
    dont `served_model` commence par `ref.family` existe (même heuristique
    que `ApexConfig.validate()`)."""
    candidates: list[ModelCandidate] = []
    for role in config.model_roles.values():
        for ref in role.candidates:
            matches = tuple(e for e in config.endpoints if serves(e, ref))
            if matches:
                for endpoint in matches:
                    candidates.append(
                        ModelCandidate(role=role.role, ref=ref, resident=role.resident, endpoint=endpoint)
                    )
            else:
                candidates.append(ModelCandidate(role=role.role, ref=ref, resident=role.resident, endpoint=None))
    return tuple(candidates)


def filter_available(candidates: Iterable[ModelCandidate]) -> tuple[ModelCandidate, ...]:
    return tuple(c for c in candidates if c.is_available)


def filter_by_role(candidates: Iterable[ModelCandidate], role: str) -> tuple[ModelCandidate, ...]:
    return tuple(c for c in candidates if c.role == role)


def filter_by_capability_class(candidates: Iterable[ModelCandidate], capability_class: str) -> tuple[ModelCandidate, ...]:
    return tuple(c for c in candidates if c.ref.capability_class == capability_class)


def filter_local(candidates: Iterable[ModelCandidate], *, local: bool = True) -> tuple[ModelCandidate, ...]:
    return tuple(c for c in candidates if c.is_local == local)


def filter_by_min_context(candidates: Iterable[ModelCandidate], min_context: int) -> tuple[ModelCandidate, ...]:
    """Exige un `endpoint` réel : un candidat sans endpoint n'a pas de
    `max_context` connu — exclu, jamais supposé suffisant."""
    return tuple(c for c in candidates if c.endpoint is not None and c.endpoint.max_context >= min_context)
