"""DEPENDENCY ORDER derrière le contrat générique de capacité.

Même format de sortie que `program_trace` (trace `a3,b7,c4=4`), mais les instructions sont données
dans le désordre : une trace n'est juste que si les NOMS sont écrits dans l'ordre d'exécution ET
que chaque valeur et la réponse sont exactes. Ici l'ordre est l'objet du raisonnement : les noms
comptent dans la métrique (contrairement à `program_trace`)."""

from __future__ import annotations

import hashlib
from typing import Any

from apex.capabilities.contract import Capability, CapabilityContract, CapabilityEvaluation
from apex.capabilities.dependency_order.benchmark import (
    DependencyOrderBenchmark,
    OrderProblem,
    OrderSuite,
    suite_name,
)
from apex.capabilities.program_trace.generator import parse_trace
from apex.neural.dataset import TrainingCandidate
from apex.neural.evaluation import DocScore, EvalReport, model_fingerprint
from apex.neural.model import ApexLM, generate_batch
from apex.neural.tokenizer import ByteBPETokenizer
from apex.sandbox.provider import SandboxProvider

METRIC = "order_error"
MAX_NEW_TOKENS = 48
TASKS_DOMAIN = "order_tasks"
TASKS_PREFIX = "task:dependency_order/"

CONTRACT = CapabilityContract(
    name="dependency_order",
    version="1.0",
    description=(
        "Exécuter un programme dont les instructions sont données DANS LE DÉSORDRE : retrouver l'ordre "
        "imposé par les dépendances, puis écrire chaque variable dans cet ordre avec sa valeur ; ordre "
        "et valeurs vérifiés contre l'exécution réelle du programme ordonné dans le sandbox."
    ),
    input="instructions mélangées `c=b-a;a=3;b=a+4` suivies de la question `?c:` (jamais l'ordre)",
    output="trace générée dans l'ordre d'exécution `a3,b7,c4=4`",
    environment="génération par le modèle APEX ; vérité terrain calculée dans le sandbox NAMESPACED",
    allowed_tools=("décodage glouton du modèle APEX, budget fixe de jetons",),
    safety_constraints=(
        "vérité terrain obtenue par exécution sandbox (réseau coupé), jamais par le modèle",
        "le texte généré n'est jamais exécuté : il est analysé puis comparé",
        "budget de génération fixe, indépendant de la réponse attendue",
    ),
    success_criteria=(
        "noms dans l'ordre d'exécution, toutes les valeurs et la réponse exactes (coût 0)",
        "acceptation d'un modèle : baisse significative de l'erreur sur la suite held-out (programmes "
        "jamais vus, ni ici ni par program_trace, même ordonnés), sans régression des gardes",
    ),
    failure_criteria=(
        "un nom hors de l'ordre d'exécution, une valeur fausse, manquante ou en trop ; texte après la "
        "réponse ; forme illisible (coût 1)",
    ),
    reproducibility=(
        "benchmark gelé dans le monde (empreintes), relu et revérifié par un résolveur hôte indépendant",
        "génération gloutonne déterministe",
    ),
    provenance="générateur procédural APEX (programmes de program_trace mélangés), graine fixée",
    metric=METRIC,
    guard_tolerance=0.05,
    target_value=0.25,
    training_domains=(TASKS_DOMAIN,),
    acceptance_suite=suite_name("heldout"),
    suites=tuple(suite_name(s) for s in ("train", "validation", "heldout")),
    memorization_suite=suite_name("train"),
    limits=(
        "programmes en chaîne (une seule racine, une instruction par variable), arithmétique +/- sur "
        "des chiffres ; profondeur évaluée 3",
        "tâche APPARENTÉE à program_trace (même arithmétique, même format) : sert à mesurer le "
        "transfert, pas un raisonnement général",
    ),
)


def score_output(problem: OrderProblem, text: str) -> dict[str, Any]:
    parsed = parse_trace(text)
    want = list(problem.base.values)
    if parsed is None:
        return {"exact": False, "well_formed": False, "steps_ok": 0, "final_ok": False, "order_ok": False}
    names, values, final = parsed
    order_ok = names == list(problem.base.names)
    written = list(zip(names, values, strict=True))
    steps_ok = sum(
        1
        for got, exp in zip(written, zip(problem.base.names, want, strict=True), strict=False)
        if got == exp
    )
    return {
        "exact": order_ok and values == want and final == want[-1],
        "well_formed": True,
        "steps_ok": steps_ok,
        "final_ok": final == want[-1],
        "order_ok": order_ok,
        # Lecture de gauche à droite : noms écrits dans l'ordre de PRÉSENTATION (échec type).
        "textual_order": names == [s.partition("=")[0] for s in problem.statements],
    }


def solve(model: ApexLM, tokenizer: ByteBPETokenizer, problems: tuple[OrderProblem, ...]) -> list[str]:
    prompts = [tokenizer.encode(p.prompt, add_bos=True) for p in problems]
    outputs = generate_batch(model, prompts, MAX_NEW_TOKENS, eos_id=tokenizer.eos_id)
    return [tokenizer.decode(ids) for ids in outputs]


def suite_report(suite: OrderSuite, results: list[dict[str, Any]], model: ApexLM) -> EvalReport:
    scores = tuple(
        DocScore(
            r["instance"],
            0.0 if r["solved"] else 1.0,
            r["steps_ok"] / (p.depth + 1),
            len(p.document.encode()),
            p.depth + 1,
        )
        for p, r in zip(suite.problems, results, strict=True)
    )
    return EvalReport(suite.name, suite.fingerprint, model_fingerprint(model), scores, metric=METRIC)


class DependencyOrderCapability(Capability):
    contract = CONTRACT

    def load(self, payload: dict[str, Any]) -> DependencyOrderBenchmark:
        return DependencyOrderBenchmark.from_dict(payload)

    def dump(self, data: DependencyOrderBenchmark) -> dict[str, Any]:
        return data.to_dict()

    def fingerprint(self, data: DependencyOrderBenchmark) -> str:
        return data.fingerprint

    def suite_fingerprints(self, data: DependencyOrderBenchmark) -> dict[str, str]:
        return {name: suite.fingerprint for name, suite in data.suites.items()}

    def protected_texts(self, data: DependencyOrderBenchmark) -> list[str]:
        """Documents évalués, instructions mélangées ET programme ordonné : ni la solution, ni
        l'entrée, ni sa forme ordonnée (qui porte la même trace) n'entrent à l'entraînement."""
        return [t for p in data.eval_problems() for t in (p.document, p.shown, p.base.program)]

    def train_documents(self, data: DependencyOrderBenchmark) -> list[TrainingCandidate]:
        return [
            TrainingCandidate(
                candidate_id=f"{TASKS_PREFIX}{index:05d}",
                text=p.document,
                source_id=f"{TASKS_PREFIX}{index:05d}",
                content_hash="sha256:" + hashlib.sha256(p.document.encode()).hexdigest(),
                license_status="free",
                quality="high_value",
                origin="capability:dependency_order:pool",
            )
            for index, p in enumerate(data.pool)
        ]

    def longest_input(self, data: DependencyOrderBenchmark, tokenizer: ByteBPETokenizer) -> int:
        longest = 0
        for suite in data.suites.values():
            for p in suite.problems:
                prompt = tokenizer.encode(p.prompt, add_bos=True)
                if tokenizer.encode(p.document, add_bos=True)[: len(prompt)] != prompt:
                    raise ValueError(f"tokenizer : l'invite {p.prompt!r} n'est pas un préfixe du document")
                longest = max(longest, len(prompt))
        return MAX_NEW_TOKENS + longest

    def evaluate(
        self,
        model: ApexLM,
        tokenizer: ByteBPETokenizer,
        data: DependencyOrderBenchmark,
        *,
        provider: SandboxProvider,
    ) -> CapabilityEvaluation:
        reports = {}
        details = {}
        for name, suite in sorted(data.suites.items()):
            texts = solve(model, tokenizer, suite.problems)
            results = []
            for pid, p, text in zip(suite.ids(), suite.problems, texts, strict=True):
                score = score_output(p, text)
                solved = score.pop("exact")
                results.append({"instance": pid, "solved": solved, "output": text[:80], **score})
            reports[name] = suite_report(suite, results, model)
            details[name] = results
        return CapabilityEvaluation(reports, details)


__all__ = ["CONTRACT", "DependencyOrderCapability", "score_output"]
