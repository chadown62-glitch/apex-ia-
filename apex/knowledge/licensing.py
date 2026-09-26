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

from apex.knowledge.model import LicenseInfo, LicenseStatus

_FREE = ("cc0", "public domain", "public-domain", "unlicense", "domaine public")
_RESTRICTED = (
    "all rights reserved",
    "tous droits réservés",
    "proprietary",
    "propriétaire",
    "-nc",
    "-nd",
    "noncommercial",
    "non-commercial",
    "noderivatives",
)
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
    for marker in _RESTRICTED:
        if marker in text:
            return LicenseInfo(LicenseStatus.RESTRICTED, hint.strip(), f"clause restrictive « {marker} »")
    for marker in _FREE:
        if marker in text:
            return LicenseInfo(LicenseStatus.FREE, hint.strip(), f"marqueur « {marker} »")
    for identifier, pattern in _COMPATIBLE:
        if re.search(pattern, text):
            return LicenseInfo(LicenseStatus.COMPATIBLE, identifier, f"licence reconnue « {identifier} »")
    return LicenseInfo(LicenseStatus.UNKNOWN, hint.strip(), "licence non reconnue")
