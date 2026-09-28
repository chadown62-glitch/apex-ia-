"""Vue en lecture seule de l'état RÉEL du cerveau natif, pour l'API et l'interface.

Tout provient des fichiers écrits par le moteur dans le répertoire de travail d'une boucle :
`world.json` (monde gelé), `registry.jsonl` (champion, chaîne de hachage), `journal.jsonl`
(décisions, chaîne de hachage), `runs/<expérience>/telemetry.jsonl` (travail en cours) et
`loop.lock` (processus de boucle). Rien n'est déduit ni simulé : une information absente est
renvoyée comme `{"status": "not_available", "reason": ...}`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from apex.neural.loop import telemetry
from apex.neural.loop.journal import ExperimentJournal, ExperimentRecord, attest

TEXT_PRIMARY = "heldout"

# Étiquettes des événements du journal (le nom d'origine reste dans `event`).
LOOP_LABELS = {
    "started": "LOOP_STARTED",
    "stopped": "LOOP_STOPPED",
    "cycle_started": "CYCLE_STARTED",
    "cycle_completed": "CYCLE_COMPLETED",
    "adopted": "CHAMPION_ADOPTED",
    "rollback": "ROLLBACK",
    "target_exhausted": "TARGET_EXHAUSTED",
    "plan_refused": "PLAN_REFUSED",
}
EXPERIMENT_LABELS = {
    "planned": "STRATEGY_SELECTED",
    "started": "EXPERIMENT_STARTED",
    "analyzed": "CAUSE_ANALYSIS",
    "aborted": "EXPERIMENT_ABORTED",
    "interrupted": "EXPERIMENT_INTERRUPTED",
    "recovered": "EXPERIMENT_RECOVERED",
}


def not_available(reason: str) -> dict[str, Any]:
    return {"status": "not_available", "reason": reason}


class BrainWorkdir:
    """Un répertoire de travail de boucle. Le résumé du monde est mis en cache par date de
    modification : le fichier complet (dizaines de Mo) n'est relu que s'il change."""

    _world_cache: dict[str, tuple[float, dict[str, Any]]] = {}  # noqa: RUF012 - cache de processus

    def __init__(self, root: Path) -> None:
        self.root = root

    # -- sources --------------------------------------------------------------------

    def world(self) -> dict[str, Any]:
        path = self.root / "world.json"
        if not path.exists():
            return not_available("world.json absent : aucun monde construit dans ce répertoire")
        mtime = path.stat().st_mtime
        cached = self._world_cache.get(str(path))
        if cached is None or cached[0] != mtime:
            data = json.loads(path.read_text(encoding="utf-8"))
            spec = data["spec"]
            summary = {
                "status": "real",
                "fingerprint": data["fingerprint"],
                "parent": spec.get("parent"),
                "capabilities": list(spec.get("capabilities") or []),
                "domains": sorted(spec.get("domains") or {}),
                "architecture": {
                    k: spec.get(k)
                    for k in ("vocab_size", "hidden_size", "num_layers", "num_heads", "context_length")
                },
                "train_documents": len(data.get("splits", {}).get("train", [])),
            }
            self._world_cache[str(path)] = (mtime, summary)
        return self._world_cache[str(path)][1]

    def journal(self) -> ExperimentJournal | None:
        path = self.root / "journal.jsonl"
        return ExperimentJournal(path) if path.exists() else None

    def registry_entries(self) -> list[dict[str, Any]]:
        path = self.root / "registry.jsonl"
        if not path.exists():
            return []
        from apex.neural.registry import ModelRegistry

        return ModelRegistry(path, primary=TEXT_PRIMARY).entries()

    # -- vues -----------------------------------------------------------------------

    def champion(self, entries: list[dict[str, Any]], journal: ExperimentJournal | None) -> dict[str, Any]:
        promoted = [e for e in entries if e["promoted"]]
        if not promoted:
            return not_available("aucune version promue au registre")
        current = promoted[-1]
        problem: str | None
        try:
            from apex.neural.loop.judge import JUDGE_ID
        except ImportError as exc:  # extra « neural » absent
            problem = f"attestation non vérifiable : {exc}"
        else:
            from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore

            world = self.world()
            store = LocalFilesystemArtifactStore(root=str(self.root / "artifacts"))
            problem = (
                "journal absent"
                if journal is None
                else attest(
                    entries,
                    journal,
                    current,
                    judge_id=JUDGE_ID,
                    parent_world=world.get("parent"),
                    artifact_exists=store.exists,
                )
            )
        return {
            "status": "real",
            "name": current["name"],
            "index": current["index"],
            "kind": current.get("kind", "experiment"),
            "artifact": current.get("artifact"),
            "at": current.get("at"),
            "attested": problem is None,
            "attestation_problem": problem,
            "reports": {n: round(r["value"], 5) for n, r in sorted(current["reports"].items())},
            "metrics": {n: r.get("metric", "bits_per_byte") for n, r in sorted(current["reports"].items())},
            "versions": len(entries),
            "promotions": len(promoted),
        }

    def protections(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]] | dict[str, Any]:
        promoted = [e for e in entries if e["promoted"]]
        if not promoted:
            return not_available("aucune version promue : aucune ancre")
        from apex.capabilities.registry import get_capability
        from apex.neural.protection import protections

        suites = set(promoted[-1]["reports"])
        groups: dict[str, tuple[str, ...]] = {
            "text": (
                TEXT_PRIMARY,
                *sorted(s for s in suites if not s.startswith("cap:") and s != TEXT_PRIMARY),
            )
        }
        for name in self.world().get("capabilities", []):
            contract = get_capability(name).contract
            others = [s for s in contract.suites if s != contract.acceptance_suite]
            groups[name] = (contract.acceptance_suite, *others)
        return [p.to_dict() for p in protections(entries, groups)]

    def loop(self, journal: ExperimentJournal | None) -> dict[str, Any]:
        """La boucle tourne si un processus DÉTIENT le verrou `flock` de `loop.lock` (lu dans
        /proc/locks, sans jamais prendre le verrou) ; le pid écrit dans le fichier ne suffit pas :
        il y reste après l'arrêt."""
        events = journal.loop_events() if journal is not None else []
        last = _label_loop(events[-1]) if events else None
        holder = _lock_holder(self.root / "loop.lock")
        if holder is None:
            return {
                "status": "not_available",
                "reason": "/proc/locks illisible : état de la boucle inconnu",
                "last_event": last,
            }
        running = holder > 0
        return {
            "status": "real",
            "state": "running" if running else "idle",
            "pid": holder if running else None,
            "last_event": last,
            "resources": _resources(holder) if running else not_available("aucune boucle en cours"),
        }

    def current(self, journal: ExperimentJournal | None) -> dict[str, Any] | None:
        if journal is None:
            return None
        unfinished = journal.unfinished()
        if not unfinished:
            return None
        record = unfinished[-1]
        rows = telemetry.read(self.root / "runs" / record.experiment_id)
        progress = next((r for r in reversed(rows) if r["phase"] == telemetry.TRAINING_PROGRESS), None)
        return {
            "experiment_id": record.experiment_id,
            "journal_status": record.status,
            "phase": rows[-1]["phase"] if rows else None,
            "phase_at": rows[-1]["t"] if rows else None,
            "progress": progress,
            "telemetry": rows[-50:],
        }

    def experiments(self, journal: ExperimentJournal | None) -> list[dict[str, Any]]:
        if journal is None:
            return []
        return [_experiment_summary(r) for r in journal.experiments().values()]

    def state(self) -> dict[str, Any]:
        if not self.root.exists():
            return not_available(f"répertoire de travail absent : {self.root}")
        journal = self.journal()
        entries = self.registry_entries()
        return {
            "status": "real",
            "workdir": str(self.root),
            "world": self.world(),
            "loop": self.loop(journal),
            "champion": self.champion(entries, journal),
            "protections": self.protections(entries),
            "current": self.current(journal),
            "experiments": self.experiments(journal),
        }

    def events(self, after: int = -1, limit: int = 200) -> list[dict[str, Any]]:
        """Événements de numéro de séquence > `after` (le journal commence à 0)."""
        journal = self.journal()
        if journal is None:
            return []
        rows = []
        for raw in journal.entries():
            if raw["seq"] <= after:
                continue
            rows.append(_label_loop(raw) if raw["kind"] == "loop" else _label_experiment(raw))
            if len(rows) >= limit:
                break
        return rows


def _lock_holder(lock: Path) -> int | None:
    """Pid qui détient le `flock` du fichier, 0 si personne, None si /proc/locks est illisible."""
    locks = Path("/proc/locks")
    if not locks.exists():
        return None
    if not lock.exists():
        return 0
    inode = lock.stat().st_ino
    for line in locks.read_text().splitlines():
        fields = line.split()
        # « 1: FLOCK  ADVISORY  WRITE 4377 fd:01:123456 0 EOF » (ou « -> » pour un bloqué)
        if "FLOCK" in fields and "->" not in fields:
            ident = fields[fields.index("FLOCK") + 4]
            if ident.rsplit(":", 1)[-1] == str(inode):
                return int(fields[fields.index("FLOCK") + 3])
    return 0


def _resources(pid: int) -> dict[str, Any]:
    status = Path(f"/proc/{pid}/status")
    stat = Path(f"/proc/{pid}/stat")
    if not status.exists() or not stat.exists():
        return not_available("/proc indisponible sur cet hôte")
    rss_kb = next(
        (int(line.split()[1]) for line in status.read_text().splitlines() if line.startswith("VmRSS")), None
    )
    fields = stat.read_text().rsplit(")", 1)[1].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return {
        "status": "real",
        "rss_mb": round(rss_kb / 1024, 1) if rss_kb is not None else None,
        "cpu_seconds": round((int(fields[11]) + int(fields[12])) / ticks, 1),
        "loadavg": Path("/proc/loadavg").read_text().split()[:3],
    }


def _base(raw: dict[str, Any], label: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {
        "seq": raw["seq"],
        "at": raw["at"],
        "kind": raw["kind"],
        "event": raw["event"],
        "experiment_id": raw.get("experiment_id"),
        "label": label,
        "summary": summary,
        **extra,
    }


def _label_loop(raw: dict[str, Any]) -> dict[str, Any]:
    event = raw["event"]
    if "cycle" in raw:
        detail = f"cycle {raw['cycle']}"
    else:
        detail = str(raw.get("reason") or raw.get("target") or "")
    return _base(raw, LOOP_LABELS.get(event, event.upper()), detail)


def _label_experiment(raw: dict[str, Any]) -> dict[str, Any]:
    event = raw["event"]
    if event == "planned":
        hyp = raw["plan"]["hypothesis"]
        return _base(
            raw,
            "STRATEGY_SELECTED",
            f"{hyp['strategy']} : {hyp['modification']}",
            target=hyp["target_benchmark"],
            rationale=(raw.get("decision") or {}).get("rationale", []),
        )
    if event == "executed":
        if not raw.get("ok"):
            return _base(raw, "EXECUTION_FAILED", str(raw.get("error")))
        return _base(
            raw,
            "TRAINING_COMPLETED",
            f"{raw.get('steps')} pas, {raw.get('tokens_seen')} jetons vus, {raw.get('duration_s')} s",
        )
    if event == "judged":
        result = raw["result"]
        forgotten = [f for f in result.get("forgetting", []) if f["status"] == "forgotten"]
        return _base(
            raw,
            f"JUDGE_{result['decision'].upper()}",
            "; ".join(result.get("reasons", [])[:3]),
            regressions=[
                {k: f[k] for k in ("group", "suite", "before", "after", "gain_ci", "tolerance")}
                for f in forgotten
            ],
        )
    if event == "analyzed":
        analysis = raw["analysis"]
        return _base(raw, "CAUSE_ANALYSIS", f"{analysis.get('cause')} ({analysis.get('status')})")
    if event == "replicated":
        return _base(
            raw, f"REPLICATION_{str(raw.get('decision')).upper()}", "; ".join(raw.get("reasons", [])[:2])
        )
    if event == "promotion":
        promoted = bool(raw.get("promoted"))
        return _base(raw, "NEW_CHAMPION" if promoted else "NOT_PROMOTED", str(raw.get("reason", "")))
    return _base(
        raw, EXPERIMENT_LABELS.get(event, event.upper()), str(raw.get("reason") or raw.get("detail") or "")
    )


def _experiment_summary(record: ExperimentRecord) -> dict[str, Any]:
    plan = record.plan
    hyp = plan.get("hypothesis", {})
    judged = (record.payload("judged") or {}).get("result", {})
    comps = {c["suite"]: c for c in judged.get("comparisons", [])}
    target = hyp.get("target_benchmark")
    target_comp = comps.get(target or "")
    return {
        "experiment_id": record.experiment_id,
        "status": record.status,
        "finished": record.finished,
        "strategy": hyp.get("strategy"),
        "modification": hyp.get("modification"),
        "target": target,
        "target_before": target_comp.get("baseline_bpb") if target_comp else None,
        "target_after": target_comp.get("candidate_bpb") if target_comp else None,
        "target_verdict": target_comp.get("verdict") if target_comp else None,
        "decision": record.decision,
        "promoted": record.promoted,
        "cause": (record.analysis or {}).get("cause"),
        "forgotten": [f["suite"] for f in judged.get("forgetting", []) if f["status"] == "forgotten"],
        # Nature du gain par capacité, établie par le Judge (généralisation, transfert, mémorisation).
        "learning": {cap: v.get("kinds", []) for cap, v in (judged.get("learning") or {}).items()},
        "reasons": judged.get("reasons", [])[:5],
        "started_at": record.events[0]["at"] if record.events else None,
        "ended_at": record.events[-1]["at"] if record.finished else None,
    }
