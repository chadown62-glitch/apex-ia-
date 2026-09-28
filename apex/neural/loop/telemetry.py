"""Télémétrie VIVANTE d'une expérience : `runs/<expérience>/telemetry.jsonl`.

Le journal reste le seul registre des décisions (la reprise après interruption repart de son dernier
événement) ; la télémétrie dit seulement où en est le travail en cours (phase, pas, perte). Une ligne
JSON par événement, écrite et vidée immédiatement : un autre processus (API, interface) la lit
pendant que l'expérience tourne."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

FILENAME = "telemetry.jsonl"

TRAINING_STARTED = "training_started"
DATASET_READY = "dataset_ready"
TRAINING_PROGRESS = "training_progress"
TRAINING_COMPLETED = "training_completed"
JUDGE_STARTED = "judge_started"
REPLICATION_STARTED = "replication_started"


class Telemetry:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / FILENAME

    def emit(self, phase: str, **fields: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": round(time.time(), 3), "phase": phase, **fields}) + "\n")
            handle.flush()


def read(run_dir: Path) -> list[dict[str, Any]]:
    """Lignes complètes seulement : une ligne en cours d'écriture est ignorée."""
    path = run_dir / FILENAME
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
