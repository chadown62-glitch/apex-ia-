"""Registre des versions du modèle APEX : qui est la version en service, et
pourquoi.

Journal JSONL en ajout seul, chaîné par empreintes (même principe que
`apex.knowledge.store`). Chaque proposition est consignée, acceptée ou non :
- sans version en service, un candidat n'est promu que si son expérience a
  montré un gain sur V0 (verdict ACCEPT de son propre Judge) ;
- ensuite, il est comparé à la version en service par `NeuralJudge`, sur les
  MÊMES benchmarks (empreintes identiques, sinon refus) ; seul ACCEPT promeut.
Une version rejetée reste inscrite avec ses raisons : rien n'est effacé.

Retour arrière (`rollback`) : une nouvelle entrée remet en service une
version DÉJÀ promue auparavant (jamais une version rejetée), avec la raison ;
l'historique n'est pas réécrit. `artifact` désigne le checkpoint dans
l'ArtifactStore (adressé par contenu) : c'est lui qui rend le retour possible."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apex.neural.evaluation import BPB, EvalReport, JudgeDecision, NeuralJudge
from apex.neural.protection import FORGOTTEN, forgetting, protections

GENESIS = "sha256:" + "0" * 64


class RegistryCorruptedError(RuntimeError):
    """La chaîne d'empreintes du registre est rompue."""


@dataclass(frozen=True)
class Proposal:
    name: str
    checkpoint_sha256: str
    dataset_fingerprint: str
    reports: dict[str, EvalReport]
    in_run_decision: str
    artifact: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Suite principale PRÉ-ENREGISTRÉE par l'expérience (cible de son plan) ;
    #: retenue seulement si le registre la connaît comme suite d'acceptation.
    primary: str | None = None
    #: Mesures du Judge sur les AUTRES graines de la même recette (protocole multi-graines P3.7) :
    #: le registre recalcule lui-même le verdict depuis toutes les graines avant de promouvoir.
    seed_reports: tuple[dict[str, EvalReport], ...] = ()
    #: Réglages du verdict fixés à la planification (historique de variance, cible, mémorisation).
    variance: dict[str, Any] | None = None


def _digest(entry: dict[str, Any]) -> str:
    payload = json.dumps(
        {k: v for k, v in entry.items() if k != "hash"}, sort_keys=True, ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ModelRegistry:
    def __init__(
        self,
        path: Path,
        *,
        primary: str = "heldout",
        acceptance: frozenset[str] | None = None,
        tolerances: dict[str, float] | None = None,
        protected_groups: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        """`protected_groups` : groupes de suites dont l'oubli, mesuré contre leur ancre (meilleur
        niveau démontré par une version promue), interdit une promotion."""
        self.path = path
        self.primary = primary
        self.acceptance = acceptance or frozenset({primary})
        self.tolerances = tolerances or {}
        self.protected_groups = protected_groups or {}

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        prev = GENESIS
        for index, entry in enumerate(entries):
            if entry.get("prev") != prev or entry.get("hash") != _digest(entry):
                raise RegistryCorruptedError(f"entrée {index} : chaînage ou contenu modifié")
            prev = entry["hash"]
        return entries

    def current(self) -> dict[str, Any] | None:
        promoted = [e for e in self.entries() if e["promoted"]]
        return promoted[-1] if promoted else None

    def propose(self, proposal: Proposal) -> dict[str, Any]:
        entries = self.entries()
        current = next((e for e in reversed(entries) if e["promoted"]), None)
        primary = proposal.primary if proposal.primary in self.acceptance else self.primary
        if current is None:
            promoted = proposal.in_run_decision == JudgeDecision.ACCEPT.value
            decision = proposal.in_run_decision
            reasons = [
                "première version : promue car son expérience démontre un gain sur V0"
                if promoted
                else f"première version non promue : verdict de son expérience {proposal.in_run_decision}"
            ]
            suites: list[dict[str, Any]] = []
        elif proposal.seed_reports:
            # Protocole multi-graines : verdict RECALCULÉ ici depuis les mesures de toutes les graines
            # et les ancres de CE registre ; la décision du contrôleur n'est jamais recopiée.
            from apex.neural.variance import ACCEPT, seed_verdict

            settings = proposal.variance or {}
            recomputed = seed_verdict(
                seeds=[proposal.reports, *proposal.seed_reports],
                protections=protections(entries, self.protected_groups),
                tolerances={**self.tolerances, BPB: 0.01},
                prior=dict(settings.get("prior") or {}),
                target=primary,
                memorization_suite=settings.get("memorization_suite"),
                new_capability=bool(settings.get("capability_target")),
                invalid=[],
                max_seeds=int(settings.get("max_seeds", 3)),
            )
            promoted = recomputed["decision"] == ACCEPT
            decision = JudgeDecision.ACCEPT.value if promoted else JudgeDecision.REJECT.value
            reasons = [
                f"verdict recalculé par le registre, {recomputed['k']} graines : {recomputed['decision']}",
                *recomputed["reasons"],
            ]
            suites = []
            rows = recomputed["rows"]
        else:
            baseline = {name: EvalReport.from_dict(r) for name, r in current["reports"].items()}
            verdict = NeuralJudge(primary=primary, tolerances=self.tolerances).judge(
                baseline, proposal.reports
            )
            promoted = verdict.decision is JudgeDecision.ACCEPT
            decision = verdict.decision.value
            reasons = [f"comparée à « {current['name']} » : {r}" for r in verdict.reasons]
            suites = verdict.to_dict()["suites"]  # type: ignore[assignment]
            tolerances = {**self.tolerances, BPB: 0.01}
            rows = [
                row
                for protection in protections(entries, self.protected_groups)
                if set(protection.suites) <= set(proposal.reports)  # sinon déjà refusée (suites)
                for row in forgetting(protection, proposal.reports, tolerances)
            ]
            forgotten = [r for r in rows if r["status"] == FORGOTTEN]
            if forgotten:
                promoted = False
                decision = JudgeDecision.REJECT.value
                reasons += [
                    f"oubli de « {r['group']} » sur « {r['suite']} » : {r['before']} -> {r['after']} "
                    f"(ancre #{r['anchor_index']})"
                    for r in forgotten
                ]
        entry: dict[str, Any] = {
            "kind": "proposal",
            "name": proposal.name,
            "checkpoint_sha256": proposal.checkpoint_sha256,
            "dataset_fingerprint": proposal.dataset_fingerprint,
            "decision": decision,
            "promoted": promoted,
            "reasons": reasons,
            "suites": suites,
            "primary": primary,
            "reports": {name: report.to_dict() for name, report in proposal.reports.items()},
            "artifact": proposal.artifact,
            "metadata": proposal.metadata,
        }
        if current is not None and self.protected_groups:
            entry["forgetting"] = rows
        return self._append(entries, entry)

    def adopt(
        self,
        *,
        name: str,
        checkpoint_sha256: str,
        dataset_fingerprint: str,
        reports: dict[str, EvalReport],
        artifact: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Première entrée d'un monde ENFANT : le champion du parent devient la version en
        service, avec des rapports RE-MESURÉS dans ce monde (jamais ceux du parent). Refusée
        si le registre n'est pas vide : une adoption ne remplace jamais un champion."""
        entries = self.entries()
        if entries:
            raise ValueError("adoption refusée : le registre contient déjà des versions")
        entry: dict[str, Any] = {
            "kind": "adoption",
            "name": name,
            "checkpoint_sha256": checkpoint_sha256,
            "dataset_fingerprint": dataset_fingerprint,
            "decision": "adopted",
            "promoted": True,
            "reasons": ["champion du monde parent, re-mesuré sur toutes les suites de ce monde"],
            "suites": [],
            "primary": self.primary,
            "reports": {suite: report.to_dict() for suite, report in reports.items()},
            "artifact": artifact,
            "metadata": metadata,
        }
        return self._append(entries, entry)

    def promoted_history(self) -> list[dict[str, Any]]:
        return [e for e in self.entries() if e["promoted"]]

    def rollback(self, reason: str, *, to_index: int | None = None) -> dict[str, Any]:
        """Remet en service une version promue AVANT la version courante (la
        plus récente par défaut, ou l'entrée `to_index`, qui doit avoir été
        promue). La version courante est RETIRÉE : aucun retour arrière
        ultérieur ne la remettra en service. Refuse s'il n'existe aucune
        version antérieure non retirée."""
        entries = self.entries()
        promoted = [e for e in entries if e["promoted"]]
        if not promoted:
            raise LookupError("aucune version en service : rien à restaurer")
        current = promoted[-1]

        def origin(entry: dict[str, Any]) -> int:
            """Une version restaurée garde l'identité de sa promotion d'origine."""
            return int(entry.get("restores", entry["index"]))

        # Une version remplacée par un retour arrière est RETIRÉE : elle ne
        # redevient jamais championne par un retour arrière ultérieur.
        withdrawn = {int(e["replaces_origin"]) for e in entries if e.get("kind") == "rollback"}
        candidates = [
            e for e in promoted[:-1] if origin(e) != origin(current) and origin(e) not in withdrawn
        ]
        if to_index is not None:
            candidates = [e for e in candidates if origin(e) == to_index]
        if not candidates:
            raise LookupError("aucune version antérieure promue à restaurer")
        source = entries[origin(candidates[-1])]
        entry = {
            "kind": "rollback",
            "name": source["name"],
            "checkpoint_sha256": source["checkpoint_sha256"],
            "dataset_fingerprint": source["dataset_fingerprint"],
            "decision": "rollback",
            "promoted": True,
            "reasons": [f"retour à « {source['name']} » depuis « {current['name']} » : {reason}"],
            "suites": [],
            "reports": source["reports"],
            "artifact": source.get("artifact"),
            "metadata": source.get("metadata", {}),
            "restores": source["index"],
            "replaces": current["index"],
            "replaces_origin": origin(current),
        }
        return self._append(entries, entry)

    def _append(self, entries: list[dict[str, Any]], body: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "index": len(entries),
            "prev": entries[-1]["hash"] if entries else GENESIS,
            "at": datetime.now(UTC).isoformat(),
            **body,
        }
        entry["hash"] = _digest(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
        return entry


def proposal_from_report(path: Path, version: str = "v1", *, exclude: tuple[str, ...] = ()) -> Proposal:
    """Construit une proposition depuis un rapport de `run_experiment`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    reports = {
        name: EvalReport.from_dict(report)
        for name, report in data["evaluations"][version].items()
        if name not in exclude
    }
    return Proposal(
        name=f"{path.stem}:{version}",
        checkpoint_sha256=data["checkpoints"][version],
        dataset_fingerprint=data["dataset"]["fingerprint"],
        reports=reports,
        in_run_decision=data["verdicts"][f"{version}_vs_v0"]["decision"]
        if version == "v1"
        else "inconclusive",
    )
