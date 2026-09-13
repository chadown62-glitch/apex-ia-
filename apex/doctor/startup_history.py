"""Historique des lancements — passe « Self-Healing Launcher » (N+19, AXE B).

Persistance locale (`state/startup_history.jsonl`, un enregistrement JSON par
ligne — append-only, jamais réécrit) plutôt que la table `events` déjà
réelle (`apex.db.repositories.EventRepository`) : au moment où le launcher
diagnostique l'environnement, PostgreSQL — la seule chose vers laquelle
`events` peut être écrite — est précisément l'une des choses en cours de
diagnostic (`service.postgres`, potentiellement injoignable). Un historique
de démarrage qui exigerait une base de données déjà saine pour exister
serait circulaire. `state/` (le même répertoire que celui déjà évoqué par
la mission) est créé paresseusement, jamais supposé préexistant.

**Ce n'est PAS du machine learning** (§24 de la mission) — seulement de la
télémétrie structurée : `recurring_problems()` compte des occurrences
RÉELLES et un taux de succès RÉEL de réparation automatique par nom de
check, rien de plus, une base pour une future analyse, jamais un
apprentissage réalisé ici."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

DEFAULT_HISTORY_PATH = os.path.join("state", "startup_history.jsonl")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RepairRecord:
    check_name: str
    action: str
    attempted: bool
    succeeded: bool | None


@dataclass(frozen=True)
class StartupRecord:
    startup_id: str
    timestamp: str
    problems_detected: tuple[str, ...]
    repairs: tuple[RepairRecord, ...]
    final_status: str
    duration_s: float

    @classmethod
    def new(
        cls, *, problems_detected: tuple[str, ...], repairs: tuple[RepairRecord, ...],
        final_status: str, duration_s: float,
    ) -> StartupRecord:
        return cls(
            startup_id=str(uuid.uuid4()), timestamp=_utcnow_iso(),
            problems_detected=problems_detected, repairs=repairs,
            final_status=final_status, duration_s=duration_s,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "startup_id": self.startup_id,
            "timestamp": self.timestamp,
            "problems_detected": list(self.problems_detected),
            "repairs": [asdict(r) for r in self.repairs],
            "final_status": self.final_status,
            "duration_s": self.duration_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StartupRecord:
        return cls(
            startup_id=data["startup_id"], timestamp=data["timestamp"],
            problems_detected=tuple(data.get("problems_detected", ())),
            repairs=tuple(RepairRecord(**r) for r in data.get("repairs", ())),
            final_status=data["final_status"], duration_s=data["duration_s"],
        )


def append_record(record: StartupRecord, *, path: str = DEFAULT_HISTORY_PATH) -> None:
    """Écrit UNE ligne JSON — jamais une réécriture du fichier existant
    (append-only, même discipline que `EvalRunRow`/`TaskRow` : un historique
    ne change jamais après coup)."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def read_history(*, path: str = DEFAULT_HISTORY_PATH, limit: int | None = None) -> tuple[StartupRecord, ...]:
    """Fichier absent -> historique vide, jamais une exception (premier
    lancement réel de la machine, cas normal, pas une erreur)."""
    if not os.path.exists(path):
        return ()
    records: list[StartupRecord] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(StartupRecord.from_dict(json.loads(line)))
    if limit is not None:
        records = records[-limit:]
    return tuple(records)


@dataclass(frozen=True)
class RecurringProblem:
    check_name: str
    occurrences: int
    auto_fix_attempts: int
    auto_fix_successes: int

    @property
    def auto_fix_success_rate(self) -> float | None:
        """`None` — jamais `0.0` — si aucune tentative n'a encore eu lieu :
        un taux de succès n'existe que s'il y a eu au moins une tentative
        réelle (§4 : UNKNOWN plutôt qu'une valeur devinée)."""
        if self.auto_fix_attempts == 0:
            return None
        return self.auto_fix_successes / self.auto_fix_attempts


def recurring_problems(records: tuple[StartupRecord, ...]) -> tuple[RecurringProblem, ...]:
    """Télémétrie structurée PURE sur un historique déjà réel — ne lit ni
    n'écrit rien lui-même. §24 : « ne constitue PAS encore du machine
    learning » — un simple comptage."""
    occurrences: dict[str, int] = {}
    attempts: dict[str, int] = {}
    successes: dict[str, int] = {}
    for record in records:
        for problem in record.problems_detected:
            occurrences[problem] = occurrences.get(problem, 0) + 1
        for repair in record.repairs:
            if repair.attempted:
                attempts[repair.check_name] = attempts.get(repair.check_name, 0) + 1
                if repair.succeeded:
                    successes[repair.check_name] = successes.get(repair.check_name, 0) + 1
    return tuple(
        RecurringProblem(
            check_name=name, occurrences=count,
            auto_fix_attempts=attempts.get(name, 0), auto_fix_successes=successes.get(name, 0),
        )
        for name, count in sorted(occurrences.items(), key=lambda kv: (-kv[1], kv[0]))
    )
