"""Données d'instruction du Cerveau APEX v2 : conversations tokenisées et lots « empaquetés ».

    exemples JSONL ({"messages": [...]}) -> tokens.bin + mask.bin + index.npy par split (+ tokenizer.json)

Plusieurs conversations courtes partagent une même fenêtre (chacune commence par `<bos>`), au lieu d'une
conversation par fenêtre complétée de remplissage : beaucoup moins de calcul perdu. Les cibles hors réponses
de l'assistant valent `IGNORE_INDEX` (voir `apex.brain.chat`)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import torch

from apex.brain.chat import Message, encode_chat
from apex.brain.model import IGNORE_INDEX
from apex.brain.tokenizer import ByteBPETokenizer


def prepare_sft(
    splits: dict[str, Iterable[dict[str, Any]]], tokenizer: ByteBPETokenizer, out_dir: Path, *, window: int
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tokenizer.save(out_dir / "tokenizer.json")
    manifest: dict[str, Any] = {
        "format": 1,
        "tokenizer_fingerprint": tokenizer.fingerprint(),
        "window": window,
        "splits": {},
    }
    for split, rows in splits.items():
        tokens: list[np.ndarray[Any, Any]] = []
        masks: list[np.ndarray[Any, Any]] = []
        index: list[tuple[int, int]] = []
        families: dict[str, int] = {}
        offset = truncated = 0
        for row in rows:
            ids, mask = encode_chat(tokenizer, [Message(m["role"], m["content"]) for m in row["messages"]])
            if len(ids) > window + 1:
                truncated += 1
                ids, mask = ids[: window + 1], mask[: window + 1]
            if not any(mask[1:]):
                continue  # rien à apprendre dans la fenêtre
            tokens.append(np.asarray(ids, dtype=np.uint16))
            masks.append(np.asarray(mask, dtype=np.uint8))
            index.append((offset, len(ids)))
            offset += len(ids)
            family = str(row.get("family", "?"))
            families[family] = families.get(family, 0) + 1
        np.concatenate(tokens).tofile(out_dir / f"{split}-tokens.bin")
        np.concatenate(masks).tofile(out_dir / f"{split}-mask.bin")
        np.save(out_dir / f"{split}-index.npy", np.asarray(index, dtype=np.int64))
        manifest["splits"][split] = {
            "examples": len(index),
            "tokens": offset,
            "truncated": truncated,
            "learned_tokens": int(sum(int(m.sum()) for m in masks)),
            "families": families,
        }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


class _Examples:
    def __init__(self, data_dir: Path, split: str) -> None:
        self.tokens = np.fromfile(data_dir / f"{split}-tokens.bin", dtype=np.uint16)
        self.mask = np.fromfile(data_dir / f"{split}-mask.bin", dtype=np.uint8)
        self.index = np.load(data_dir / f"{split}-index.npy")
        self.pad_id = ByteBPETokenizer.load(data_dir / "tokenizer.json").pad_id

    def get(self, i: int) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
        start, length = (int(x) for x in self.index[i])
        return self.tokens[start : start + length], self.mask[start : start + length]

    def pack(self, order: Iterable[int], window: int) -> tuple[torch.Tensor, torch.Tensor]:
        ids = np.full(window + 1, self.pad_id, dtype=np.int64)
        learn = np.zeros(window + 1, dtype=np.uint8)
        used = 0
        for i in order:
            tokens, mask = self.get(i)
            if used and used + len(tokens) > window + 1:
                break
            n = min(len(tokens), window + 1 - used)
            ids[used : used + n] = tokens[:n]
            learn[used : used + n] = mask[:n]
            used += n
            if used >= window + 1:
                break
        targets = ids[1:].copy()
        targets[learn[1:] == 0] = IGNORE_INDEX
        return torch.from_numpy(ids[:-1].copy()), torch.from_numpy(targets)


class ChatBatcher:
    """Lots d'instruction aléatoires et reproductibles (interface `apex.neural.training.Batcher`)."""

    def __init__(self, data_dir: Path, *, split: str, window: int, batch_size: int, seed: int) -> None:
        self.examples = _Examples(data_dir, split)
        if len(self.examples.index) == 0:
            raise ValueError(f"aucun exemple « {split} » dans {data_dir}")
        self.window = window
        self.batch_size = batch_size
        self.generator = torch.Generator().manual_seed(seed)

    def next_batch(self) -> tuple[torch.Tensor, torch.Tensor]:
        rows = []
        count = len(self.examples.index)
        for _ in range(self.batch_size):
            order = torch.randint(0, count, (64,), generator=self.generator).tolist()
            rows.append(self.examples.pack(order, self.window))
        return torch.stack([r[0] for r in rows]), torch.stack([r[1] for r in rows])

    def state_dict(self) -> dict[str, Any]:
        return {"generator": self.generator.get_state()}

    def load_state_dict(self, state: dict[str, Any]) -> None:
        self.generator.set_state(state["generator"])


def validation_batches(
    data_dir: Path, *, window: int, batch_size: int, limit: int = 2000
) -> Iterator[tuple[torch.Tensor, torch.Tensor]]:
    """Exemples de validation dans l'ordre, UN par fenêtre (mesure exacte, sans voisinage)."""
    examples = _Examples(data_dir, "validation")
    rows = [examples.pack([i], window) for i in range(min(limit, len(examples.index)))]
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        yield torch.stack([r[0] for r in chunk]), torch.stack([r[1] for r in chunk])


class ReplayBatcher:
    """Instruction avec REJEU : chaque ligne d'un lot est, avec la probabilité `replay_fraction`, une
    fenêtre du corpus de pré-entraînement (toutes ses positions apprises) au lieu d'une conversation.
    Protège la maîtrise générale de la langue pendant l'instruction (oubli mesuré : bits par octet du corpus
    avant/après). L'état des deux sources et du tirage fait partie du checkpoint."""

    def __init__(self, chat: ChatBatcher, corpus: Any, *, replay_fraction: float, seed: int) -> None:
        if not 0.0 <= replay_fraction < 1.0:
            raise ValueError("replay_fraction dans [0, 1)")
        self.chat = chat
        self.corpus = corpus
        self.replay_fraction = replay_fraction
        self.generator = torch.Generator().manual_seed(seed + 7919)
        self.corpus.batch_size = 1

    @property
    def batch_size(self) -> int:
        return self.chat.batch_size

    @batch_size.setter
    def batch_size(self, value: int) -> None:
        self.chat.batch_size = value

    def next_batch(self) -> tuple[torch.Tensor, torch.Tensor]:
        inputs, targets = self.chat.next_batch()
        draws = torch.rand(inputs.shape[0], generator=self.generator)
        for row in range(inputs.shape[0]):
            if float(draws[row]) < self.replay_fraction:
                window = self.corpus.next_batch()[0]
                inputs[row] = window[:-1]
                targets[row] = window[1:]
        return inputs, targets

    def state_dict(self) -> dict[str, Any]:
        return {
            "generator": self.generator.get_state(),
            "chat": self.chat.state_dict(),
            "corpus": self.corpus.state_dict(),
        }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        self.generator.set_state(state["generator"])
        self.chat.load_state_dict(state["chat"])
        self.corpus.load_state_dict(state["corpus"])
