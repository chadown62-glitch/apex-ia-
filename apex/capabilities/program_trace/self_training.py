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

SELF_DOMAIN = "self_data"
SELF_PREFIX = "self:data/"
ARMS = ("labeled", "unverified", "verified", "mixed")


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


def arm_texts(attempts: list[SelfAttempt], arm: str, count: int) -> list[str]:
    """Exactement `count` documents pour un bras du protocole P5 (même nombre de documents, même
    format, mêmes problèmes tentés ; seule la SOURCE de la trace change) :
    - `labeled`    : trace de référence des problèmes tentés (étiquettes) ;
    - `unverified` : trace du modèle, sans filtre (forme valide) ;
    - `verified`   : trace du modèle dont le résultat est vérifié par exécution ;
    - `mixed`      : moitié étiquettes, moitié tentatives vérifiées, sur des problèmes distincts."""
    labeled = [a.problem.document for a in attempts]
    verified = [a.document() for a in attempts if a.final_ok]
    unverified = [a.document() for a in attempts if a.well_formed]
    if arm == "labeled":
        texts = labeled
    elif arm == "verified":
        texts = verified
    elif arm == "unverified":
        texts = unverified
    elif arm == "mixed":
        half = count // 2
        taken = {a.problem for a in attempts[:half]}
        texts = labeled[:half] + [a.document() for a in attempts if a.final_ok and a.problem not in taken]
    else:
        raise ValueError(f"bras inconnu « {arm} » (bras : {ARMS})")
    if len(texts) < count:
        raise ValueError(f"bras « {arm} » : {len(texts)} documents disponibles pour {count} demandés")
    return texts[:count]


def documents(texts: list[str], origin: str) -> list[TrainingCandidate]:
    return [
        TrainingCandidate(
            candidate_id=f"{SELF_PREFIX}{index:05d}",
            text=text,
            source_id=f"{SELF_PREFIX}{index:05d}",
            content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
            license_status="free",
            quality="high_value",
            origin=origin,
        )
        for index, text in enumerate(texts)
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
