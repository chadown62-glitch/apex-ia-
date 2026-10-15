"""Construit l'artefact APEX Runtime — deux sources possibles, jamais
confondues dans le manifeste produit.

A) `build_runtime(commit, out_dir)` — RELEASE. `git archive` matérialise
   le contenu EXACT d'un commit Git résolu en SHA complet (aucune
   modification du dépôt de travail, aucune dépendance à un fichier non
   committé). Reproductible par construction : le même commit produit
   toujours le même `apex/` byte-pour-byte (voir `hashing.py` et
   `tests/test_runtime_packaging.py::TestReproducibility`).

B) `build_runtime_from_worktree(out_dir)` — VALIDATION, jamais une
   release. Copie directement l'arbre de travail courant (peut inclure des
   modifications non committées) — utile pour valider un Runtime incluant
   des phases pas encore committées (ex. Phase 0/1 de cette session). Le
   manifeste marque explicitement `"release": false`, enregistre le SHA de
   HEAD à titre INFORMATIF SEULEMENT (ce n'est PAS le contenu construit
   s'il y a des modifications non committées) et l'état propre/sale du
   dépôt au moment du build. Ne fabrique JAMAIS de faux commit, ne modifie
   JAMAIS l'historique Git, ne committe JAMAIS automatiquement.

Usage CLI :
    python -m runtime_build.build --source commit --commit HEAD --out dist/apex-runtime
    python -m runtime_build.build --source worktree --out dist/apex-runtime-dev
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from runtime_build.hashing import hash_tree
from runtime_build.selection import (
    RESEARCH_PACKAGES,
    RUNTIME_FILES,
    RUNTIME_PACKAGES,
    verify_partition_matches_repo,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST_NAME = "RUNTIME_BUILD_MANIFEST.json"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _is_working_tree_dirty() -> bool:
    return bool(_git("status", "--porcelain"))


def _extract_commit(commit: str, dest: Path) -> str:
    """Matérialise le contenu exact de `commit` dans `dest` via `git
    archive` — jamais une copie du répertoire de travail. Retourne le SHA
    complet résolu (le manifeste de release doit toujours pointer un
    commit exact, jamais une référence mouvante comme `HEAD`)."""
    resolved = _git("rev-parse", commit)
    with tempfile.NamedTemporaryFile(suffix=".tar") as tar_file:
        subprocess.run(
            ["git", "archive", "--format=tar", resolved, "-o", tar_file.name],
            cwd=REPO_ROOT,
            check=True,
        )
        with tarfile.open(tar_file.name) as tar:
            tar.extractall(dest, filter="data")
    return resolved


def _copy_runtime_apex(src_apex: Path, dest_apex: Path) -> None:
    dest_apex.mkdir(parents=True, exist_ok=True)
    for name in sorted(RUNTIME_FILES):
        src = src_apex / name
        if not src.is_file():
            raise FileNotFoundError(f"fichier Runtime attendu absent de la source : apex/{name}")
        shutil.copy2(src, dest_apex / name)
    for name in sorted(RUNTIME_PACKAGES):
        src = src_apex / name
        if not src.is_dir():
            raise FileNotFoundError(f"sous-paquet Runtime attendu absent de la source : apex/{name}")
        shutil.copytree(
            src,
            dest_apex / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )


def _assemble_common(src_root: Path, out_dir: Path) -> None:
    """Partie commune aux deux modes : copie `apex/` filtré + migrations
    (chaîne officielle complète) + alembic.ini + config.yaml + pyproject
    Runtime, à partir d'une racine déjà matérialisée sur disque
    (extraction git archive OU répertoire de travail direct)."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    src_apex = src_root / "apex"
    verify_partition_matches_repo(src_apex)
    _copy_runtime_apex(src_apex, out_dir / "apex")

    # Fixed trusted Blender/UE workers and the frozen judging contract are
    # runtime inputs.  Large proof bundles and model weights stay outside
    # the distribution; operators install weights explicitly.
    worker_source = src_root / "scripts" / "game_graphics"
    if worker_source.is_dir():
        shutil.copytree(worker_source, out_dir / "scripts" / "game_graphics",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    benchmark = src_root / "docs/game_graphics/studio/benchmark-v1.json"
    if benchmark.is_file():
        target = out_dir / "docs/game_graphics/studio/benchmark-v1.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(benchmark, target)

    # Chaîne Alembic officielle COMPLÈTE, jamais un sous-ensemble — voir
    # runtime_build/README.md, décision explicite : une seule chaîne de
    # migrations partagée, y compris les migrations qui créent des tables
    # aujourd'hui liées à la recherche (skills, genome_versions, tasks,
    # eval_runs). Leur présence en base ne rend aucun module recherche
    # importable ni actif côté Runtime (voir verify.py).
    shutil.copytree(
        src_root / "migrations",
        out_dir / "migrations",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(src_root / "alembic.ini", out_dir / "alembic.ini")
    shutil.copy2(src_root / "config.yaml", out_dir / "config.yaml")
    # Configuration du profil autonome (APEX.exe) : même schéma que config.yaml, sans infrastructure serveur.
    shutil.copy2(src_root / "config.standalone.yaml", out_dir / "config.standalone.yaml")

    # Cerveau APEX servi (0.4.1) : poids exportés d'une version PROMUE du registre (`brain/<nom>/`, safetensors,
    # jamais un checkpoint d'entraînement), vérifiés à chaque chargement (SHA-256 du fichier, empreinte des poids).
    brain = src_root / "brain"
    if brain.is_dir():
        shutil.copytree(brain, out_dir / "brain")

    frontend_dist = src_root / "frontend" / "dist"
    if frontend_dist.is_dir():
        shutil.copytree(frontend_dist, out_dir / "frontend_dist")

    pyproject_template = Path(__file__).resolve().parent / "pyproject.runtime.toml"
    shutil.copy2(pyproject_template, out_dir / "pyproject.toml")


def _write_manifest(out_dir: Path, extra_fields: dict[str, object]) -> dict[str, object]:
    manifest: dict[str, object] = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_packages": sorted(RUNTIME_PACKAGES),
        "excluded_research_packages": sorted(RESEARCH_PACKAGES),
        **extra_fields,
    }
    # Le hash de contenu s'auto-exclut (il ne peut pas se contenir
    # lui-même) — calculé sur tout le reste de l'artefact déjà écrit.
    manifest["content_hash_sha256"] = hash_tree(out_dir, exclude_names=frozenset({_MANIFEST_NAME}))
    (out_dir / _MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def build_runtime(commit: str, out_dir: Path) -> dict[str, object]:
    """RELEASE — construit l'artefact Runtime pour `commit` (résolu en SHA
    complet) dans `out_dir` (recréé intégralement à chaque appel). Ne
    dépend jamais du répertoire de travail : deux appels avec le même
    `commit`, même sur des machines différentes, produisent un artefact
    dont `content_hash_sha256` est identique (voir
    `tests/test_runtime_packaging.py::TestReproducibility`)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        resolved_commit = _extract_commit(commit, tmp_path)
        _assemble_common(tmp_path, out_dir)

    return _write_manifest(
        out_dir,
        {
            "release": True,
            "source": "commit",
            "source_commit": resolved_commit,
        },
    )


def build_runtime_from_worktree(out_dir: Path) -> dict[str, object]:
    """VALIDATION — construit l'artefact Runtime depuis le répertoire de
    travail courant (JAMAIS une release : peut contenir des modifications
    non committées). N'exécute aucun `git commit`, ne modifie aucune
    référence Git, ne falsifie jamais un SHA — `head_commit` est
    enregistré à titre INFORMATIF (le dernier commit connu), pas comme le
    contenu réellement construit si `working_tree_dirty` est vrai."""
    _assemble_common(REPO_ROOT, out_dir)
    return _write_manifest(
        out_dir,
        {
            "release": False,
            "source": "worktree",
            "head_commit": _git("rev-parse", "HEAD"),
            "working_tree_dirty": _is_working_tree_dirty(),
            "warning": (
                "build de VALIDATION, pas une release : construit depuis le répertoire de "
                "travail, peut contenir des modifications non committées par rapport à "
                "head_commit — non reproductible depuis un SHA seul."
            ),
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("commit", "worktree"), default="commit")
    parser.add_argument("--commit", default="HEAD", help="commit/branche/tag Git (mode commit uniquement)")
    parser.add_argument("--out", required=True, type=Path, help="répertoire de sortie")
    args = parser.parse_args(argv)

    if args.source == "worktree":
        manifest = build_runtime_from_worktree(args.out)
        print(
            f"APEX Runtime (VALIDATION, non-release) construit depuis l'arbre de travail "
            f"(HEAD={manifest['head_commit']}, dirty={manifest['working_tree_dirty']}) -> {args.out}"
        )
    else:
        manifest = build_runtime(args.commit, args.out)
        print(f"APEX Runtime (release) construit depuis {manifest['source_commit']} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
