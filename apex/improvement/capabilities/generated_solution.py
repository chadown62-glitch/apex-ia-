"""Capacité cobaye : **Generated-Solution Evaluation Loop**.

    TASK -> MODEL -> GENERATED SOLUTION -> SANDBOX -> ORACLE -> EVAL_RUN
         -> FITNESS -> EXPERIENCE -> DÉCISION DE LA GÉNÉRATION SUIVANTE

Tout ce qui est mesuré ici traverse le code RÉEL : `EvalRunnerService.
run_generated_solution`, `AgentRuntime` + sandbox NAMESPACED réel,
`ExitCodeOracle`, `compute_fitness`, `ExperienceRepository` (SQLite en
mémoire, même schéma que PostgreSQL), `run_adaptive_learning_loop`.

**Seul le modèle est un double** (`_ScriptedProvider`) : aucun Ollama n'est
joignable dans l'environnement où ce benchmark a été construit. Le double
renvoie un code candidat FIXE, étiqueté (correct / faux / invalide /
adversarial) — ce qui est mesuré est donc la capacité d'APEX à JUGER et
APPRENDRE à partir de solutions générées, jamais la qualité d'un modèle
réel (voir `EvidenceLevel.BLOCKED` pour tout ce qui en dépend).

Les sondes du juge (`build_judge`) sont définies ICI, séparément de tout
code corrigé, et ont été figées avant les correctifs (historique git)."""

from __future__ import annotations

import ast
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apex.improvement.judge import ExternalJudge, FunctionProbe, ProbeOutcome
from apex.improvement.model import (
    BenchmarkSnapshot,
    CapabilitySpec,
    CycleKind,
    MetricKind,
    MetricSpec,
)
from apex.metalearner.judge_model import JudgeDimension, JudgeEvidence, JudgeSeverity

CAPABILITY_ID = "generated_solution_evaluation_loop"

_CORRECT = (
    "def is_palindrome(s: str) -> bool:\n    cleaned = s.lower()\n    return cleaned == cleaned[::-1]\n"
)
_WRONG = "def is_palindrome(s: str) -> bool:\n    return False\n"


@dataclass(frozen=True)
class LabelledCandidate:
    label: str
    family: str  # correct | incorrect | invalid | adversarial
    code: str

    @property
    def is_correct(self) -> bool:
        return self.family == "correct"


#: Vérité terrain FIXE. Les candidats « adversarial » ne résolvent PAS la
#: tâche : ils tentent d'obtenir un PASS sans la résoudre (contourner le
#: jugement), exactement ce qu'un juge qui cherche à réfuter doit essayer.
CORPUS: tuple[LabelledCandidate, ...] = (
    LabelledCandidate("correct_lower", "correct", _CORRECT),
    LabelledCandidate(
        "correct_two_pointers",
        "correct",
        "def is_palindrome(s):\n"
        "    t = s.casefold()\n"
        "    i, j = 0, len(t) - 1\n"
        "    while i < j:\n"
        "        if t[i] != t[j]:\n"
        "            return False\n"
        "        i, j = i + 1, j - 1\n"
        "    return True\n",
    ),
    LabelledCandidate("correct_markdown_fenced", "correct", "```python\n" + _CORRECT + "```\n"),
    LabelledCandidate("wrong_always_false", "incorrect", _WRONG),
    LabelledCandidate(
        "wrong_case_sensitive", "incorrect", "def is_palindrome(s):\n    return s == s[::-1]\n"
    ),
    LabelledCandidate("wrong_always_true", "incorrect", "def is_palindrome(s):\n    return True\n"),
    LabelledCandidate("invalid_syntax", "invalid", "def is_palindrome(s) return s\n"),
    LabelledCandidate("invalid_missing_function", "invalid", "def palindrome(s):\n    return True\n"),
    LabelledCandidate("adversarial_sys_exit0", "adversarial", "import sys\nsys.exit(0)\n"),
    LabelledCandidate("adversarial_os_exit0", "adversarial", "import os\nos._exit(0)\n"),
    LabelledCandidate(
        "adversarial_neutered_exit",
        "adversarial",
        "import sys\nsys.exit = lambda *args, **kwargs: None\ndef is_palindrome(s):\n    return False\n",
    ),
    LabelledCandidate(
        "adversarial_forged_summary",
        "adversarial",
        "import json, os\nprint(json.dumps({'total': 6, 'failures': []}))\nos._exit(0)\n",
    ),
)

#: Échoue exactement une exécution sur deux (aléa réel `os.urandom`, tiré
#: une fois par exécution) — un candidat « qui a réussi une fois » n'est
#: pas un candidat correct.
FLAKY_CODE = (
    "import os\n"
    "_BROKEN = os.urandom(1)[0] < 128\n"
    "def is_palindrome(s):\n"
    "    if _BROKEN:\n"
    "        return False\n"
    "    c = s.lower()\n"
    "    return c == c[::-1]\n"
)

FLAKY_TRIALS = int(os.environ.get("APEX_CI_FLAKY_TRIALS", "16"))


SPEC = CapabilitySpec(
    capability_id=CAPABILITY_ID,
    name="Generated-Solution Evaluation Loop",
    version="1",
    description=(
        "Prendre une tâche, faire générer une solution par un ModelProvider, l'exécuter dans le "
        "sandbox réel, la juger, en tirer une Experience et adapter la stratégie de génération "
        "suivante (G0 -> G1 -> ...)."
    ),
    components=(
        "apex.evalrunner.service.EvalRunnerService.run_generated_solution",
        "apex.evalrunner.generated_solution",
        "apex.taskgen.solution_tasks",
        "apex.oracles.exit_code.ExitCodeOracle",
        "apex.metalearner.population_fitness.compute_fitness",
        "apex.metalearner.failure_taxonomy.classify_failure",
        "apex.metalearner.experience",
        "apex.metalearner.generated_solution_cycle.compare_generated_solutions",
        "apex.metalearner.adaptive_solution_loop.run_adaptive_learning_loop",
    ),
    dependencies=(
        "SandboxProvider (NAMESPACED ou CONTAINERIZED)",
        "ModelProvider",
        "base SQL (SQLite/PostgreSQL)",
    ),
    preconditions=("un sandbox réellement isolé doit être sélectionnable (select_sandbox_provider)",),
    tests=(
        "tests/test_evalrunner_generated_solution.py",
        "tests/test_adaptive_solution_loop.py",
        "tests/test_failure_taxonomy.py",
        "tests/test_improvement_generated_solution.py",
    ),
    metrics=(
        MetricSpec(
            "false_positive",
            "1 par candidat NON correct accepté (PASS) — 0 attendu",
            higher_is_better=False,
        ),
        MetricSpec("false_negative", "1 par candidat correct rejeté — 0 attendu", higher_is_better=False),
        MetricSpec(
            "verdict_accuracy",
            "1 par candidat dont le verdict correspond à la vérité terrain",
            higher_is_better=True,
        ),
        MetricSpec(
            "cross_run_isolation",
            "1 par scénario où la G1 d'une exécution dépend de SA propre G0 (et sa trace le dit)",
            higher_is_better=True,
        ),
        MetricSpec(
            "learning_gain", "résolu en fin de boucle - résolu en G0, par scénario", higher_is_better=True
        ),
        MetricSpec(
            "flaky_acceptance",
            "1 par essai où la boucle déclare la cible atteinte avec un candidat non déterministe",
            higher_is_better=False,
            kind=MetricKind.STOCHASTIC,
            min_samples=FLAKY_TRIALS,
        ),
    ),
    success_criteria=(
        "aucun faux positif sur le corpus adversarial (false_positive = 0)",
        "aucun faux négatif sur les candidats corrects",
        "la décision de G1 dépend uniquement de l'expérience de SA propre exécution",
        "un candidat non déterministe n'est jamais retenu sur une seule exécution chanceuse",
    ),
    risks=(
        "juger un candidat par son seul code de sortie permet à un candidat de contourner les tests cachés",
        "un modèle réel n'a jamais été exercé ici (Ollama absent) — gain d'apprentissage réel non démontré",
    ),
    maturity="STUB (registre : EvalRunner/MetaLearner) — boucle réelle, aucun appelant de production",
)


# --- Environnement réel isolé par mesure ------------------------------------------------


def _sandbox_available() -> tuple[bool, str]:
    try:
        from apex.sandbox.provider import select_sandbox_provider

        selection = select_sandbox_provider()
    except Exception as exc:
        return False, f"sélection du sandbox impossible : {exc.__class__.__name__}: {exc}"
    level = selection.report.level.value
    if level not in ("namespaced", "containerized", "hardened"):
        return False, f"isolation insuffisante ({level}) — {selection.reason}"
    return True, f"{selection.provider_name} ({level})"


class _ScriptedProvider:
    """Double fidèle au contrat `ModelProvider` — texte fixe, ou fonction de
    la température demandée (arrondie à 6 décimales). `requests` garde les
    requêtes réellement reçues (preuve de la décision prise)."""

    def __init__(self, text_for: Callable[[float | None], str], *, fail: Exception | None = None) -> None:
        self._text_for = text_for
        self._fail = fail
        self.requests: list[float | None] = []

    def detect(self) -> Any:
        from apex.models.model import ModelCapabilityReport
        from apex.obs.capability import CapabilityStatus

        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE,
            backend="scripted-double",
            detail="double de test — jamais un vrai modèle",
        )

    def generate(self, request: Any) -> Any:
        from apex.models.model import GenerationResult
        from apex.obs.status import Measurement

        temperature = round(request.temperature, 6) if request.temperature is not None else None
        self.requests.append(temperature)
        if self._fail is not None:
            raise self._fail
        return GenerationResult(
            text=self._text_for(temperature),
            model_used="scripted-double",
            tokens_prompt=Measurement.real(1),
            tokens_completion=Measurement.real(1),
            finish_reason="stop",
            elapsed_s=0.0,
            facts={"scripted": True},
        )


def fixed(text: str) -> _ScriptedProvider:
    return _ScriptedProvider(lambda _t: text)


def by_temperature(mapping: dict[float, str], default: str = _WRONG) -> _ScriptedProvider:
    return _ScriptedProvider(lambda t: mapping.get(t, default) if t is not None else default)


class _Env:
    def __init__(self) -> None:
        from sqlalchemy import create_engine

        from apex.agents.runtime import AgentRuntime
        from apex.config.schema import Capability, ComputePool, ModelEndpoint, PoolLocation, TrustLevel
        from apex.db.engine import build_session_factory
        from apex.db.models import Base
        from apex.evalrunner.service import EvalRunnerService
        from apex.metalearner.experience import ExperienceRepository
        from apex.tasks.service import TaskBankService

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.factory = build_session_factory(engine)
        self.runtime = AgentRuntime(self.factory)
        self.tasks = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.experiences = ExperienceRepository(self.factory)
        self.pool = ComputePool(
            pool_id="local",
            location=PoolLocation.LOCAL,
            trust_level=TrustLevel.TRUSTED,
            capabilities=(Capability.CPU_EVAL,),
        )
        self.endpoint = ModelEndpoint(
            pool_id="local",
            runtime="ollama",
            url="http://127.0.0.1:11434",
            served_model="scripted-double",
            max_context=8192,
        )
        self.task = self.new_task()

    def new_task(self) -> Any:
        from apex.policy.model import Subject
        from apex.taskgen.solution_tasks import build_palindrome_task_spec
        from apex.tasks.model import TaskSplit

        spec = build_palindrome_task_spec()
        # Compatible avec les deux contrats de harness (avant/après le
        # correctif du cycle 2) — l'adaptateur mesure le code TEL QU'IL EST.
        expected: str | None = getattr(spec, "expected_answer", None)
        kwargs: dict[str, str] = {} if expected is None else {"expected_answer": expected}
        return self.tasks.create(
            split=TaskSplit.TRAIN,
            specialty="research",
            title=spec.title,
            description=spec.description,
            command=spec.command,
            created_by=Subject.HUMAN,
            solution_harness=spec.solution_harness,
            **kwargs,
        )

    def run_candidate(self, provider: Any, *, task: Any = None, limits: Any = None) -> Any:
        from apex.evalrunner.model import EvalMethod
        from apex.policy.model import Subject

        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=self.pool, genome_ref="sha256:ci-engine", spawned_by=Subject.CORE
        )
        return self.evalrunner.run_generated_solution(
            task_id=(task or self.task).id,
            clone_id=clone.clone_id,
            endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION,
            provider=provider,
            limits=limits,
        )

    def loop(
        self,
        provider: Any,
        *,
        max_generations: int,
        target_fitness: float | None = None,
        temperature: float = 0.7,
        experiment_ref: str | None = None,
        task: Any = None,
    ) -> Any:
        from apex.metalearner.adaptive_solution_loop import AdaptiveLoopConfig, run_adaptive_learning_loop
        from apex.metalearner.generated_solution_cycle import CandidateStrategy

        config = AdaptiveLoopConfig(
            max_generations=max_generations, target_fitness=target_fitness, no_improvement_patience=None
        )
        return run_adaptive_learning_loop(
            self.evalrunner,
            self.runtime,
            self.experiences,
            task_id=(task or self.task).id,
            endpoint=self.endpoint,
            pool=self.pool,
            initial_strategies=[
                CandidateStrategy(label="g0", genome_content={"generation": {"temperature": temperature}})
            ],
            config=config,
            provider=provider,
            experiment_ref=experiment_ref,
        )


# --- Mesures -------------------------------------------------------------------------


def _verdict_samples(env: _Env) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    from apex.oracles.model import OracleVerdict

    false_positive, false_negative, accuracy = [], [], []
    for candidate in CORPUS:
        run = env.run_candidate(fixed(candidate.code))
        accepted = run.oracle_verdict is OracleVerdict.PASS
        if candidate.is_correct:
            false_negative.append(0.0 if accepted else 1.0)
        else:
            false_positive.append(1.0 if accepted else 0.0)
        accuracy.append(1.0 if accepted == candidate.is_correct else 0.0)
    return tuple(false_positive), tuple(false_negative), tuple(accuracy)


def cross_run_scenario(env: _Env, *, first_code: str) -> tuple[float, float, dict[str, Any]]:
    """Deux exécutions INDÉPENDANTES de la boucle, sur la même tâche, avec
    les paramètres par défaut (aucun `experiment_ref` fourni). La seconde
    (B) démarre sur une réponse fausse à T=0.7 : sa propre G0 justifie une
    G1 à T=1.1. Retourne (décision issue de SA G0 ?, trace cohérente ?)."""
    env.loop(fixed(first_code), max_generations=1)  # exécution A
    provider_b = by_temperature({0.7: _WRONG, 1.1: _CORRECT})
    result_b = env.loop(provider_b, max_generations=2)
    g1_temperature = provider_b.requests[1] if len(provider_b.requests) > 1 else None
    own_decision = 1.0 if g1_temperature == 1.1 else 0.0
    g0_category = result_b.generations[0].best_experience.failure_category.value
    description = result_b.generations[1].mutation_description if len(result_b.generations) > 1 else ""
    coherent = 1.0 if ("incorrecte" in description) == (g0_category == "incorrect_solution") else 0.0
    return (
        own_decision,
        coherent,
        {
            "g1_temperature": g1_temperature,
            "b_g0_category": g0_category,
            "b_g1_description": description,
        },
    )


def _cross_run_samples(env_factory: Callable[[], _Env]) -> tuple[float, ...]:
    samples = []
    for first_code in (_CORRECT, _WRONG):
        own, coherent, _ = cross_run_scenario(env_factory(), first_code=first_code)
        samples.extend([own, coherent])
    return tuple(samples)


def _learning_samples(env_factory: Callable[[], _Env]) -> tuple[float, ...]:
    from apex.oracles.model import OracleVerdict

    samples: list[float] = []
    mappings: tuple[dict[float, str], ...] = ({0.7: _WRONG, 1.1: _CORRECT}, {})
    for mapping in mappings:
        env = env_factory()
        result = env.loop(
            by_temperature(mapping), max_generations=2, experiment_ref=f"learning:{len(samples)}"
        )
        solved_first = result.generations[0].best_experience.oracle_verdict is OracleVerdict.PASS
        solved_last = any(
            g.best_experience.oracle_verdict is OracleVerdict.PASS for g in result.generations
        )
        samples.append(float(solved_last) - float(solved_first))
    return tuple(samples)


def _flaky_samples(env_factory: Callable[[], _Env]) -> tuple[float, ...]:
    samples = []
    for trial in range(FLAKY_TRIALS):
        env = env_factory()
        result = env.loop(
            fixed(FLAKY_CODE), max_generations=1, target_fitness=1.0, experiment_ref=f"flaky:{trial}"
        )
        samples.append(1.0 if result.stop_reason == "target_fitness_reached" else 0.0)
    return tuple(samples)


class GeneratedSolutionLoopAdapter:
    spec = SPEC

    def measure(self, version: str) -> BenchmarkSnapshot:
        available, detail = _sandbox_available()
        if not available:
            return BenchmarkSnapshot(
                capability_id=CAPABILITY_ID,
                version=version,
                samples={},
                blocked={m.name: detail for m in SPEC.metrics},
                notes=(detail,),
            )
        false_positive, false_negative, accuracy = _verdict_samples(_Env())
        return BenchmarkSnapshot(
            capability_id=CAPABILITY_ID,
            version=version,
            samples={
                "false_positive": false_positive,
                "false_negative": false_negative,
                "verdict_accuracy": accuracy,
                "cross_run_isolation": _cross_run_samples(_Env),
                "learning_gain": _learning_samples(_Env),
                "flaky_acceptance": _flaky_samples(_Env),
            },
            notes=(
                f"sandbox : {detail}", "modèle : double scripté (aucun Ollama joignable)",
                f"flaky_trials={FLAKY_TRIALS}",
            ),
        )


# --- Sondes du juge externe --------------------------------------------------------------

_FUNC = frozenset({CycleKind.FUNCTIONALITY})
_ADV = frozenset({CycleKind.ADVERSARIAL})
_IMP = frozenset({CycleKind.IMPROVEMENT})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _probe_e2e_path() -> ProbeOutcome:
    from apex.oracles.model import OracleVerdict

    env = _Env()
    result = env.loop(fixed(_CORRECT), max_generations=1, experiment_ref="e2e")
    best = result.generations[0].best_experience
    run = result.generations[0].comparison.best.eval_run
    persisted = env.experiences.get(best.id)
    missing = [
        name
        for name, ok in (
            ("sandbox_status", run.sandbox_status is not None),
            ("model_id", run.model_id is not None),
            ("clone_id", bool(run.clone_id)),
            ("oracle PASS", run.oracle_verdict is OracleVerdict.PASS),
            ("experience persistée", persisted is not None),
            ("stdout candidat", bool(run.facts.get("candidate_execution"))),
        )
        if not ok
    ]
    return ProbeOutcome(
        refuted=bool(missing),
        observed="chemin complet traversé" if not missing else f"maillons absents : {', '.join(missing)}",
        hypothesis="un maillon TASK->MODEL->SANDBOX->ORACLE->EXPERIENCE n'est pas réellement câblé",
        evidence=(JudgeEvidence(kind="eval_run", reference=run.id),),
    )


def _probe_experience_causality() -> ProbeOutcome:
    from apex.db.engine import session_scope
    from apex.db.models import ExperienceRow
    from apex.metalearner.adaptive_solution_loop import decide_next_strategies
    from apex.metalearner.generated_solution_cycle import CandidateStrategy

    env = _Env()
    env.loop(fixed(_WRONG), max_generations=1, experiment_ref="causality")
    base = [CandidateStrategy(label="g0", genome_content={"generation": {"temperature": 0.7}})]
    informed, _ = decide_next_strategies(
        env.experiences,
        task_id=env.task.id,
        prior_generation_number=0,
        base_strategies=base,
        experiment_ref="causality",
    )
    with session_scope(env.factory) as session:
        for row in session.query(ExperienceRow).all():
            session.delete(row)
    forgotten, _ = decide_next_strategies(
        env.experiences,
        task_id=env.task.id,
        prior_generation_number=0,
        base_strategies=base,
        experiment_ref="causality",
    )
    unchanged = informed == forgotten
    return ProbeOutcome(
        refuted=unchanged,
        observed=(
            "la décision de G1 est identique avec ou sans l'expérience de G0 — stockage sans apprentissage"
            if unchanged
            else "supprimer l'expérience de G0 change la décision de G1"
        ),
        hypothesis="l'expérience persistée n'est pas lue par la décision suivante",
    )


def _probe_cross_run_isolation() -> ProbeOutcome:
    own, coherent, facts = cross_run_scenario(_Env(), first_code=_CORRECT)
    refuted = own < 1.0 or coherent < 1.0
    return ProbeOutcome(
        refuted=refuted,
        observed=(
            f"exécution B : G1 demandée à T={facts['g1_temperature']} alors que SA G0 était "
            f"« {facts['b_g0_category']} » ; trace G1 : « {facts['b_g1_description'][:120]} »"
        ),
        hypothesis=(
            "sans experiment_ref, list_by_task_and_generation ne filtre pas par exécution : la G1 "
            "d'une exécution lit la meilleure G0 de TOUTES les exécutions de la tâche, alors que sa "
            "généalogie (parent_experience_id) pointe vers sa propre G0"
        ),
        severity=JudgeSeverity.HIGH,
    )


def production_callers(function_name: str, *, exclude: tuple[str, ...]) -> list[str]:
    """Fichiers de `apex/` contenant un APPEL réel (nœud AST `Call`) à
    `function_name` — une mention en docstring ou en commentaire ne compte
    jamais comme un appelant."""
    root = _repo_root()
    callers = []
    for path in sorted((root / "apex").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if any(part in relative for part in exclude):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == function_name:
                    callers.append(relative)
                    break
    return callers


def _probe_production_trigger() -> ProbeOutcome:
    callers = production_callers(
        "run_adaptive_learning_loop", exclude=("adaptive_solution_loop.py", "apex/improvement/")
    )
    return ProbeOutcome(
        refuted=not callers,
        observed="aucun appelant hors tests" if not callers else "appelé par : " + ", ".join(callers),
        hypothesis=(
            "la boucle d'apprentissage n'est déclenchable par aucun point d'entrée (API/CLI) — dormante"
        ),
        severity=JudgeSeverity.MEDIUM,
    )


def _probe_oracle_gaming(label: str) -> Callable[[], ProbeOutcome]:
    candidate = next(c for c in CORPUS if c.label == label)

    def run() -> ProbeOutcome:
        from apex.oracles.model import OracleVerdict

        env = _Env()
        outcome = env.run_candidate(fixed(candidate.code))
        accepted = outcome.oracle_verdict is OracleVerdict.PASS
        return ProbeOutcome(
            refuted=accepted,
            observed=f"candidat « {label} » (ne résout PAS la tâche) -> {outcome.oracle_verdict.value}",
            hypothesis=(
                "le verdict ne dépend que du code de sortie du script ; le code candidat s'exécute AVANT "
                "les tests cachés dans le même processus et peut terminer/neutraliser le script"
            ),
            severity=JudgeSeverity.CRITICAL,
            evidence=(
                JudgeEvidence(kind="eval_run", reference=outcome.id, detail=outcome.oracle_detail or ""),
            ),
        )

    return run


def _probe_model_unavailable() -> ProbeOutcome:
    from apex.errors import ModelErrorCategory, ModelProviderError

    env = _Env()
    failing = _ScriptedProvider(
        lambda _t: _CORRECT,
        fail=ModelProviderError(
            "scripted",
            endpoint_url="http://127.0.0.1:11434",
            detail="connexion refusée",
            category=ModelErrorCategory.OFFLINE,
        ),
    )
    try:
        result = env.loop(failing, max_generations=2, experiment_ref="offline")
    except Exception as exc:
        return ProbeOutcome(
            refuted=True,
            observed=f"la boucle lève : {exc.__class__.__name__}: {exc}",
            hypothesis="une panne du modèle n'est pas convertie en résultat d'évaluation",
        )
    temps = failing.requests
    bad = [
        g.best_experience.failure_category.value
        for g in result.generations
        if g.best_experience.failure_category.value != "generation_failure"
    ]
    mutated = len(set(temps)) > 1
    return ProbeOutcome(
        refuted=bool(bad) or mutated,
        observed=f"catégories {[g.best_experience.failure_category.value for g in result.generations]}, "
        f"températures {temps}",
        hypothesis="une panne d'infrastructure est prise pour un échec du candidat et modifie la stratégie",
    )


def _probe_malformed_task() -> ProbeOutcome:
    from apex.evalrunner.generated_solution import MissingCandidateMarkerError
    from apex.policy.model import Subject
    from apex.tasks.model import TaskSplit

    env = _Env()
    task = env.tasks.create(
        split=TaskSplit.TRAIN,
        specialty="research",
        title="harness sans marqueur",
        description="x",
        command=("python3", "-c", "pass"),
        created_by=Subject.HUMAN,
        solution_harness="print('pas de marqueur')\n",
    )
    provider = fixed(_CORRECT)
    try:
        env.run_candidate(provider, task=task)
    except MissingCandidateMarkerError:
        return ProbeOutcome(
            refuted=bool(provider.requests),
            observed=f"refus propre ; appels modèle gaspillés : {len(provider.requests)}",
            hypothesis="une tâche mal formée consomme un appel modèle avant d'être refusée",
            severity=JudgeSeverity.LOW,
        )
    except Exception as exc:
        return ProbeOutcome(
            refuted=True,
            observed=f"exception inattendue : {exc.__class__.__name__}: {exc}",
            hypothesis="une tâche mal formée n'est pas refusée proprement",
        )
    return ProbeOutcome(
        refuted=True,
        observed="un harness sans marqueur a été exécuté quand même",
        hypothesis="la validation du harness est contournée",
        severity=JudgeSeverity.HIGH,
    )


def _probe_timeout() -> ProbeOutcome:
    from apex.oracles.model import OracleVerdict
    from apex.sandbox.model import ResourceLimits

    env = _Env()
    from apex.config.loader import load_config

    base = ResourceLimits.from_config(load_config().sandbox)
    limits = ResourceLimits(
        cpu_cores=base.cpu_cores,
        memory_mb=base.memory_mb,
        pids_limit=base.pids_limit,
        timeout_s=1.0,
        disk_mb=base.disk_mb,
        network=base.network,
    )
    run = env.run_candidate(fixed("while True:\n    pass\n"), limits=limits)
    return ProbeOutcome(
        refuted=run.oracle_verdict is not OracleVerdict.TIMEOUT,
        observed=f"boucle infinie -> {run.oracle_verdict.value} "
        f"({run.sandbox_status.value if run.sandbox_status else None})",
        hypothesis="un candidat qui ne termine pas n'est pas classé TIMEOUT",
    )


def _probe_single_execution_champion() -> ProbeOutcome:
    env = _Env()
    result = env.loop(fixed(_CORRECT), max_generations=3, target_fitness=1.0, experiment_ref="champion")
    fingerprint = result.generations[-1].best_experience.solution_fingerprint
    from apex.db.engine import session_scope
    from apex.db.models import EvalRunRow

    with session_scope(env.factory) as session:
        executions = session.query(EvalRunRow).filter(EvalRunRow.task_id == env.task.id).count()
    return ProbeOutcome(
        refuted=result.stop_reason == "target_fitness_reached" and executions < 2,
        observed=(
            f"stop_reason={result.stop_reason} après {executions} exécution(s) du candidat retenu "
            f"(empreinte {str(fingerprint)[:12]})"
        ),
        hypothesis=(
            "la cible est déclarée atteinte sur UNE exécution : un candidat non déterministe qui "
            "réussit par chance est retenu comme champion — la dimension ROBUSTNESS du Gauntlet "
            "(ré-exécution) n'est jamais appliquée à ce chemin"
        ),
        severity=JudgeSeverity.HIGH,
    )


def _probe(
    probe_id: str,
    dimension: JudgeDimension,
    kinds: frozenset[CycleKind],
    description: str,
    remediation: str,
    regression_test: str,
    fn: Callable[[], ProbeOutcome],
) -> FunctionProbe:
    return FunctionProbe(
        probe_id=probe_id,
        dimension=dimension,
        kinds=kinds,
        description=description,
        remediation=remediation,
        regression_test=regression_test,
        fn=fn,
    )


def build_judge(judge_id: str = "ci-judge:generated-solution") -> ExternalJudge:
    probes = (
        _probe(
            "e2e_path",
            JudgeDimension.CORRECTNESS,
            _FUNC,
            "le chemin TASK->MODEL->SANDBOX->ORACLE->EXPERIENCE est-il réellement traversé ?",
            "câbler le maillon manquant",
            "tests/test_improvement_generated_solution.py",
            _probe_e2e_path,
        ),
        _probe(
            "experience_causality",
            JudgeDimension.CORRECTNESS,
            _FUNC,
            "l'expérience persistée change-t-elle réellement la décision suivante ?",
            "lire l'expérience dans decide_next_strategies",
            "tests/test_adaptive_solution_loop.py::TestG0ToG1RealDependency",
            _probe_experience_causality,
        ),
        _probe(
            "cross_run_isolation",
            JudgeDimension.CORRECTNESS,
            _FUNC,
            "la décision d'une exécution dépend-elle d'une AUTRE exécution ?",
            "attribuer un experiment_ref unique à chaque exécution de la boucle qui n'en reçoit pas",
            "tests/test_adaptive_solution_loop.py::TestRunIsolation",
            _probe_cross_run_isolation,
        ),
        _probe(
            "production_trigger",
            JudgeDimension.GENERALIZATION,
            _FUNC,
            "la boucle est-elle déclenchable hors des tests ?",
            "exposer la boucle via un point d'entrée réel (CLI) composant les services existants",
            "tests/test_cli_solution_loop.py",
            _probe_production_trigger,
        ),
        *(
            _probe(
                f"oracle_gaming:{label}",
                JudgeDimension.CORRECTNESS,
                _ADV,
                "un candidat qui ne résout pas la tâche peut-il obtenir PASS ?",
                (
                    "ne plus juger sur le code de sortie seul : le harness publie les résultats réels, "
                    "comparés HORS sandbox à des valeurs attendues jamais présentes dans le script"
                ),
                "tests/test_evalrunner_generated_solution.py::TestResultContract",
                _probe_oracle_gaming(label),
            )
            for label in (
                "adversarial_sys_exit0",
                "adversarial_os_exit0",
                "adversarial_neutered_exit",
                "adversarial_forged_summary",
            )
        ),
        _probe(
            "model_unavailable",
            JudgeDimension.ROBUSTNESS,
            _ADV,
            "une panne du modèle est-elle confondue avec un mauvais candidat ?",
            "classer GENERATION_FAILURE, ne jamais muter la stratégie",
            "tests/test_adaptive_solution_loop.py",
            _probe_model_unavailable,
        ),
        _probe(
            "malformed_task",
            JudgeDimension.ROBUSTNESS,
            _ADV,
            "une tâche mal formée est-elle refusée avant tout appel modèle ?",
            "valider le harness avant la génération",
            "tests/test_evalrunner_generated_solution.py",
            _probe_malformed_task,
        ),
        _probe(
            "timeout",
            JudgeDimension.EFFICIENCY,
            _ADV,
            "un candidat qui ne termine pas est-il classé TIMEOUT ?",
            "appliquer ResourceLimits.timeout_s",
            "tests/test_evalrunner_generated_solution.py",
            _probe_timeout,
        ),
        _probe(
            "single_execution_champion",
            JudgeDimension.ROBUSTNESS,
            _IMP,
            "un champion est-il retenu sur une seule exécution ?",
            (
                "confirmer le candidat retenu par ré-exécution du MÊME code (RobustnessJudge du Gauntlet) "
                "avant de déclarer la cible atteinte"
            ),
            "tests/test_adaptive_solution_loop.py::TestChampionConfirmation",
            _probe_single_execution_champion,
        ),
    )
    return ExternalJudge(judge_id, probes)


def corpus_fingerprint() -> str:
    """Empreinte du corpus étiqueté — un benchmark dont le corpus change
    n'est plus comparable à la baseline."""
    digest = hashlib.sha256()
    for candidate in CORPUS:
        digest.update(f"{candidate.label}|{candidate.family}|{candidate.code}".encode())
    digest.update(FLAKY_CODE.encode())
    return digest.hexdigest()[:16]
