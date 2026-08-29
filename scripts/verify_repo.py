#!/usr/bin/env python3
"""Vérifie l'intégrité et la complétude du dépôt.

Compare chaque fichier à MANIFEST.sha256. Trois verdicts possibles :
fichier manquant, fichier modifié, fichier non répertorié. Aucun n'est
silencieux — un dépôt partiellement extrait produirait autrement des échecs
de test incompréhensibles.

Stdlib uniquement : doit fonctionner avant toute installation.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

MANIFEST = "MANIFEST.sha256"
IGNORED = {"__pycache__", ".git", ".apex", ".venv", "node_modules"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(root: Path):
    for item in sorted(root.rglob("*")):
        if item.is_file() and not any(part in IGNORED for part in item.parts):
            rel = item.relative_to(root)
            if str(rel) != MANIFEST:
                yield rel, item


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        print(f"ECHEC: {MANIFEST} introuvable — archive incomplete ou mauvaise racine")
        print(f"       racine testee: {root}")
        return 2

    expected: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            sha, _, name = line.partition("  ")
            expected[name.strip()] = sha.strip()

    actual = {str(rel): path for rel, path in walk(root)}

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    modified = sorted(
        name for name in set(expected) & set(actual) if digest(actual[name]) != expected[name]
    )

    for label, items in (("MANQUANT", missing), ("MODIFIE", modified), ("NON REPERTORIE", extra)):
        for name in items:
            print(f"{label:<15} {name}")

    total = len(expected)
    ok = total - len(missing) - len(modified)
    print(f"\n{ok}/{total} fichiers conformes")

    if missing or modified:
        print("VERDICT: depot INCOMPLET ou ALTERE — ne pas executer le Gate")
        return 1
    if extra:
        print("VERDICT: depot complet, fichiers supplementaires presents (sans gravite)")
        return 0
    print("VERDICT: depot complet et intact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
