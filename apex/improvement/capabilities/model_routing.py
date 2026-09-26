"""Deuxième capacité du Continuous Improvement Engine : LEARNING / MODEL ROUTING.

Question mesurée : une expérience passée change-t-elle RÉELLEMENT le modèle
choisi ensuite par le point d'entrée de production (`apex solve`,
`apex.metalearner.solution_loop_runner.run_solution_loop`) ?

Chaque scénario :
1. construit une configuration contrôlée à partir de la vraie (`load_config`) :
   deux candidats locaux du rôle `coder_main`, `alpha-coder` (candidat de
   référence, en tête) et `beta-coder` ;
2. produit un HISTORIQUE par de vraies exécutions (EvalRunner, sandbox,
   oracle, Experience réels) sur chaque modèle — le backend du modèle est un
   double qui renvoie `model_used = served_model` comme Ollama ;
3. appelle `run_solution_loop` SANS fournir de provider : c'est le code de
   production qui choisit l'endpoint ; le double enregistre quel modèle a
   réellement été appelé.

Même adaptateur pour mesurer avant et après un correctif : il n'utilise que
des interfaces stables (`run_solution_loop`, `run_adaptive_learning_loop`,
`EvalRunnerService`)."""

from __future__ import annotations

import dataclasses
import hashlib
import sys
from collections.abc import Callable
from typing import Any, ClassVar
from unittest.mock import patch

from apex.improvement.judge import ExternalJudge, FunctionProbe, ProbeOutcome
from apex.improvement.model import BenchmarkSnapshot, CapabilitySpec, CycleKind, MetricSpec
from apex.metalearner.judge_model import JudgeDimension, JudgeSeverity

CAPABILITY_ID = "learning_model_routing"
ALPHA = "alpha-coder:1"
BETA = "beta-coder:1"

_CORRECT = "def is_palindrome(s):\n    c = s.lower()\n    return c == c[::-1]\n"
_WRONG = "def is_palindrome(s):\n    return False\n"

#: Comportement du double de backend, par modèle servi.
CORRECT, WRONG, DEAD, LUCKY = "correct", "wrong", "dead", "lucky"

SPEC = CapabilitySpec(
    capability_id=CAPABILITY_ID,
    name="Learning / Model Routing",
    version="1",
    description=(
        "Le résultat jugé d'une solution générée (Experience) doit pouvoir changer le modèle "
        "choisi par le point d'entrée de production pour les tâches suivantes."
    ),
    components=(
        "apex.metalearner.solution_loop_runner.run_solution_loop",
        "apex.metalearner.solution_loop_runner.resolve_endpoint",
        "apex.routing.router.select_model",
        "apex.metalearner.experience",
        "apex.evalrunner.service.EvalRunnerService.run_generated_solution",
    ),
    dependencies=("SandboxProvider (NAMESPACED ou CONTAINERIZED)", "base SQL", "ModelProvider"),
    preconditions=("un sandbox réellement isolé doit être sélectionnable",),
    tests=("tests/test_improvement_model_routing.py",),
    metrics=(
        MetricSpec(
            "picks_proven_model", "référence en échec, autre modèle confirmé -> l'autre est choisi", True
        ),
        MetricSpec(
            "keeps_proven_reference", "garde-fou : référence confirmée, autre en échec -> référence", True
        ),
        MetricSpec(
            "reference_without_history", "garde-fou : sans historique -> candidat de référence", True
        ),
        MetricSpec("avoids_dead_model", "référence injoignable (génération impossible) -> l'autre", True),
        MetricSpec("infra_not_blamed_on_model", "pannes d'infrastructure non imputées au modèle", True),
        MetricSpec("unconfirmed_success_not_credited", "succès jamais confirmés non crédités", True),
        MetricSpec(
            "abandons_failing_for_untried", "référence en échec, autre jamais essayé -> l'autre", True
        ),
    ),
    success_criteria=(
        "une expérience jugée change le choix du modèle suivant",
        "aucune décision fondée sur une panne d'infrastructure ou un succès non confirmé",
        "sans historique, le candidat de référence de la configuration reste choisi",
    ),
    risks=(
        "modèle réel jamais exercé ici : seuls les chemins de décision sont mesurés",
        "un modèle écarté n'est jamais ré-essayé (famine) si aucune exploration n'existe",
    ),
    maturity="ModelRouter STUB ; performance= jamais transmis en production (audit)",
)


class _Backend:
    """Double fidèle au contrat `ModelProvider`, un par endpoint — comme Ollama,
    `model_used` est le modèle servi."""

    calls: ClassVar[list[str]] = []
    behaviour: ClassVar[dict[str, str]] = {}

    def __init__(self, endpoint: Any) -> None:
        self._served = endpoint.served_model
        self._url = endpoint.url

    def detect(self) -> Any:
        from apex.models.model import ModelCapabilityReport
        from apex.obs.capability import CapabilityStatus

        return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="double", detail="double")

    def generate(self, request: Any) -> Any:
        from apex.errors import ModelErrorCategory, ModelProviderError
        from apex.models.model import GenerationResult
        from apex.obs.status import Measurement

        _Backend.calls.append(self._served)
        mode = _Backend.behaviour.get(self._served, WRONG)
        if mode == DEAD:
            raise ModelProviderError(
                "double",
                endpoint_url=self._url,
                detail="connexion refusée",
                category=ModelErrorCategory.OFFLINE,
            )
        return GenerationResult(
            text=_CORRECT if mode in (CORRECT, LUCKY) else _WRONG,
            model_used=self._served,
            tokens_prompt=Measurement.real(1),
            tokens_completion=Measurement.real(1),
            finish_reason="stop",
            elapsed_s=0.0,
            facts={},
        )


def _unavailable_sandbox() -> Any:
    """Sandbox réellement indisponible pour ce scénario (panne d'infrastructure)."""
    from apex.errors import IsolationUnavailable
    from apex.sandbox.linux_namespace import LinuxNamespaceSandbox

    class _Unavailable(LinuxNamespaceSandbox):
        def execute(self, spec: Any, *, cancel_token: Any = None) -> Any:
            raise IsolationUnavailable(
                "scénario : sandbox indisponible", required="namespaced", available="aucun"
            )

    return _Unavailable()


class _Env:
    def __init__(self) -> None:
        from sqlalchemy import create_engine

        from apex.agents.runtime import AgentRuntime
        from apex.config.loader import load_config
        from apex.config.schema import ModelEndpoint, ModelRef, ModelRole
        from apex.db.engine import build_session_factory
        from apex.db.models import Base
        from apex.metalearner.solution_loop_runner import resolve_local_pool
        from apex.policy.model import Subject
        from apex.taskgen.solution_tasks import build_palindrome_task_spec
        from apex.tasks.model import TaskSplit
        from apex.tasks.service import TaskBankService

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.factory = build_session_factory(engine)
        base = load_config()
        pool = resolve_local_pool(base)
        refs = tuple(
            ModelRef(family=name.split(":")[0], version="1", quantization="q4", capability_class="coder")
            for name in (ALPHA, BETA)
        )
        endpoints = tuple(
            ModelEndpoint(
                pool_id=pool.pool_id,
                runtime="ollama",
                url="http://127.0.0.1:11434",
                served_model=name,
                max_context=8192,
            )
            for name in (ALPHA, BETA)
        )
        roles = dict(base.model_roles)
        roles["coder_main"] = ModelRole(role="coder_main", candidates=refs, selection="benchmark_driven")
        self.config = dataclasses.replace(base, model_roles=roles, endpoints=base.endpoints + endpoints)
        self.endpoints = {e.served_model: e for e in endpoints}
        self.pool = pool
        self.runtime = AgentRuntime(self.factory)
        spec = build_palindrome_task_spec()
        self.tasks = TaskBankService(self.factory)
        self._spec = spec
        self._subject = Subject
        self._split = TaskSplit
        self.task = self.new_task()

    def new_task(self) -> Any:
        spec = self._spec
        return self.tasks.create(
            split=self._split.TRAIN,
            specialty="research",
            title=spec.title,
            description=spec.description,
            command=spec.command,
            created_by=self._subject.HUMAN,
            solution_harness=spec.solution_harness,
            expected_answer=spec.expected_answer,
        )

    def history(
        self,
        model: str,
        mode: str,
        *,
        runs: int = 2,
        confirmation_runs: int = 1,
        sandbox_down: bool = False,
    ) -> None:
        """Vraies exécutions sur `model` : Experiences réellement produites."""
        from apex.evalrunner.service import EvalRunnerService
        from apex.metalearner import generated_solution_cycle
        from apex.metalearner.adaptive_solution_loop import AdaptiveLoopConfig, run_adaptive_learning_loop
        from apex.metalearner.experience import ExperienceRepository
        from apex.metalearner.generated_solution_cycle import CandidateStrategy

        class _Diverging(generated_solution_cycle.ReplayProvider):
            def __init__(self, text: str, *, original_model_id: str | None) -> None:
                super().__init__(_WRONG, original_model_id=original_model_id)

        replay = _Diverging if mode == LUCKY else generated_solution_cycle.ReplayProvider
        evalrunner = EvalRunnerService(
            self.factory,
            agent_runtime=self.runtime,
            sandbox=_unavailable_sandbox() if sandbox_down else None,
        )
        _Backend.behaviour[model] = mode
        with (
            patch.object(generated_solution_cycle, "ReplayProvider", replay),
            patch("apex.evalrunner.service.get_default_model_provider", _Backend),
        ):
            for index in range(runs):
                run_adaptive_learning_loop(
                    evalrunner,
                    self.runtime,
                    ExperienceRepository(self.factory),
                    task_id=self.new_task().id,
                    endpoint=self.endpoints[model],
                    pool=self.pool,
                    initial_strategies=[
                        CandidateStrategy(label="h", genome_content={"generation": {"temperature": 0.7}})
                    ],
                    config=AdaptiveLoopConfig(
                        max_generations=1, no_improvement_patience=None, confirmation_runs=confirmation_runs
                    ),
                    experiment_ref=f"history:{model}:{mode}:{index}:{sandbox_down}",
                )

    def decide(self) -> str | None:
        """Modèle réellement appelé par le point d'entrée de production."""
        from apex.metalearner.solution_loop_runner import SolutionLoopRequest, run_solution_loop

        _Backend.behaviour.update({ALPHA: CORRECT, BETA: CORRECT})
        _Backend.calls = []
        with patch("apex.evalrunner.service.get_default_model_provider", _Backend):
            run_solution_loop(
                self.factory,
                self.config,
                SolutionLoopRequest(
                    task_id=self.new_task().id, generations=1, candidates=1, target_fitness=None
                ),
            )
        return _Backend.calls[0] if _Backend.calls else None


def _scenario(setup: Callable[[_Env], None], expected: str) -> tuple[float, str]:
    _Backend.behaviour = {}
    env = _Env()
    setup(env)
    chosen = env.decide()
    return (1.0 if chosen == expected else 0.0), f"choisi={chosen} attendu={expected}"


def _alpha_wrong_beta_correct(env: _Env) -> None:
    env.history(ALPHA, WRONG)
    env.history(BETA, CORRECT)


def _alpha_correct_beta_wrong(env: _Env) -> None:
    env.history(ALPHA, CORRECT)
    env.history(BETA, WRONG)


def _no_history(env: _Env) -> None:
    return None


def _alpha_dead_beta_correct(env: _Env) -> None:
    env.history(ALPHA, DEAD)
    env.history(BETA, CORRECT)


def _alpha_infra_outages(env: _Env) -> None:
    env.history(ALPHA, CORRECT, runs=1)
    env.history(ALPHA, CORRECT, runs=3, sandbox_down=True)
    env.history(BETA, CORRECT, runs=1)


def _alpha_lucky_beta_correct(env: _Env) -> None:
    env.history(ALPHA, LUCKY)
    env.history(BETA, CORRECT)


def _alpha_wrong_beta_untried(env: _Env) -> None:
    env.history(ALPHA, WRONG)


SCENARIOS: dict[str, tuple[Callable[[_Env], None], str]] = {
    "picks_proven_model": (_alpha_wrong_beta_correct, BETA),
    "keeps_proven_reference": (_alpha_correct_beta_wrong, ALPHA),
    "reference_without_history": (_no_history, ALPHA),
    "avoids_dead_model": (_alpha_dead_beta_correct, BETA),
    "infra_not_blamed_on_model": (_alpha_infra_outages, ALPHA),
    "unconfirmed_success_not_credited": (_alpha_lucky_beta_correct, BETA),
    "abandons_failing_for_untried": (_alpha_wrong_beta_untried, BETA),
}


def run_scenario(name: str) -> tuple[float, str]:
    setup, expected = SCENARIOS[name]
    return _scenario(setup, expected)


def _sandbox_detail() -> tuple[bool, str]:
    try:
        from apex.sandbox.provider import select_sandbox_provider

        selection = select_sandbox_provider()
    except Exception as exc:
        return False, f"sélection du sandbox impossible : {exc.__class__.__name__}: {exc}"
    level = selection.report.level.value
    ok = level in ("namespaced", "containerized", "hardened")
    return ok, f"{selection.provider_name} ({level})"


class ModelRoutingAdapter:
    spec = SPEC

    def benchmark_fingerprint(self) -> str:
        source = repr(sorted((k, v[0].__name__, v[1]) for k, v in SCENARIOS.items())) + _CORRECT + _WRONG
        return "scenarios=" + hashlib.sha256(source.encode()).hexdigest()[:16]

    def measure(self, version: str) -> BenchmarkSnapshot:
        available, detail = _sandbox_detail()
        environment = {
            "sandbox": detail,
            "model": "scripted-double",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }
        if not available:
            return BenchmarkSnapshot(
                capability_id=CAPABILITY_ID,
                version=version,
                samples={},
                blocked={m.name: detail for m in SPEC.metrics},
                notes=(detail,),
                environment=environment,
            )
        samples: dict[str, tuple[float, ...]] = {}
        notes = [f"sandbox : {detail}", "modèle : double scripté, un par endpoint"]
        for metric in SPEC.metrics:
            value, observed = run_scenario(metric.name)
            samples[metric.name] = (value,)
            notes.append(f"{metric.name}: {observed}")
        return BenchmarkSnapshot(
            capability_id=CAPABILITY_ID,
            version=version,
            samples=samples,
            notes=tuple(notes),
            environment=environment,
        )


def _scenario_probe(name: str, hypothesis: str) -> Callable[[], ProbeOutcome]:
    def run() -> ProbeOutcome:
        value, observed = run_scenario(name)
        return ProbeOutcome(
            refuted=value < 1.0, observed=observed, hypothesis=hypothesis, severity=JudgeSeverity.HIGH
        )

    return run


def _probe_history_causality() -> ProbeOutcome:
    a, observed_a = run_scenario("keeps_proven_reference")
    b, observed_b = run_scenario("picks_proven_model")
    return ProbeOutcome(
        refuted=not (a == 1.0 and b == 1.0),
        observed=f"historique A : {observed_a} ; historique B : {observed_b}",
        hypothesis="le choix du modèle ne dépend pas de l'expérience passée (même décision pour A et B)",
        severity=JudgeSeverity.HIGH,
    )


def _probe(
    probe_id: str,
    kinds: frozenset[CycleKind],
    description: str,
    remediation: str,
    fn: Callable[[], ProbeOutcome],
) -> FunctionProbe:
    return FunctionProbe(
        probe_id=probe_id,
        dimension=JudgeDimension.CORRECTNESS,
        kinds=kinds,
        description=description,
        remediation=remediation,
        regression_test="tests/test_improvement_model_routing.py",
        fn=fn,
    )


_F, _A, _I = CycleKind.FUNCTIONALITY, CycleKind.ADVERSARIAL, CycleKind.IMPROVEMENT


def build_judge(judge_id: str = "ci-judge:model-routing") -> ExternalJudge:
    return ExternalJudge(
        judge_id,
        (
            _probe(
                "history_causality",
                frozenset({_F}),
                "deux historiques différents mènent au même choix de modèle",
                "construire un signal de performance depuis les Experience et le transmettre au routeur",
                _probe_history_causality,
            ),
            _probe(
                "reference_without_history",
                frozenset({_F, _A, _I}),
                "sans historique, le candidat de référence n'est plus choisi",
                "ne jamais inventer de préférence sans preuve",
                _scenario_probe("reference_without_history", "une préférence est inventée sans historique"),
            ),
            _probe(
                "avoids_dead_model",
                frozenset({_A}),
                "un modèle injoignable continue d'être choisi",
                "attribuer l'échec de génération au modèle demandé",
                _scenario_probe(
                    "avoids_dead_model",
                    "un échec de génération ne porte pas le modèle demandé (model_id=None)",
                ),
            ),
            _probe(
                "infra_not_blamed_on_model",
                frozenset({_A}),
                "une panne d'infrastructure fait écarter un modèle qui fonctionne",
                "exclure les pannes d'infrastructure du signal de performance",
                _scenario_probe(
                    "infra_not_blamed_on_model",
                    "les pannes de sandbox sont comptées comme des échecs du modèle",
                ),
            ),
            _probe(
                "unconfirmed_success_not_credited",
                frozenset({_A}),
                "des succès chanceux, jamais confirmés, font préférer un modèle",
                "ne créditer que les succès CONFIRMED",
                _scenario_probe(
                    "unconfirmed_success_not_credited",
                    "un succès réfuté ou seulement observé est crédité au modèle",
                ),
            ),
            _probe(
                "abandons_failing_for_untried",
                frozenset({_I}),
                "un modèle qui échoue reste préféré à un modèle jamais essayé",
                "sans preuve, ne pas classer un modèle jamais essayé sous un modèle en échec",
                _scenario_probe(
                    "abandons_failing_for_untried",
                    "un taux de succès nul vaut autant qu'une absence de données",
                ),
            ),
        ),
    )
