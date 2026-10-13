"""Provenance and family-separated examples, without claiming any weight training."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apex.game_graphics.models import file_sha256
from apex.game_graphics.studio.judge import verified_manifest

FAMILY_SPLITS = {"vehicle": "train", "building": "validation", "chair": "heldout"}


def record_example(
    path: Path,
    *,
    family: str,
    prompt: str,
    root: Path,
    script: Path,
    provenance: dict[str, Any],
    correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retain rejected examples for audit, but never make them training eligible.

    Split is derived from semantic family, not an output directory or revision.
    A corrected chair remains HELDOUT even if the corrected version passes.
    """
    if family not in FAMILY_SPLITS:
        raise ValueError("unregistered_task_family")
    manifest = verified_manifest(root)
    if manifest.get("family") != family:
        raise ValueError("dataset_family_manifest_mismatch")
    judge = json.loads((root / "judge-report.json").read_text())
    if judge.get("family") != family or judge.get("case_id") != manifest.get("case_id"):
        raise ValueError("dataset_judgement_mismatch")
    if judge.get("manifest_sha256") != file_sha256(root / "manifest.json"):
        raise ValueError("dataset_stale_judgement")
    verdict = judge.get("verdict")
    if verdict not in {"pass", "fail", "unknown", "error", "timeout"}:
        raise ValueError("invalid_persisted_judgement")
    if not isinstance(provenance, dict) or not all(
        provenance.get(key) for key in ("method", "reference", "rights", "model")
    ):
        raise ValueError("dataset_missing_provenance")
    if provenance["method"] == "neural_3d" and not all(
        provenance.get(key) for key in ("weights_sha256", "weights_revision", "weights_source")
    ):
        raise ValueError("neural_weight_provenance_required")
    existing = (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )
    for row in existing:
        if row["family"] not in FAMILY_SPLITS or row["split"] != FAMILY_SPLITS[row["family"]]:
            raise ValueError("dataset_family_leakage")
    digest = file_sha256(root / "manifest.json")
    if any(row["manifest_sha256"] == digest for row in existing):
        raise ValueError("duplicate_dataset_revision")
    split = FAMILY_SPLITS[family]
    eligible = (
        split == "train"
        and verdict == "pass"
        and not judge.get("defects")
        and not judge.get("unavailable")
        and bool(correction and correction.get("plan"))
    )
    row = {
        "schema_version": 1,
        "family": family,
        "split": split,
        "prompt": prompt,
        "artifact_root": str(root.resolve().relative_to(path.parent.resolve())),
        "artifact_paths_relative_to": "dataset directory",
        "manifest_sha256": digest,
        "files": manifest["files"],
        "script": str(script.resolve().relative_to(Path(__file__).resolve().parents[3])),
        "script_path_relative_to": "repository root",
        "script_sha256": file_sha256(script),
        "judge_sha256": file_sha256(root / "judge-report.json"),
        "judgement": judge,
        "verdict": verdict,
        "provenance": provenance,
        "correction": correction,
        "training_eligible": eligible,
        "eligible_scope": "bounded artist operations, technical evidence; no aesthetic ground-truth label",
        "weights_training_executed": False,
        "independent_evaluation_eligible": False,
        "evaluation_limit": ("development benchmark; controller and tools "
                             "were debugged using observed cases"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, allow_nan=False) + "\n")
    return row


def training_readiness(path: Path, benchmark: dict[str, Any]) -> dict[str, Any]:
    rows = (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )
    accepted = {split: [] for split in ("train", "validation", "heldout")}
    for row in rows:
        if row["family"] not in FAMILY_SPLITS or row["split"] != FAMILY_SPLITS[row["family"]]:
            raise ValueError("dataset_family_leakage")
        if (
            row["verdict"] == "pass"
            and not row["judgement"].get("defects")
            and not row["judgement"].get("unavailable")
        ):
            accepted[row["split"]].append(row)
    requirements = benchmark["training"]
    counts = {split: len({row["manifest_sha256"] for row in items}) for split, items in accepted.items()}
    families = len({row["family"] for row in rows if row["verdict"] == "pass"})
    reasons = []
    if counts["train"] < requirements["minimum_accepted_training_examples_before_lora"]:
        reasons.append("insufficient independent accepted TRAIN examples")
    if families < requirements["minimum_task_families_before_lora"]:
        reasons.append("insufficient task-family diversity")
    if counts["validation"] < requirements["minimum_independent_validation_examples"]:
        reasons.append("insufficient independent VALIDATION examples")
    if counts["heldout"] < requirements["minimum_independent_heldout_examples"]:
        reasons.append("insufficient independent HELDOUT examples")
    # Multiple corrections of one asset are not independent baseline tasks.
    reasons.append("independent prompt baseline and controller generalization evaluation remain required")
    return {
        "status": "blocked",
        "training_executed": False,
        "weights_changed": False,
        "accepted_revision_counts": counts,
        "accepted_families": families,
        "independent_heldout_examples": 0,
        "family_splits": FAMILY_SPLITS,
        "requirements": requirements,
        "reasons": reasons,
        "warning": "Revisions are not independent tasks; keep each correction chain in its family split.",
    }
