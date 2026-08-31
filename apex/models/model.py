"""Modèle de domaine de `ModelProvider` — dataclasses pures, aucune
dépendance réseau (même discipline que `apex.sandbox.model`/`apex.artifacts.
model`).

`GenerationRequest` porte un `ModelEndpoint` déjà résolu (`apex.config.
schema`) : la résolution rôle -> endpoint (`Placement`, PLANNED) reste
explicitement hors périmètre de cette étape. Aucun modèle n'est jamais codé
en dur ici — l'endpoint et le modèle viennent TOUJOURS de l'appelant, qui
les a lui-même lus depuis `ApexConfig`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from apex.config.schema import ModelEndpoint
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement


@dataclass(frozen=True)
class GenerationRequest:
    """`timeout_s` est obligatoire (principe 13 : aucune opération à effet
    de bord sans budget de temps). `cancel_event` est une annulation
    coopérative propre à un appel HTTP — délibérément PAS
    `apex.sandbox.model.CancellationToken` (conçu pour tuer un groupe de
    processus noyau, une responsabilité différente ; décision explicite,
    Phase 6)."""

    endpoint: ModelEndpoint
    prompt: str
    max_tokens: int | None = None
    temperature: float | None = None
    timeout_s: float = 30.0
    cancel_event: threading.Event | None = None


@dataclass(frozen=True)
class GenerationResult:
    """`model_used` est le nom RÉELLEMENT renvoyé par le backend — jamais
    supposé identique à `endpoint.served_model` demandé. `tokens_prompt`/
    `tokens_completion` sont `Measurement` (REAL/UNKNOWN) : un comptage de
    tokens n'est affirmé QUE si le backend l'a réellement fourni."""

    text: str
    model_used: str
    tokens_prompt: Measurement
    tokens_completion: Measurement
    finish_reason: str | None
    elapsed_s: float
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelCapabilityReport:
    """Même forme que `SandboxCapabilityReport`/`ArtifactStoreCapabilityReport`
    (étapes 7/10) : résultat d'une détection EMPIRIQUE, jamais supposée."""

    status: CapabilityStatus
    backend: str
    detail: str
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE
