"""Tests du Design Engine (N+17) — machine à états, modèle de domaine,
recherche/génération visuelle honnêtes en l'absence de backend réel,
synthèse déterministe, moteur de prompts, validation structurelle,
orchestration du pipeline (y compris reprise après échec), et un chemin
« complété avec un backend visuel réel » via un double de test QUI
SOUS-CLASSE le contrat réel `VisualGenerationProvider` (même discipline que
`_FakeProvider(ModelProvider)` dans `tests/test_routing_generation.py`) —
jamais un mock générique, jamais présenté comme une preuve de génération
d'image réelle en production."""

from __future__ import annotations

import os
import tempfile
import unittest

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.design.model import DesignPackage, DesignRequest, ResearchResult, VisualPrompt
from apex.design.pipeline import DesignPipeline
from apex.design.prompt_engine import build_visual_prompts
from apex.design.research import NullResearchProvider, ResearchProvider
from apex.design.states import (
    DESIGN_STEP_ORDER,
    DesignPipelineState,
    DesignStep,
    DesignStepState,
    InvalidDesignTransition,
    assert_transition_allowed,
    step_to_state,
)
from apex.design.synthesis import analyze_request, finalize_brief, synthesize_concepts
from apex.design.validation import validate_package
from apex.design.visual_provider import (
    NullVisualGenerationProvider,
    VisualCapabilityReport,
    VisualGenerationProvider,
    VisualJobStatus,
)
from apex.errors import VisualBackendUnavailable
from apex.obs.capability import CapabilityStatus
from apex.policy.model import Subject


# -- doubles de test, UNIQUEMENT ici (jamais dans apex/design/) -------------


class _MockVisualGenerationProvider(VisualGenerationProvider):
    """Double de test fidèle au contrat `VisualGenerationProvider` — simule
    un backend visuel RÉELLEMENT branché, pour exercer le chemin `state=
    "completed"` du pipeline sans dépendre d'un service externe réel. Jamais
    utilisé en production (voir `apex.design.visual_provider.
    get_default_provider`, qui retourne toujours `NullVisualGenerationProvider`)."""

    def __init__(self, *, fail: bool = False, invalid: bool = False) -> None:
        self._fail = fail
        self._invalid = invalid
        self._jobs: dict[str, bytes] = {}

    def detect(self) -> VisualCapabilityReport:
        return VisualCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="mock", detail="backend de test")

    def generate(self, prompt: VisualPrompt) -> str:
        job_id = f"job-{prompt.id}"
        self._jobs[job_id] = b"" if self._invalid else b"\x89PNG\r\n\x1a\n" + prompt.render().encode("utf-8")
        return job_id

    def status(self, job_id: str) -> VisualJobStatus:
        if self._fail:
            return VisualJobStatus(job_id=job_id, ready=False, failed=True, detail="échec simulé")
        return VisualJobStatus(job_id=job_id, ready=True, failed=False, detail="prêt")

    def retrieve(self, job_id: str) -> bytes:
        return self._jobs[job_id]

    def validate(self, data: bytes) -> bool:
        return len(data) > 0


class _FlakyOnceResearchProvider(NullResearchProvider):
    """Lève une VRAIE exception au premier `detect()` (simulation d'une
    panne transitoire), se comporte comme `NullResearchProvider` ensuite —
    utilisé pour exercer `DesignPipeline.resume`."""

    def __init__(self) -> None:
        self.calls = 0

    def detect(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("panne transitoire simulée")
        return super().detect()


def _artifact_store(tmp_dir: str) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(root=tmp_dir)


class _DesignPipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp()
        self.store = _artifact_store(self._tmp_dir)

    def _pipeline(self, *, research=None, visual=None) -> DesignPipeline:
        return DesignPipeline(
            artifact_store=self.store,
            research_provider=research or NullResearchProvider(),
            visual_provider=visual or NullVisualGenerationProvider(),
        )


# -- états --------------------------------------------------------------------


class TestStates(unittest.TestCase):
    def test_step_order_is_fixed_and_covers_the_seven_named_steps(self) -> None:
        self.assertEqual(
            [s.value for s in DESIGN_STEP_ORDER],
            ["analyze", "research", "synthesize", "design", "visualize", "validate", "package"],
        )

    def test_request_can_only_move_to_analyzing_or_failed(self) -> None:
        assert_transition_allowed(DesignPipelineState.REQUEST, DesignPipelineState.ANALYZING)
        assert_transition_allowed(DesignPipelineState.REQUEST, DesignPipelineState.FAILED)
        with self.assertRaises(InvalidDesignTransition):
            assert_transition_allowed(DesignPipelineState.REQUEST, DesignPipelineState.COMPLETED)

    def test_completed_is_terminal(self) -> None:
        with self.assertRaises(InvalidDesignTransition):
            assert_transition_allowed(DesignPipelineState.COMPLETED, DesignPipelineState.ANALYZING)
        self.assertTrue(DesignPipelineState.COMPLETED.is_terminal)
        self.assertTrue(DesignPipelineState.FAILED.is_terminal)
        self.assertFalse(DesignPipelineState.ANALYZING.is_terminal)

    def test_failed_can_resume_into_any_processing_step(self) -> None:
        for step in DESIGN_STEP_ORDER:
            assert_transition_allowed(DesignPipelineState.FAILED, step_to_state(step))

    def test_step_to_state_covers_every_step(self) -> None:
        for step in DesignStep:
            self.assertIsInstance(step_to_state(step), DesignPipelineState)


# -- modèle ---------------------------------------------------------------


class TestModel(unittest.TestCase):
    def test_design_request_new_generates_a_unique_id(self) -> None:
        a = DesignRequest.new("un pavillon", requested_by=Subject.HUMAN)
        b = DesignRequest.new("un pavillon", requested_by=Subject.HUMAN)
        self.assertNotEqual(a.id, b.id)
        self.assertTrue(a.id.startswith("design_"))

    def test_visual_prompt_render_includes_every_dimension(self) -> None:
        prompt = VisualPrompt(
            id="p1", concept_id="c1", variant=1, subject="s", environment="e", composition="c",
            architecture_design="a", materials="m", colors="col", light="l", camera="cam",
            style="st", constraints="con", elements_to_avoid="avoid",
        )
        rendered = prompt.render()
        for fragment in ("s", "e", "c", "a", "m", "col", "l", "cam", "st", "con", "avoid"):
            self.assertIn(fragment, rendered)

    def test_design_package_to_dict_round_trips_nested_structures(self) -> None:
        request = DesignRequest.new("un pavillon", requested_by=Subject.HUMAN)
        package = DesignPackage(design_id=request.id, request=request, state=DesignPipelineState.REQUEST)
        data = package.to_dict()
        self.assertEqual(data["design_id"], request.id)
        self.assertEqual(data["state"], "request")
        self.assertEqual(data["steps"], [])
        self.assertIsNone(data["brief"])


# -- recherche --------------------------------------------------------------


class TestResearchProvider(unittest.TestCase):
    def test_null_provider_reports_unavailable_never_fabricates(self) -> None:
        provider: ResearchProvider = NullResearchProvider()
        report = provider.detect()
        self.assertFalse(report.is_available)
        self.assertEqual(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)
        self.assertEqual(provider.search("un pavillon"), ())


# -- génération visuelle -----------------------------------------------------


class TestVisualProvider(unittest.TestCase):
    def test_null_provider_generate_raises_visual_backend_unavailable(self) -> None:
        provider = NullVisualGenerationProvider()
        self.assertFalse(provider.detect().is_available)
        with self.assertRaises(VisualBackendUnavailable):
            provider.generate(_a_prompt())

    def test_mock_provider_completes_a_real_looking_job(self) -> None:
        provider = _MockVisualGenerationProvider()
        prompt = _a_prompt()
        job_id = provider.generate(prompt)
        status = provider.status(job_id)
        self.assertTrue(status.ready)
        data = provider.retrieve(job_id)
        self.assertTrue(provider.validate(data))


def _a_prompt() -> VisualPrompt:
    return VisualPrompt(
        id="p1", concept_id="c1", variant=1, subject="s", environment="e", composition="c",
        architecture_design="a", materials="m", colors="col", light="l", camera="cam",
        style="st", constraints="con", elements_to_avoid="avoid",
    )


# -- synthèse ---------------------------------------------------------------


class TestSynthesis(unittest.TestCase):
    def test_analyze_request_rejects_empty_text(self) -> None:
        request = DesignRequest(id="x", text="   ", requested_by=Subject.HUMAN, created_at=_now())
        with self.assertRaises(ValueError):
            analyze_request(request)

    def test_analyze_request_detects_style_keywords(self) -> None:
        request = DesignRequest.new(
            "Créer le concept d'un skatepark urbain moderne", requested_by=Subject.HUMAN
        )
        brief = analyze_request(request)
        self.assertIn("moderne", brief.style_keywords)
        self.assertIn("urbain", brief.style_keywords)
        self.assertEqual(brief.provenance, "deterministic-template")

    def test_finalize_brief_notes_research_volume(self) -> None:
        request = DesignRequest.new("un jardin japonais", requested_by=Subject.HUMAN)
        brief = analyze_request(request)
        research = (
            ResearchResult(source="s", title="t", url=None, excerpt="e", relevance=0.5, timestamp=_now(), provenance="mock"),
        )
        finalized = finalize_brief(brief, research)
        self.assertIn("1 résultat", finalized.summary)

    def test_synthesize_concepts_produces_at_least_one_concept(self) -> None:
        request = DesignRequest.new("un abri de jardin", requested_by=Subject.HUMAN)
        brief = analyze_request(request)
        concepts = synthesize_concepts(brief, ())
        self.assertGreaterEqual(len(concepts), 1)
        self.assertTrue(all(c.provenance == "deterministic-template" for c in concepts))

    def test_synthesize_concepts_versions_one_per_style_keyword(self) -> None:
        request = DesignRequest.new("un pavillon moderne et urbain", requested_by=Subject.HUMAN)
        brief = analyze_request(request)
        concepts = synthesize_concepts(brief, ())
        self.assertEqual([c.version for c in concepts], list(range(1, len(concepts) + 1)))
        self.assertEqual(len(concepts), len(brief.style_keywords[:3]))


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# -- moteur de prompts --------------------------------------------------------


class TestPromptEngine(unittest.TestCase):
    def test_build_visual_prompts_produces_multiple_variants_linked_to_concept(self) -> None:
        request = DesignRequest.new("un pavillon moderne", requested_by=Subject.HUMAN)
        brief = analyze_request(request)
        concept = synthesize_concepts(brief, ())[0]
        prompts = build_visual_prompts(concept)
        self.assertGreaterEqual(len(prompts), 2)
        self.assertTrue(all(p.concept_id == concept.id for p in prompts))
        self.assertEqual({p.variant for p in prompts}, set(range(1, len(prompts) + 1)))


# -- validation ---------------------------------------------------------------


class TestValidation(unittest.TestCase):
    def test_empty_package_fails_validation(self) -> None:
        request = DesignRequest.new("x", requested_by=Subject.HUMAN)
        package = DesignPackage(design_id=request.id, request=request, state=DesignPipelineState.VALIDATING)
        validation = validate_package(package)
        self.assertFalse(validation.passed)
        self.assertTrue(validation.failures)
        self.assertTrue(validation.checks)


# -- pipeline (bout en bout) --------------------------------------------------


class TestPipelineRun(_DesignPipelineTestCase):
    def test_full_run_completes_and_persists_artifacts(self) -> None:
        pipeline = self._pipeline()
        request = DesignRequest.new("Créer le concept d'un skatepark urbain moderne", requested_by=Subject.HUMAN)
        package = pipeline.run(request)
        self.assertEqual(package.state, DesignPipelineState.COMPLETED)
        self.assertTrue(package.ready)
        self.assertIsNone(package.error)
        self.assertEqual([s.step for s in package.steps], list(DESIGN_STEP_ORDER))
        self.assertGreaterEqual(len(package.artifacts), 1)
        for ref in package.artifacts:
            self.assertTrue(self.store.exists(ref.content_hash))

    def test_visualize_reports_unavailable_honestly_without_real_backend(self) -> None:
        pipeline = self._pipeline()
        request = DesignRequest.new("Un pont piéton", requested_by=Subject.HUMAN)
        package = pipeline.run(request)
        self.assertTrue(package.visual_results)
        self.assertTrue(all(v.state == "unavailable" for v in package.visual_results))
        self.assertTrue(all(v.artifact_content_hash is None for v in package.visual_results))

    def test_visualize_completes_with_a_real_looking_backend(self) -> None:
        pipeline = self._pipeline(visual=_MockVisualGenerationProvider())
        request = DesignRequest.new("Une serre bioclimatique", requested_by=Subject.HUMAN)
        package = pipeline.run(request)
        self.assertTrue(any(v.state == "completed" for v in package.visual_results))
        completed = [v for v in package.visual_results if v.state == "completed"]
        for v in completed:
            self.assertIsNotNone(v.artifact_content_hash)
            self.assertTrue(self.store.exists(v.artifact_content_hash))

    def test_visualize_backend_failure_is_reported_as_failed_not_crashed(self) -> None:
        pipeline = self._pipeline(visual=_MockVisualGenerationProvider(fail=True))
        request = DesignRequest.new("Un phare contemporain", requested_by=Subject.HUMAN)
        package = pipeline.run(request)
        self.assertEqual(package.state, DesignPipelineState.COMPLETED)
        self.assertTrue(all(v.state == "failed" for v in package.visual_results))


class TestPipelineEdgeCases(_DesignPipelineTestCase):
    def test_empty_request_text_fails_at_analyze_without_crashing_the_process(self) -> None:
        pipeline = self._pipeline()
        request = DesignRequest(id="x", text="   ", requested_by=Subject.HUMAN, created_at=_now())
        package = pipeline.run(request)
        self.assertEqual(package.state, DesignPipelineState.FAILED)
        self.assertEqual(package.steps[0].step, DesignStep.ANALYZE)
        self.assertEqual(package.steps[0].state, DesignStepState.FAILED)

    def test_research_unavailable_does_not_block_the_pipeline(self) -> None:
        pipeline = self._pipeline(research=NullResearchProvider())
        request = DesignRequest.new("Un centre culturel", requested_by=Subject.HUMAN)
        package = pipeline.run(request)
        self.assertEqual(package.state, DesignPipelineState.COMPLETED)
        research_step = next(s for s in package.steps if s.step == DesignStep.RESEARCH)
        self.assertEqual(research_step.state, DesignStepState.SKIPPED)

    def test_visual_provider_unavailable_does_not_block_the_pipeline(self) -> None:
        pipeline = self._pipeline(visual=NullVisualGenerationProvider())
        request = DesignRequest.new("Un marché couvert", requested_by=Subject.HUMAN)
        package = pipeline.run(request)
        self.assertEqual(package.state, DesignPipelineState.COMPLETED)

    def test_missing_artifact_kind_is_never_silently_skipped_in_validation(self) -> None:
        # Un DesignPackage manuellement construit sans étapes exécutées doit
        # échouer la validation plutôt que d'être déclaré valide par défaut.
        request = DesignRequest.new("x", requested_by=Subject.HUMAN)
        package = DesignPackage(design_id=request.id, request=request, state=DesignPipelineState.VALIDATING)
        validation = validate_package(package)
        self.assertFalse(validation.passed)

    def test_resume_on_completed_package_is_a_no_op(self) -> None:
        pipeline = self._pipeline()
        request = DesignRequest.new("Un kiosque à musique", requested_by=Subject.HUMAN)
        package = pipeline.run(request)
        resumed = pipeline.resume(package)
        self.assertIs(resumed, package)

    def test_interrupted_pipeline_resumes_from_the_failed_step_only(self) -> None:
        flaky = _FlakyOnceResearchProvider()
        pipeline = self._pipeline(research=flaky)
        request = DesignRequest.new("Un observatoire astronomique", requested_by=Subject.HUMAN)
        first = pipeline.run(request)
        self.assertEqual(first.state, DesignPipelineState.FAILED)
        self.assertEqual(first.steps[-1].step, DesignStep.RESEARCH)
        analyze_artifacts_before = len(first.artifacts)

        resumed = pipeline.resume(first)
        self.assertEqual(resumed.state, DesignPipelineState.COMPLETED)
        self.assertEqual([s.step for s in resumed.steps], list(DESIGN_STEP_ORDER))
        # L'étape ANALYZE (déjà réussie) n'a pas été ré-exécutée : son
        # artefact n'a pas été dupliqué.
        analyze_artifacts_after = sum(1 for a in resumed.artifacts if a.kind == "brief")
        self.assertEqual(analyze_artifacts_after, 1)
        self.assertGreaterEqual(len(resumed.artifacts), analyze_artifacts_before)

    def test_resume_without_any_failed_step_recorded_is_a_no_op(self) -> None:
        pipeline = self._pipeline()
        request = DesignRequest.new("x", requested_by=Subject.HUMAN)
        package = DesignPackage(design_id=request.id, request=request, state=DesignPipelineState.FAILED)
        resumed = pipeline.resume(package)
        self.assertIs(resumed, package)


if __name__ == "__main__":
    unittest.main()
