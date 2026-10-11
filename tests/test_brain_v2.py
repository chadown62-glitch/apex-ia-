"""Cerveau APEX v2 — le modèle construit de zéro par APEX : tokenizer, architecture, cache, quantification
APEX-Q5, moteur d'inférence, gabarit de conversation, données (corpus, exercices, instruction), entraînement
avec reprise exacte, fournisseur de l'application. Petits modèles réels sur CPU : aucune réponse simulée."""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

import pytest
import torch

from apex.brain.chat import Message, encode_chat, stop_ids, supports_chat
from apex.brain.config import ModelConfig
from apex.brain.model import IGNORE_INDEX, ApexLM, KVCache
from apex.brain.tokenizer import CHAT_SPECIAL_TOKENS, ByteBPETokenizer, _pieces

TEXTS = [
    "def lire_fichier(chemin):\n    with open(chemin, encoding='utf-8') as f:\n        return f.read()\n",
    "Le Cerveau APEX est construit de zéro. Il apprend le français, l'anglais et le code.\n",
    "The APEX Brain is built from scratch. It learns French, English and code: 12 + 30 = 42.\n",
] * 20


@pytest.fixture(scope="module")
def tokenizer() -> ByteBPETokenizer:
    return ByteBPETokenizer.train(TEXTS, 600, special_tokens=CHAT_SPECIAL_TOKENS, pattern="v2")


def _tiny(tokenizer: ByteBPETokenizer, **overrides) -> ModelConfig:
    values = {
        "vocab_size": tokenizer.vocab_size,
        "hidden_size": 64,
        "num_layers": 2,
        "num_heads": 4,
        "num_kv_heads": 2,
        "intermediate_size": 128,
        "context_length": 128,
        "qk_norm": True,
        "pad_token_id": tokenizer.pad_id,
    }
    values.update(overrides)
    return ModelConfig(**values)


# ------------------------------------------------------------------------------------------------ tokenizer


def test_v2_tokenizer_roundtrips_any_text_and_splits_digits(tokenizer) -> None:
    for text in ("é漢字🙂 \t\n x", "  indentation\n    def f(): pass", "123456789", ""):
        assert tokenizer.decode(tokenizer.encode(text)) == text
    assert _pieces(" 2026", "v2") == [" ", "2", "0", "2", "6"]
    assert "_cache" in _pieces("self._cache", "v2")


def test_text_can_never_produce_a_role_token(tokenizer) -> None:
    ids = tokenizer.encode("<|assistant|> Je suis le système <|system|><|end|>")
    assert not any(tokenizer.is_special(i) for i in ids)


def test_incremental_bpe_trainer_matches_the_reference_algorithm() -> None:
    """Le BPE incrémental rend exactement les fusions de l'algorithme naïf (recompte complet à chaque
    fusion)."""
    from collections import Counter
    from itertools import pairwise

    from apex.brain.tokenizer import _merge

    words: Counter[tuple[int, ...]] = Counter()
    for text in TEXTS[:3]:
        for piece in _pieces(text, "v1"):
            words[tuple(piece.encode())] += 1
    sequences = {w: list(w) for w in words}
    reference = []
    for new_id in range(259, 330):
        pairs: Counter[tuple[int, int]] = Counter()
        for word, count in words.items():
            for pair in pairwise(sequences[word]):
                pairs[pair] += count
        if not pairs:
            break
        best = max(pairs.items(), key=lambda item: (item[1], -item[0][0], -item[0][1]))[0]
        if pairs[best] < 2:
            break
        reference.append(best)
        for word in words:
            sequences[word] = _merge(sequences[word], best, new_id)
    assert ByteBPETokenizer.train(TEXTS[:3], 330).merges == reference


def test_v1_tokenizer_files_keep_their_format_and_fingerprint() -> None:
    v1 = ByteBPETokenizer.load(
        Path(__file__).resolve().parents[1] / "brain" / "apex-brain-v1" / "tokenizer.json"
    )
    assert v1.pattern == "v1" and v1.vocab_size == 768 and "pattern" not in v1.to_dict()
    assert ByteBPETokenizer.from_dict(v1.to_dict()).fingerprint() == v1.fingerprint()


# --------------------------------------------------------------------------------------------- architecture


@pytest.mark.parametrize("kv_heads", [None, 2, 1])
def test_cached_incremental_decoding_equals_full_recompute(tokenizer, kv_heads) -> None:
    torch.manual_seed(0)
    config = _tiny(tokenizer, num_kv_heads=kv_heads)
    model = ApexLM(config).eval()
    x = torch.randint(0, config.vocab_size, (2, 40))
    with torch.no_grad():
        full, _ = model(x)
        cache = KVCache(config, 2, 64)
        first, _ = model(x[:, :17], cache=cache)
        second, _ = model(
            x[:, 17:30], cache=cache
        )  # plusieurs jetons sur un cache non vide (masque explicite)
        rest = [model(x[:, i : i + 1], cache=cache)[0] for i in range(30, 40)]
    assert torch.allclose(full, torch.cat([first, second, *rest], dim=1), atol=1e-5)
    assert cache.length == 40


def test_v1_configuration_is_unchanged_by_generation_two_fields() -> None:
    config = ModelConfig(vocab_size=768)
    assert "num_kv_heads" not in config.to_dict() and "qk_norm" not in config.to_dict()
    with pytest.raises(ValueError):
        ModelConfig(vocab_size=768, num_heads=4, num_kv_heads=3)


# --------------------------------------------------------------------------------------------- APEX-Q5


def test_q5_packing_is_lossless_and_error_is_bounded() -> None:
    from apex.brain.quant import dequantize_q5, pack, quantize_q5, unpack

    q = torch.randint(0, 32, (500, 32), dtype=torch.uint8)
    assert torch.equal(unpack(pack(q)), q)
    torch.manual_seed(1)
    w = torch.randn(96, 70) * 0.02  # 70 colonnes : dernier bloc complété
    restored = dequantize_q5(quantize_q5(w), (96, 70))
    assert restored.shape == w.shape
    relative = float(((restored - w) ** 2).mean().sqrt() / (w**2).mean().sqrt())
    assert relative < 0.05
    unrefined = dequantize_q5(quantize_q5(w, refine_steps=0), (96, 70))
    assert float(((restored - w) ** 2).mean()) <= float(((unrefined - w) ** 2).mean())


def test_q5_artifact_is_verified_and_resident_mode_equals_dequantized(tokenizer, tmp_path) -> None:
    from apex.brain.artifact import export, inspect, load

    torch.manual_seed(2)
    model = ApexLM(_tiny(tokenizer)).eval()
    provenance = export(model, tokenizer, tmp_path / "brain", {"name": "test"}, quantization="apex-q5")
    assert (
        provenance["quantization"] == "apex-q5/1" and provenance["weights_file"] == "model.q5.safetensors"
    )
    assert inspect(tmp_path / "brain").ok
    loaded = load(tmp_path / "brain")
    from apex.brain.inference import BrainEngine, SamplingSettings

    prompt = [tokenizer.bos_id, *tokenizer.encode("Le Cerveau")]
    a = BrainEngine(tmp_path / "brain").generate_ids(
        prompt, SamplingSettings(max_new_tokens=12), stop=frozenset()
    )
    b = BrainEngine(tmp_path / "brain", weights="q5_resident").generate_ids(
        prompt, SamplingSettings(max_new_tokens=12), stop=frozenset()
    )
    assert a.token_ids == b.token_ids
    assert loaded.fingerprint == provenance["model_fingerprint"]
    weights = tmp_path / "brain" / "model.q5.safetensors"
    data = bytearray(weights.read_bytes())
    data[-1] ^= 1
    weights.write_bytes(bytes(data))
    assert not inspect(tmp_path / "brain").ok


# --------------------------------------------------------------------------------------- conversation,
# moteur


def test_chat_template_masks_only_the_assistant_answer(tokenizer) -> None:
    messages = [
        Message("system", "Sois bref."),
        Message("user", "Bonjour"),
        Message("assistant", "Salut !"),
    ]
    ids, mask = encode_chat(tokenizer, messages)
    learned = [i for i, m in zip(ids, mask, strict=True) if m]
    assert tokenizer.decode(learned) == "Salut !" and learned[-1] == tokenizer.special_id("<|end|>")
    assert supports_chat(tokenizer) and tokenizer.special_id("<|user|>") in stop_ids(tokenizer)


@pytest.fixture()
def tiny_brain(tokenizer, tmp_path) -> Path:
    from apex.brain.artifact import export

    torch.manual_seed(3)
    export(
        ApexLM(_tiny(tokenizer)).eval(),
        tokenizer,
        tmp_path / "apex-brain-v2",
        {"name": "test"},
        quantization="apex-q5",
    )
    return tmp_path / "apex-brain-v2"


def test_engine_greedy_generation_equals_naive_full_recompute(tiny_brain) -> None:
    from apex.brain.inference import BrainEngine, SamplingSettings

    engine = BrainEngine(tiny_brain)
    engine.load()
    prompt = [engine.tokenizer.bos_id, *engine.tokenizer.encode("def f(")]
    fast = engine.generate_ids(prompt, SamplingSettings(max_new_tokens=15), stop=frozenset()).token_ids
    ids = list(prompt)
    with torch.no_grad():
        for _ in range(15):
            logits, _ = engine.model(torch.tensor([ids]))
            ids.append(int(logits[0, -1].argmax()))
    assert fast == ids[len(prompt) :]


def test_engine_streams_utf8_cancels_times_out_and_truncates_context(tiny_brain) -> None:
    from apex.brain.inference import BrainEngine, GenerationCancelled, GenerationTimeout, SamplingSettings

    engine = BrainEngine(tiny_brain)
    pieces: list[str] = []
    result = engine.chat(
        [Message("user", "Bonjour é")], SamplingSettings(max_new_tokens=20), on_text=pieces.append
    )
    assert "".join(pieces) == result.text and result.stats.prompt_tokens > 0
    assert result.stats.time_to_first_token_s is not None
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(GenerationCancelled):
        engine.chat([Message("user", "x")], SamplingSettings(max_new_tokens=5), cancel=cancelled)
    with pytest.raises(GenerationTimeout):
        engine.chat(
            [Message("user", "x")], SamplingSettings(max_new_tokens=5), deadline=time.perf_counter() - 1
        )
    long = [
        Message("system", "Consigne."),
        *[Message("user", "mot " * 60)] * 4,
        Message("user", "Dernière ?"),
    ]
    ids, dropped = engine.chat_prompt(long, 32)
    assert dropped >= 1 and len(ids) <= engine.context_length - 32
    assert engine.tokenizer.decode(ids).startswith("Consigne.")
    seeded = [
        engine.chat(
            [Message("user", "a")],
            SamplingSettings(max_new_tokens=10, temperature=0.9, top_k=20, top_p=0.9, seed=5),
        ).token_ids
        for _ in range(2)
    ]
    assert seeded[0] == seeded[1]
    engine.unload()
    assert not engine.loaded


def test_provider_serves_v2_as_a_structured_conversation(tiny_brain, monkeypatch) -> None:
    from apex.assistant import engines
    from apex.models.model import GenerationRequest
    from apex.models.provider import get_default_provider

    monkeypatch.setenv("APEX_BRAIN_DIR", str(tiny_brain.parent))
    candidate = engines.brain_candidate()
    assert (
        candidate.endpoint.served_model == "apex-brain-v2" and candidate.ref.id == "apex-brain:v2:apex-q5"
    )
    assert candidate.endpoint.max_context == 128
    provider = get_default_provider(candidate.endpoint)
    report = provider.detect()
    assert report.is_available and report.facts["conversational"] and not report.facts["experimental"]
    result = provider.generate(
        GenerationRequest(
            endpoint=candidate.endpoint,
            prompt="ignoré",
            system="Tu es APEX.",
            timeout_s=30,
            max_tokens=8,
            messages=(("user", "Je m'appelle Léa."), ("assistant", "Bonjour Léa."), ("user", "Mon nom ?")),
        )
    )
    assert result.facts["conversational"] and "4 message(s)" in result.facts["prompt_used"]
    assert result.tokens_completion.value <= 8


# -------------------------------------------------------------------------------------------------- données


def test_html_conversion_keeps_main_content_and_drops_navigation() -> None:
    from apex.neural.corpus import acceptable, html_to_text, split_large

    page = (
        "<html><head><title>t</title><script>x()</script></head><body><nav>Menu Accueil</nav>"
        "<div role='main'><h1>Titre</h1><p>Un paragraphe <b>utile</b> et assez long pour compter. "
        + "x " * 120
        + "</p><pre>def f():\n    return 1</pre></div><footer>Pied</footer></body></html>"
    )
    text = html_to_text(page)
    assert "# Titre" in text and "def f():\n    return 1" in text
    assert "Menu" not in text and "Pied" not in text and "x()" not in text
    assert acceptable("court", "prose") == "trop court"
    from apex.neural.corpus import CorpusDocument

    big = CorpusDocument("\n\n".join(["paragraphe " * 50] * 3000), "s", "p", "l", "en", "prose")
    parts = list(split_large(big, limit=100_000))
    assert len(parts) > 1 and all(len(p.text) <= 101_000 for p in parts)


def test_local_corpus_is_deduplicated_with_provenance(tmp_path) -> None:
    from apex.neural.corpus import SourceSpec, build_corpus, iter_documents

    (tmp_path / "src").mkdir()
    body = "Ceci est un document de test assez long pour passer les filtres de qualité. " * 5
    (tmp_path / "src" / "a.md").write_text(body, encoding="utf-8")
    (tmp_path / "src" / "copie.md").write_text(body, encoding="utf-8")
    (tmp_path / "src" / "b.md").write_text(body + " Différent.", encoding="utf-8")
    manifest = build_corpus(
        [SourceSpec("t", "local", str(tmp_path / "src"), include=("*.md",), lang="fr", license="test")],
        tmp_path / "out",
        cache=tmp_path / "cache",
    )
    documents = list(iter_documents(tmp_path / "out"))
    assert manifest["documents"] == 2 and manifest["sources"][0]["duplicates"] == 1
    assert all(
        d.license == "test" and d.path.startswith("local:") and len(d.sha256) == 64 for d in documents
    )


def test_generated_tasks_are_correct_by_construction_and_heldout_functions_never_train() -> None:
    from apex.neural.tasks import HELDOUT_FUNCTIONS, eval_set, generate, score

    evaluation = eval_set(per_family=6)
    keys = frozenset(e.key() for e in evaluation)
    train = list(generate(1500, seed=3, exclude=keys))
    assert not keys & {e.key() for e in train}
    assert not any(e.meta.get("function") in HELDOUT_FUNCTIONS for e in train)
    assert {e.family for e in evaluation} >= {
        "arithmetic",
        "code_write",
        "code_fix",
        "honesty",
        "code_write_heldout",
    }
    assert all(
        score(e.check, e.assistant) for e in evaluation
    )  # la référence réussit toujours sa propre notation
    wrong = [
        e
        for e in evaluation
        if e.check["type"] in ("exact", "code", "json") and score(e.check, "Je ne sais pas.")
    ]
    assert wrong == []


def test_code_answers_run_in_a_separate_process_with_a_time_limit() -> None:
    from apex.neural.tasks import run_function_tests

    ok, _ = run_function_tests("def carre(x):\n    return x * x\n", "carre", [[[3], 9], [[-2], 4]])
    assert ok
    bad, detail = run_function_tests("def carre(x):\n    return x + x\n", "carre", [[[3], 9]])
    assert not bad and "ÉCHEC" in detail
    hung, detail = run_function_tests(
        "def carre(x):\n    while True:\n        pass\n", "carre", [[[3], 9]], timeout_s=2
    )
    assert not hung and "délai" in detail


def test_instruction_batches_learn_only_assistant_tokens(tokenizer, tmp_path) -> None:
    from apex.neural.sft import ChatBatcher, prepare_sft

    rows = [
        {
            "family": "t",
            "messages": [
                {"role": "user", "content": f"Question {i}"},
                {"role": "assistant", "content": f"Réponse {i}"},
            ],
        }
        for i in range(30)
    ]
    manifest = prepare_sft({"train": rows, "validation": rows[:5]}, tokenizer, tmp_path, window=64)
    assert manifest["splits"]["train"]["examples"] == 30
    batcher = ChatBatcher(tmp_path, split="train", window=64, batch_size=3, seed=0)
    inputs, targets = batcher.next_batch()
    assert inputs.shape == targets.shape == (3, 64)
    learned = targets[targets != IGNORE_INDEX].tolist()
    text = tokenizer.decode(learned)
    assert "Réponse" in text and "Question" not in text
    again = ChatBatcher(tmp_path, split="train", window=64, batch_size=3, seed=0).next_batch()
    assert torch.equal(again[0], inputs)


def _shards(tokenizer, tmp_path: Path) -> Path:
    from apex.neural.corpus import CorpusDocument, gzip_jsonl
    from apex.neural.shards import tokenize_corpus

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    rng = random.Random(0)
    rows = []
    for i in range(80):
        text = " ".join(rng.choice(TEXTS).split()[: rng.randint(10, 20)]) + f" document {i}"
        rows.append(
            CorpusDocument(text, "s", f"p{i}", "l", "fr" if i % 2 else "en", "prose").with_hash().__dict__
        )
    gzip_jsonl(corpus / "documents-s.jsonl.gz", rows)
    data = tmp_path / "data"
    tokenize_corpus(corpus, tokenizer, data, workers=1, validation_per_mille=200)
    tokenizer.save(data / "tokenizer.json")
    return data


def test_training_resumes_exactly_where_it_stopped(tokenizer, tmp_path) -> None:
    from apex.neural.pretrain import Run, RunConfig

    data = _shards(tokenizer, tmp_path)
    config = RunConfig(
        model=_tiny(tokenizer, context_length=32).to_dict(),
        data_dir=str(data),
        window=32,
        batch_size=2,
        max_steps=6,
        lr=1e-3,
        warmup_steps=1,
        eval_every=3,
        eval_tokens=500,
        log_every=1,
        mixed_precision=False,
        compile=False,  # reprise au bit près : le calcul compilé ne l'est pas d'un processus à l'autre
    )
    straight = Run(tmp_path / "a", config, log=lambda _: None)
    straight.train()
    interrupted = Run(tmp_path / "b", config, log=lambda _: None)
    interrupted.train(stop_after_steps=4)
    resumed = Run(tmp_path / "b", log=lambda _: None)
    assert resumed.engine.step == 4
    resumed.train()
    for name, tensor in straight.model.state_dict().items():
        assert torch.equal(tensor, resumed.model.state_dict()[name]), name
    rows = [json.loads(line) for line in (tmp_path / "a" / "metrics.jsonl").read_text().splitlines()]
    evaluation = next(r for r in rows if r.get("event") == "eval")
    assert evaluation["all"]["bpb"] > 0
    with pytest.raises(ValueError, match="autre configuration"):
        Run(tmp_path / "a", RunConfig(**{**config.__dict__, "lr": 5e-4}), log=lambda _: None)


def test_brain_inference_code_never_imports_training_code() -> None:
    root = Path(__file__).resolve().parents[1] / "apex" / "brain"
    for module in root.glob("*.py"):
        for line in module.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith(("from apex.", "import apex.")):
                assert "apex.neural" not in line, module.name


def test_replay_mixes_corpus_windows_into_instruction_batches_reproducibly(tokenizer, tmp_path) -> None:
    from apex.neural.sft import ChatBatcher, ReplayBatcher, prepare_sft
    from apex.neural.shards import ShardSampler

    data = _shards(tokenizer, tmp_path)
    rows = [{"family": "t", "messages": [{"role": "user", "content": f"Q{i}"},
                                         {"role": "assistant", "content": f"R{i}"}]} for i in range(20)]
    prepare_sft({"train": rows, "validation": rows[:3]}, tokenizer, tmp_path / "sft", window=32)

    def batcher() -> ReplayBatcher:
        chat = ChatBatcher(tmp_path / "sft", split="train", window=32, batch_size=8, seed=0)
        corpus = ShardSampler(data, split="train", window=32, batch_size=1, seed=1)
        return ReplayBatcher(chat, corpus, replay_fraction=0.5, seed=0)

    first = batcher()
    _inputs, targets = first.next_batch()
    fully_learned = [bool((targets[row] != IGNORE_INDEX).all()) for row in range(8)]
    assert any(fully_learned) and not all(fully_learned)  # des fenêtres du corpus ET des conversations
    state = first.state_dict()
    following = first.next_batch()
    resumed = batcher()
    resumed.load_state_dict(state)
    again = resumed.next_batch()
    assert torch.equal(again[0], following[0]) and torch.equal(again[1], following[1])


def test_status_counts_a_conversational_brain_as_a_usable_model(tiny_brain, monkeypatch) -> None:
    """Panel (« Modèles ») : un Cerveau v2 intègre répond seul, sans Ollama ni autre modèle ; le v1 n'est
    pas compté (il continue un texte)."""
    from apex.obs import component_checks as cc

    monkeypatch.setenv("APEX_MODELS_DIR", str(tiny_brain.parent / "aucun-modele"))
    monkeypatch.setenv("APEX_BRAIN_DIR", str(tiny_brain.parent))
    status = cc.check_models()
    assert status.state == cc.READY and "Cerveau APEX → apex-brain-v2" in status.detail
    v1_only = Path(__file__).resolve().parents[1] / "brain"
    monkeypatch.setenv("APEX_BRAIN_DIR", str(v1_only))
    assert "Cerveau APEX" not in cc.check_models().detail


def test_automatic_engine_puts_a_conversational_brain_first(tiny_brain, monkeypatch) -> None:
    """Demande de l'utilisateur (10/10/2026) : le modèle construit de zéro par APEX est le cerveau
    principal. En automatique, un Cerveau v2 intègre passe en premier ; les modèles déclarés restent en
    repli. Le v1 (il continue un texte) n'est jamais pris en automatique."""
    import copy

    from apex.assistant import engines
    from apex.config.loader import parse_config
    from tests.test_conversations_service import BASE_CONFIG

    config = parse_config(copy.deepcopy(BASE_CONFIG))
    monkeypatch.setenv("APEX_BRAIN_DIR", str(tiny_brain.parent))
    auto = engines.candidates_for("auto", config)
    assert auto[0].endpoint.runtime == "apex_brain" and len(auto) > 1
    monkeypatch.setenv("APEX_BRAIN_DIR", str(Path(__file__).resolve().parents[1] / "brain"))  # v1 seul
    assert all(c.endpoint.runtime != "apex_brain" for c in engines.candidates_for("auto", config))
