"""Contrat `VisualGenerationProvider` — génération d'image agnostique du
backend pour l'étape VISUALIZE du pipeline de conception. Même patron que
`apex.artifacts.provider`/`apex.models.provider` : une ABC (`generate`/
`status`/`retrieve`/`validate`), un seul point de construction
(`get_default_provider()`).

Aucun backend visuel réel (DALL-E, Stable Diffusion, ou autre) n'est
connecté dans ce dépôt aujourd'hui — confirmé par l'audit N+17 (aucune
dépendance, aucune clé API, `apex.routing.model.Capability.VISION` jamais
dérivée). `NullVisualGenerationProvider` est le comportement de production
honnête : `generate()` lève `VisualBackendUnavailable` plutôt que de
prétendre lancer une génération. Un futur backend réel s'ajouterait ICI,
dans `get_default_provider()`, sans modifier `apex.design.pipeline`.

Un double de test (`_MockVisualGenerationProvider`, sous-classe de cette
même ABC) existe UNIQUEMENT dans `tests/test_design_pipeline.py` — jamais
dans ce module de production, même convention que `_FakeProvider(
ModelProvider)` dans `tests/test_routing_generation.py`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from apex.design.model import VisualPrompt
from apex.obs.capability import CapabilityStatus


@dataclass(frozen=True)
class VisualCapabilityReport:
    status: CapabilityStatus
    backend: str
    detail: str
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE


@dataclass(frozen=True)
class VisualJobStatus:
    job_id: str
    ready: bool
    failed: bool
    detail: str


class VisualGenerationProvider(ABC):
    @abstractmethod
    def detect(self) -> VisualCapabilityReport:
        """Sonde la disponibilité RÉELLE d'un backend visuel — jamais
        supposée. Ne lève jamais."""

    @abstractmethod
    def generate(self, prompt: VisualPrompt) -> str:
        """Lance une génération RÉELLE et retourne un identifiant de job.
        Lève `apex.errors.VisualBackendUnavailable` si aucun backend réel
        n'est disponible — jamais un identifiant de job fictif."""

    @abstractmethod
    def status(self, job_id: str) -> VisualJobStatus:
        """État RÉEL d'un job précédemment lancé par `generate()`."""

    @abstractmethod
    def retrieve(self, job_id: str) -> bytes:
        """Octets RÉELS du résultat — lève si le job n'est pas prêt ou a
        échoué. Jamais un contenu de substitution."""

    @abstractmethod
    def validate(self, data: bytes) -> bool:
        """Vérifie que `data` est un contenu image plausible (format,
        taille non nulle) — une validation structurelle minimale, jamais
        une évaluation de qualité (hors périmètre : réservé à une future
        validation par modèle de vision, voir `apex.design.validation`)."""


class NullVisualGenerationProvider(VisualGenerationProvider):
    """Comportement honnête en l'absence de tout backend de génération
    visuelle réel dans ce dépôt."""

    _DETAIL = (
        "aucun backend de génération visuelle réel n'est connecté dans ce dépôt "
        "(aucune clé API, aucune dépendance de génération d'image) — l'abstraction "
        "et l'orchestration sont réelles et testées, mais aucune image n'est "
        "réellement générée : VISUAL_BACKEND_UNAVAILABLE"
    )

    def detect(self) -> VisualCapabilityReport:
        return VisualCapabilityReport(
            status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="null", detail=self._DETAIL
        )

    def generate(self, prompt: VisualPrompt) -> str:
        from apex.errors import VisualBackendUnavailable

        raise VisualBackendUnavailable(self._DETAIL)

    def status(self, job_id: str) -> VisualJobStatus:
        from apex.errors import VisualBackendUnavailable

        raise VisualBackendUnavailable(self._DETAIL)

    def retrieve(self, job_id: str) -> bytes:
        from apex.errors import VisualBackendUnavailable

        raise VisualBackendUnavailable(self._DETAIL)

    def validate(self, data: bytes) -> bool:
        return False


def get_default_provider() -> VisualGenerationProvider:
    """Seul point de construction — un futur backend réel serait choisi
    ICI, jamais par chaque consommateur individuellement."""
    return NullVisualGenerationProvider()
