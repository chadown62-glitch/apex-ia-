"""Mémoire SYSTÈME d'APEX : indexation -> représentation -> récupération -> contexte -> décision.

Vraie base, vraie récupération (BM25 lexical), vraie boucle, vraie sandbox, vrai Judge, vrai Chat ; seuls les
modèles sont des DOUBLURES qui enregistrent les prompts reçus. Prouve le MÉCANISME (ce qui est indexé, ce qui
est retrouvé, avec quelle provenance, ce qui n'est jamais montré) — jamais qu'un vrai modèle en tire profit.
"""

from __future__ import annotations

import functools
import json
from typing import ClassVar

import pytest

from apex.agents.runtime import AgentRuntime
from apex.memory.indexing import chunk_text, index_document
from apex.memory.model import MemoryCategory, MemoryKind, MemoryRecord
from apex.memory.retrieval import score_candidates, tokenize
from apex.memory.service import MemoryService
from apex.errors import PolicyDenied
from apex.policy.model import Subject
from apex.taskloop.loop import _MEMORY_HEADER, run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits
from apex.taskloop.runs import TaskRunService
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import build_user_function_task_spec
from tests.test_taskloop_adaptation import _kinds
from tests.test_taskloop_execution import _FakeModelProvider, a_candidate, a_pool, sqlite_session_factory

ADD_CASES = [{"args": [1, 2], "expected": 3}, {"args": [-5, 5], "expected": 0}, {"args": [10, 20], "expected": 30}]
RIGHT_ADD = "def add(a, b):\n    return a + b\n"


@pytest.fixture
def factory():
    _Recorder.prompts = []
    return sqlite_session_factory()


def _record(content: str, *, category: MemoryCategory | None = MemoryCategory.KNOWLEDGE, source: str = "s"):
    return MemoryRecord.new(kind=MemoryKind.SEMANTIC, content=content, source=source, created_by="human",
                            category=category)


# -- représentation et pertinence ------------------------------------------------------------------------


def test_tokenize_folds_accents_drops_stopwords_and_splits_identifiers() -> None:
    assert tokenize("Le Fichier est_palindrome à corriger") == [
        "fichier", "est_palindrome", "palindrome", "corriger"]


def test_bm25_ranks_the_relevant_record_first_and_returns_nothing_without_overlap() -> None:
    records = [
        _record("Recette de la tarte aux pommes et à la cannelle"),
        _record("La fonction is_palindrome compare une chaîne et son inverse"),
        _record("Configuration du réseau et du pare-feu"),
    ]
    ranked = score_candidates("écrire is_palindrome pour une chaîne", records)
    assert ranked[0][0].content.startswith("La fonction is_palindrome") and len(ranked) == 1
    assert score_candidates("astronomie galactique", records) == []
    assert score_candidates("", records) == []


def test_chunks_are_bounded_and_cover_the_document() -> None:
    text = " ".join(f"Phrase numéro {i}." for i in range(600))
    chunks = chunk_text(text, size=500, overlap=50)
    assert len(chunks) > 5 and all(len(c) <= 500 for c in chunks)
    assert "Phrase numéro 0." in chunks[0] and "Phrase numéro 599." in chunks[-1]


# -- politique et isolation ------------------------------------------------------------------------------


@pytest.mark.parametrize("clone", [s for s in Subject if s not in (Subject.CORE, Subject.HUMAN)])
def test_no_clone_can_write_or_read_the_system_memory(factory, clone) -> None:
    memory = MemoryService(factory)
    with pytest.raises(PolicyDenied):
        memory.write_system(category=MemoryCategory.KNOWLEDGE, content="x", source="s", created_by=clone)
    with pytest.raises(PolicyDenied):
        memory.retrieve("x", requested_by=clone, categories=list(MemoryCategory))


def test_a_clone_private_memory_never_surfaces_in_system_retrieval(factory) -> None:
    memory = MemoryService(factory)
    memory.write(kind=MemoryKind.SEMANTIC, content="secret du clone sur palindrome", source="clone",
                 created_by=Subject.RESEARCH)
    memory.write_system(category=MemoryCategory.KNOWLEDGE, content="note publique sur palindrome",
                        source="document:d#0", created_by=Subject.HUMAN)
    found = memory.retrieve("palindrome", requested_by=Subject.CORE, categories=list(MemoryCategory))
    assert [r.record.source for r in found] == ["document:d#0"]


def test_retrieval_filters_categories_and_excluded_sources(factory) -> None:
    memory = MemoryService(factory)
    memory.write_system(category=MemoryCategory.CONVERSATION, content="tri fusion évoqué", source="conversation:a:1",
                        created_by=Subject.HUMAN)
    memory.write_system(category=MemoryCategory.KNOWLEDGE, content="tri fusion documenté", source="document:d#0",
                        created_by=Subject.HUMAN)
    only_knowledge = memory.retrieve("tri fusion", requested_by=Subject.HUMAN,
                                     categories=[MemoryCategory.KNOWLEDGE])
    assert [r.record.source for r in only_knowledge] == ["document:d#0"]
    excluded = memory.retrieve("tri fusion", requested_by=Subject.HUMAN, categories=list(MemoryCategory),
                               exclude_source_prefixes=("conversation:a:",))
    assert [r.record.source for r in excluded] == ["document:d#0"]


def test_an_empty_document_is_refused(factory) -> None:
    with pytest.raises(ValueError):
        index_document(MemoryService(factory), title="t", content="  ", document_id="d", created_by=Subject.HUMAN)


# -- TaskLoop : exécution -> indexation -> récupération -> décision --------------------------------------


class _Recorder(_FakeModelProvider):
    prompts: ClassVar[list[str]] = []

    def generate(self, request):
        type(self).prompts.append(request.prompt)
        return super().generate(request)


def _task(factory, *, function="add", description="Additionne a et b.", cases=ADD_CASES):
    spec = build_user_function_task_spec(function=function, description=description, cases=cases)
    return TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer)


def _service(factory, code):
    loop = functools.partial(run_task_loop, candidates=(a_candidate(),), registry=TaskLoopRegistry(),
                             provider_factory=lambda _c: _Recorder(mode="ok", text=code))
    return TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)


def _start(factory, task, code, **kwargs):
    return _service(factory, code).start(
        task_id=task.id, objective="o", wait=True,
        limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1), **kwargs)


def _execution_records(factory):
    return MemoryService(factory)._repo.list_system_records(categories=["execution"], limit=100)


def test_a_finished_run_is_indexed_with_its_judged_code_and_never_the_hidden_cases(factory) -> None:
    task = _task(factory)
    run = _start(factory, task, RIGHT_ADD)
    assert run.status.value == "completed"
    (record,) = _execution_records(factory)
    assert record.source == f"task_run:{run.id}" and record.category is MemoryCategory.EXECUTION
    assert "return a + b" in record.content and "dernier verdict pass" in record.content
    assert record.verification_status.value == "verified"
    for hidden in ("[-5, 5]", "_APEX_INPUTS", task.expected_answer, "[3, 0, 30]"):
        assert hidden not in record.content


def test_a_failed_run_is_remembered_as_a_failure_with_only_the_allowed_facts(factory) -> None:
    task = _task(factory)
    run = _start(factory, task, "def add(a, b):\n    return a - b\n")
    assert run.status.value != "completed"
    (record,) = _execution_records(factory)
    assert record.verification_status.value == "false" and "dernier verdict fail" in record.content
    assert "[-5, 5]" not in record.content and task.expected_answer not in record.content


def test_use_memory_injects_the_relevant_past_execution_with_its_provenance(factory) -> None:
    first = _start(factory, _task(factory), RIGHT_ADD)
    _Recorder.prompts = []
    second_task = _task(factory, function="add_three", description="Additionne a, b et c.",
                        cases=[{"args": [1, 2, 3], "expected": 6}])
    run = _start(factory, second_task, "def add_three(a, b, c):\n    return a + b + c\n", use_memory=True)
    assert run.status.value == "completed" and run.use_memory is True
    (prompt,) = _Recorder.prompts
    assert _MEMORY_HEADER in prompt and f"task_run:{first.id}" in prompt and "return a + b" in prompt
    assert prompt.index(second_task.description) < prompt.index(_MEMORY_HEADER)
    (retrieved,) = [e for e in _kinds(factory, run.id) if e["kind"] == "memory.retrieved"]
    assert [i["source"] for i in retrieved["items"]] == [f"task_run:{first.id}"]


def test_without_use_memory_nothing_is_recalled_even_if_memory_exists(factory) -> None:
    _start(factory, _task(factory), RIGHT_ADD)
    _Recorder.prompts = []
    task = _task(factory)
    run = _start(factory, task, RIGHT_ADD)
    assert _Recorder.prompts == [task.description]
    assert not [e for e in _kinds(factory, run.id) if e["kind"].startswith("memory.")]


def test_an_unavailable_memory_never_stops_the_task(factory, monkeypatch) -> None:
    def broken(*_a, **_k):
        raise RuntimeError("base mémoire indisponible")

    monkeypatch.setattr(MemoryService, "retrieve", broken)
    run = _start(factory, _task(factory), RIGHT_ADD, use_memory=True)
    assert run.status.value == "completed"
    (event,) = [e for e in _kinds(factory, run.id) if e["kind"] == "memory.unavailable"]
    assert "indisponible" in event["error"]


def test_an_indexing_failure_is_logged_and_never_changes_the_run(factory, monkeypatch) -> None:
    def broken(*_a, **_k):
        raise RuntimeError("écriture refusée")

    monkeypatch.setattr("apex.memory.indexing.index_task_run", broken)
    run = _start(factory, _task(factory), RIGHT_ADD)
    assert run.status.value == "completed" and _execution_records(factory) == []
    (event,) = [e for e in _kinds(factory, run.id) if e["kind"] == "memory.index_failed"]
    assert "écriture refusée" in event["error"]


# -- Chat : conversation -> indexation -> récupération dans une AUTRE conversation -----------------------


class TestChatMemory:
    @pytest.fixture(autouse=True)
    def _setup(self, factory):
        from apex.conversations.service import ConversationService
        from tests.test_conversations_service import _FakeProvider, a_config

        self.factory = factory
        self.service = ConversationService(factory)
        self.provider = _FakeProvider(text="Noté : le projet s'appelle Zéphyr.")
        self.config = a_config()

    def _send(self, conversation, content, **kwargs):
        return self.service.send_message(conversation.id, content=content, created_by=Subject.HUMAN,
                                         config=self.config, model_role="fast",
                                         provider_factory=lambda c: self.provider, **kwargs)

    def test_another_conversation_recalls_the_exchange_with_its_source(self) -> None:
        a = self.service.create(title="a", created_by=Subject.HUMAN)
        b = self.service.create(title="b", created_by=Subject.HUMAN)
        _, answer = self._send(a, "Mon projet s'appelle Zéphyr.")
        self._send(b, "Comment s'appelle mon projet ?")
        prompt = self.provider.received_requests[1].prompt
        assert "Souvenirs pertinents d'APEX" in prompt and f"conversation:{a.id}:{answer.id}" in prompt
        assert prompt.endswith("Comment s'appelle mon projet ?")

    def test_the_same_conversation_is_never_recalled_twice(self) -> None:
        a = self.service.create(title="a", created_by=Subject.HUMAN)
        self._send(a, "Mon projet s'appelle Zéphyr.")
        self._send(a, "Comment s'appelle mon projet ?")
        assert "Souvenirs pertinents" not in self.provider.received_requests[1].prompt

    def test_use_memory_false_neither_reads_nor_writes_the_system_memory(self) -> None:
        a = self.service.create(title="a", created_by=Subject.HUMAN)
        b = self.service.create(title="b", created_by=Subject.HUMAN)
        self._send(a, "Mon projet s'appelle Zéphyr.", use_memory=False)
        assert MemoryService(self.factory)._repo.list_system_records(categories=["conversation"], limit=10) == []
        self._send(b, "Mon projet ?")
        self._send(b, "Comment s'appelle mon projet Zéphyr ?", use_memory=False)
        assert "Souvenirs pertinents" not in self.provider.received_requests[2].prompt


# -- API ---------------------------------------------------------------------------------------------------


def test_memory_api_ingests_a_document_and_searches_it_with_provenance(factory, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from apex.api import dependencies
    from apex.api.app import create_app
    from tests.api_test_support import TEST_API_TOKEN

    monkeypatch.setattr(dependencies, "get_session_factory", lambda: factory)
    client = TestClient(create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"})
    body = client.post("/api/v1/memory/documents", json={
        "title": "Guide du déploiement", "content": "Le déploiement passe par launch.sh puis le Startup Gate.",
    }).json()["data"]
    assert body["chunks"] == 1
    found = client.get("/api/v1/memory/search", params={"q": "comment passer le Startup Gate"}).json()["data"]
    assert found[0]["source"] == f"document:{body['document_id']}#0" and found[0]["category"] == "knowledge"
    assert found[0]["score"] > 0 and "Startup Gate" in found[0]["snippet"]
    assert client.get("/api/v1/memory/search", params={"q": "x", "categories": "inconnue"}).status_code == 422
    assert client.post("/api/v1/memory/documents", json={"title": "t", "content": ""}).status_code == 422
    assert json.dumps(found)  # sérialisable tel quel
