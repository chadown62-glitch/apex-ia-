"""Orchestration du Hardware & Environment Gate."""

from __future__ import annotations

import os
from pathlib import Path

from apex.config.loader import load_config
from apex.errors import ApexError
from apex.gate.checks import ALL_GATE_CHECKS, GateContext
from apex.gate.model import GateCheckResult, GateStatus
from apex.gate.report import EnvironmentReport, load_exceptions
from apex.obs.logging import get_logger
from apex.util.probe import Probe, SystemProbe

log = get_logger("gate")

DEFAULT_EXCEPTIONS_PATH = "gate.exceptions.yaml"


def run_gate(
    *,
    probe: Probe | None = None,
    config_path: str | None = None,
    exceptions_path: str | Path = DEFAULT_EXCEPTIONS_PATH,
    allow_container_exec: bool = False,
    allow_network_probe: bool = False,
    timeout_s: float = 20.0,
) -> EnvironmentReport:
    actual_probe = probe or SystemProbe()
    # La provenance n'est pas déclarée par l'appelant : elle est constatée.
    probe_is_real = isinstance(actual_probe, SystemProbe)

    try:
        config = load_config(config_path)
    except ApexError as exc:
        log.warning("configuration non chargée", error=str(exc))
        config = None

    ctx = GateContext(
        probe=actual_probe,
        config=config,
        allow_container_exec=allow_container_exec,
        allow_network_probe=allow_network_probe,
        timeout_s=timeout_s,
    )

    results: list[GateCheckResult] = []
    for check_id, func in ALL_GATE_CHECKS:
        try:
            results.append(func(ctx))
        except Exception as exc:  # le Gate ne plante jamais
            log.exception("contrôle en erreur", check=check_id)
            results.append(
                GateCheckResult(
                    check_id=check_id,
                    title=check_id,
                    status=GateStatus.FAIL,
                    message=f"contrôle en erreur : {exc.__class__.__name__}: {exc}",
                    remediation="Anomalie du Gate lui-même — à corriger avant de s'y fier.",
                )
            )

    return EnvironmentReport(
        checks=tuple(results),
        probe_is_real=probe_is_real,
        exceptions=load_exceptions(exceptions_path),
        machine_label=os.environ.get("APEX_MACHINE_LABEL", ""),
    )


_GLYPH = {
    GateStatus.PASS: "   PASS   ",
    GateStatus.FAIL: "   FAIL   ",
    GateStatus.DEGRADED: " DEGRADED ",
    GateStatus.UNKNOWN: " UNKNOWN  ",
    GateStatus.SIMULATED: "SIMULATED ",
}


def render_text(report: EnvironmentReport, *, verbose: bool = False) -> str:
    lines = ["", "APEX — HARDWARE & ENVIRONMENT GATE (phase 1)", "=" * 78]

    if not report.probe_is_real:
        lines += [
            "",
            "  /!\\  MESURES NON RÉELLES",
            "  Les contrôles n'ont pas été exécutés avec une sonde système. Tous les",
            "  résultats concluants sont dégradés en SIMULATED. Ce rapport ne peut PAS",
            "  servir à autoriser la phase 2 — il valide l'instrument, pas la machine.",
        ]

    lines.append("")
    for item in report.effective_checks:
        excused = " (exception acceptée)" if item.check_id in report.excused else ""
        lines.append(f"  [{_GLYPH[item.status]}] {item.check_id}  {item.title}")
        lines.append(f"               {item.message}{excused}")
        if item.remediation and (verbose or item.status is not GateStatus.PASS):
            for line in item.remediation.splitlines():
                lines.append(f"               -> {line}")
        if item.to_validate_on_target:
            lines.append("               -> À VALIDER SUR LA MACHINE CIBLE.")
        lines.append("")

    counts = report.counts
    lines += [
        "-" * 78,
        f"  PASS {counts['PASS']} · FAIL {counts['FAIL']} · DEGRADED {counts['DEGRADED']} · "
        f"UNKNOWN {counts['UNKNOWN']} · SIMULATED {counts['SIMULATED']}",
        f"  Machine : {report.machine_label or 'non étiquetée'}",
        f"  Empreinte matérielle : {report.fingerprint()}",
        f"  Statut du Gate : {report.status.value}",
    ]

    if report.expired_exceptions:
        lines.append(f"  Exceptions EXPIRÉES (sans effet) : "
                     f"{', '.join(e.check_id for e in report.expired_exceptions)}")
    if report.blocking:
        lines.append(f"  Bloquants : {', '.join(c.check_id for c in report.blocking)}")
    if report.unresolved:
        lines.append(f"  Non concluants : {', '.join(c.check_id for c in report.unresolved)}")

    lines += [
        "",
        f"  Phase 2 autorisée : {'OUI' if report.phase_2_authorized else 'NON'}",
    ]
    if not report.phase_2_authorized:
        lines.append("  Lever chaque point, ou l'inscrire dans gate.exceptions.yaml avec une")
        lines.append("  date d'expiration et un motif.")
    lines.append("")
    return "\n".join(lines)
