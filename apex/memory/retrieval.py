"""Récupération par PERTINENCE dans la mémoire système (06/10/2026).

Représentation : LEXICALE (sac de termes normalisés : minuscules, accents retirés, mots vides français/anglais
retirés, identifiants `snake_case` découpés en plus d'être gardés entiers), score BM25 (k1 = 1,5 ; b = 0,75) sur
les souvenirs candidats. C'est une recherche réelle et déterministe, sans modèle. Les embeddings (représentation
dense, `EmbeddingProvider`) restent NON implémentés : ils s'ajouteront derrière `score_candidates` le jour où un
modèle d'embedding sera réellement disponible et branché — jamais simulés ici.

Rien n'est injecté s'il n'y a pas de recouvrement de termes (score nul) : la mémoire n'est jamais « collée » en
entier dans un prompt, seuls les meilleurs extraits, bornés, avec leur provenance.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from apex.memory.model import MemoryRecord

_WORD = re.compile(r"[a-z0-9_]+")
_STOPWORDS = frozenset(
    "a au aux avec ce ces cet cette dans de des du elle en est et il ils je la le les leur lui ma mais me mes "
    "moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses si son sur ta te tes toi ton tu un une "
    "vos votre vous y est sont etre avoir fait faire plus tres ca cela comme donc alors aussi "
    "the of and to in is it for on that this with as be are was by an or at from not but have has"
    .split()
)
K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    out: list[str] = []
    for word in _WORD.findall(folded):
        if len(word) < 2 or word in _STOPWORDS:
            continue
        out.append(word)
        if "_" in word:
            out.extend(part for part in word.split("_") if len(part) >= 2 and part not in _STOPWORDS)
    return out


@dataclass(frozen=True)
class Retrieved:
    record: MemoryRecord
    score: float
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.record.id, "category": self.record.category.value if self.record.category else None,
            "source": self.record.source, "score": round(self.score, 4), "snippet": self.snippet,
            "created_at": self.record.created_at.isoformat(),
        }


def score_candidates(query: str, records: Sequence[MemoryRecord]) -> list[tuple[MemoryRecord, float]]:
    terms = set(tokenize(query))
    if not terms or not records:
        return []
    docs = [Counter(tokenize(r.content)) for r in records]
    lengths = [sum(d.values()) for d in docs]
    average = (sum(lengths) / len(lengths)) or 1.0
    n = len(records)
    df = {t: sum(1 for d in docs if t in d) for t in terms}
    scored = []
    for record, doc, length in zip(records, docs, lengths, strict=True):
        score = 0.0
        for term in terms:
            tf = doc.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / average))
        if score > 0:
            scored.append((record, score))
    scored.sort(key=lambda item: (-item[1], item[0].created_at), reverse=False)
    return scored


def snippet(content: str, query: str, max_chars: int) -> str:
    """Extrait centré sur la première occurrence d'un terme de la requête (borné)."""
    if len(content) <= max_chars:
        return content
    lowered = content.lower()
    positions = [lowered.find(t) for t in tokenize(query)]
    positions = [p for p in positions if p >= 0]
    start = max(0, min(positions) - max_chars // 4) if positions else 0
    return ("…" if start else "") + content[start:start + max_chars] + "…"


def render_context(items: Sequence[Retrieved], *, header: str) -> str:
    lines = [header]
    for item in items:
        category = item.record.category.value if item.record.category else "mémoire"
        lines.append(f"- [{category} · {item.record.source}] {item.snippet}")
    return "\n".join(lines)
