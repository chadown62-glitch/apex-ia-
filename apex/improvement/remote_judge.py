"""Juge exécuté dans un PROCESSUS SÉPARÉ, avec une définition éventuellement
ÉPINGLÉE à une révision git antérieure à la modification jugée.

    moteur ──(subprocess)──> python -m apex.improvement judge-worker ...
                              └─ charge build_judge() depuis le module du juge :
                                 - arbre de travail courant, ou
                                 - `git show <judge_ref>:<module>` (définition figée)
                              └─ exécute les sondes + le debugger
                              └─ imprime UNE ligne JSON : résultats par sonde + findings

Le processus ne renvoie jamais de décision : le moteur agrège lui-même avec
`GauntletRunner`. Toute panne (code de sortie non nul, délai dépassé, sortie
illisible) devient un résultat INCONCLUSIVE — jamais un PASS.

Avec `judge_ref`, la modification jugée ne peut pas affaiblir les sondes qui
la jugent : elles sont lues depuis la révision épinglée, tandis que le code
sous test est celui de l'arbre de travail."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from apex.improvement.judge import JudgeAssessment
from apex.improvement.model import CycleKind, Finding
from apex.metalearner.judge_model import JudgeDecision, JudgeDimension, JudgeEvidence, JudgeResult

RESULT_PREFIX = "APEX_JUDGE_ASSESSMENT:"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _failure(judge_id: str, detail: str, provenance: dict[str, str]) -> JudgeAssessment:
    return JudgeAssessment(
        results=(
            JudgeResult(
                judge_id=f"{judge_id}:process",
                judge_version="1",
                dimension=JudgeDimension.CORRECTNESS,
                decision=JudgeDecision.INCONCLUSIVE,
                score=None,
                confidence=0.0,
                rationale=f"judge_error: {detail}",
                evidence=(JudgeEvidence(kind="judge_process", reference=judge_id, detail=detail),),
            ),
        ),
        findings=(),
        provenance={**provenance, "error": detail},
    )


def encode_assessment(assessment: JudgeAssessment) -> str:
    return RESULT_PREFIX + json.dumps(
        {
            "results": [r.to_dict() for r in assessment.results],
            "findings": [f.to_dict() for f in assessment.findings],
            "provenance": assessment.provenance,
        },
        ensure_ascii=False,
    )


def decode_assessment(stdout: str) -> JudgeAssessment:
    lines = [line for line in stdout.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(lines) != 1:
        raise ValueError(f"{len(lines)} ligne(s) de résultat au lieu d'une")
    data = json.loads(lines[0][len(RESULT_PREFIX) :])
    return JudgeAssessment(
        results=tuple(JudgeResult.from_dict(r) for r in data["results"]),
        findings=tuple(Finding.from_dict(f) for f in data["findings"]),
        provenance={str(k): str(v) for k, v in data["provenance"].items()},
    )


def pinned_module_source(judge_ref: str, module_path: str, *, repo_root: Path = _REPO_ROOT) -> str:
    if not judge_ref or judge_ref.startswith("-"):
        raise ValueError(f"révision de juge invalide : {judge_ref!r}")
    completed = subprocess.run(  # noqa: S603 - argv en liste, référence validée ci-dessus
        ["git", "show", f"{judge_ref}:{module_path}"],  # noqa: S607 - git résolu par PATH, comme ailleurs
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"définition du juge introuvable à {judge_ref}:{module_path} — {completed.stderr.strip()}"
        )
    return completed.stdout


class SubprocessJudge:
    def __init__(
        self,
        judge_id: str,
        *,
        capability: str,
        module_path: str,
        judge_ref: str | None = None,
        timeout_s: float = 1800.0,
        python: str = sys.executable,
        repo_root: Path = _REPO_ROOT,
    ) -> None:
        self._judge_id = judge_id
        self._capability = capability
        self._module_path = module_path
        self._judge_ref = judge_ref
        self._timeout_s = timeout_s
        self._python = python
        self._repo_root = repo_root

    @property
    def judge_id(self) -> str:
        return self._judge_id

    def _pinning_facts(self, pinned_source: str) -> dict[str, str]:
        """Faits vérifiés, jamais une affirmation d'antériorité non contrôlée."""
        working = self._repo_root / self._module_path
        differs = not working.exists() or working.read_text(encoding="utf-8") != pinned_source
        ancestor = subprocess.run(  # noqa: S603 - argv en liste, référence déjà validée
            ["git", "merge-base", "--is-ancestor", self._judge_ref or "", "HEAD"],  # noqa: S607
            cwd=self._repo_root, capture_output=True, check=False,
        )
        relation = {0: "oui", 1: "non"}.get(ancestor.returncode, "inconnu")
        return {
            "judge_ref_is_ancestor_of_head": relation,
            "judge_definition_differs_from_working_tree": "oui" if differs else "non",
        }

    def assess(
        self,
        kind: CycleKind,
        *,
        author_id: str,
        capability_id: str,
        cycle_index: int,
        version: str,
        cycle_id: str,
    ) -> JudgeAssessment:
        provenance = {"mode": "subprocess", "judge_ref": self._judge_ref or "arbre de travail"}
        if self._judge_ref is None:
            provenance["independence"] = "processus séparé ; sondes de l'arbre de travail du candidat"
        command = [
            self._python,
            "-m",
            "apex.improvement",
            "judge-worker",
            "--capability",
            self._capability,
            "--kind",
            kind.value,
            "--author",
            author_id,
            "--version",
            version,
            "--cycle-index",
            str(cycle_index),
            "--cycle-id",
            cycle_id,
            "--judge-id",
            self._judge_id,
        ]
        with tempfile.TemporaryDirectory(prefix="apex-judge-") as tmp:
            if self._judge_ref is not None:
                try:
                    source = pinned_module_source(
                        self._judge_ref, self._module_path, repo_root=self._repo_root
                    )
                except ValueError as exc:
                    return _failure(self._judge_id, str(exc), provenance)
                module_file = Path(tmp) / "pinned_judge.py"
                module_file.write_text(source, encoding="utf-8")
                provenance["judge_source_sha256"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
                provenance.update(self._pinning_facts(source))
                command += ["--judge-module", str(module_file)]
            try:
                completed = subprocess.run(  # noqa: S603 - argv en liste, aucun shell
                    command,
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return _failure(self._judge_id, f"délai de {self._timeout_s}s dépassé", provenance)
        if completed.returncode != 0:
            tail = completed.stderr.strip().splitlines()[-1:] or ["aucun détail"]
            return _failure(
                self._judge_id, f"code de sortie {completed.returncode} : {tail[0]}", provenance
            )
        try:
            assessment = decode_assessment(completed.stdout)
        except (ValueError, KeyError, TypeError) as exc:
            return _failure(self._judge_id, f"sortie illisible : {exc}", provenance)
        return JudgeAssessment(
            results=assessment.results,
            findings=assessment.findings,
            provenance={**assessment.provenance, **provenance},
        )
