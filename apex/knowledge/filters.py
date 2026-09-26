"""Filtres du Knowledge Agent — chacun renvoie des mesures, jamais un verdict
caché. Ce sont des heuristiques explicites : elles signalent, la curation
décide, et un doute mène à REVIEW ou QUARANTINE, jamais à ACCEPT."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

_STOPWORDS = {
    "en": {
        "the",
        "and",
        "of",
        "to",
        "is",
        "in",
        "that",
        "it",
        "for",
        "with",
        "as",
        "are",
        "this",
        "be",
        "on",
    },
    "fr": {
        "le",
        "la",
        "les",
        "de",
        "des",
        "et",
        "est",
        "un",
        "une",
        "du",
        "que",
        "qui",
        "dans",
        "pour",
        "pas",
    },
}

INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore\s+(all\s+|the\s+|any\s+)?(previous|prior|above|earlier)?\s*instructions",
    r"disregard\s+(all\s+|the\s+|any\s+)?(previous|prior|above)?\s*(instructions|rules)",
    r"ignore[rz]?\s+(toutes\s+)?les\s+(instructions|règles|consignes)",
    r"you\s+are\s+now\b",
    r"tu\s+es\s+(maintenant|désormais)\b",
    r"system\s+prompt",
    r"prompt\s+syst[eè]me",
    r"\bnew\s+instructions?\s*:",
    r"nouvelles?\s+instructions?\s*:",
    r"ex[ée]cute[rz]?\s+(la\s+commande|ce\s+code|les\s+commandes|the\s+following|this\s+command)",
    r"\brm\s+-rf\b",
    r"curl\s+[^\n|]*\|\s*(ba)?sh",
    r"<\|im_start\|>|\[/?INST\]|###\s*system",
    r"(mark|classify|set)\s+(this|it)\s+(as\s+)?(accept|trusted|verified|high[_ ]value)",
    r"(marque|classe)[rz]?\s+(ceci|ce\s+contenu)\s+comme\s+(accept|fiable|vérifié)",
    r"reveal\s+(your|the)\s+(secrets?|password|api\s*key|system)",
)
_INJECTION = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def detect_language(text: str) -> tuple[str, float]:
    tokens = words(text)
    if not tokens:
        return "unknown", 0.0
    scores = {lang: sum(1 for t in tokens if t in stop) / len(tokens) for lang, stop in _STOPWORDS.items()}
    lang, score = max(scores.items(), key=lambda item: item[1])
    return (lang, score) if score >= 0.03 else ("unknown", score)


def relevance(text: str, topics: tuple[str, ...]) -> float:
    """Fraction des thèmes demandés présents dans le texte (1.0 sans thème)."""
    if not topics:
        return 1.0
    vocabulary = set(words(text))
    hits = sum(1 for topic in topics if set(words(topic)) <= vocabulary)
    return hits / len(topics)


@dataclass(frozen=True)
class QualityScore:
    score: float
    n_words: int
    alpha_ratio: float
    unique_line_ratio: float
    mean_word_length: float
    reasons: tuple[str, ...]
    lexical_diversity: float = 1.0


def quality(text: str) -> QualityScore:
    tokens = words(text)
    n_words = len(tokens)
    chars = [c for c in text if not c.isspace()]
    alpha_ratio = sum(c.isalpha() for c in chars) / len(chars) if chars else 0.0
    lines = [line for line in text.split("\n") if line.strip()]
    unique_line_ratio = len(set(lines)) / len(lines) if lines else 0.0
    mean_word_length = sum(map(len, tokens)) / n_words if n_words else 0.0
    lexical_diversity = len(set(tokens)) / n_words if n_words else 0.0
    reasons: list[str] = []
    score = 1.0
    if n_words < 40:
        # Décisif à lui seul : un fragment de quelques mots n'apprend rien.
        score -= 0.6
        reasons.append(f"trop court ({n_words} mots)")
    elif n_words < 120:
        score -= 0.2
        reasons.append(f"court ({n_words} mots)")
    if alpha_ratio < 0.5:
        score -= 0.3
        reasons.append(f"peu de texte ({alpha_ratio:.0%} de lettres)")
    if unique_line_ratio < 0.6:
        score -= 0.6
        reasons.append(f"répétitif ({unique_line_ratio:.0%} de lignes uniques)")
    if n_words >= 40 and lexical_diversity < 0.2:
        score -= 0.6
        reasons.append(f"vocabulaire répétitif ({lexical_diversity:.0%} de mots distincts)")
    if n_words and not 2.5 <= mean_word_length <= 12:
        score -= 0.2
        reasons.append(f"longueur de mot atypique ({mean_word_length:.1f})")
    return QualityScore(
        max(0.0, score),
        n_words,
        alpha_ratio,
        unique_line_ratio,
        mean_word_length,
        tuple(reasons),
        lexical_diversity,
    )


def injection_matches(text: str) -> tuple[str, ...]:
    return tuple(m.group(0) for pattern in _INJECTION if (m := pattern.search(text)))


def _shingles(text: str, size: int = 5) -> set[str]:
    tokens = words(text)
    if len(tokens) < size:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def minhash(text: str, permutations: int = 64) -> tuple[int, ...]:
    shingles = _shingles(text)
    if not shingles:
        return tuple([0] * permutations)
    return tuple(
        min(
            int.from_bytes(
                hashlib.blake2b(s.encode(), digest_size=8, salt=seed.to_bytes(8, "little")).digest(),
                "little",
            )
            for s in shingles
        )
        for seed in range(permutations)
    )


def similarity(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)


class DuplicateIndex:
    """Doublons exacts (texte normalisé) et quasi-doublons (MinHash)."""

    def __init__(self, threshold: float = 0.8) -> None:
        self.threshold = threshold
        self._exact: dict[str, str] = {}
        self._signatures: list[tuple[str, tuple[int, ...]]] = []

    def find(self, text: str) -> tuple[str | None, float]:
        """(identifiant de l'original, similarité) ou (None, meilleure similarité).
        Ne modifie pas l'index : seul `add` y inscrit un texte."""
        key = hashlib.sha256(" ".join(words(text)).encode()).hexdigest()
        if key in self._exact:
            return self._exact[key], 1.0
        signature = minhash(text)
        best_id, best = None, 0.0
        for other_id, other in self._signatures:
            sim = similarity(signature, other)
            if sim > best:
                best_id, best = other_id, sim
        return (best_id, best) if best >= self.threshold else (None, best)

    def add(self, item_id: str, text: str) -> None:
        key = hashlib.sha256(" ".join(words(text)).encode()).hexdigest()
        self._exact.setdefault(key, item_id)
        self._signatures.append((item_id, minhash(text)))

    def check(self, item_id: str, text: str) -> tuple[str | None, float]:
        """`find` puis `add` si le texte est nouveau."""
        original, sim = self.find(text)
        if original is None:
            self.add(item_id, text)
        return original, sim


_CLAIM = re.compile(
    r"(?P<subject>[\w' -]{3,60}?)\s+(?P<verb>is|are|est|sont)\s+"
    r"(?P<neg>not\s+|pas\s+|n'est\s+pas\s+)?(?P<object>[\w' -]{2,60}?)[.;\n]",
    re.IGNORECASE,
)
_NUMERIC = re.compile(
    r"(?P<subject>[A-Za-z_][\w .-]{1,40}?)\s*(?:=|equals|vaut|égale?)\s*(?P<value>-?\d+(?:\.\d+)?)"
)


@dataclass(frozen=True)
class Claim:
    subject: str
    value: str
    negated: bool


def claims(text: str) -> tuple[Claim, ...]:
    found: list[Claim] = []
    for m in _CLAIM.finditer(text):
        found.append(
            Claim(
                " ".join(words(m.group("subject"))),
                " ".join(words(m.group("object"))),
                bool(m.group("neg")),
            )
        )
    for m in _NUMERIC.finditer(text):
        found.append(Claim(" ".join(words(m.group("subject"))), m.group("value"), False))
    return tuple(c for c in found if c.subject and c.value)


class ClaimIndex:
    """Contradiction : même sujet, même objet, négation opposée ; ou même
    sujet numérique, valeur différente. Volontairement conservateur."""

    def __init__(self) -> None:
        self._claims: dict[str, list[tuple[str, Claim]]] = {}

    def conflicts(self, text: str) -> tuple[tuple[str, Claim, Claim], ...]:
        conflicts = []
        for claim in claims(text):
            for other_id, other in self._claims.get(claim.subject, []):
                numeric = (
                    claim.value.replace(".", "", 1).lstrip("-").isdigit()
                    and other.value.replace(".", "", 1).lstrip("-").isdigit()
                )
                if (numeric and claim.value != other.value) or (
                    not numeric and claim.value == other.value and (claim.negated != other.negated)
                ):
                    conflicts.append((other_id, other, claim))
        return tuple(conflicts)

    def add(self, item_id: str, text: str) -> None:
        for claim in claims(text):
            self._claims.setdefault(claim.subject, []).append((item_id, claim))
