"""Expériences d'APEX -> candidats d'entraînement, sous conditions strictes.

- Un SUCCÈS n'entre que s'il est CONFIRMÉ (`SuccessEvidence.CONFIRMED` : même
  verdict sur ré-exécution du même code). OBSERVED ou REFUTED : refusé — une
  réussite isolée n'est jamais une vérité d'entraînement.
- Le texte fourni doit correspondre à l'empreinte enregistrée par
  l'expérience : on n'entraîne pas sur un code autre que celui qui a passé.
- Une ERREUR n'entre que si sa cause est établie par la taxonomie (solution
  incorrecte, échec d'exécution, délai, candidat invalide, refus de
  sécurité) ET décrite ; les pannes d'infrastructure, de ressources, de
  génération ou de l'oracle ne disent rien de la solution : refusées.
- La source est la TÂCHE : toutes les expériences d'une même tâche tombent
  dans le même split (pas de fuite d'une tentative à l'autre).
- Un code produit par un modèle externe est marqué EXTERNAL_MODEL_DEPENDENCY
  dans l'origine : ce n'est pas une connaissance native d'APEX."""

from __future__ import annotations

import hashlib

from apex.knowledge.model import sha256_text
from apex.knowledge.multimodal import EXTERNAL_MODEL_DEPENDENCY
from apex.metalearner.experience import Experience, SuccessEvidence
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.neural.dataset import TrainingCandidate

ESTABLISHED_FAILURE_CAUSES = frozenset(
    {
        FailureCategory.INCORRECT_SOLUTION,
        FailureCategory.EXECUTION_FAILURE,
        FailureCategory.TIMEOUT,
        FailureCategory.INVALID_CANDIDATE,
        FailureCategory.POLICY_SECURITY_REJECTION,
    }
)


class ExperienceRejectedError(ValueError):
    """L'expérience ne peut pas devenir une donnée d'entraînement."""


def _origin(kind: str, experience: Experience) -> str:
    model = experience.model_id
    return f"experience:{kind}" + (f"|{EXTERNAL_MODEL_DEPENDENCY}:{model}" if model else "")


def confirmed_success_candidate(
    experience: Experience, *, task_prompt: str, solution_text: str, license_status: str
) -> TrainingCandidate:
    if experience.failure_category is not FailureCategory.SUCCESS:
        raise ExperienceRejectedError(
            f"{experience.id} : pas un succès ({experience.failure_category.value})"
        )
    if experience.success_evidence is not SuccessEvidence.CONFIRMED:
        evidence = experience.success_evidence.value if experience.success_evidence else "absente"
        raise ExperienceRejectedError(f"{experience.id} : succès non confirmé (preuve {evidence})")
    if not experience.solution_fingerprint:
        raise ExperienceRejectedError(f"{experience.id} : aucune empreinte de solution enregistrée")
    if hashlib.sha256(solution_text.encode("utf-8")).hexdigest() != experience.solution_fingerprint:
        raise ExperienceRejectedError(f"{experience.id} : le texte fourni n'est pas la solution évaluée")
    text = f"# Task\n\n{task_prompt.strip()}\n\n# Verified solution\n\n{solution_text.strip()}\n"
    return TrainingCandidate(
        candidate_id=f"experience:{experience.id}",
        text=text,
        source_id=f"task:{experience.task_id}",
        content_hash=sha256_text(text),
        license_status=license_status,
        quality="high_value",
        origin=_origin("confirmed_success", experience),
        evidence=(f"eval_run:{experience.eval_run_id}", f"solution:{experience.solution_fingerprint}"),
    )


def failure_lesson_candidate(
    experience: Experience, *, task_prompt: str, cause: str, license_status: str
) -> TrainingCandidate:
    category = experience.failure_category
    if category is FailureCategory.SUCCESS:
        raise ExperienceRejectedError(f"{experience.id} : un succès n'est pas une leçon d'erreur")
    if category not in ESTABLISHED_FAILURE_CAUSES:
        raise ExperienceRejectedError(
            f"{experience.id} : cause « {category.value} » extérieure à la solution — non établie"
        )
    if not cause.strip():
        raise ExperienceRejectedError(f"{experience.id} : cause de l'erreur non décrite")
    text = (
        f"# Task\n\n{task_prompt.strip()}\n\n# Failed attempt\n\n"
        f"Category: {category.value}\nCause: {cause.strip()}\n"
    )
    return TrainingCandidate(
        candidate_id=f"lesson:{experience.id}",
        text=text,
        source_id=f"task:{experience.task_id}",
        content_hash=sha256_text(text),
        license_status=license_status,
        quality="medium_value",
        origin=_origin(f"failure:{category.value}", experience),
        evidence=(f"eval_run:{experience.eval_run_id}",),
    )
