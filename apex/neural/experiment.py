"""Expérience d'apprentissage de bout en bout, reproductible :

    Knowledge Agent (sources réelles) -> candidats -> dataset (train / validation / held-out)
    -> tokenizer APEX (train seulement) -> V0 (initialisation) -> entraînement -> V1
    -> évaluation séparée -> Judge (V1 vs V0)
    -> V2 = V1 entraîné sur un flux corrompu -> Judge (V2 vs V1) : doit être REJETÉ
    -> tentative de comparaison sur un benchmark modifié : doit être REFUSÉE

Chaque version est un checkpoint complet. Le résultat est un dictionnaire
JSON-sérialisable : configurations, empreintes (dataset, tokenizer,
benchmarks, poids), courbes de loss, mesures par suite et verdicts."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import torch

from apex.knowledge.acquisition import (
    FileAcquirer,
    LocalDirectoryDiscovery,
    PythonDocsAcquirer,
    PythonDocsDiscovery,
)
from apex.knowledge.export import to_training_candidates
from apex.knowledge.model import SourceKind
from apex.knowledge.pipeline import AgentConfig, KnowledgeAgent
from apex.neural.checkpoint import load_checkpoint, save_checkpoint
from apex.neural.config import ModelConfig, OptimizerSpec, SchedulerSpec, TrainingConfig
from apex.neural.dataset import Split, build_dataset
from apex.neural.evaluation import Benchmark, EvalReport, NeuralJudge, evaluate_suites
from apex.neural.model import ApexLM
from apex.neural.tokenizer import ByteBPETokenizer
from apex.neural.training import TokenBatcher, TrainingEngine

STDLIB_MODULES: tuple[str, ...] = (
    "abc",
    "argparse",
    "ast",
    "asyncio",
    "base64",
    "bisect",
    "calendar",
    "cmd",
    "codecs",
    "collections",
    "colorsys",
    "configparser",
    "contextlib",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "difflib",
    "dis",
    "email",
    "enum",
    "filecmp",
    "fileinput",
    "fnmatch",
    "fractions",
    "ftplib",
    "functools",
    "getopt",
    "gettext",
    "glob",
    "graphlib",
    "gzip",
    "hashlib",
    "heapq",
    "hmac",
    "html",
    "http",
    "imaplib",
    "inspect",
    "io",
    "ipaddress",
    "itertools",
    "json",
    "keyword",
    "linecache",
    "locale",
    "logging",
    "lzma",
    "mailbox",
    "mimetypes",
    "numbers",
    "operator",
    "optparse",
    "os",
    "pathlib",
    "pdb",
    "pickle",
    "pkgutil",
    "platform",
    "plistlib",
    "pprint",
    "profile",
    "pstats",
    "queue",
    "quopri",
    "random",
    "re",
    "reprlib",
    "sched",
    "secrets",
    "selectors",
    "shelve",
    "shlex",
    "shutil",
    "signal",
    "smtplib",
    "socket",
    "socketserver",
    "sqlite3",
    "ssl",
    "stat",
    "statistics",
    "string",
    "struct",
    "subprocess",
    "tarfile",
    "tempfile",
    "textwrap",
    "threading",
    "timeit",
    "token",
    "tokenize",
    "trace",
    "traceback",
    "tracemalloc",
    "types",
    "typing",
    "unittest",
    "urllib",
    "uuid",
    "warnings",
    "wave",
    "weakref",
    "webbrowser",
    "zipfile",
    "zipimport",
)


@dataclass(frozen=True)
class ExperimentConfig:
    modules: tuple[str, ...] = STDLIB_MODULES
    guard_docs: Path | None = None
    # "guard" : la documentation d'APEX sert de garde hors domaine, jamais
    # entraînée ; "train" : elle entre dans le dataset (découpée par source)
    # et le held-out est aussi mesuré par domaine.
    docs_mode: str = "guard"
    dropout: float = 0.0
    weight_decay: float = 0.1
    chunk_words: int = 120
    vocab_size: int = 768
    hidden_size: int = 192
    num_layers: int = 4
    num_heads: int = 6
    intermediate_size: int = 512
    context_length: int = 128
    batch_size: int = 16
    train_steps: int = 600
    corrupt_steps: int = 200
    lr: float = 3e-3
    seed: int = 0
    log_every: int = 50
    extra: dict[str, Any] = field(default_factory=dict)


def _tokens(tokenizer: ByteBPETokenizer, texts: list[str]) -> list[int]:
    stream: list[int] = []
    for text in texts:
        stream.extend(tokenizer.encode(text, add_bos=True, add_eos=True))
    return stream


def _summaries(reports: dict[str, EvalReport]) -> dict[str, object]:
    return {name: report.to_dict() for name, report in reports.items()}


def _train(
    engine: TrainingEngine, batcher: TokenBatcher, steps: int, log_every: int
) -> list[dict[str, float]]:
    curve: list[dict[str, float]] = []
    for _ in range(steps):
        result = engine.train_step(batcher)
        if result.step % log_every == 0 or result.step == 1:
            curve.append(
                {
                    "step": result.step,
                    "loss": round(result.loss, 4),
                    "lr": result.lr,
                    "grad_norm": round(result.grad_norm, 4),
                }
            )
    return curve


def run_experiment(
    config: ExperimentConfig, out_dir: Path, *, log: Callable[[str], None] = print
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    started = time.perf_counter()
    torch.manual_seed(config.seed)

    # 1. Connaissances : sources réelles, curation, candidats.
    agent = KnowledgeAgent(
        [PythonDocsAcquirer(), FileAcquirer()], AgentConfig(chunk_words=config.chunk_words)
    )
    if config.docs_mode not in ("guard", "train"):
        raise ValueError(f"docs_mode inconnu : {config.docs_mode}")
    discoveries = PythonDocsDiscovery(config.modules).discover()
    if config.docs_mode == "train" and config.guard_docs is not None:
        discoveries += LocalDirectoryDiscovery(
            config.guard_docs, ("*.md",), SourceKind.DOCUMENTATION, "apex-own"
        ).discover()
    report = agent.run(discoveries)
    candidates = to_training_candidates(report.items, chunk_words=config.chunk_words)
    timings["knowledge_s"] = time.perf_counter() - started
    log(f"knowledge: {report.decisions} -> {len(candidates)} candidats")

    # 2. Dataset : découpage par source, contrôle de fuite, empreinte.
    dataset = build_dataset(
        candidates, validation_ratio=0.1, test_ratio=0.15, salt=f"apex-{config.seed}", decontaminate=True
    )
    train_texts = dataset.texts(Split.TRAIN)
    log(
        f"dataset: { ({s.value: len(dataset.splits[s]) for s in Split}) } "
        f"fuite max {dataset.leakage_ratio:.3f}"
    )

    # 3. Tokenizer APEX, entraîné sur le TRAIN uniquement.
    t0 = time.perf_counter()
    tokenizer = ByteBPETokenizer.train(train_texts, config.vocab_size)
    train_tokens = _tokens(tokenizer, train_texts)
    timings["tokenizer_s"] = time.perf_counter() - t0

    suites = [
        Benchmark.from_dataset("heldout", dataset, Split.TEST),
        Benchmark.from_dataset("validation", dataset, Split.VALIDATION),
    ]
    guard_report: dict[str, int] = {}
    if config.docs_mode == "train":
        # Tranches par domaine du MÊME held-out : une moyenne ne masque pas
        # la régression d'un domaine.
        for name, prefix in (("heldout_stdlib", "pydoc:"), ("heldout_apex_docs", "file://")):
            docs = tuple(
                (c.candidate_id, c.text)
                for c in dataset.splits[Split.TEST]
                if c.source_id.startswith(prefix)
            )
            if len(docs) >= 5:
                suites.append(Benchmark(name, docs))
    elif config.guard_docs is not None:
        # Garde hors domaine : documentation d'APEX, jamais vue à l'entraînement.
        guard_agent = KnowledgeAgent([FileAcquirer()], AgentConfig(chunk_words=config.chunk_words))
        guard = guard_agent.run(
            LocalDirectoryDiscovery(
                config.guard_docs, ("*.md",), SourceKind.DOCUMENTATION, "apex-own"
            ).discover()
        )
        guard_report = guard.decisions
        guard_candidates = to_training_candidates(guard.items, chunk_words=config.chunk_words)
        suites.append(
            Benchmark("guard_apex_docs", tuple((c.candidate_id, c.text) for c in guard_candidates[:60]))
        )
    train_sample = Benchmark(
        "train_sample", tuple((c.candidate_id, c.text) for c in dataset.splits[Split.TRAIN][:60])
    )

    model_config = ModelConfig(
        vocab_size=tokenizer.vocab_size,
        hidden_size=config.hidden_size,
        num_layers=config.num_layers,
        num_heads=config.num_heads,
        intermediate_size=config.intermediate_size,
        context_length=config.context_length,
        dropout=config.dropout,
    )
    training_config = TrainingConfig(
        max_steps=config.train_steps,
        batch_size=config.batch_size,
        seed=config.seed,
        optimizer=OptimizerSpec(lr=config.lr, weight_decay=config.weight_decay),
        scheduler=SchedulerSpec(name="cosine", warmup_steps=max(1, config.train_steps // 20)),
    )
    provenance = dataset.provenance()

    def checkpoint(name: str, engine: TrainingEngine, batcher: TokenBatcher) -> str:
        return save_checkpoint(
            out_dir / f"{name}.pt",
            engine,
            tokenizer=tokenizer,
            batcher=batcher,
            dataset_fingerprint=dataset.fingerprint,
            dataset_provenance=provenance,
            dataset_tokens=len(train_tokens),
        )

    # 4. V0 : le modèle APEX avant tout apprentissage.
    model = ApexLM(model_config)
    engine = TrainingEngine(model, training_config)
    batcher = TokenBatcher(
        train_tokens, window=config.context_length, batch_size=config.batch_size, seed=config.seed
    )
    v0_sha = checkpoint("v0", engine, batcher)
    v0 = evaluate_suites(model, tokenizer, suites)
    v0_train = evaluate_suites(model, tokenizer, [train_sample])
    log(
        f"V0 {model.num_parameters()} paramètres : "
        f"{ ({k: round(r.bits_per_byte, 4) for k, r in v0.items()}) }"
    )

    # 5. V1 : entraînement réel (forward, loss, backward, optimizer.step).
    t0 = time.perf_counter()
    curve_v1 = _train(engine, batcher, config.train_steps, config.log_every)
    timings["train_v1_s"] = time.perf_counter() - t0
    v1_sha = checkpoint("v1", engine, batcher)
    v1 = evaluate_suites(model, tokenizer, suites)
    v1_train = evaluate_suites(model, tokenizer, [train_sample])
    log(f"V1 : { ({k: round(r.bits_per_byte, 4) for k, r in v1.items()}) } en {timings['train_v1_s']:.1f}s")

    judge = NeuralJudge(primary="heldout", seed=config.seed)
    verdict_v1 = judge.judge(v0, v1)
    log(f"Judge V1 vs V0 : {verdict_v1.decision.value}")

    # 6. V2 : reprise de V1 sur un flux corrompu (tokens mélangés) -> régression attendue.
    corrupt = torch.tensor(train_tokens)[
        torch.randperm(len(train_tokens), generator=torch.Generator().manual_seed(1))
    ]
    engine_v1, _, info_v1 = load_checkpoint(out_dir / "v1.pt")
    corrupt_batcher = TokenBatcher(
        corrupt.tolist(), window=config.context_length, batch_size=config.batch_size, seed=config.seed + 1
    )
    # Nouveau moteur à taux constant, initialisé avec les poids de V1.
    model_v2 = ApexLM(model_config)
    model_v2.load_state_dict(engine_v1.model.state_dict())
    engine_v2 = TrainingEngine(
        model_v2,
        TrainingConfig(
            max_steps=config.corrupt_steps,
            batch_size=config.batch_size,
            seed=config.seed,
            optimizer=OptimizerSpec(lr=config.lr),
            scheduler=SchedulerSpec(name="constant"),
        ),
    )
    t0 = time.perf_counter()
    curve_v2 = _train(engine_v2, corrupt_batcher, config.corrupt_steps, config.log_every)
    timings["train_v2_s"] = time.perf_counter() - t0
    v2_sha = checkpoint("v2_corrupted", engine_v2, corrupt_batcher)
    v2 = evaluate_suites(engine_v2.model, tokenizer, suites)
    verdict_v2 = judge.judge(v1, v2)
    log(f"Judge V2 vs V1 : {verdict_v2.decision.value}")

    # 7. Tentative de masquer la régression en changeant le benchmark.
    worst = sorted(v2["heldout"].scores, key=lambda s: s.bits_per_byte, reverse=True)
    dropped = {s.doc_id for s in worst[: len(worst) // 3]}
    easier = Benchmark("heldout", tuple(d for d in suites[0].documents if d[0] not in dropped))
    v2_easier = {**v2, "heldout": evaluate_suites(engine_v2.model, tokenizer, [easier])["heldout"]}
    verdict_masked = judge.judge(v1, v2_easier)
    log(f"Judge V2 (benchmark allégé) vs V1 : {verdict_masked.decision.value}")

    # Illustration qualitative (pas une preuve) : même amorce, V0 puis V1.
    prompt = tokenizer.encode("Return the", add_bos=True)
    samples = {}
    for name, path in (("v0", "v0.pt"), ("v1", "v1.pt")):
        eng, _, _ = load_checkpoint(out_dir / path)
        torch.manual_seed(config.seed)
        out = eng.model.generate(torch.tensor([prompt]), max_new_tokens=40, temperature=0.8)
        samples[name] = tokenizer.decode(out[0].tolist())

    timings["total_s"] = time.perf_counter() - started
    return {
        "config": {
            k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(config).items() if k != "modules"
        },
        "modules_requested": len(config.modules),
        "knowledge": {
            "decisions": report.decisions,
            "qualities": report.qualities,
            "candidates": len(candidates),
            "guard_decisions": guard_report,
        },
        "dataset": {
            "fingerprint": dataset.fingerprint,
            "splits": {s.value: len(dataset.splits[s]) for s in Split},
            "sources": {s.value: len(dataset.manifest[s.value]) for s in Split},
            "dropped_duplicates": len(dataset.dropped_duplicates),
            "decontaminated_train_fragments": list(dataset.decontaminated),
            "max_test_overlap": round(dataset.leakage_ratio, 4),
            "train_tokens": len(train_tokens),
        },
        "tokenizer": {
            "vocab_size": tokenizer.vocab_size,
            "fingerprint": tokenizer.fingerprint(),
            "bytes_per_token": round(sum(len(t.encode()) for t in train_texts) / len(train_tokens), 3),
        },
        "model": {"config": model_config.to_dict(), "parameters": model.num_parameters()},
        "training": training_config.to_dict(),
        "checkpoints": {"v0": v0_sha, "v1": v1_sha, "v2_corrupted": v2_sha, "v1_step": info_v1.step},
        "curves": {"v1": curve_v1, "v2_corrupted": curve_v2},
        "evaluations": {
            "v0": _summaries(v0),
            "v1": _summaries(v1),
            "v2_corrupted": _summaries(v2),
            "v0_train_sample": _summaries(v0_train),
            "v1_train_sample": _summaries(v1_train),
        },
        "verdicts": {
            "v1_vs_v0": verdict_v1.to_dict(),
            "v2_vs_v1": verdict_v2.to_dict(),
            "v2_easier_benchmark_vs_v1": verdict_masked.to_dict(),
        },
        "samples_illustration_only": samples,
        "environment": {
            "torch": torch.__version__,
            "cuda": torch.cuda.is_available(),
            "threads": torch.get_num_threads(),
        },
        "timings_s": {k: round(v, 2) for k, v in timings.items()},
    }
