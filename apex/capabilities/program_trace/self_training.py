"""P5-noyau : apprendre de ses PROPRES tentatives vérifiées.

Le modèle tente des problèmes NEUFS (forme canonique absente de l'évaluation ET du réservoir
d'entraînement : il n'en a jamais vu la solution). Le vérificateur ne connaît que le RÉSULTAT :
la valeur finale calculée par Python dans le sandbox — comme un test unitaire qui ne voit pas les
étapes. Une tentative gardée devient un document d'entraînement avec la trace ÉCRITE PAR LE
MODÈLE (jamais la trace de référence).

Consigné à part, jamais utilisé pour choisir : combien de tentatives gardées ont pourtant une
étape fausse (réponse juste par chance ou par compensation) — le prix d'un vérificateur de
résultat seul."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

from apex.capabilities.program_trace.benchmark import ProgramTraceBenchmark, sandbox_values
from apex.capabilities.program_trace.capability import score_output, solve
from apex.capabilities.program_trace.generator import Problem, generate, parse_trace
from apex.neural.dataset import TrainingCandidate
from apex.neural.model import ApexLM
from apex.neural.tokenizer import ByteBPETokenizer
from apex.sandbox.provider import SandboxProvider

VERIFIED_DOMAIN = "self_verified"
REJECTED_DOMAIN = "self_rejected"
PREFIXES = {VERIFIED_DOMAIN: "self:verified/", REJECTED_DOMAIN: "self:rejected/"}


def fresh_problems(
    bench: ProgramTraceBenchmark, count: int, depth: int, rng: random.Random
) -> list[Problem]:
    """Problèmes dont la forme canonique n'est ni évaluée ni dans le réservoir d'entraînement."""
    seen = {p.canonical for p in bench.pool} | {p.canonical for p in bench.eval_problems()}
    out: list[Problem] = []
    for _ in range(count * 1000):
        if len(out) == count:
            return out
        problem = generate(depth, rng)
        if problem.canonical not in seen:
            seen.add(problem.canonical)
            out.append(problem)
    if len(out) == count:
        return out
    raise ValueError(f"profondeur {depth} : espace de problèmes neufs épuisé ({len(out)}/{count})")


@dataclass(frozen=True)
class SelfAttempt:
    problem: Problem
    output: str
    well_formed: bool
    final_ok: bool  # seul signal du vérificateur (exécution réelle)
    steps_ok: bool  # consigné, jamais utilisé pour choisir

    def document(self) -> str:
        return self.problem.prompt + self.output


def attempt_and_verify(
    model: ApexLM,
    tokenizer: ByteBPETokenizer,
    problems: list[Problem],
    provider: SandboxProvider,
) -> list[SelfAttempt]:
    outputs = solve(model, tokenizer, tuple(problems))
    executed = sandbox_values(provider, problems)
    attempts = []
    for problem, output, values in zip(problems, outputs, executed, strict=True):
        parsed = parse_trace(output)
        final_ok = parsed is not None and parsed[2] == values[-1]
        attempts.append(
            SelfAttempt(
                problem,
                output,
                parsed is not None,
                final_ok,
                score_output(problem, output)["exact"],
            )
        )
    return attempts


def documents(attempts: list[SelfAttempt], domain: str) -> list[TrainingCandidate]:
    """`self_verified` : tentatives dont le résultat est vérifié ; `self_rejected` : tentatives de
    forme valide que le vérificateur refuse. Les deux domaines sont disjoints : un bras « sans
    vérificateur » les mélange dans leur proportion naturelle (toutes les tentatives)."""
    if domain == VERIFIED_DOMAIN:
        keep = [a for a in attempts if a.final_ok]
    else:
        keep = [a for a in attempts if a.well_formed and not a.final_ok]
    prefix = PREFIXES[domain]
    return [
        TrainingCandidate(
            candidate_id=f"{prefix}{index:05d}",
            text=a.document(),
            source_id=f"{prefix}{index:05d}",
            content_hash="sha256:" + hashlib.sha256(a.document().encode()).hexdigest(),
            license_status="free",
            quality="high_value",
            origin=f"self:program_trace:{domain}",
        )
        for index, a in enumerate(keep)
    ]


def summary(attempts: list[SelfAttempt]) -> dict[str, Any]:
    verified = [a for a in attempts if a.final_ok]
    return {
        "attempts": len(attempts),
        "well_formed": sum(a.well_formed for a in attempts),
        "outcome_verified": len(verified),
        "outcome_verified_with_a_wrong_step": sum(not a.steps_ok for a in verified),
        "fully_correct": sum(a.steps_ok for a in attempts),
    }
