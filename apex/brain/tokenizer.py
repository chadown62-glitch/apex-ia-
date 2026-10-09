"""Tokenizer propre à APEX : BPE au niveau octet, entraîné sur le corpus d'APEX.

Identifiants 0..255 = octets bruts (tout texte UTF-8 est encodable, aucun
token inconnu), puis les tokens spéciaux, puis les fusions apprises par ordre
de rang. `decode(encode(texte)) == texte` pour tout texte."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path

SPECIAL_TOKENS = ("<bos>", "<eos>", "<pad>")
#: Couvre CHAQUE caractère (dernière alternative) : la pré-tokenisation ne perd rien.
_PRETOKEN = re.compile(r" ?[^\W\d_]+| ?\d+| ?[^\s\w]+|\s+|.", re.UNICODE | re.DOTALL)


def _pieces(text: str) -> list[str]:
    pieces = _PRETOKEN.findall(text)
    if "".join(pieces) != text:
        raise AssertionError("pré-tokenisation avec perte")
    return pieces


def _merge(ids: list[int], pair: tuple[int, int], new_id: int) -> list[int]:
    out: list[int] = []
    i = 0
    while i < len(ids):
        if i + 1 < len(ids) and ids[i] == pair[0] and ids[i + 1] == pair[1]:
            out.append(new_id)
            i += 2
        else:
            out.append(ids[i])
            i += 1
    return out


class ByteBPETokenizer:
    def __init__(self, merges: list[tuple[int, int]] | None = None) -> None:
        self.merges: list[tuple[int, int]] = list(merges or [])
        self._special = {tok: 256 + i for i, tok in enumerate(SPECIAL_TOKENS)}
        first_merge_id = 256 + len(SPECIAL_TOKENS)
        self._ranks = {pair: first_merge_id + i for i, pair in enumerate(self.merges)}
        self._bytes: dict[int, bytes] = {i: bytes([i]) for i in range(256)}
        for pair, new_id in self._ranks.items():
            self._bytes[new_id] = self._bytes[pair[0]] + self._bytes[pair[1]]
        self._cache: dict[str, list[int]] = {}

    @property
    def vocab_size(self) -> int:
        return 256 + len(SPECIAL_TOKENS) + len(self.merges)

    @property
    def bos_id(self) -> int:
        return self._special["<bos>"]

    @property
    def eos_id(self) -> int:
        return self._special["<eos>"]

    @property
    def pad_id(self) -> int:
        return self._special["<pad>"]

    @classmethod
    def train(cls, texts: Iterable[str], vocab_size: int) -> ByteBPETokenizer:
        base = 256 + len(SPECIAL_TOKENS)
        if vocab_size < base:
            raise ValueError(f"vocab_size doit être >= {base}")
        words: Counter[tuple[int, ...]] = Counter()
        for text in texts:
            for piece in _pieces(text):
                words[tuple(piece.encode("utf-8"))] += 1
        sequences = {word: list(word) for word in words}
        merges: list[tuple[int, int]] = []
        for new_id in range(base, vocab_size):
            pairs: Counter[tuple[int, int]] = Counter()
            for word, count in words.items():
                seq = sequences[word]
                for pair in pairwise(seq):
                    pairs[pair] += count
            if not pairs:
                break
            best = max(pairs.items(), key=lambda item: (item[1], -item[0][0], -item[0][1]))[0]
            if pairs[best] < 2:
                break
            merges.append(best)
            for word in words:
                sequences[word] = _merge(sequences[word], best, new_id)
        return cls(merges)

    def _encode_piece(self, piece: str) -> list[int]:
        cached = self._cache.get(piece)
        if cached is not None:
            return cached
        ids = list(piece.encode("utf-8"))
        while len(ids) > 1:
            candidates = [(self._ranks.get(p, 1 << 62), p) for p in pairwise(ids)]
            rank, pair = min(candidates)
            if rank == 1 << 62:
                break
            ids = _merge(ids, pair, rank)
        if len(self._cache) < 100_000:
            self._cache[piece] = ids
        return ids

    def encode(self, text: str, *, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        ids = [self.bos_id] if add_bos else []
        for piece in _pieces(text):
            ids.extend(self._encode_piece(piece))
        if add_eos:
            ids.append(self.eos_id)
        return ids

    def token_bytes(self, token_id: int) -> bytes:
        """Octets représentés par un token (vide pour un token spécial) —
        base des mesures en bits par octet, indépendantes du tokenizer."""
        return b"" if token_id in self._special.values() else self._bytes[token_id]

    def decode(self, ids: Iterable[int]) -> str:
        special_ids = set(self._special.values())
        data = b"".join(self._bytes[i] for i in ids if i not in special_ids)
        return data.decode("utf-8", errors="replace")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "byte_bpe",
            "special_tokens": list(SPECIAL_TOKENS),
            "merges": [list(m) for m in self.merges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ByteBPETokenizer:
        specials = data.get("special_tokens")
        merges = data.get("merges", [])
        if (
            data.get("kind") != "byte_bpe"
            or not isinstance(specials, list)
            or specials != list(SPECIAL_TOKENS)
        ):
            raise ValueError("tokenizer incompatible")
        if not isinstance(merges, list):
            raise ValueError("fusions illisibles")
        return cls([(int(a), int(b)) for a, b in merges])

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ByteBPETokenizer:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()
