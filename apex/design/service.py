"""`DesignService` — façade unique du Design Engine, consommée directement
ou via `apex.operator.service.OperatorService` (N+16, submission sans
terminal). Compose les providers réels de ce dépôt (`apex.artifacts`,
`apex.design.research`, `apex.design.visual_provider`) — jamais un second
mécanisme de stockage ou d'orchestration.

Exécution SYNCHRONE et volontairement pas asynchrone : aucune étape de ce
pipeline n'effectue réellement d'appel réseau bloquant aujourd'hui (aucun
backend de recherche/génération visuelle réel connecté — voir
`apex.design.research`/`apex.design.visual_provider`), donc une exécution en
tâche de fond apporterait une complexité de threading sans bénéfice réel ;
le jour où un backend réel et lent est branché, `submit()` reste le seul
point à faire évoluer (même discipline que `get_default_provider()`).

État en mémoire, PAR PROCESSUS — même limite honnête, déjà documentée, que
`apex.operator.service.OperatorService` (redémarrer le processus API perd
les exécutions en mémoire ; le `DesignPackage` final, lui, reste durable
car déjà écrit dans `apex.artifacts`, adressé par contenu)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from apex.artifacts.provider import ArtifactStoreProvider, get_default_provider as get_default_artifact_provider
from apex.design.model import DesignPackage, DesignRequest
from apex.design.pipeline import DesignPipeline
from apex.design.research import ResearchProvider, get_default_provider as get_default_research_provider
from apex.design.visual_provider import (
    VisualGenerationProvider,
    get_default_provider as get_default_visual_provider,
)
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DesignService:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStoreProvider | None = None,
        research_provider: ResearchProvider | None = None,
        visual_provider: VisualGenerationProvider | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self._artifacts = artifact_store or get_default_artifact_provider()
        self._research = research_provider or get_default_research_provider()
        self._visual = visual_provider or get_default_visual_provider()
        self.policy = policy or PolicyEngine()
        self._lock = threading.Lock()
        self._runs: dict[str, DesignPackage] = {}
        self._active = 0

    def active_count(self) -> int:
        """Nombre d'exécutions de pipeline actuellement en cours — utilisé
        par `apex.operator.service.OperatorService._observe_activity` pour
        dériver `RUNNING` sans dupliquer d'état."""
        with self._lock:
            return self._active

    def submit(self, request_text: str, *, requested_by: Subject = Subject.HUMAN) -> DesignPackage:
        self.policy.authorize(requested_by, Action.SUBMIT_DESIGN)
        if not request_text or not request_text.strip():
            raise ValueError("la requête de conception ne peut pas être vide")
        request = DesignRequest.new(request_text, requested_by=requested_by)
        pipeline = self._new_pipeline()
        with self._lock:
            self._active += 1
        try:
            package = pipeline.run(request)
        finally:
            with self._lock:
                self._active -= 1
        with self._lock:
            self._runs[package.design_id] = package
        return package

    def resume(self, design_id: str) -> DesignPackage:
        with self._lock:
            existing = self._runs.get(design_id)
        if existing is None:
            raise KeyError(f"aucune conception « {design_id} » connue")
        if existing.state.value != "failed":
            return existing
        pipeline = self._new_pipeline()
        with self._lock:
            self._active += 1
        try:
            package = pipeline.resume(existing)
        finally:
            with self._lock:
                self._active -= 1
        with self._lock:
            self._runs[design_id] = package
        return package

    def get(self, design_id: str) -> DesignPackage | None:
        with self._lock:
            return self._runs.get(design_id)

    # Alias explicites — même vocabulaire que `apex.operator.service.
    # OperatorService.get_task_status`/`get_task_result`, consommés
    # identiquement par les routes API.
    get_status = get
    get_result = get

    def list_recent(self, limit: int = 100) -> tuple[DesignPackage, ...]:
        with self._lock:
            packages = sorted(self._runs.values(), key=lambda p: p.created_at, reverse=True)
        return tuple(packages[:limit])

    def _new_pipeline(self) -> DesignPipeline:
        return DesignPipeline(
            artifact_store=self._artifacts, research_provider=self._research, visual_provider=self._visual,
        )
