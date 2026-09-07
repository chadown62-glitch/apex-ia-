"""Tests de `DesignService` (N+17) — façade seule, sans Operator : construction
par défaut (providers réels honnêtes), permissions (`SUBMIT_DESIGN`,
HUMAIN seul), suivi en mémoire (`get`/`list_recent`), `active_count`
observable pendant une exécution, `resume` après échec."""

from __future__ import annotations

import tempfile
import threading
import unittest

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.design.model import VisualPrompt
from apex.design.research import NullResearchProvider
from apex.design.service import DesignService
from apex.design.visual_provider import VisualCapabilityReport, VisualGenerationProvider, VisualJobStatus
from apex.errors import PolicyDenied
from apex.obs.capability import CapabilityStatus
from apex.policy.model import Subject


class _BlockingVisualProvider(VisualGenerationProvider):
    """Double de test — bloque `generate()` jusqu'à ce que le test le
    libère, pour observer `DesignService.active_count()` PENDANT une
    exécution réelle (pas une valeur devinée)."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()

    def detect(self) -> VisualCapabilityReport:
        return VisualCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="blocking", detail="test")

    def generate(self, prompt: VisualPrompt) -> str:
        self.entered.set()
        self.release.wait(timeout=5)
        raise RuntimeError("interrompu volontairement après déblocage — le test ne lit jamais ce résultat")

    def status(self, job_id: str) -> VisualJobStatus:
        raise NotImplementedError

    def retrieve(self, job_id: str) -> bytes:
        raise NotImplementedError

    def validate(self, data: bytes) -> bool:
        return False


class DesignServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.service = DesignService(
            artifact_store=LocalFilesystemArtifactStore(root=self._tmp),
            research_provider=NullResearchProvider(),
        )


class TestSubmit(DesignServiceTestCase):
    def test_submit_returns_a_completed_package(self) -> None:
        package = self.service.submit("Créer le concept d'un skatepark urbain moderne")
        self.assertEqual(package.state.value, "completed")
        self.assertTrue(package.ready)

    def test_submit_rejects_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            self.service.submit("   ")

    def test_submit_denies_non_human_subject(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.submit("x", requested_by=Subject.CORE)
        with self.assertRaises(PolicyDenied):
            self.service.submit("x", requested_by=Subject.RESEARCH)


class TestTracking(DesignServiceTestCase):
    def test_get_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(self.service.get("does-not-exist"))

    def test_get_returns_the_submitted_package(self) -> None:
        package = self.service.submit("Un pavillon minimaliste")
        self.assertEqual(self.service.get(package.design_id).design_id, package.design_id)
        self.assertEqual(self.service.get_status(package.design_id).design_id, package.design_id)
        self.assertEqual(self.service.get_result(package.design_id).design_id, package.design_id)

    def test_list_recent_orders_newest_first(self) -> None:
        first = self.service.submit("Premier concept")
        second = self.service.submit("Deuxième concept")
        recent = self.service.list_recent()
        self.assertEqual(recent[0].design_id, second.design_id)
        self.assertEqual(recent[1].design_id, first.design_id)

    def test_list_recent_respects_limit(self) -> None:
        for i in range(5):
            self.service.submit(f"Concept {i}")
        self.assertEqual(len(self.service.list_recent(limit=2)), 2)


class TestResume(DesignServiceTestCase):
    def test_resume_unknown_id_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.service.resume("does-not-exist")

    def test_resume_on_a_completed_design_is_a_no_op(self) -> None:
        package = self.service.submit("Un kiosque")
        resumed = self.service.resume(package.design_id)
        self.assertEqual(resumed.state.value, "completed")


class TestActiveCount(unittest.TestCase):
    def test_active_count_reflects_an_in_progress_submission(self) -> None:
        tmp = tempfile.mkdtemp()
        blocking = _BlockingVisualProvider()
        service = DesignService(
            artifact_store=LocalFilesystemArtifactStore(root=tmp),
            research_provider=NullResearchProvider(),
            visual_provider=blocking,
        )
        self.assertEqual(service.active_count(), 0)

        thread = threading.Thread(target=lambda: service.submit("Un jardin suspendu"))
        thread.start()
        try:
            self.assertTrue(blocking.entered.wait(timeout=5), "generate() jamais atteint")
            # Le pipeline est réellement bloqué DANS `generate()` à cet
            # instant précis — `active_count()` doit refléter une exécution
            # réellement en cours, jamais une valeur devinée.
            self.assertEqual(service.active_count(), 1)
        finally:
            blocking.release.set()
            thread.join(timeout=5)
        self.assertEqual(service.active_count(), 0)


if __name__ == "__main__":
    unittest.main()
