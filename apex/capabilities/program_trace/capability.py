"""PROGRAM TRACE derrière le contrat générique de capacité.

Le modèle GÉNÈRE la trace (décodage glouton, aucune sélection parmi des candidats) ; la sortie
entière doit avoir la forme d'une trace, puis chaque valeur est comparée à la vérité terrain
exécutée dans le sandbox à la construction du benchmark. Une valeur fausse, manquante ou en trop,
ou du texte après la réponse : erreur (coût 1). Les NOMS écrits ne comptent pas dans la métrique
(on juge le calcul) ; leur exactitude est consignée à part (fidélité de recopie)."""

from __future__ import annotations

import hashlib
from typing import Any

from apex.capabilities.contract import Capability, CapabilityContract, CapabilityEvaluation
from apex.capabilities.program_trace.benchmark import ProgramTraceBenchmark, TraceSuite, suite_name
from apex.capabilities.program_trace.generator import Problem, parse_trace
from apex.neural.dataset import TrainingCandidate
from apex.neural.evaluation import DocScore, EvalReport, model_fingerprint
from apex.neural.model import ApexLM, generate_batch
from apex.neural.tokenizer import ByteBPETokenizer
from apex.sandbox.provider import SandboxProvider

METRIC = "trace_error"
#: Budget de génération FIXE (jamais dérivé de la réponse attendue : une troncature à la bonne
#: longueur rendrait juste une sortie qui ne s'arrête pas).
MAX_NEW_TOKENS = 48
#: Domaine du monde qui porte le réservoir d'entraînement (split train seulement).
TASKS_DOMAIN = "reasoning_tasks"
TASKS_PREFIX = "task:program_trace/"

CONTRACT = CapabilityContract(
    name="program_trace",
    version="1.0",
    description=(
        "Exécuter mentalement un programme en ligne droite d'affectations dépendantes : le modèle "
        "GÉNÈRE la valeur de chaque variable dans l'ordre puis la réponse ; chaque valeur est "
        "vérifiée contre l'exécution réelle du programme par Python dans le sandbox."
    ),
    input="programme `a=3;b=a+4;c=b-a` suivi de la question `?c:` (jamais la trace)",
    output="trace générée `a3,b7,c4=4` (valeur de chaque variable, puis réponse finale)",
    environment="génération par le modèle APEX ; vérité terrain calculée dans le sandbox NAMESPACED",
    allowed_tools=("décodage glouton du modèle APEX, budget fixe de jetons",),
    safety_constraints=(
        "vérité terrain obtenue par exécution sandbox (réseau coupé), jamais par le modèle",
        "le texte généré n'est jamais exécuté : il est analysé puis comparé",
        "budget de génération fixe, indépendant de la réponse attendue",
    ),
    success_criteria=(
        "sortie entière de la forme d'une trace, TOUTES les valeurs et la réponse exactes (coût 0)",
        "acceptation d'un modèle : baisse significative de l'erreur sur la suite held-out (programmes "
        "de forme canonique jamais vue, bootstrap apparié par problème) sans régression des gardes",
    ),
    failure_criteria=(
        "une valeur fausse, manquante ou en trop ; texte après la réponse ; forme illisible (coût 1)",
    ),
    reproducibility=(
        "benchmark gelé dans le monde (empreinte par suite, réservoir et globale), relu et revérifié",
        "génération gloutonne déterministe, regroupée par longueur (aucun remplissage)",
    ),
    provenance="générateur procédural APEX (program_trace/generator.py), graine fixée",
    metric=METRIC,
    guard_tolerance=0.05,
    target_value=0.25,
    training_domains=(TASKS_DOMAIN,),
    acceptance_suite=suite_name("heldout"),
    suites=tuple(suite_name(s) for s in ("train", "validation", "heldout", "deeper")),
    memorization_suite=suite_name("train"),
    transfer_suites=(suite_name("deeper"),),
    limits=(
        "arithmétique entière +/- sur des chiffres ; programmes en ligne droite sans boucle ni branche",
        "profondeur entraînée 1 à 3 ; la suite `deeper` (profondeur 4) mesure la généralisation en "
        "longueur, attendue en échec tant qu'aucun mécanisme ne l'apporte",
        "la fidélité causale de la trace (l'étape est-elle utilisée ?) n'est PAS dans la métrique : "
        "elle se mesure par intervention (expérience séparée)",
    ),
)


def score_output(problem: Problem, text: str) -> dict[str, Any]:
    """Vérifie UNE sortie générée contre la vérité terrain du problème."""
    parsed = parse_trace(text)
    want = list(problem.values)
    if parsed is None:
        return {"exact": False, "well_formed": False, "steps_ok": 0, "final_ok": False, "names_ok": False}
    names, values, final = parsed
    steps_ok = sum(1 for got, exp in zip(values, want, strict=False) if got == exp)
    exact = values == want and final == want[-1]
    return {
        "exact": exact,
        "well_formed": True,
        "steps_ok": steps_ok,
        "final_ok": final == want[-1],
        "names_ok": names == list(problem.names),
    }


def solve(model: ApexLM, tokenizer: ByteBPETokenizer, problems: tuple[Problem, ...]) -> list[str]:
    prompts = [tokenizer.encode(p.prompt, add_bos=True) for p in problems]
    outputs = generate_batch(model, prompts, MAX_NEW_TOKENS, eos_id=tokenizer.eos_id)
    return [tokenizer.decode(ids) for ids in outputs]


def _row(instance: str, problem: Problem, text: str) -> dict[str, Any]:
    """Détail d'UNE tâche, au format du contrat (`instance`, `solved`) lu par le Judge."""
    score = score_output(problem, text)
    return {
        "instance": instance,
        "solved": score.pop("exact"),
        "depth": problem.depth,
        "output": text[:80],
        **score,
    }


def suite_report(suite: TraceSuite, results: list[dict[str, Any]], model: ApexLM) -> EvalReport:
    scores = tuple(
        DocScore(
            r["instance"],
            0.0 if r["solved"] else 1.0,  # valeur de la métrique (erreur, plus bas = mieux)
            r["steps_ok"] / (p.depth + 1),  # fraction des valeurs intermédiaires exactes
            len(p.document.encode()),
            p.depth + 1,
        )
        for p, r in zip(suite.problems, results, strict=True)
    )
    return EvalReport(suite.name, suite.fingerprint, model_fingerprint(model), scores, metric=METRIC)


class ProgramTraceCapability(Capability):
    contract = CONTRACT

    def load(self, payload: dict[str, Any]) -> ProgramTraceBenchmark:
        return ProgramTraceBenchmark.from_dict(payload)

    def dump(self, data: ProgramTraceBenchmark) -> dict[str, Any]:
        return data.to_dict()

    def fingerprint(self, data: ProgramTraceBenchmark) -> str:
        return data.fingerprint

    def suite_fingerprints(self, data: ProgramTraceBenchmark) -> dict[str, str]:
        return {name: suite.fingerprint for name, suite in data.suites.items()}

    def protected_texts(self, data: ProgramTraceBenchmark) -> list[str]:
        """Documents complets (programme, question, trace) des suites d'évaluation."""
        return [p.document for p in data.eval_problems()]

    def train_documents(self, data: ProgramTraceBenchmark) -> list[TrainingCandidate]:
        return [
            TrainingCandidate(
                candidate_id=f"{TASKS_PREFIX}{index:05d}",
                text=p.document,
                source_id=f"{TASKS_PREFIX}{index:05d}",
                content_hash="sha256:" + hashlib.sha256(p.document.encode()).hexdigest(),
                license_status="free",
                quality="high_value",
                origin="capability:program_trace:pool",
            )
            for index, p in enumerate(data.pool)
        ]

    def longest_input(self, data: ProgramTraceBenchmark, tokenizer: ByteBPETokenizer) -> int:
        return MAX_NEW_TOKENS + max(
            len(tokenizer.encode(p.prompt, add_bos=True))
            for suite in data.suites.values()
            for p in suite.problems
        )

    def evaluate(
        self,
        model: ApexLM,
        tokenizer: ByteBPETokenizer,
        data: ProgramTraceBenchmark,
        *,
        provider: SandboxProvider,
    ) -> CapabilityEvaluation:
        """`provider` inutilisé à l'évaluation : la vérité terrain a été exécutée dans le sandbox à
        la construction et est revérifiée à chaque relecture du benchmark."""
        reports = {}
        details = {}
        for name, suite in sorted(data.suites.items()):
            texts = solve(model, tokenizer, suite.problems)
            results = [
                _row(pid, p, text) for pid, p, text in zip(suite.ids(), suite.problems, texts, strict=True)
            ]
            reports[name] = suite_report(suite, results, model)
            details[name] = results
        return CapabilityEvaluation(reports, details)


__all__ = ["CONTRACT", "ProgramTraceCapability", "score_output"]
