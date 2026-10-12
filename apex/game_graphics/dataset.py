"""Append verified, locally created examples; never infer rights to third-party assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apex.game_graphics.models import file_sha256


def record_example(
    path: Path,
    *,
    family: str,
    split: str,
    prompt: str,
    root: Path,
    verdict: str,
    script: Path,
    correction: dict[str, Any] | None = None,
) -> None:
    if split not in ("train", "heldout"):
        raise ValueError("invalid_dataset_split")
    manifest = json.loads((root / "manifest.json").read_text())
    for entry in manifest["files"]:
        artifact = root / entry["path"]
        if artifact.parent.resolve() != root.resolve() or file_sha256(artifact) != entry["sha256"]:
            raise ValueError("dataset_artifact_unverified")
    existing = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    if any(row["family"] == family and row["split"] != split for row in existing):
        raise ValueError("dataset_family_leakage")
    row = {
        "family": family,
        "split": split,
        "prompt": prompt,
        "artifact_root": str(root),
        "manifest_sha256": file_sha256(root / "manifest.json"),
        "files": manifest["files"],
        "script": str(script),
        "script_sha256": file_sha256(script),
        "verdict": verdict,
        "judgement": json.loads((root / "judge-report.json").read_text()),
        "correction": correction,
        "provenance": "own prompts, procedural script, Blender geometry and renders",
        "rights": "LicenseRef-APEX-User-Authorized-Internal; no external redistribution license assigned",
        "training_eligible": split == "train" and verdict == "pass",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row) + "\n")
