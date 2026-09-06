"""Empreinte de contenu d'un artefact Runtime — pour prouver la
reproductibilité, pas simplement l'affirmer.

`hash_tree_detailed()` retourne le hash SHA-256 de CHAQUE fichier (pour
diagnostiquer précisément où deux builds divergeraient) ; `hash_tree()`
agrège ces hashes en une seule empreinte (ordre des chemins trié — jamais
l'ordre de parcours du système de fichiers, qui n'est pas garanti stable)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_tree_detailed(root: Path, *, exclude_names: frozenset[str] = frozenset()) -> dict[str, str]:
    """Chemin relatif POSIX -> sha256 hexdigest du contenu, pour chaque
    fichier régulier sous `root`, triés. `exclude_names` retire des
    fichiers par NOM (ex. le manifeste lui-même, qui contient un
    horodatage et ne peut donc pas s'auto-inclure dans son propre hash)."""
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in exclude_names:
            continue
        rel = path.relative_to(root).as_posix()
        digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def hash_tree(root: Path, *, exclude_names: frozenset[str] = frozenset()) -> str:
    """Empreinte agrégée unique : sha256 de la concaténation triée de
    `"<chemin>\\0<hash>\\n"` pour chaque fichier — sensible à un fichier
    ajouté/retiré/modifié OU déplacé, insensible à l'ordre de parcours du
    système de fichiers."""
    detailed = hash_tree_detailed(root, exclude_names=exclude_names)
    aggregate = hashlib.sha256()
    for rel_path in sorted(detailed):
        aggregate.update(rel_path.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(detailed[rel_path].encode("utf-8"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def diff_trees(a: dict[str, str], b: dict[str, str]) -> dict[str, list[str]]:
    """Compare deux résultats de `hash_tree_detailed()`. Retourne
    `{"only_in_a": [...], "only_in_b": [...], "different": [...]}`, toutes
    vides si les deux arbres sont identiques."""
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    different = sorted(p for p in (set(a) & set(b)) if a[p] != b[p])
    return {"only_in_a": only_a, "only_in_b": only_b, "different": different}
