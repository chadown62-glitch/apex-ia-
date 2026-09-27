"""Mémoire des expériences : journal d'événements en ajout seul, chaîné par
empreintes.

Chaque étape d'une expérience est un ÉVÉNEMENT (planned, started, executed,
judged, analyzed, promotion, interrupted, recovered, aborted) ; l'état d'une
expérience est la fusion de ses événements. Rien n'est réécrit : une
expérience interrompue garde la trace de son interruption, une expérience
rejetée garde sa raison et son analyse. Modifier, supprimer ou réordonner
une ligne casse la chaîne (`verify`).

Ce que la boucle peut demander à sa mémoire :
- « ai-je déjà essayé ce plan ? » (`by_plan`) ;
- « pourquoi cela a-t-il échoué ? » (`analysis` de l'expérience) ;
- « dans quelles conditions cela a-t-il fonctionné ? » (`successes`)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "sha256:" + "0" * 64
TERMINAL_EVENTS = frozenset({"promotion", "aborted"})


class JournalCorruptedError(RuntimeError):
    """La chaîne d'empreintes du journal est rompue."""


def _digest(entry: dict[str, Any]) -> str:
    payload = json.dumps(
        {k: v for k, v in entry.items() if k != "hash"}, sort_keys=True, ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ExperimentRecord:
    experiment_id: str
    events: list[dict[str, Any]] = field(default_factory=list)

    def _last(self, event: str) -> dict[str, Any] | None:
        return next((e for e in reversed(self.events) if e["event"] == event), None)

    @property
    def plan(self) -> dict[str, Any]:
        planned = self._last("planned")
        return planned["plan"] if planned else {}

    @property
    def status(self) -> str:
        return self.events[-1]["event"] if self.events else "unknown"

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_EVENTS

    @property
    def decision(self) -> str | None:
        """Décision FINALE : celle du Judge, sauf si la réplique (graine indépendante) ne l'a pas
        confirmée ; une acceptation non répliquée est un rejet."""
        judged = self._last("judged")
        if judged is None:
            return None
        replicated = self._last("replicated")
        if replicated is not None and not replicated["accepted"]:
            return "reject"
        return str(judged["result"]["decision"])

    @property
    def promoted(self) -> bool:
        promotion = self._last("promotion")
        return bool(promotion and promotion["promoted"])

    @property
    def analysis(self) -> dict[str, Any] | None:
        analyzed = self._last("analyzed")
        return analyzed["analysis"] if analyzed else None

    def payload(self, event: str) -> dict[str, Any] | None:
        return self._last(event)


class ExperimentJournal:
    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        prev = GENESIS
        for index, line in enumerate(self.path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JournalCorruptedError(f"ligne {index} illisible : {exc}") from exc
            if (
                entry.get("seq") != len(entries)
                or entry.get("prev") != prev
                or entry.get("hash") != _digest(entry)
            ):
                raise JournalCorruptedError(f"entrée {len(entries)} : chaînage ou contenu modifié")
            prev = entry["hash"]
            entries.append(entry)
        return entries

    def append(self, kind: str, event: str, **payload: Any) -> dict[str, Any]:
        entries = self.entries()
        entry: dict[str, Any] = {
            "seq": len(entries),
            "prev": entries[-1]["hash"] if entries else GENESIS,
            "at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "event": event,
            **payload,
        }
        entry["hash"] = _digest(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
        return entry

    def experiment(self, experiment_id: str, event: str, **payload: Any) -> dict[str, Any]:
        return self.append("experiment", event, experiment_id=experiment_id, **payload)

    def loop(self, event: str, **payload: Any) -> dict[str, Any]:
        return self.append("loop", event, **payload)

    # -- vues ------------------------------------------------------------------

    def experiments(self) -> dict[str, ExperimentRecord]:
        records: dict[str, ExperimentRecord] = {}
        for entry in self.entries():
            if entry["kind"] == "experiment":
                records.setdefault(
                    entry["experiment_id"], ExperimentRecord(entry["experiment_id"])
                ).events.append(entry)
        return records

    def by_plan(self, plan_fingerprint: str) -> list[ExperimentRecord]:
        return [r for r in self.experiments().values() if r.plan.get("fingerprint") == plan_fingerprint]

    def unfinished(self) -> list[ExperimentRecord]:
        return [r for r in self.experiments().values() if not r.finished]

    def successes(self) -> list[ExperimentRecord]:
        return [r for r in self.experiments().values() if r.promoted]

    def failures(self) -> list[ExperimentRecord]:
        return [r for r in self.experiments().values() if r.finished and not r.promoted]

    def loop_events(self, event: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self.entries() if e["kind"] == "loop" and (event is None or e["event"] == event)]
