"""Orchestration DIAGNOSTIC -> AUTO-REPAIR -> VERIFY -> (Gate) — passe
« Self-Healing Launcher » (N+19, AXE B).

Compose des briques déjà réelles, ne réimplémente aucune d'entre elles :
`apex.doctor.runner.run_checks` (diagnostic, lecture seule, INCHANGÉ),
`apex.doctor.repair` (classification + réparation sûre, ce module),
`apex.gate.runner.run_gate` (autorisation Phase 2, JAMAIS court-circuitée —
même discipline que `apex.cli.main._cmd_serve`, qui vérifie Gate
inconditionnellement). Le démarrage réel du serveur (`uvicorn.run`,
bloquant) reste dans `apex.cli.main._cmd_start` — cette fonction s'arrête
juste avant, à `StartupOutcome.ready_to_serve`, pour rester testable sans
jamais lancer un vrai serveur HTTP.

`run_gate` n'est JAMAIS appelé si le doctor reste `FAIL` après réparation —
Gate reste seul juge de l'autorisation, mais inutile de le solliciter sur un
environnement déjà connu cassé."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from apex.doctor.check import Severity
from apex.doctor.repair import RepairAction, RepairOutcome, RepairPlan, apply_repairs, plan_repairs
from apex.doctor.runner import CheckFunc, DoctorReport, build_context, run_checks
from apex.doctor.startup_history import RepairRecord, StartupRecord, append_record


class StartupStatus(str, Enum):
    #: `--dry-run` : diagnostic + plan de réparation produits, RIEN exécuté.
    DRY_RUN = "dry_run"
    #: Le doctor reste FAIL même après les réparations sûres tentées —
    #: démarrage refusé, jamais forcé.
    BLOCKED_DOCTOR = "blocked_doctor"
    #: Le doctor est sain mais Gate refuse — démarrage refusé, jamais
    #: contourné (même principe que `_cmd_serve`).
    BLOCKED_GATE = "blocked_gate"
    #: Doctor sain ET Gate autorise — prêt pour le démarrage réel.
    READY_TO_SERVE = "ready_to_serve"


@dataclass(frozen=True)
class StartupOutcome:
    initial_report: DoctorReport
    plans: tuple[RepairPlan, ...]
    repair_outcomes: tuple[RepairOutcome, ...]
    verify_report: DoctorReport | None
    gate_authorized: bool | None
    status: StartupStatus
    duration_s: float


def run_preflight_and_repair(
    *,
    config_path: str | None = None,
    dry_run: bool = False,
    timeout_s: float = 5.0,
    checks: Any = None,
    history_path: str | None = None,
    gate_kwargs: dict[str, Any] | None = None,
) -> StartupOutcome:
    """`checks` : sous-ensemble optionnel de `apex.doctor.runner.ALL_CHECKS`
    (jamais un second jeu de checks — les mêmes fonctions réelles), utile
    pour les tests. `history_path=None` -> pas d'écriture d'historique
    (utilisé par les tests qui ne veulent pas toucher le disque)."""
    started = time.monotonic()
    ctx = build_context(config_path=config_path, timeout_s=timeout_s)
    initial_report = run_checks(ctx, checks=checks)
    plans = plan_repairs(initial_report.results)
    repair_outcomes = apply_repairs(plans, dry_run=dry_run)

    problems = tuple(
        r.name for r in initial_report.results if r.severity in (Severity.FAIL, Severity.WARN)
    )

    if dry_run:
        outcome = StartupOutcome(
            initial_report=initial_report, plans=plans, repair_outcomes=repair_outcomes,
            verify_report=None, gate_authorized=None, status=StartupStatus.DRY_RUN,
            duration_s=time.monotonic() - started,
        )
        _record(outcome, problems, history_path)
        return outcome

    verify_ctx = build_context(config_path=config_path, timeout_s=timeout_s)
    verify_report = run_checks(verify_ctx, checks=checks)
    if verify_report.worst is Severity.FAIL:
        outcome = StartupOutcome(
            initial_report=initial_report, plans=plans, repair_outcomes=repair_outcomes,
            verify_report=verify_report, gate_authorized=None, status=StartupStatus.BLOCKED_DOCTOR,
            duration_s=time.monotonic() - started,
        )
        _record(outcome, problems, history_path)
        return outcome

    from apex.gate.runner import run_gate

    gate_report = run_gate(config_path=config_path, timeout_s=timeout_s, **(gate_kwargs or {}))
    status = StartupStatus.READY_TO_SERVE if gate_report.phase_2_authorized else StartupStatus.BLOCKED_GATE
    outcome = StartupOutcome(
        initial_report=initial_report, plans=plans, repair_outcomes=repair_outcomes,
        verify_report=verify_report, gate_authorized=gate_report.phase_2_authorized, status=status,
        duration_s=time.monotonic() - started,
    )
    _record(outcome, problems, history_path)
    return outcome


def _record(outcome: StartupOutcome, problems: tuple[str, ...], history_path: str | None) -> None:
    if history_path is None:
        return
    record = StartupRecord.new(
        problems_detected=problems,
        repairs=tuple(
            RepairRecord(
                check_name=o.plan.check_name, action=o.plan.action.value,
                attempted=o.attempted, succeeded=o.succeeded,
            )
            for o in outcome.repair_outcomes
        ),
        final_status=outcome.status.value, duration_s=outcome.duration_s,
    )
    append_record(record, path=history_path)


def render_text(outcome: StartupOutcome, *, verbose: bool = False) -> str:
    lines: list[str] = ["", "APEX SELF-HEALING LAUNCHER", "=" * 72, ""]
    lines.append("[1/4] DIAGNOSTIC")
    for r in outcome.initial_report.results:
        if r.severity in (Severity.FAIL, Severity.WARN) or verbose:
            lines.append(f"  [{r.severity.value.upper():>5}] {r.name:<24} {r.message}")

    lines.append("")
    lines.append("[2/4] AUTO-REPAIR")
    any_action = False
    for plan, done in zip(outcome.plans, outcome.repair_outcomes):
        if plan.action is RepairAction.RETRY_ONLY and not verbose:
            continue
        any_action = True
        lines.append(f"  {plan.check_name:<24} {plan.action.value:<18} {done.detail}")
    if not any_action:
        lines.append("  (rien à réparer)")

    if outcome.status is StartupStatus.DRY_RUN:
        lines.append("")
        lines.append("[dry-run] aucune réparation exécutée, aucun démarrage tenté.")
        return "\n".join(lines)

    lines.append("")
    lines.append("[3/4] VERIFY")
    if outcome.verify_report is not None:
        lines.append(f"  état après réparation : {outcome.verify_report.worst.value.upper()}")

    lines.append("")
    lines.append("[4/4] GATE")
    if outcome.gate_authorized is None:
        lines.append("  non sollicité (doctor bloquant)")
    else:
        lines.append(f"  autorisation Phase 2 : {'OUI' if outcome.gate_authorized else 'NON'}")

    lines.append("")
    lines.append(f"Statut final : {outcome.status.value.upper()} ({outcome.duration_s:.1f}s)")
    lines.append("")
    return "\n".join(lines)
