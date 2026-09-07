"""Orchestrateur du pipeline de conception — REQUEST -> ANALYZE -> RESEARCH
-> SYNTHESIZE -> DESIGN -> VISUALIZE -> VALIDATE -> PACKAGE.

Chaque étape produit un `apex.design.model.StepRecord` (état, résumé
d'entrée/sortie, erreur éventuelle, logs, durée, provenance) et fait
progresser `DesignPackage.state` via `apex.design.states.
assert_transition_allowed` — jamais un raccourci silencieux. Un artefact
est persisté (`apex.artifacts`, adressé par contenu) dès qu'une étape
produit un résultat durable, jamais attendu jusqu'à PACKAGE seul.

Une étape RESEARCH/VISUALIZE qui constate l'absence d'un backend réel ne
lève jamais et n'interrompt jamais le pipeline (`DesignStepState.SKIPPED`)
— seule une véritable erreur (entrée invalide, échec de validation
structurelle, exception inattendue) transitionne le pipeline vers
`DesignPipelineState.FAILED` et l'arrête à cette étape."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

from apex.artifacts.provider import ArtifactStoreProvider
from apex.design.model import (
    DesignArtifactRef,
    DesignPackage,
    DesignRequest,
    StepRecord,
    VisualPrompt,
    VisualResult,
    new_id,
)
from apex.design.prompt_engine import build_visual_prompts
from apex.design.research import ResearchProvider
from apex.design.states import (
    DESIGN_STEP_ORDER,
    DesignPipelineState,
    DesignStep,
    DesignStepState,
    assert_transition_allowed,
    step_to_state,
)
from apex.design.synthesis import analyze_request, finalize_brief, synthesize_concepts
from apex.design.validation import validate_package
from apex.design.visual_provider import VisualCapabilityReport, VisualGenerationProvider
from apex.errors import VisualBackendUnavailable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DesignPipeline:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStoreProvider,
        research_provider: ResearchProvider,
        visual_provider: VisualGenerationProvider,
    ) -> None:
        self._artifacts = artifact_store
        self._research = research_provider
        self._visual = visual_provider

    # -- points d'entrée -------------------------------------------------

    def run(self, request: DesignRequest) -> DesignPackage:
        package = DesignPackage(design_id=request.id, request=request, state=DesignPipelineState.REQUEST)
        return self._advance(package, from_index=0)

    def resume(self, package: DesignPackage) -> DesignPackage:
        """Reprend un pipeline en échec à l'étape qui a échoué — jamais en
        ré-exécutant les étapes déjà réussies. Sans effet (retourne le
        paquet inchangé) si `package.state` n'est pas `FAILED`."""
        if package.state is not DesignPipelineState.FAILED:
            return package
        failed_step = next(
            (record.step for record in reversed(package.steps) if record.state is DesignStepState.FAILED),
            None,
        )
        if failed_step is None:
            return package
        from_index = DESIGN_STEP_ORDER.index(failed_step)
        kept_steps = tuple(
            record for record in package.steps if DESIGN_STEP_ORDER.index(record.step) < from_index
        )
        package = replace(package, steps=kept_steps, error=None)
        return self._advance(package, from_index=from_index)

    # -- boucle d'exécution ------------------------------------------------

    def _advance(self, package: DesignPackage, *, from_index: int) -> DesignPackage:
        handlers = {
            DesignStep.ANALYZE: self._step_analyze,
            DesignStep.RESEARCH: self._step_research,
            DesignStep.SYNTHESIZE: self._step_synthesize,
            DesignStep.DESIGN: self._step_design,
            DesignStep.VISUALIZE: self._step_visualize,
            DesignStep.VALIDATE: self._step_validate,
            DesignStep.PACKAGE: self._step_package,
        }
        for step in DESIGN_STEP_ORDER[from_index:]:
            target_state = step_to_state(step)
            assert_transition_allowed(package.state, target_state)
            package = replace(package, state=target_state)
            started = _utcnow()
            try:
                package, record = handlers[step](package, started)
            except Exception as exc:  # capturé pour un StepRecord.FAILED explicite, jamais masqué
                record = StepRecord(
                    step=step, state=DesignStepState.FAILED,
                    input_summary=package.request.text[:200], output_summary="",
                    provenance="pipeline", error=str(exc), started_at=started, completed_at=_utcnow(),
                )
                package = replace(package, steps=package.steps + (record,))
            else:
                package = replace(package, steps=package.steps + (record,))
            if record.state is DesignStepState.FAILED:
                assert_transition_allowed(package.state, DesignPipelineState.FAILED)
                return replace(
                    package, state=DesignPipelineState.FAILED,
                    error=record.error or record.output_summary,
                )
        assert_transition_allowed(package.state, DesignPipelineState.COMPLETED)
        return replace(package, state=DesignPipelineState.COMPLETED, completed_at=_utcnow())

    # -- étapes ------------------------------------------------------------

    def _step_analyze(self, package: DesignPackage, started: datetime) -> tuple[DesignPackage, StepRecord]:
        brief = analyze_request(package.request)
        package = replace(package, brief=brief)
        package, _ = self._persist_json(package, brief.to_dict(), kind="brief")
        record = StepRecord(
            step=DesignStep.ANALYZE, state=DesignStepState.COMPLETED,
            input_summary=package.request.text[:200], output_summary=brief.summary[:200],
            provenance=brief.provenance, started_at=started, completed_at=_utcnow(),
        )
        return package, record

    def _step_research(self, package: DesignPackage, started: datetime) -> tuple[DesignPackage, StepRecord]:
        assert package.brief is not None
        report = self._research.detect()
        if not report.is_available:
            record = StepRecord(
                step=DesignStep.RESEARCH, state=DesignStepState.SKIPPED,
                input_summary=package.brief.summary[:200],
                output_summary="0 résultat — aucun backend de recherche disponible",
                provenance=f"unavailable:{report.backend}", logs=(report.detail,),
                started_at=started, completed_at=_utcnow(),
            )
            return package, record
        results = self._research.search(package.brief.summary)
        package = replace(package, research=results)
        if results:
            package, _ = self._persist_json(
                package, {"results": [r.to_dict() for r in results]}, kind="research"
            )
        record = StepRecord(
            step=DesignStep.RESEARCH,
            state=DesignStepState.COMPLETED if results else DesignStepState.SKIPPED,
            input_summary=package.brief.summary[:200],
            output_summary=f"{len(results)} résultat(s)", provenance=report.backend,
            started_at=started, completed_at=_utcnow(),
        )
        return package, record

    def _step_synthesize(self, package: DesignPackage, started: datetime) -> tuple[DesignPackage, StepRecord]:
        assert package.brief is not None
        finalized = finalize_brief(package.brief, package.research)
        package = replace(package, brief=finalized)
        package, _ = self._persist_json(package, finalized.to_dict(), kind="brief_final")
        record = StepRecord(
            step=DesignStep.SYNTHESIZE, state=DesignStepState.COMPLETED,
            input_summary=f"brief + {len(package.research)} résultat(s) de recherche",
            output_summary=finalized.summary[:200], provenance=finalized.provenance,
            started_at=started, completed_at=_utcnow(),
        )
        return package, record

    def _step_design(self, package: DesignPackage, started: datetime) -> tuple[DesignPackage, StepRecord]:
        assert package.brief is not None
        concepts = synthesize_concepts(package.brief, package.research)
        package = replace(package, concepts=concepts)
        for concept in concepts:
            package, _ = self._persist_json(package, concept.to_dict(), kind="concept")
        record = StepRecord(
            step=DesignStep.DESIGN, state=DesignStepState.COMPLETED,
            input_summary=package.brief.summary[:200], output_summary=f"{len(concepts)} concept(s)",
            provenance="deterministic-template", started_at=started, completed_at=_utcnow(),
        )
        return package, record

    def _step_visualize(self, package: DesignPackage, started: datetime) -> tuple[DesignPackage, StepRecord]:
        prompts: list[VisualPrompt] = []
        for concept in package.concepts:
            prompts.extend(build_visual_prompts(concept))
        package = replace(package, visual_prompts=tuple(prompts))
        for prompt in prompts:
            package, _ = self._persist_json(package, prompt.to_dict(), kind="visual_prompt")

        report = self._visual.detect()
        results: list[VisualResult] = []
        for prompt in prompts:
            result, artifact_ref = self._generate_one(prompt, report)
            results.append(result)
            if artifact_ref is not None:
                package = replace(package, artifacts=package.artifacts + (artifact_ref,))
        package = replace(package, visual_results=tuple(results))
        n_completed = sum(1 for r in results if r.state == "completed")
        state = DesignStepState.COMPLETED if n_completed else DesignStepState.SKIPPED
        record = StepRecord(
            step=DesignStep.VISUALIZE, state=state,
            input_summary=f"{len(prompts)} prompt(s) visuel(s)",
            output_summary=f"{n_completed}/{len(results)} généré(s) réellement",
            provenance=report.backend,
            logs=() if report.is_available else (report.detail,),
            started_at=started, completed_at=_utcnow(),
        )
        return package, record

    def _generate_one(
        self, prompt: VisualPrompt, report: VisualCapabilityReport
    ) -> tuple[VisualResult, DesignArtifactRef | None]:
        if not report.is_available:
            return (
                VisualResult(
                    id=new_id("visual"), prompt_id=prompt.id, state="unavailable",
                    provider=report.backend, detail=report.detail,
                ),
                None,
            )
        try:
            job_id = self._visual.generate(prompt)
            job_status = self._visual.status(job_id)
            if job_status.failed:
                return (
                    VisualResult(
                        id=new_id("visual"), prompt_id=prompt.id, state="failed",
                        provider=report.backend, detail=job_status.detail,
                    ),
                    None,
                )
            if not job_status.ready:
                return (
                    VisualResult(
                        id=new_id("visual"), prompt_id=prompt.id, state="failed",
                        provider=report.backend,
                        detail="job non prêt de façon synchrone — non pris en charge en v1",
                    ),
                    None,
                )
            data = self._visual.retrieve(job_id)
            if not self._visual.validate(data):
                return (
                    VisualResult(
                        id=new_id("visual"), prompt_id=prompt.id, state="failed",
                        provider=report.backend,
                        detail="validation structurelle du contenu généré échouée",
                    ),
                    None,
                )
            metadata = self._artifacts.put(data, content_type="application/octet-stream")
            ref = DesignArtifactRef(
                content_hash=metadata.content_hash, kind="visual_result", created_at=metadata.created_at
            )
            return (
                VisualResult(
                    id=new_id("visual"), prompt_id=prompt.id, state="completed",
                    provider=report.backend, detail="généré et validé",
                    artifact_content_hash=metadata.content_hash,
                ),
                ref,
            )
        except VisualBackendUnavailable as exc:
            return (
                VisualResult(
                    id=new_id("visual"), prompt_id=prompt.id, state="unavailable",
                    provider=report.backend, detail=str(exc),
                ),
                None,
            )

    def _step_validate(self, package: DesignPackage, started: datetime) -> tuple[DesignPackage, StepRecord]:
        validation = validate_package(package)
        package = replace(package, validation=validation)
        package, _ = self._persist_json(package, validation.to_dict(), kind="validation")
        record = StepRecord(
            step=DesignStep.VALIDATE,
            state=DesignStepState.COMPLETED if validation.passed else DesignStepState.FAILED,
            input_summary=f"{len(validation.checks)} vérification(s) exécutée(s)",
            output_summary="validation réussie" if validation.passed
            else f"{len(validation.failures)} échec(s) de validation",
            provenance="apex.design.validation",
            error=None if validation.passed else "; ".join(validation.failures),
            started_at=started, completed_at=_utcnow(),
        )
        return package, record

    def _step_package(self, package: DesignPackage, started: datetime) -> tuple[DesignPackage, StepRecord]:
        payload = package.to_dict()
        package, ref = self._persist_json(package, payload, kind="package")
        record = StepRecord(
            step=DesignStep.PACKAGE, state=DesignStepState.COMPLETED,
            input_summary=f"{len(package.artifacts)} artefact(s) accumulé(s)",
            output_summary=f"empaqueté sous {ref.content_hash}", provenance="apex.artifacts",
            started_at=started, completed_at=_utcnow(),
        )
        return package, record

    # -- persistance ---------------------------------------------------------

    def _persist_json(self, package: DesignPackage, data: dict, *, kind: str) -> tuple[DesignPackage, DesignArtifactRef]:
        payload = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        metadata = self._artifacts.put(payload, content_type="application/json")
        ref = DesignArtifactRef(content_hash=metadata.content_hash, kind=kind, created_at=metadata.created_at)
        package = replace(package, artifacts=package.artifacts + (ref,))
        return package, ref
