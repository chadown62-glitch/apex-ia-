"""Contrôle anti-fuite : vérifie qu'un artefact APEX Runtime construit ne
contient et n'importe AUCUN module classé Research/DEV-only.

Trois vérifications indépendantes, jamais une seule (une whitelist de
fichiers ne suffit pas à elle seule — voir la consigne d'origine) :

1. STATIQUE — présence de fichiers : aucun répertoire `apex/<research>/`
   ne doit exister dans l'artefact.
2. STATIQUE — grep des imports : aucun fichier `.py` de l'artefact ne doit
   contenir `from apex.<research>` / `import apex.<research>`, même dans
   une branche jamais exécutée par un simple test d'import (attrape un
   import mort mais présent, que la vérification 3 seule manquerait).
3. COMPLÉTUDE — chaque `apex.<nom>` importé par un fichier de l'artefact (même
   paresseusement, dans une fonction) existe dans l'artefact : un module de
   premier niveau oublié dans `RUNTIME_FILES` (constaté : `apex/discovery.py`,
   importé par `apex serve`) casse le Runtime au premier usage, pas à l'import.
   Seule exception, déclarée : `OPTIONAL_PACKAGES` (import gardé, absence prévue).
4. DYNAMIQUE — import réel de chaque module Runtime dans un sous-processus
   n'ayant QUE l'artefact sur `sys.path` (jamais le dépôt DEV complet, en
   exécutant depuis `artifact_dir` comme répertoire courant) : si un
   import réussissait via une fuite transitive non détectée par 1/2,
   celle-ci l'attraperait en inspectant `sys.modules` après coup.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from runtime_build.selection import RESEARCH_PACKAGES, RUNTIME_PACKAGES

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+apex\.([a-z_][a-z0-9_]*)", re.MULTILINE)


@dataclass
class VerificationReport:
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def render(self) -> str:
        lines = ["APEX RUNTIME — contrôle anti-fuite", "=" * 60]
        if self.ok:
            lines.append("AUCUNE violation détectée.")
        else:
            lines.append(f"{len(self.violations)} violation(s) détectée(s) :")
            for v in self.violations:
                lines.append(f"  - {v}")
        return "\n".join(lines)


def _check_forbidden_directories(apex_dir: Path) -> list[str]:
    return [
        f"répertoire interdit présent : apex/{pkg}"
        for pkg in sorted(RESEARCH_PACKAGES)
        if (apex_dir / pkg).exists()
    ]


def _check_forbidden_imports(apex_dir: Path) -> list[str]:
    violations: list[str] = []
    for py_file in sorted(apex_dir.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        for match in _IMPORT_RE.finditer(text):
            target = match.group(1)
            if target in RESEARCH_PACKAGES:
                rel = py_file.relative_to(apex_dir.parent)
                violations.append(f"import interdit dans {rel} : apex.{target}")
    return violations


_FROM_APEX_RE = re.compile(r"^\s*from\s+apex\s+import\s+([a-z_][a-z0-9_]*)", re.MULTILINE)

#: Paquets que le Runtime importe SEULEMENT derrière une garde d'absence (la fonctionnalité n'est alors pas
#: offerte, et c'est dit) : leur absence de l'artefact n'est pas une violation de complétude. Jamais une
#: exemption des contrôles de recherche (1, 2, 4). Chacun a un test qui prouve que le Runtime marche
#: sans lui :
#: - `game_studio` : outils de délégation de Game Studio (Codex, PR #25, qui l'ajoute aussi à
#:   `RUNTIME_PACKAGES`), enregistrés par `apex/assistant/tools.py::register_game_studio`
#:   (`tests/test_assistant_game_studio.py`).
OPTIONAL_PACKAGES: frozenset[str] = frozenset({"game_studio"})


def _check_missing_runtime_modules(apex_dir: Path) -> list[str]:
    violations: list[str] = []
    for py_file in sorted(apex_dir.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        targets = {m.group(1) for m in _IMPORT_RE.finditer(text)} | {m.group(1) for m in _FROM_APEX_RE.finditer(text)}
        for target in sorted(targets - RESEARCH_PACKAGES - OPTIONAL_PACKAGES):
            if not (apex_dir / f"{target}.py").is_file() and not (apex_dir / target / "__init__.py").is_file():
                rel = py_file.relative_to(apex_dir.parent)
                violations.append(f"module absent de l'artefact, importé par {rel} : apex.{target}")
    return violations


def _check_dynamic_isolated_import(artifact_dir: Path) -> list[str]:
    """Importe chaque module Runtime dans un sous-processus exécuté depuis
    `artifact_dir` (jamais le dépôt DEV) et vérifie qu'aucun module
    `apex.<research>` n'apparaît dans `sys.modules` après coup."""
    probe = (
        "import sys, json\n"
        f"runtime_names = {sorted(RUNTIME_PACKAGES)!r}\n"
        f"research_names = {sorted(RESEARCH_PACKAGES)!r}\n"
        "errors = []\n"
        "for name in runtime_names:\n"
        "    try:\n"
        "        __import__(f'apex.{name}')\n"
        "    except Exception as exc:\n"
        "        errors.append(f'{name}: {exc.__class__.__name__}: {exc}')\n"
        "leaked = []\n"
        "for mod_name in list(sys.modules):\n"
        "    parts = mod_name.split('.')\n"
        "    if len(parts) >= 2 and parts[0] == 'apex' and parts[1] in research_names:\n"
        "        leaked.append(mod_name)\n"
        "print(json.dumps({'leaked': leaked, 'errors': errors}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=artifact_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    violations: list[str] = []
    if result.returncode != 0:
        violations.append(
            f"sonde d'import isolée en erreur (code {result.returncode}) : "
            f"{result.stderr.strip()[-2000:]}"
        )
        return violations
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        violations.append(f"sortie de sonde illisible : {exc} — {result.stdout!r}")
        return violations
    violations += [f"module research importé dynamiquement : {m}" for m in payload.get("leaked", [])]
    violations += [f"échec d'import d'un module Runtime attendu : {e}" for e in payload.get("errors", [])]
    return violations


def verify_runtime_artifact(artifact_dir: Path) -> VerificationReport:
    apex_dir = artifact_dir / "apex"
    violations: list[str] = []
    violations += _check_forbidden_directories(apex_dir)
    violations += _check_forbidden_imports(apex_dir)
    violations += _check_missing_runtime_modules(apex_dir)
    violations += _check_dynamic_isolated_import(artifact_dir)
    return VerificationReport(violations=violations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    args = parser.parse_args(argv)
    report = verify_runtime_artifact(args.artifact_dir)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
