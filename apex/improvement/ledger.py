"""Historique expérimental append-only (JSON Lines).

Deux types d'entrées : `snapshot` (une mesure datée d'une version) et
`cycle` (un `CycleRecord` complet). Jamais de réécriture ni de suppression :
une erreur se corrige par une nouvelle entrée. Choix d'un fichier plutôt
qu'une table : aucun consommateur concurrent n'existe, et un historique
versionnable dans le dépôt est lui-même une preuve relisible (voir
`docs/improvement/`).

Chaînage : chaque nouvelle entrée porte `prev`, le SHA-256 de TOUT le
fichier qui la précède, et `sha256`, l'empreinte de son propre contenu.
Modifier une entrée, ou supprimer/insérer une ligne avant une entrée
chaînée, est détecté (`verify`) ; le moteur refuse alors de lancer un
cycle. Les entrées antérieures au chaînage (sans `prev`) sont couvertes par
la première entrée chaînée qui les suit.

Limite : quelqu'un qui peut écrire le fichier peut aussi recalculer toute la
chaîne. L'ancrage externe est l'historique git du ledger versionné."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from apex.improvement.model import BenchmarkSnapshot, CycleRecord


def _entry_digest(entry: dict[str, Any]) -> str:
    content = {k: v for k, v in entry.items() if k != "sha256"}
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


class LedgerIntegrityError(ValueError):
    """Le ledger a été modifié après coup — aucun cycle ne se fonde dessus."""


class ImprovementLedger:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _raw(self) -> bytes:
        return self._path.read_bytes() if self._path.exists() else b""

    def _append(self, entry: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = {**entry, "prev": hashlib.sha256(self._raw()).hexdigest()}
        entry["sha256"] = _entry_digest(entry)
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

    def verify(self) -> list[str]:
        """Problèmes d'intégrité constatés (liste vide = chaîne intacte)."""
        problems: list[str] = []
        raw = self._raw()
        offset = 0
        chained = False
        for number, line in enumerate(raw.splitlines(keepends=True), start=1):
            if line.strip():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    problems.append(f"ligne {number} : JSON invalide")
                    offset += len(line)
                    continue
                prev = entry.get("prev") if isinstance(entry, dict) else None
                if prev is None:
                    if chained:
                        problems.append(f"ligne {number} : entrée non chaînée après le début du chaînage")
                else:
                    if prev != hashlib.sha256(raw[:offset]).hexdigest():
                        problems.append(f"ligne {number} : le contenu qui la précède a été modifié")
                    if entry.get("sha256") != _entry_digest(entry):
                        problems.append(f"ligne {number} : entrée modifiée après écriture")
                chained = chained or prev is not None
            offset += len(line)
        return problems

    def require_intact(self) -> None:
        problems = self.verify()
        if problems:
            raise LedgerIntegrityError(f"{self._path} : " + " ; ".join(problems))

    def contains_snapshot(self, snapshot: BenchmarkSnapshot) -> bool:
        digest = snapshot.digest()
        return any(s.digest() == digest for s in self.snapshots(snapshot.capability_id))

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
