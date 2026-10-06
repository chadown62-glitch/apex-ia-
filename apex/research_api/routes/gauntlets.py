"""`GET /gauntlets/{id}`, `GET /population/{specialty}/gauntlets` — passe
« Strict Judge System + Gauntlet Learning Loop » (N+8).

Ce routeur ne réimplémente aucun moteur métier : il ne fait que projeter
`apex.metalearner.gauntlet_repository.GauntletRepository` (lecture seule,
INSERT seul côté domaine — voir sa docstring) — même patron que
`apex.api.routes.population` vis-à-vis de `PopulationService`.

**Objectif : observabilité (§19/§25 de la mission)** — répondre à
« pourquoi cette candidate a-t-elle été acceptée/rejetée, quel juge a
trouvé le problème, quelle preuve, quelle différence avec le champion ».
Surface volontairement MINIMALE : deux routes, toutes deux en LECTURE
seule (le Judge System s'exécute exclusivement à l'intérieur
d'`apex.metalearner.evolution.PopulationEvolutionService`, jamais
déclenché depuis l'API — aucune route mutative ici).

Import différé d'`apex.metalearner` dans le corps des fonctions, jamais au
niveau module — même discipline qu'`apex.api.routes.population`/
`learning_sessions` (ce module ne fait pas partie de l'environnement/
artefact packagé, `tests/test_runtime_packaging.py`, hors périmètre de
cette passe)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from apex.api.schemas import DataEnvelope, GauntletResultOut, JudgeEvidenceOut, JudgeFindingOut, JudgeResultOut

if TYPE_CHECKING:
    from apex.metalearner.judge_model import GauntletResult, JudgeResult

router = APIRouter(tags=["gauntlets"])


def _to_judge_result_out(judge_result: "JudgeResult") -> JudgeResultOut:
    return JudgeResultOut(
        judge_id=judge_result.judge_id, judge_version=judge_result.judge_version,
        dimension=judge_result.dimension.value, decision=judge_result.decision.value,
        score=judge_result.score, confidence=judge_result.confidence, rationale=judge_result.rationale,
        veto=judge_result.veto,
        findings=[
            JudgeFindingOut(
                dimension=f.dimension.value, severity=f.severity.value, summary=f.summary, detail=f.detail,
            )
            for f in judge_result.findings
        ],
        evidence=[
            JudgeEvidenceOut(kind=e.kind, reference=e.reference, detail=e.detail) for e in judge_result.evidence
        ],
    )


def _to_gauntlet_out(result: "GauntletResult") -> GauntletResultOut:
    return GauntletResultOut(
        id=result.id, specialty=result.specialty, generation=result.generation, member_id=result.member_id,
        stage=result.stage.value, protocol_version=result.protocol_version, decision=result.decision.value,
        overall_score=result.overall_score, overall_confidence=result.overall_confidence, veto=result.veto,
        veto_dimension=result.veto_dimension.value if result.veto_dimension else None,
        disagreement=result.disagreement, stopped_after_critical_failure=result.stopped_after_critical_failure,
        judge_results=[_to_judge_result_out(jr) for jr in result.judge_results],
        compared_champion_id=result.compared_champion_id, created_at=result.created_at.isoformat(),
    )


@router.get("/gauntlets/{gauntlet_id}", response_model=DataEnvelope[GauntletResultOut])
def get_gauntlet(gauntlet_id: str) -> DataEnvelope[GauntletResultOut]:
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.gauntlet_repository import GauntletRepository

    try:
        result = GauntletRepository(get_session_factory()).get(gauntlet_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if result is None:
        raise HTTPException(status_code=404, detail=f"gauntlet « {gauntlet_id} » introuvable")
    return DataEnvelope(status="real", data=_to_gauntlet_out(result))


@router.get("/population/{specialty}/gauntlets", response_model=DataEnvelope[list[GauntletResultOut]])
def list_gauntlets_for_specialty(
    specialty: str, member_id: str | None = None, limit: int = 50,
) -> DataEnvelope[list[GauntletResultOut]]:
    """`member_id` filtre sur UN membre précis (l'historique CHEAP puis,
    éventuellement, FULL d'un même candidat) — sans lui, les gauntlets les
    plus récents de la spécialité entière, tous membres confondus."""
    from apex.specialties.registry import by_id

    if by_id(specialty) is None:
        raise HTTPException(status_code=422, detail=f"spécialité « {specialty} » inconnue")

    from apex.api.dependencies import get_session_factory
    from apex.metalearner.gauntlet_repository import GauntletRepository

    try:
        repo = GauntletRepository(get_session_factory())
        results = (
            repo.list_by_member(member_id, limit=limit) if member_id is not None
            else repo.list_by_specialty(specialty, limit=limit)
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_gauntlet_out(r) for r in results])
