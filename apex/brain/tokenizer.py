"""Tokenizer propre à APEX : BPE au niveau octet, entraîné sur le corpus d'APEX.

Identifiants 0..255 = octets bruts (tout texte UTF-8 est encodable, aucun
token inconnu), puis les tokens spéciaux, puis les fusions apprises par ordre
de rang. `decode(encode(texte)) == texte` pour tout texte.

Deux générations, relues telles qu'elles ont été écrites (un fichier dit laquelle) :

- **v1** (Cerveau APEX v1) : 3 tokens spéciaux (`<bos>`, `<eos>`, `<pad>`), nombres groupés.
- **v2** (Cerveau APEX v2, conversation) : tokens de rôle (`<|system|>`, `<|user|>`, `<|assistant|>`,
  `<|end|>`) et d'outils, réserves pour l'avenir ; chiffres découpés UN PAR UN (le modèle apprend
  l'arithmétique chiffre à chiffre au lieu de mémoriser des nombres) ; `_` fait partie des mots (code).

Sécurité : `encode` ne produit JAMAIS un token spécial à partir du texte — un message contenant
« <|assistant|> » est encodé comme du texte ordinaire. Seul le gabarit de conversation
(`apex.brain.chat`) place les tokens de rôle, par identifiant.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from itertools import pairwise
from pathlib import Path

SPECIAL_TOKENS = ("<bos>", "<eos>", "<pad>")
#: Tokens spéciaux de la génération 2 : rôles de conversation, appels d'outils, réserves (jamais
#: renumérotés).
CHAT_SPECIAL_TOKENS = (
    "<bos>", "<eos>", "<pad>",
    "<|system|>", "<|user|>", "<|assistant|>", "<|end|>",
    "<|tool_call|>", "<|tool_result|>",
    *(f"<|reserved_{i}|>" for i in range(7)),
)
#: Couvre CHAQUE caractère (dernière alternative) : la pré-tokenisation ne perd rien.
_PATTERNS = {
    "v1": r" ?[^\W\d_]+| ?\d+| ?[^\s\w]+|\s+|.",
    # Mots (lettres et `_`) avec leur espace de tête ; chaque chiffre seul ; ponctuation groupée ; les
    # blancs laissent leur dernier espace au mot qui suit (indentation du code : « ␣␣␣ » + « ␣def »).
    "v2": r" ?[^\W\d]+|\d| ?[^\s\w]+|\s+(?!\S)|\s+|.",
}
_COMPILED = {name: re.compile(pattern, re.UNICODE | re.DOTALL) for name, pattern in _PATTERNS.items()}


def _pieces(text: str, pattern: str = "v1") -> list[str]:
    pieces = _COMPILED[pattern].findall(text)
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
    def __init__(self, merges: list[tuple[int, int]] | None = None, *,
                 special_tokens: tuple[str, ...] = SPECIAL_TOKENS, pattern: str = "v1") -> None:
        if pattern not in _PATTERNS:
            raise ValueError(f"pré-tokenisation inconnue : {pattern}")
        if len(set(special_tokens)) != len(special_tokens) or tuple(special_tokens[:3]) != SPECIAL_TOKENS:
            raise ValueError("tokens spéciaux : <bos>, <eos>, <pad> d'abord, sans doublon")
        self.merges: list[tuple[int, int]] = list(merges or [])
        self.special_tokens = tuple(special_tokens)
        self.pattern = pattern
        self._special = {tok: 256 + i for i, tok in enumerate(self.special_tokens)}
        self._special_ids = frozenset(self._special.values())
        first_merge_id = 256 + len(self.special_tokens)
        self._ranks = {pair: first_merge_id + i for i, pair in enumerate(self.merges)}
        self._bytes: dict[int, bytes] = {i: bytes([i]) for i in range(256)}
        for pair, new_id in self._ranks.items():
            if pair[0] not in self._bytes or pair[1] not in self._bytes:
                raise ValueError(f"fusion {pair} : identifiant inconnu (fusions désordonnées ou "
                                 "corrompues)")
            self._bytes[new_id] = self._bytes[pair[0]] + self._bytes[pair[1]]
        self._cache: dict[str, list[int]] = {}

    @property
    def vocab_size(self) -> int:
        return 256 + len(self.special_tokens) + len(self.merges)

    @property
    def bos_id(self) -> int:
        return self._special["<bos>"]

    @property
    def eos_id(self) -> int:
        return self._special["<eos>"]

    @property
    def pad_id(self) -> int:
        return self._special["<pad>"]

    def special_id(self, token: str) -> int:
        """Identifiant d'un token spécial (`KeyError` si ce tokenizer ne le connaît pas)."""
        return self._special[token]

    def has_special(self, token: str) -> bool:
        return token in self._special

    def is_special(self, token_id: int) -> bool:
        return token_id in self._special_ids

    @classmethod
    def train(cls, texts: Iterable[str], vocab_size: int, *,
              special_tokens: tuple[str, ...] = SPECIAL_TOKENS,
              pattern: str = "v1", min_frequency: int = 2) -> ByteBPETokenizer:
        """Apprend les fusions (paire la plus fréquente d'abord ; égalité : plus petits identifiants). Le
        résultat ne dépend que des textes : même corpus, mêmes fusions."""
        base = 256 + len(special_tokens)
        if vocab_size < base:
            raise ValueError(f"vocab_size doit être >= {base}")
        words: Counter[str] = Counter()
        for text in texts:
            words.update(_pieces(text, pattern))
        merges = _learn_merges(words, base, vocab_size, min_frequency)
        return cls(merges, special_tokens=special_tokens, pattern=pattern)

    def _encode_piece(self, piece: str) -> list[int]:
        cached = self._cache.get(piece)
        if cached is not None:
            return cached
        ids = list(piece.encode("utf-8"))
        while len(ids) > 1:
            rank, pair = min((self._ranks.get(p, 1 << 62), p) for p in pairwise(ids))
            if rank == 1 << 62:
                break
            ids = _merge(ids, pair, rank)
        if len(self._cache) < 200_000:
            self._cache[piece] = ids
        return ids

    def encode(self, text: str, *, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        """Texte -> identifiants. Les chaînes ressemblant à des tokens spéciaux restent du TEXTE."""
        ids = [self.bos_id] if add_bos else []
        for piece in _pieces(text, self.pattern):
            ids.extend(self._encode_piece(piece))
        if add_eos:
            ids.append(self.eos_id)
        return ids

    def token_bytes(self, token_id: int) -> bytes:
        """Octets représentés par un token (vide pour un token spécial) —
        base des mesures en bits par octet, indépendantes du tokenizer."""
        return b"" if token_id in self._special_ids else self._bytes[token_id]

    def decode(self, ids: Iterable[int]) -> str:
        data = b"".join(self._bytes[i] for i in ids if i not in self._special_ids)
        return data.decode("utf-8", errors="replace")

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "kind": "byte_bpe",
            "special_tokens": list(self.special_tokens),
            "merges": [list(m) for m in self.merges],
        }
        if self.pattern != "v1":
            data["pattern"] = self.pattern  # v1 : format historique inchangé (mêmes empreintes)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ByteBPETokenizer:
        specials = data.get("special_tokens")
        merges = data.get("merges", [])
        pattern = data.get("pattern", "v1")
        if (
            data.get("kind") != "byte_bpe"
            or not isinstance(specials, list)
            or not all(isinstance(s, str) for s in specials)
            or tuple(specials[:3]) != SPECIAL_TOKENS
            or not isinstance(pattern, str)
            or pattern not in _PATTERNS
        ):
            raise ValueError("tokenizer incompatible")
        if not isinstance(merges, list):
            raise ValueError("fusions illisibles")
        return cls([(int(a), int(b)) for a, b in merges], special_tokens=tuple(specials), pattern=pattern)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ByteBPETokenizer:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


def _learn_merges(words: Counter[str], base: int, vocab_size: int,
                  min_frequency: int) -> list[tuple[int, int]]:
    """BPE incrémental : seuls les mots qui contiennent la paire fusionnée sont recomptés (au lieu de tout
    le corpus à chaque fusion) ; la meilleure paire sort d'un tas à invalidation paresseuse. Même résultat
    que l'algorithme naïf : paire la plus fréquente, égalité départagée par les plus petits identifiants."""
    sequences: list[list[int]] = []
    counts: list[int] = []
    for word, count in words.items():
        encoded = list(word.encode("utf-8"))
        if len(encoded) > 1:
            sequences.append(encoded)
            counts.append(count)
    pair_counts: Counter[tuple[int, int]] = Counter()
    where: dict[tuple[int, int], set[int]] = {}
    for index, seq in enumerate(sequences):
        for pair in pairwise(seq):
            pair_counts[pair] += counts[index]
            where.setdefault(pair, set()).add(index)
    heap = [(-count, pair) for pair, count in pair_counts.items()]
    heapq.heapify(heap)
    merges: list[tuple[int, int]] = []
    for new_id in range(base, vocab_size):
        best = None
        while heap:
            negative, pair = heapq.heappop(heap)
            current = pair_counts.get(pair, 0)
            if current <= 0:
                continue
            if -negative != current:
                heapq.heappush(heap, (-current, pair))  # compte périmé : remis à sa vraie place
                continue
            best = pair
            break
        if best is None or pair_counts[best] < min_frequency:
            break
        merges.append(best)
        touched: set[tuple[int, int]] = set()
        for index in where.pop(best, set()):
            seq, count = sequences[index], counts[index]
            for pair in pairwise(seq):
                pair_counts[pair] -= count
                touched.add(pair)
                bucket = where.get(pair)
                if bucket is not None:
                    bucket.discard(index)
            merged = _merge(seq, best, new_id)
            sequences[index] = merged
            for pair in pairwise(merged):
                pair_counts[pair] += count
                touched.add(pair)
                where.setdefault(pair, set()).add(index)
        pair_counts.pop(best, None)
        for pair in touched:
            count = pair_counts.get(pair, 0)
            if count > 0:
                heapq.heappush(heap, (-count, pair))
            elif pair in pair_counts:
                del pair_counts[pair]
    return merges


def iter_text_chunks(text: str, size: int = 1 << 16) -> Iterator[str]:
    """Découpe un long texte en morceaux sans couper une ligne (pour l'encodage par lots)."""
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start:
                end = newline + 1
        yield text[start:end]
        start = end
