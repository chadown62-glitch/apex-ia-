"""Corpus tokenisé en flux binaires (`uint16`/`uint32`, lus par projection mémoire) et échantillonneur
de lots.

    documents JSONL -> tokenizer (processus parallèles) -> <split>-<groupe>.bin + manifest.json

- Le découpage train/validation se fait par DOCUMENT (empreinte du texte) : aucun fragment d'un document de
  validation n'est vu à l'entraînement.
- Chaque document est encadré par `<bos>` … `<eos>`.
- `ShardSampler` tire des fenêtres aléatoires ; le groupe (`en`, `fr`, `code`, `translation`) est d'abord
  tiré selon des POIDS de mélange (le français, plus rare, est ainsi suréchantillonné). Son état
  (générateur) fait partie du checkpoint : une reprise voit exactement les mêmes lots.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from apex.brain.tokenizer import ByteBPETokenizer
from apex.neural.corpus import CorpusDocument, group_of, iter_documents

FORMAT = 1
_WORKER_TOKENIZER: ByteBPETokenizer | None = None


def _init_worker(tokenizer_data: dict[str, Any]) -> None:
    global _WORKER_TOKENIZER
    _WORKER_TOKENIZER = ByteBPETokenizer.from_dict(tokenizer_data)


def _encode(text: str) -> list[int]:
    assert _WORKER_TOKENIZER is not None
    return _WORKER_TOKENIZER.encode(text, add_bos=True, add_eos=True)


def split_of(document: CorpusDocument, validation_per_mille: int) -> str:
    digest = document.sha256 or hashlib.sha256(document.text.encode("utf-8")).hexdigest()
    return "validation" if int(digest[:8], 16) % 1000 < validation_per_mille else "train"


def _dtype_for(vocab_size: int) -> np.dtype[Any]:
    return np.dtype(np.uint16) if vocab_size <= 65535 else np.dtype(np.uint32)


def tokenize_corpus(
    corpus_dir: Path,
    tokenizer: ByteBPETokenizer,
    out_dir: Path,
    *,
    validation_per_mille: int = 5,
    workers: int = 4,
    log: Any = print,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dtype = _dtype_for(tokenizer.vocab_size)
    handles: dict[str, Any] = {}
    stats: dict[str, dict[str, int]] = {}

    def batches() -> Iterator[list[CorpusDocument]]:
        batch: list[CorpusDocument] = []
        for document in iter_documents(corpus_dir):
            batch.append(document)
            if len(batch) >= 256:
                yield batch
                batch = []
        if batch:
            yield batch

    context = multiprocessing.get_context("spawn")
    with context.Pool(workers, initializer=_init_worker, initargs=(tokenizer.to_dict(),)) as pool:
        for batch in batches():
            encoded = pool.map(_encode, [d.text for d in batch], chunksize=8)
            for document, ids in zip(batch, encoded, strict=True):
                key = f"{split_of(document, validation_per_mille)}-{group_of(document)}"
                if key not in handles:
                    handles[key] = (out_dir / f"{key}.bin").open("wb")
                    stats[key] = {"documents": 0, "tokens": 0, "bytes": 0}
                handles[key].write(np.asarray(ids, dtype=dtype).tobytes())
                stats[key]["documents"] += 1
                stats[key]["tokens"] += len(ids)
                stats[key]["bytes"] += len(document.text.encode("utf-8"))
            total = sum(s["tokens"] for s in stats.values())
            log(f"tokenisation : {total / 1e6:.1f} M jetons")
    for handle in handles.values():
        handle.close()
    corpus_manifest = corpus_dir / "manifest.json"
    manifest = {
        "format": FORMAT,
        "dtype": dtype.name,
        "tokenizer_fingerprint": tokenizer.fingerprint(),
        "vocab_size": tokenizer.vocab_size,
        "validation_per_mille": validation_per_mille,
        "corpus_manifest_sha256": hashlib.sha256(corpus_manifest.read_bytes()).hexdigest()
        if corpus_manifest.is_file()
        else None,
        "streams": stats,
        "files": {
            f"{key}.bin": hashlib.sha256((out_dir / f"{key}.bin").read_bytes()).hexdigest() for key in stats
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


@dataclass(frozen=True)
class Stream:
    name: str
    tokens: np.ndarray[Any, Any]


def open_streams(data_dir: Path, split: str) -> dict[str, Stream]:
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    dtype = np.dtype(manifest["dtype"])
    streams = {}
    for key in manifest["streams"]:
        stream_split, group = key.split("-", 1)
        if stream_split != split:
            continue
        path = data_dir / f"{key}.bin"
        streams[group] = Stream(group, np.memmap(path, dtype=dtype, mode="r"))
    return streams


class ShardSampler:
    """Fenêtres aléatoires de `window + 1` jetons, groupe tiré selon `weights`. Interface de
    `apex.neural.training.TokenBatcher` (`next_batch`, `state_dict`, `load_state_dict`)."""

    def __init__(
        self,
        data_dir: Path,
        *,
        split: str,
        window: int,
        batch_size: int,
        seed: int,
        weights: dict[str, float] | None = None,
    ) -> None:
        import torch

        self.streams = {
            name: s for name, s in open_streams(data_dir, split).items() if len(s.tokens) > window + 1
        }
        if not self.streams:
            raise ValueError(f"aucun flux « {split} » assez long dans {data_dir}")
        requested = weights or {name: float(len(s.tokens)) for name, s in self.streams.items()}
        self.groups = [name for name in sorted(self.streams) if requested.get(name, 0) > 0]
        if not self.groups:
            raise ValueError("aucun groupe avec un poids positif")
        total = sum(requested[name] for name in self.groups)
        self.probabilities = torch.tensor(
            [requested[name] / total for name in self.groups], dtype=torch.float64
        )
        self.window = window
        self.batch_size = batch_size
        self.generator = torch.Generator().manual_seed(seed)

    def next_batch(self) -> Any:
        import torch

        picks = torch.multinomial(
            self.probabilities, self.batch_size, replacement=True, generator=self.generator
        )
        rows = []
        for pick in picks.tolist():
            tokens = self.streams[self.groups[pick]].tokens
            start = int(torch.randint(0, len(tokens) - self.window - 1, (1,), generator=self.generator))
            rows.append(torch.from_numpy(tokens[start : start + self.window + 1].astype(np.int64)))
        return torch.stack(rows)

    def state_dict(self) -> dict[str, Any]:
        return {"generator": self.generator.get_state()}

    def load_state_dict(self, state: dict[str, Any]) -> None:
        self.generator.set_state(state["generator"])


def iter_eval_windows(stream: Stream, window: int, limit_tokens: int) -> Iterator[np.ndarray[Any, Any]]:
    """Fenêtres CONSÉCUTIVES et déterministes (évaluation reproductible), au plus `limit_tokens` jetons."""
    end = min(len(stream.tokens), limit_tokens)
    for start in range(0, max(0, end - window - 1), window):
        yield np.asarray(stream.tokens[start : start + window + 1], dtype=np.int64)
