"""Droits d'usage d'une source. En cas de doute : UNKNOWN, jamais supposé libre.

- FREE : domaine public, CC0, Unlicense.
- COMPATIBLE : licences permissives ou à attribution (MIT, BSD, Apache,
  PSF, ISC, MPL, CC-BY, CC-BY-SA) et la documentation propre d'APEX.
- RESTRICTED : tous droits réservés, clauses non commerciales ou sans
  œuvre dérivée (NC, ND), propriétaire.
- UNKNOWN : tout le reste.

Seuls FREE et COMPATIBLE autorisent la conservation du texte intégral et
l'entraînement ; sinon seules les métadonnées et la provenance sont gardées."""

from __future__ import annotations

import re
import unicodedata

from apex.knowledge.model import LicenseInfo, LicenseStatus

#: Clauses restrictives, cherchées sur la forme TOKENISÉE de l'indication
#: (minuscules, sans accents, ponctuation -> espaces) : « CC-BY-NC »,
#: « CC BY NC » et « cc_by_nc » sont la même licence.
_RESTRICTED_TOKENS = frozenset(
    {"nc", "nd", "noncommercial", "noderivatives", "noderivs", "proprietary", "proprietaire"}
)
_RESTRICTED_PHRASES = (
    "all rights reserved",
    "tous droits reserves",
    "non commercial",
    "no derivatives",
    "no derivative",
    "no derivs",
    "pas d utilisation commerciale",
    "pas de modification",
)
_FREE_PHRASES = ("cc0", "public domain", "unlicense", "domaine public")


def _tokens(text: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.lower()).split())


_COMPATIBLE = (
    ("mit", r"\bmit\b"),
    ("bsd", r"\bbsd\b"),
    ("apache-2.0", r"apache(?:[- ]license)?[- ]?2(?:\.0)?"),
    ("psf-2.0", r"\bpsf\b|python software foundation"),
    ("isc", r"\bisc\b"),
    ("mpl-2.0", r"\bmpl\b|mozilla public license"),
    ("cc-by-sa", r"cc[- ]by[- ]sa"),
    ("cc-by", r"cc[- ]by\b"),
    ("apex-own", r"\bapex-own\b"),
)


def classify_license(hint: str | None) -> LicenseInfo:
    if not hint or not hint.strip():
        return LicenseInfo(LicenseStatus.UNKNOWN, None, "aucune information de licence")
    text = hint.strip().lower()
    tokens = _tokens(hint)
    padded = f" {tokens} "
    for token in tokens.split():
        if token in _RESTRICTED_TOKENS:
            return LicenseInfo(LicenseStatus.RESTRICTED, hint.strip(), f"clause restrictive « {token} »")
    for phrase in _RESTRICTED_PHRASES:
        if f" {phrase} " in padded:
            return LicenseInfo(LicenseStatus.RESTRICTED, hint.strip(), f"clause restrictive « {phrase} »")
    for phrase in _FREE_PHRASES:
        if f" {phrase} " in padded:
            return LicenseInfo(LicenseStatus.FREE, hint.strip(), f"marqueur « {phrase} »")
    for identifier, pattern in _COMPATIBLE:
        if re.search(pattern, text):
            return LicenseInfo(LicenseStatus.COMPATIBLE, identifier, f"licence reconnue « {identifier} »")
    return LicenseInfo(LicenseStatus.UNKNOWN, hint.strip(), "licence non reconnue")
