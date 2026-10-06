"""Backend NATIF (`runtime: transformers`) : chargement, tokenisation et génération RÉELS dans le processus.

Le modèle est construit ICI (architecture Qwen2, poids aléatoires non entraînés, tokenizer BPE entraîné sur
quelques phrases) : vrais calculs PyTorch/transformers, aucune capacité revendiquée — ce test prouve le
MÉCANISME du backend (contrat, délai, annulation, graine, contexte, refus), jamais la qualité d'un modèle.
Les capacités d'un vrai modèle pré-entraîné sont mesurées ailleurs (`scripts/apex_real_validation.py`).
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("transformers")

from apex.config.schema import ModelEndpoint  # noqa: E402
from apex.errors import ModelErrorCategory, ModelProviderError, TimeoutExceeded  # noqa: E402
from apex.models import native  # noqa: E402
from apex.models.model import GenerationRequest  # noqa: E402
from apex.models.provider import get_default_provider  # noqa: E402

CHAT_TEMPLATE = (
    "{% for m in messages %}<|{{ m['role'] }}|>{{ m['content'] }}<|end|>{% endfor %}"
    "{% if add_generation_prompt %}<|assistant|>{% endif %}"
)


def build_tiny_model(directory) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM

    raw = Tokenizer(models.BPE())
    raw.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    corpus = ["def add(a, b):\n    return a + b\n", "Écris une fonction Python.", "<|user|> <|assistant|>"] * 20
    raw.train_from_iterator(corpus, trainers.BpeTrainer(
        vocab_size=300, special_tokens=["<|end|>", "<|user|>", "<|assistant|>"],
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=raw, eos_token="<|end|>", pad_token="<|end|>")
    tokenizer.chat_template = CHAT_TEMPLATE
    tokenizer.save_pretrained(directory)
    config = Qwen2Config(vocab_size=len(tokenizer), hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                         num_attention_heads=2, num_key_value_heads=1, max_position_embeddings=512,
                         eos_token_id=tokenizer.eos_token_id, pad_token_id=tokenizer.pad_token_id)
    Qwen2ForCausalLM(config).save_pretrained(directory, safe_serialization=True)


@pytest.fixture(scope="module")
def models_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("models")
    build_tiny_model(root / "tiny-qwen2")
    yield root
    native.unload_all()


def endpoint(models_dir, name="tiny-qwen2", max_context=512) -> ModelEndpoint:
    return ModelEndpoint(pool_id="local", runtime="transformers", url=f"file://{models_dir}",
                         served_model=name, max_context=max_context)


def request(ep, **kw) -> GenerationRequest:
    kw.setdefault("timeout_s", 60.0)
    return GenerationRequest(endpoint=ep, prompt=kw.pop("prompt", "Écris une fonction Python."), **kw)


def test_weights_present_make_the_backend_available_with_measured_facts(models_dir) -> None:
    report = get_default_provider(endpoint(models_dir)).detect()
    assert report.is_available and report.facts["present"] is True and report.facts["size_bytes"] > 0


def test_a_real_generation_returns_real_token_counts_and_execution_facts(models_dir) -> None:
    result = get_default_provider(endpoint(models_dir)).generate(request(endpoint(models_dir), max_tokens=12,
                                                                         temperature=0))
    assert result.tokens_prompt.value > 0 and 0 < result.tokens_completion.value <= 12
    assert result.facts["backend"] == "native_transformers" and result.facts["device"] in ("cpu", "cuda")
    assert result.model_used == "tiny-qwen2" and result.finish_reason in ("stop", "length")


def test_a_seed_makes_sampling_reproducible(models_dir) -> None:
    provider = get_default_provider(endpoint(models_dir))
    texts = [provider.generate(request(endpoint(models_dir), max_tokens=20, temperature=1.0, seed=s)).text
             for s in (7, 7, 8)]
    assert texts[0] == texts[1]


def test_greedy_decoding_is_deterministic(models_dir) -> None:
    provider = get_default_provider(endpoint(models_dir))
    a = provider.generate(request(endpoint(models_dir), max_tokens=15, temperature=0)).text
    b = provider.generate(request(endpoint(models_dir), max_tokens=15, temperature=0)).text
    assert a == b


def test_the_timeout_stops_generation_and_is_reported(models_dir) -> None:
    with pytest.raises(TimeoutExceeded):
        get_default_provider(endpoint(models_dir)).generate(
            request(endpoint(models_dir), max_tokens=400, temperature=1.0, seed=1, timeout_s=0.0001))


def test_a_cancelled_request_never_generates(models_dir) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ModelProviderError, match="annulée"):
        get_default_provider(endpoint(models_dir)).generate(request(endpoint(models_dir), cancel_event=cancel))


def test_a_prompt_longer_than_the_context_is_refused(models_dir) -> None:
    ep = endpoint(models_dir, max_context=8)
    with pytest.raises(ModelProviderError) as info:
        get_default_provider(ep).generate(request(ep, prompt="def add(a, b): return a + b " * 20))
    assert info.value.category is ModelErrorCategory.CONTEXT_TOO_LONG


@pytest.mark.parametrize("name", ["absent", "../tiny-qwen2", "/etc", ".hidden"])
def test_missing_weights_or_escaping_names_are_unavailable_never_downloaded(models_dir, name) -> None:
    provider = get_default_provider(endpoint(models_dir, name=name))
    assert not provider.detect().is_available
    with pytest.raises(ModelProviderError):
        provider.generate(request(endpoint(models_dir, name=name)))


def test_a_non_file_url_is_unsupported(models_dir) -> None:
    ep = ModelEndpoint(pool_id="local", runtime="transformers", url="http://127.0.0.1:1",
                       served_model="tiny-qwen2", max_context=512)
    assert get_default_provider(ep).detect().status.value == "unsupported"


def test_the_real_task_loop_drives_the_native_backend_end_to_end(models_dir, tmp_path) -> None:
    """Aucune doublure : `provider_factory` par défaut -> `get_default_provider` -> backend natif ; vraie
    sonde de disponibilité, vraie sandbox, vrai Judge, vraie base. Le modèle n'est PAS entraîné : sa sortie est
    du bruit et le Judge doit le refuser — on vérifie la traversée, jamais une réussite."""
    import functools

    from apex.agents.runtime import AgentRuntime
    from apex.models.registry import ModelCandidate
    from apex.policy.model import Subject
    from apex.taskloop.loop import run_task_loop
    from apex.taskloop.registry import TaskLoopRegistry
    from apex.taskloop.request import TaskLimits
    from apex.taskloop.runs import TaskRunService
    from apex.tasks.model import TaskSplit
    from apex.tasks.service import TaskBankService
    from apex.tasks.solution_tasks import build_user_function_task_spec
    from tests.test_taskloop_execution import a_pool, a_ref, sqlite_session_factory

    factory = sqlite_session_factory()
    spec = build_user_function_task_spec(function="add", description="Additionne a et b.",
                                         cases=[{"args": [1, 2], "expected": 3}])
    task = TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer)
    candidate = ModelCandidate(role="coder_main", ref=a_ref(family="tiny-qwen2"), resident=False,
                               endpoint=endpoint(models_dir))
    loop = functools.partial(run_task_loop, candidates=(candidate,), registry=TaskLoopRegistry())
    run = TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop).start(
        task_id=task.id, objective="o", wait=True, generation_timeout_s=60,
        limits=TaskLimits(max_attempts=2, max_retries=1, max_candidates=1))
    assert run.status.value == "failed" and len(run.eval_run_ids) == 2
    assert [a["judge_result"] for a in run.attempts] == [None, None]
    assert run.providers_tried == (candidate.ref.id,)
