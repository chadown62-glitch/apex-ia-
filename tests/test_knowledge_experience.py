"""Expériences -> candidats : seules les réussites CONFIRMÉES et les erreurs
à cause établie entrent ; le texte doit être celui qui a été évalué."""

from __future__ import annotations

import hashlib

import pytest

from apex.knowledge.experience import (
    ESTABLISHED_FAILURE_CAUSES,
    ExperienceRejectedError,
    confirmed_success_candidate,
    failure_lesson_candidate,
)
from apex.metalearner.experience import Experience, SuccessEvidence
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.neural.dataset import Split, build_dataset
from apex.oracles.model import OracleVerdict

SOLUTION = "def add(a, b):\n    return a + b\n"
PROMPT = "Write add(a, b) returning the sum."


def experience(
    category: FailureCategory = FailureCategory.SUCCESS,
    evidence: SuccessEvidence | None = None,
    *,
    task: str = "t1",
    ident: str = "e1",
    solution: str = SOLUTION,
    model: str | None = "qwen-coder",
) -> Experience:
    return Experience(
        id=ident,
        task_id=task,
        eval_run_id=f"run-{ident}",
        candidate_id="c",
        strategy_id="s",
        generation_number=0,
        generation_config={},
        oracle_verdict=OracleVerdict.PASS if category is FailureCategory.SUCCESS else OracleVerdict.FAIL,
        fitness=1.0 if category is FailureCategory.SUCCESS else 0.0,
        failure_category=category,
        created_by="test",
        model_id=model,
        solution_fingerprint=hashlib.sha256(solution.encode()).hexdigest(),
        success_evidence=evidence,
    )


def test_confirmed_success_becomes_a_valid_candidate() -> None:
    cand = confirmed_success_candidate(
        experience(evidence=SuccessEvidence.CONFIRMED),
        task_prompt=PROMPT,
        solution_text=SOLUTION,
        license_status="compatible",
    )
    assert cand.problems() == ()
    assert cand.source_id == "task:t1" and SOLUTION.strip() in cand.text
    assert cand.origin == "experience:confirmed_success|EXTERNAL_MODEL_DEPENDENCY:qwen-coder"
    assert "eval_run:run-e1" in cand.evidence


@pytest.mark.parametrize("evidence", [SuccessEvidence.OBSERVED, SuccessEvidence.REFUTED])
def test_unconfirmed_success_never_becomes_training_truth(evidence: SuccessEvidence) -> None:
    with pytest.raises(ExperienceRejectedError, match="non confirmé"):
        confirmed_success_candidate(
            experience(evidence=evidence),
            task_prompt=PROMPT,
            solution_text=SOLUTION,
            license_status="compatible",
        )


def test_substituted_solution_text_is_refused() -> None:
    with pytest.raises(ExperienceRejectedError, match="pas la solution évaluée"):
        confirmed_success_candidate(
            experience(evidence=SuccessEvidence.CONFIRMED),
            task_prompt=PROMPT,
            solution_text=SOLUTION + "# edited\n",
            license_status="compatible",
        )


def test_failure_lessons_require_an_established_described_cause() -> None:
    lesson = failure_lesson_candidate(
        experience(FailureCategory.INCORRECT_SOLUTION),
        task_prompt=PROMPT,
        cause="returns a - b instead of a + b",
        license_status="compatible",
    )
    assert lesson.problems() == () and lesson.quality == "medium_value"
    assert "incorrect_solution" in lesson.origin
    for category in set(FailureCategory) - ESTABLISHED_FAILURE_CAUSES - {FailureCategory.SUCCESS}:
        with pytest.raises(ExperienceRejectedError, match="non établie"):
            failure_lesson_candidate(
                experience(category), task_prompt=PROMPT, cause="x", license_status="compatible"
            )
    with pytest.raises(ExperienceRejectedError, match="non décrite"):
        failure_lesson_candidate(
            experience(FailureCategory.TIMEOUT), task_prompt=PROMPT, cause="  ", license_status="compatible"
        )
    with pytest.raises(ExperienceRejectedError, match="succès"):
        failure_lesson_candidate(
            experience(evidence=SuccessEvidence.CONFIRMED),
            task_prompt=PROMPT,
            cause="x",
            license_status="compatible",
        )


def test_unknown_license_blocks_the_dataset() -> None:
    cand = confirmed_success_candidate(
        experience(evidence=SuccessEvidence.CONFIRMED),
        task_prompt=PROMPT,
        solution_text=SOLUTION,
        license_status="unknown",
    )
    assert cand.problems()


def test_all_attempts_on_a_task_stay_in_one_split() -> None:
    candidates = []
    for t in range(12):
        prompt = f"Task {t}: write function f{t} computing value {t} times {t + 1} plus offset {t * 7}."
        for k in range(3):
            sol = f"def f{t}(x):\n    return x * {t} + {k} * {t + 1}  # attempt {k}\n"
            exp = experience(
                FailureCategory.INCORRECT_SOLUTION, task=f"task{t}", ident=f"e{t}-{k}", solution=sol
            )
            candidates.append(
                failure_lesson_candidate(
                    exp, task_prompt=prompt, cause=f"off by {k} in task {t}", license_status="compatible"
                )
            )
    dataset = build_dataset(candidates, validation_ratio=0.2, test_ratio=0.2, max_overlap=1.0)
    for split in Split:
        for c in dataset.splits[split]:
            siblings = [x for x in candidates if x.source_id == c.source_id]
            assert all(
                any(s.candidate_id == x.candidate_id for x in dataset.splits[split]) for s in siblings
            )
