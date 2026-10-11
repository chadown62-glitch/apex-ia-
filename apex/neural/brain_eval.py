"""Évaluation du Cerveau APEX v2 servi — qualité, vitesse, mémoire, stabilité. Tout est MESURÉ sur le modèle
réellement servi (le même répertoire, le même moteur d'inférence que l'application), rien n'est estimé.

- `evaluate_tasks` : exercices du jeu d'évaluation (`apex.neural.tasks.eval_set`, jamais vus à
  l'entraînement), décodage glouton, notation automatique (réponse attendue, tests exécutés dans un
  processus séparé, JSON, reconnaissance des limites). Taux de réussite par famille et exemples d'échecs.
- `evaluate_bpb` : bits par octet sur la validation du corpus, poids pleine précision ET APEX-Q5 (coût réel
  de la quantification).
- `benchmark_speed` : délai avant le premier jeton, débit de génération et de pré-remplissage pour plusieurs
  longueurs d'invite, mémoire des poids et du cache.
- `stability` : N générations d'affilée ; erreurs et évolution de la mémoire résidente du processus.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import torch

from apex.brain.chat import Message, visible_text
from apex.brain.inference import BrainEngine, SamplingSettings


def _rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        import psutil  # type: ignore[import-untyped]

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return None


def _messages(row: dict[str, Any]) -> list[Message]:
    """Toute la conversation sauf la réponse de référence (la dernière)."""
    return [Message(m["role"], m["content"]) for m in row["messages"][:-1]]


def evaluate_tasks(
    engine: BrainEngine,
    rows: Sequence[dict[str, Any]],
    *,
    max_new_tokens: int = 256,
    keep_failures: int = 3,
) -> dict[str, Any]:
    from apex.neural.tasks import score

    engine.load()
    max_new_tokens = min(
        max_new_tokens, engine.context_length // 2
    )  # la moitié du contexte reste à l'invite
    per_family: dict[str, dict[str, Any]] = {}
    refused, ordinary = 0, 0  # refus abusifs : « je ne peux pas » à une question ordinaire
    started = time.perf_counter()
    for row in rows:
        result = engine.chat(_messages(row), SamplingSettings(max_new_tokens=max_new_tokens))
        passed = score(row["check"], result.text, row["messages"][-2]["content"])
        if row["check"]["type"] != "refusal":
            ordinary += 1
            refused += int(score({"type": "refusal"}, result.text))
        family = per_family.setdefault(
            row["family"], {"passed": 0, "total": 0, "failures": [], "examples": []}
        )
        family["total"] += 1
        family["passed"] += int(passed)
        sample = {
            "question": row["messages"][-2]["content"][:300],
            "answer": result.text[:400],
            "expected": visible_text(row["messages"][-1]["content"])[:300],
        }
        if not passed and len(family["failures"]) < keep_failures:
            family["failures"].append(sample)
        if passed and len(family["examples"]) < 2:
            family["examples"].append(sample)
    for family in per_family.values():
        family["rate"] = round(family["passed"] / family["total"], 4) if family["total"] else None
    total = sum(f["total"] for f in per_family.values())
    passed = sum(f["passed"] for f in per_family.values())
    return {
        "families": per_family,
        "passed": passed,
        "total": total,
        "rate": round(passed / total, 4) if total else None,
        "over_refusal_rate": round(refused / ordinary, 4) if ordinary else None,
        "elapsed_s": round(time.perf_counter() - started, 1),
    }


def baselines(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Planchers de la notation, sans aucun modèle : réponse vide, et réponse qui RÉPÈTE la question (une
    traduction notée par recouvrement partage des mots avec la phrase source, par exemple). Le taux d'une
    famille n'a de sens qu'au-dessus de son plancher."""
    from apex.neural.tasks import score

    answers = {"empty": lambda row: "", "echo": lambda row: row["messages"][-2]["content"]}
    result: dict[str, dict[str, float]] = {}
    for name, answer_of in answers.items():
        counts: dict[str, list[int]] = {}
        for row in rows:
            family = counts.setdefault(row["family"], [0, 0])
            family[0] += int(score(row["check"], answer_of(row), row["messages"][-2]["content"]))
            family[1] += 1
        result[name] = {family: round(passed / total, 4) for family, (passed, total) in counts.items()}
    return result


def evaluate_bpb(model: Any, data_dir: Path, *, window: int, eval_tokens: int = 200_000) -> dict[str, Any]:
    from types import SimpleNamespace

    from apex.neural.evaluation_v2 import validation_loss

    config = SimpleNamespace(
        data_dir=str(data_dir),
        stage="pretrain",
        window=window,
        eval_tokens=eval_tokens,
        mixed_precision=False,
    )
    model.eval()
    return validation_loss(model, config, torch.device("cpu"))


def benchmark_speed(
    engine: BrainEngine,
    *,
    prompt_lengths: Sequence[int] = (32, 256, 768),
    new_tokens: int = 128,
    repeats: int = 3,
) -> dict[str, Any]:
    engine.load()
    tokenizer = engine.tokenizer
    filler = tokenizer.encode("APEX mesure sa vitesse réelle de génération. " * 400)
    rows = []
    for length in prompt_lengths:
        if length + new_tokens > engine.context_length:
            continue
        prompt = [tokenizer.bos_id, *filler[: length - 1]]
        runs = []
        for _ in range(repeats):
            result = engine.generate_ids(
                prompt, SamplingSettings(max_new_tokens=new_tokens), stop=frozenset()
            )
            runs.append(result.stats)
        best = min(runs, key=lambda s: s.total_s)
        rows.append(
            {
                "prompt_tokens": length,
                "new_tokens": best.generated_tokens,
                "time_to_first_token_s": best.time_to_first_token_s,
                "decode_tokens_per_s": best.tokens_per_s,
                "prefill_tokens_per_s": best.prefill_tokens_per_s,
                "kv_cache_bytes": best.kv_cache_bytes,
            }
        )
    return {
        "device": engine.device.type,
        "weights_mode": engine.weights_mode,
        "threads": torch.get_num_threads(),
        "weights_bytes": engine.weight_bytes(),
        "load_s": round(engine.load_s, 3),
        "runs": rows,
    }


def stability(engine: BrainEngine, rows: Sequence[dict[str, Any]], *, count: int = 100) -> dict[str, Any]:
    engine.load()
    before = _rss_bytes()
    errors: list[str] = []
    samples = []
    started = time.perf_counter()
    for index in range(count):
        row = rows[index % len(rows)]
        try:
            engine.chat(
                _messages(row), SamplingSettings(max_new_tokens=96, temperature=0.7, top_p=0.95, seed=index)
            )
        except Exception as exc:  # une erreur est COMPTÉE, jamais masquée
            errors.append(f"{exc.__class__.__name__}: {exc}"[:200])
        if index % 25 == 24:
            samples.append(_rss_bytes())
    after = _rss_bytes()
    return {
        "generations": count,
        "errors": len(errors),
        "error_samples": errors[:3],
        "rss_before_bytes": before,
        "rss_after_bytes": after,
        "rss_samples_bytes": samples,
        "elapsed_s": round(time.perf_counter() - started, 1),
    }


@torch.no_grad()
def layer_streaming_benchmark(
    brain_dir: Path, *, new_tokens: int = 48, prompt_tokens: int = 64
) -> dict[str, Any]:
    """Technique d'AirLLM (chargement couche par couche) mesurée sur le Cerveau : avant CHAQUE bloc, ses
    poids sont relus depuis le fichier (comme un modèle qui ne tient pas en mémoire), au lieu de rester
    résidents. Même calcul, mêmes jetons (vérifié) ; débit et mémoire comparés. Optimiste pour le
    chargement : le fichier est déjà dans le cache disque du système."""
    from safetensors import safe_open

    from apex.brain.artifact import load, weights_file
    from apex.brain.quant import dequantize_q5, read_layout

    loaded = load(brain_dir)
    model, tokenizer = loaded.model, loaded.tokenizer
    path = brain_dir / weights_file(loaded.provenance)
    quantized = bool(loaded.provenance.get("quantization"))
    layout = read_layout(path)["layout"] if quantized else {}
    prompt = [
        tokenizer.bos_id,
        *tokenizer.encode("Le Cerveau APEX mesure le chargement couche par couche. " * 20),
    ]
    prompt = prompt[:prompt_tokens]

    def block_state(handle: Any, index: int) -> dict[str, torch.Tensor]:
        prefix = f"blocks.{index}."
        state = {}
        for name in model.blocks[index].state_dict():
            full = prefix + name
            if quantized and layout.get(full, {}).get("format") == "q5":
                parts = {k: handle.get_tensor(f"{full}.{k}") for k in ("q5", "scale", "min")}
                state[name] = dequantize_q5(parts, tuple(layout[full]["shape"]))
            else:
                state[name] = handle.get_tensor(full)
        return state

    def run(streaming: bool) -> tuple[list[int], float]:
        from apex.brain.model import KVCache

        cache = KVCache(model.config, 1, len(prompt) + new_tokens)
        ids = torch.tensor([prompt])
        produced: list[int] = []
        started = time.perf_counter()
        with safe_open(str(path), framework="pt") as handle:
            for _ in range(new_tokens):
                x = model.embed(ids)
                start = cache.length
                for index, block in enumerate(model.layers):
                    if streaming:
                        block.load_state_dict(block_state(handle, index))
                    x = block(x, cache, index)
                cache.length = start + ids.shape[1]
                token = int(model.head(model.norm(x[:, -1:]))[0, -1].argmax())
                produced.append(token)
                ids = torch.tensor([[token]])
        return produced, time.perf_counter() - started

    resident_ids, resident_s = run(False)
    streamed_ids, streamed_s = run(True)
    block_bytes = sum(t.numel() * t.element_size() for t in model.blocks.state_dict().values())
    return {
        "technique": "chargement couche par couche (principe d'AirLLM), mesuré sur ApexLM",
        "same_tokens": resident_ids == streamed_ids,
        "new_tokens": new_tokens,
        "resident_tokens_per_s": round(new_tokens / resident_s, 2),
        "streaming_tokens_per_s": round(new_tokens / streamed_s, 2),
        "slowdown": round(streamed_s / resident_s, 2),
        "resident_block_bytes": block_bytes,
        "verdict": (
            "plus lent, sans gain de mémoire utile : le Cerveau tient déjà entièrement en mémoire"
            if streamed_s > 1.1 * resident_s
            else "aucun gain mesuré"
        ),
    }


def long_conversation(engine: BrainEngine, *, turns: int = 40) -> dict[str, Any]:
    """Conversation plus longue que le contexte : un fait donné au premier tour, puis `turns` échanges.
    Mesure : les tours anciens sont retirés (combien), la génération aboutit, et ce que le modèle répond
    quand le fait est sorti de son contexte (il ne peut plus le connaître : l'inventer serait une
    erreur)."""
    from apex.neural.tasks import score

    engine.load()
    messages = [
        Message("user", "Je m'appelle Camille et je travaille sur un site de recettes."),
        Message("assistant", "Enchanté, Camille ! Comment puis-je t'aider ?"),
    ]
    for index in range(turns):
        messages += [
            Message("user", f"Combien font {index} + {index + 1} ?"),
            Message("assistant", f"{index} + {index + 1} = {2 * index + 1}."),
        ]
    question = Message("user", "Comment je m'appelle ?")
    result = engine.chat([*messages, question], SamplingSettings(max_new_tokens=48))
    prompt, dropped = engine.chat_prompt([*messages, question], 48)
    in_context = "Camille" in engine.tokenizer.decode(prompt)
    return {
        "turns": len(messages) + 1,
        "dropped_messages": result.stats.truncated_messages,
        "prompt_tokens": result.stats.prompt_tokens,
        "context_length": engine.context_length,
        "fact_still_in_context": in_context,
        "answer": result.text[:200],
        "invented_name": (not in_context) and "Camille" in result.text,
        "admits_not_knowing": score({"type": "refusal"}, result.text),
        "time_to_first_token_s": result.stats.time_to_first_token_s,
        "dropped_check": dropped,
    }


def memory_lifecycle(brain_dir: Path, *, cycles: int = 5) -> dict[str, Any]:
    """Mémoire résidente du processus : avant chargement, chargé, après une génération, après déchargement —
    puis `cycles` chargements/déchargements (une fuite se verrait comme une croissance régulière)."""
    import gc

    gc.collect()
    baseline = _rss_bytes()
    engine = BrainEngine(brain_dir, device="cpu")
    engine.load()
    loaded = _rss_bytes()
    engine.chat([Message("user", "Bonjour")], SamplingSettings(max_new_tokens=32))
    generated = _rss_bytes()
    engine.unload()
    gc.collect()
    unloaded = _rss_bytes()
    after_cycles = []
    for _ in range(cycles):
        engine.load()
        engine.chat([Message("user", "Bonjour")], SamplingSettings(max_new_tokens=16))
        engine.unload()
        gc.collect()
        after_cycles.append(_rss_bytes())
    return {
        "rss_baseline_bytes": baseline,
        "rss_loaded_bytes": loaded,
        "rss_after_generation_bytes": generated,
        "rss_unloaded_bytes": unloaded,
        "rss_after_each_cycle_bytes": after_cycles,
        "weights_bytes": None if loaded is None or baseline is None else loaded - baseline,
    }


def full_report(
    brain_dir: Path,
    eval_rows: Sequence[dict[str, Any]],
    *,
    data_dir: Path | None = None,
    fp32_state: dict[str, torch.Tensor] | None = None,
    stability_count: int = 100,
) -> dict[str, Any]:
    """Rapport complet d'un Cerveau servi (et, si `fp32_state` est fourni, comparaison pleine
    précision/Q5)."""
    from apex.brain.artifact import load

    report: dict[str, Any] = {"brain": str(brain_dir), "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    engine = BrainEngine(brain_dir, device="cpu")
    report["tasks"] = evaluate_tasks(engine, eval_rows)
    report["baselines"] = baselines(eval_rows)
    report["speed"] = benchmark_speed(engine)
    report["long_conversation"] = long_conversation(engine)
    report["stability"] = stability(engine, eval_rows, count=stability_count)
    engine.unload()
    report["layer_streaming"] = layer_streaming_benchmark(brain_dir)
    report["memory"] = memory_lifecycle(brain_dir)
    report["speed_cuda"] = None
    if torch.cuda.is_available():  # information : APEX.exe (Windows) exécute le Cerveau sur CPU
        gpu = BrainEngine(brain_dir, device="cuda")
        report["speed_cuda"] = benchmark_speed(gpu)
        report["speed_cuda"]["gpu"] = torch.cuda.get_device_name(0)
        gpu.unload()
    report["speed_q5_resident"] = None
    loaded = load(brain_dir)
    if loaded.provenance.get("quantization"):
        resident = BrainEngine(brain_dir, device="cpu", weights="q5_resident")
        report["speed_q5_resident"] = benchmark_speed(resident, prompt_lengths=(256,), repeats=2)
        resident.unload()
    if data_dir is not None:
        window = loaded.model.config.context_length
        report["bpb_served"] = evaluate_bpb(loaded.model, data_dir, window=window)
        if fp32_state is not None:
            from apex.brain.model import ApexLM

            full = ApexLM(loaded.model.config)
            full.load_state_dict(fp32_state)
            report["bpb_float32"] = evaluate_bpb(full, data_dir, window=window)
    return report


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
