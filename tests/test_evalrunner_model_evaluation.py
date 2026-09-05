"""Tests d'`EvalRunnerService.run_model_eval` — passe « Real Model
Evaluation », le DEUXIÈME chemin d'exécution d'EvalRunner (`ModelProvider`,
HORS sandbox), distinct de `run()` (chemin déterministe, `tests/
test_evalrunner_service.py`, jamais modifié par cette passe).

IMPORTANT — même discipline que `tests/test_missions_model_generation.py` :
`_FakeModelProvider` n'est jamais un modèle, seulement une implémentation
fidèle du contrat `ModelProvider` pour observer ce que `run_model_eval` lui
envoie et comment elle traite ce qu'il renvoie. Le SEUL test contre un VRAI
Ollama est `TestRealOllamaModelEval`, explicitement sauté si aucun Ollama
réel n'est joignable — jamais un faux PASS d'intégration.
"""

from __future__ import annotations

import os
import socket
import unittest

from sqlalchemy import create_engine

from apex.config.schema import ModelEndpoint
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import ModelProviderError
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.oracles.model import OracleVerdict
from apex.oracles.response_judge import ExactMatchResponseJudge, get_default_response_judge
from apex.policy.model import Subject
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = {
        "pool_id": "local", "runtime": "ollama", "url": "http://127.0.0.1:11434",
        "served_model": "qwen2.5-coder:7b-instruct", "max_context": 8192,
    }
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


class _FakeModelProvider(ModelProvider):
    """Double de test fidèle au contrat `ModelProvider` — jamais présenté
    comme une preuve d'inférence réelle (voir docstring du module)."""

    def __init__(self, *, response_text: str = "4", model_used: str = "qwen2.5-coder:7b-instruct") -> None:
        self.response_text = response_text
        self.model_used = model_used
        self.should_fail = False
        self.received_requests: list[GenerationRequest] = []

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend="fake-test-double",
            detail="double de test — jamais un vrai backend",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.received_requests.append(request)
        if self.should_fail:
            raise ModelProviderError(
                "FakeModelProvider.generate",
                endpoint_url=request.endpoint.url,
                detail="échec simulé pour test",
            )
        return GenerationResult(
            text=self.response_text, model_used=self.model_used,
            tokens_prompt=Measurement.real(12), tokens_completion=Measurement.real(1),
            finish_reason="stop", elapsed_s=0.002, facts={"fake": True},
        )


class TestRunModelEvalWiring(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.task_service = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=None)  # type: ignore[arg-type]
        self.endpoint = an_endpoint()

    def _a_task(self, *, expected_answer: str | None = "4"):
        return self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="Combien font 2+2 ?",
            description="Réponds uniquement par le nombre.", command=("true",),
            created_by=Subject.HUMAN, expected_answer=expected_answer,
        )

    # -- 1/2/3 : coexistence avec le chemin déterministe ---------------------

    def test_a_deterministic_task_never_needs_expected_answer(self) -> None:
        """1. Une tâche déterministe existante (sans expected_answer) reste
        valide — ce champ est purement additif."""
        task = self._a_task(expected_answer=None)
        self.assertIsNone(task.expected_answer)
        self.assertEqual(task.command, ("true",))

    def test_b_eval_run_without_model_id_still_round_trips(self) -> None:
        """2. Un EvalRun sans model_id (chemin déterministe) reste valide —
        aucune régression du contrat existant."""
        from datetime import datetime, timezone

        from apex.evalrunner.model import EvalRun
        from apex.sandbox.model import ExecutionStatus

        run = EvalRun.new(
            task_id="t1", clone_id="c1", method=EvalMethod.PRACTICE,
            oracle_verdict=OracleVerdict.PASS, oracle_detail=None,
            sandbox_status=ExecutionStatus.SUCCESS, exit_code=0, elapsed_s=0.1,
            facts={}, created_by="core", started_at=datetime.now(timezone.utc),
        )
        self.assertIsNone(run.model_id)
        self.assertEqual(run.clone_id, "c1")
        self.assertEqual(run.sandbox_status, ExecutionStatus.SUCCESS)

    def test_c_run_model_eval_persists_a_real_model_id(self) -> None:
        """3. Un EvalRun produit par run_model_eval référence bien le
        model_id RÉELLEMENT renvoyé par le provider (jamais le nom demandé)."""
        provider = _FakeModelProvider(model_used="qwen2.5-coder:7b-instruct-actually-served")
        task = self._a_task()
        run = self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        self.assertEqual(run.model_id, "qwen2.5-coder:7b-instruct-actually-served")
        self.assertIsNone(run.clone_id)
        self.assertIsNone(run.sandbox_status)

    # -- 4/5/6/7/8 : génération, persistance, évaluation, score --------------

    def test_d_fake_provider_receives_the_task_description_as_prompt(self) -> None:
        """4/5. Le ModelProvider (fake) reçoit bien une requête de génération."""
        provider = _FakeModelProvider()
        task = self._a_task()
        self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        self.assertEqual(len(provider.received_requests), 1)
        self.assertEqual(provider.received_requests[0].prompt, task.description)
        self.assertIs(provider.received_requests[0].endpoint, self.endpoint)

    def test_e_generated_response_is_persisted_in_facts(self) -> None:
        """6. La réponse générée est réellement persistée (relecture depuis
        le repository, pas seulement l'objet en mémoire)."""
        provider = _FakeModelProvider(response_text="la réponse générée")
        task = self._a_task(expected_answer=None)
        run = self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        reread = self.evalrunner.get(run.id)
        self.assertIsNotNone(reread)
        self.assertEqual(reread.facts["model_response"]["text"], "la réponse générée")
        self.assertFalse(reread.facts["model_response"]["truncated"])

    def test_f_exact_match_judge_passes_on_identical_answer(self) -> None:
        """7/8. Jugement PASS quand la réponse correspond exactement à
        expected_answer."""
        provider = _FakeModelProvider(response_text="4")
        task = self._a_task(expected_answer="4")
        run = self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        self.assertIs(run.oracle_verdict, OracleVerdict.PASS)

    def test_g_exact_match_judge_fails_on_wrong_answer(self) -> None:
        """7/8. Jugement FAIL quand la réponse ne correspond pas."""
        provider = _FakeModelProvider(response_text="5")
        task = self._a_task(expected_answer="4")
        run = self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        self.assertIs(run.oracle_verdict, OracleVerdict.FAIL)

    def test_h_no_expected_answer_yields_unknown_never_guessed(self) -> None:
        """7/8. Sans expected_answer déclarée, le verdict est UNKNOWN —
        jamais deviné (ni PASS ni FAIL fabriqué)."""
        provider = _FakeModelProvider(response_text="whatever")
        task = self._a_task(expected_answer=None)
        run = self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        self.assertIs(run.oracle_verdict, OracleVerdict.UNKNOWN)

    def test_i_response_text_is_truncated_beyond_the_size_limit_never_silently(self) -> None:
        """Sécurité §10/§11 : une réponse pathologiquement longue est bornée,
        et le fait est toujours signalé (`truncated=True`), jamais caché."""
        from apex.evalrunner import service as service_module

        huge = "x" * (service_module._MAX_STORED_RESPONSE_CHARS + 500)
        provider = _FakeModelProvider(response_text=huge)
        task = self._a_task(expected_answer=None)
        run = self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        self.assertTrue(run.facts["model_response"]["truncated"])
        self.assertEqual(
            len(run.facts["model_response"]["text"]), service_module._MAX_STORED_RESPONSE_CHARS,
        )

    # -- 9 : trace ------------------------------------------------------------

    def test_j_trace_is_recorded_as_a_real_event_and_audit_row(self) -> None:
        """9. Une tentative générée par modèle est traçable : event +
        audit_log réels, avec task_id/model_id/oracle_verdict."""
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository, EventRepository

        provider = _FakeModelProvider(response_text="4")
        task = self._a_task(expected_answer="4")
        run = self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent(limit=10)
            audits = AuditLogRepository(session).recent(limit=10)
        event_kinds = [e.msg for e in events]
        self.assertIn("evalrun.model_completed", event_kinds)
        audit_actions = [a.action for a in audits]
        self.assertIn("evalrun.model_completed", audit_actions)
        matching_audit = next(a for a in audits if a.action == "evalrun.model_completed")
        self.assertEqual(matching_audit.target, run.id)
        self.assertEqual(matching_audit.details["model_id"], run.model_id)

    # -- 10 : échec propre du provider ----------------------------------------

    def test_k_provider_failure_yields_a_clean_error_eval_run_never_an_exception(self) -> None:
        """10. Un échec du ModelProvider produit un EvalRun ERROR propre —
        jamais une exception qui remonte à l'appelant, jamais un model_id
        deviné."""
        provider = _FakeModelProvider()
        provider.should_fail = True
        task = self._a_task()
        run = self.evalrunner.run_model_eval(
            task_id=task.id, endpoint=self.endpoint, method=EvalMethod.PRACTICE, provider=provider,
        )
        self.assertIs(run.oracle_verdict, OracleVerdict.ERROR)
        self.assertIsNone(run.model_id)
        self.assertIn("échec simulé pour test", run.oracle_detail)

    def test_l_missing_task_raises_lookup_error(self) -> None:
        provider = _FakeModelProvider()
        with self.assertRaises(LookupError):
            self.evalrunner.run_model_eval(
                task_id="does-not-exist", endpoint=self.endpoint,
                method=EvalMethod.PRACTICE, provider=provider,
            )

    # -- 11 : aucun couplage direct EvalRunner -> Ollama ----------------------

    def test_m_evalrunner_module_never_imports_ollama_directly(self) -> None:
        """11. `apex.evalrunner.service` ne référence jamais `OllamaProvider`
        ni `apex.models.ollama` — uniquement l'abstraction `ModelProvider`,
        le provider restant interchangeable (prouvé ici en substituant un
        double de test complet)."""
        import inspect

        from apex.evalrunner import service as service_module

        source = inspect.getsource(service_module)
        self.assertNotIn("OllamaProvider", source)
        self.assertNotIn("apex.models.ollama", source)
        self.assertIn("ModelProvider", source)

    def test_n_default_response_judge_is_exact_match(self) -> None:
        self.assertIsInstance(get_default_response_judge(), ExactMatchResponseJudge)


def _real_ollama_url() -> str | None:
    """Constat réel — jamais supposé. Même patron que
    `tests/test_missions_model_generation.py::_real_ollama_url`."""
    configured = os.environ.get("APEX_OLLAMA_URL")
    if configured:
        return configured
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return "http://127.0.0.1:11434"
    except OSError:
        return None


@unittest.skipUnless(
    _real_ollama_url() is not None,
    "aucun Ollama réel joignable (définir APEX_OLLAMA_URL pour pointer vers le vôtre)",
)
class TestRealOllamaModelEval(unittest.TestCase):
    """Le SEUL test de ce fichier contre un VRAI Ollama — s'exécute
    automatiquement dès qu'un endpoint réel est joignable, sauté sinon,
    jamais remplacé par une simulation (§9 de la mission)."""

    def test_a_real_model_eval_gets_a_real_response_and_a_real_verdict(self) -> None:
        url = _real_ollama_url()
        assert url is not None
        served_model = os.environ.get("APEX_OLLAMA_MODEL", "qwen2.5-coder:7b-instruct")
        endpoint = an_endpoint(url=url, served_model=served_model)

        factory = sqlite_session_factory()
        task_service = TaskBankService(factory)
        evalrunner = EvalRunnerService(factory, agent_runtime=None)  # type: ignore[arg-type]
        task = task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="Combien font 2+2 ?",
            description="Réponds uniquement par le chiffre, sans aucun autre mot.",
            command=("true",), created_by=Subject.HUMAN,
        )
        run = evalrunner.run_model_eval(task_id=task.id, endpoint=endpoint, method=EvalMethod.PRACTICE)
        self.assertIsNotNone(run.model_id)
        self.assertIn(run.oracle_verdict, (OracleVerdict.UNKNOWN, OracleVerdict.PASS, OracleVerdict.FAIL))
        self.assertGreater(len(run.facts["model_response"]["text"]), 0)


if __name__ == "__main__":
    unittest.main()
