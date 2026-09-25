"""Historique expérimental append-only (JSON Lines).

Deux types d'entrées : `snapshot` (une mesure datée d'une version) et
`cycle` (un `CycleRecord` complet). Jamais de réécriture ni de suppression :
une erreur se corrige par une nouvelle entrée. Choix d'un fichier plutôt
qu'une table : aucun consommateur concurrent n'existe, et un historique
versionnable dans le dépôt est lui-même une preuve relisible (voir
`docs/improvement/`)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from apex.improvement.model import BenchmarkSnapshot, CycleRecord


class ImprovementLedger:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _append(self, entry: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append_snapshot(self, snapshot: BenchmarkSnapshot) -> None:
        self._append({"type": "snapshot", "data": snapshot.to_dict()})

    def append_cycle(self, record: CycleRecord) -> None:
        self._append({"type": "cycle", "data": record.to_dict()})

    def append_judgement(
        self,
        *,
        capability_id: str,
        version: str,
        kind: str,
        gauntlet: dict[str, Any],
        findings: list[dict[str, Any]],
    ) -> None:
        """Évaluation du juge SANS cycle (baseline : « quels défauts le juge
        trouve-t-il avant toute modification ? »)."""
        self._append(
            {
                "type": "judgement",
                "data": {
                    "capability_id": capability_id,
                    "version": version,
                    "kind": kind,
                    "gauntlet": gauntlet,
                    "findings": findings,
                },
            }
        )

    def judgements(self, capability_id: str) -> list[dict[str, Any]]:
        return [
            e["data"]
            for e in self.entries()
            if e.get("type") == "judgement" and e["data"]["capability_id"] == capability_id
        ]

    def entries(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        entries = []
        with self._path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{self._path}:{number} : entrée corrompue ({exc})") from exc
        return entries

    def snapshots(self, capability_id: str) -> list[BenchmarkSnapshot]:
        return [
            BenchmarkSnapshot.from_dict(e["data"])
            for e in self.entries()
            if e.get("type") == "snapshot" and e["data"]["capability_id"] == capability_id
        ]

    def latest_snapshot(self, capability_id: str, version: str) -> BenchmarkSnapshot | None:
        matching = [s for s in self.snapshots(capability_id) if s.version == version]
        return matching[-1] if matching else None

    def cycles(self, capability_id: str) -> list[dict[str, Any]]:
        return [
            e["data"]
            for e in self.entries()
            if e.get("type") == "cycle" and e["data"]["capability_id"] == capability_id
        ]
