"""Exécution du diagnostic.

Un check ne peut ni bloquer le diagnostic (timeout) ni le faire échouer
(exception capturée et convertie en résultat FAIL). Un doctor qui plante sur
un environnement cassé serait inutile précisément quand on en a besoin.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from apex.config.loader import load_config
from apex.doctor.check import CheckContext, CheckResult, Severity
from apex.doctor.checks import environment, platform, sandbox, services
from apex.errors import ApexError
from apex.obs.logging import get_logger
from apex.obs.metrics import REGISTRY
from apex.util.probe import Probe, SystemProbe

#: Version du contrat JSON du Doctor (`apex doctor --json`).
DOCTOR_SCHEMA_VERSION = 1

log = get_logger("doctor")

CheckFunc = Callable[[CheckContext], CheckResult]

ALL_CHECKS: tuple[tuple[str, CheckFunc], ...] = (
    environment.CHECKS + platform.CHECKS + services.CHECKS
)

#: `apex doctor --full` uniquement (`apex.cli.main`) — ajoute
#: `sandbox.CHECKS` (voir sa docstring : sonde EMPIRIQUEMENT le backend
#: Sandbox réellement sélectionnable, coûteux — jamais dans le diagnostic
#: rapide par défaut).
FULL_CHECKS: tuple[tuple[str, CheckFunc], ...] = ALL_CHECKS + sandbox.CHECKS


@dataclass(frozen=True)
class DoctorReport:
    results: tuple[CheckResult, ...]
    duration_ms: float

    @property
    def worst(self) -> Severity:
        if not self.results:
            return Severity.SKIP
        return max((r.severity for r in self.results), key=lambda s: s.rank)

    @property
    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for item in self.results:
            counts[item.severity.value] += 1
        return counts

    @property
    def exit_code(self) -> int:
        """0 si aucun échec. Les avertissements ne bloquent pas."""
        return 1 if self.worst is Severity.FAIL else 0

    def to_dict(self) -> dict[str, Any]:
        """Contrat JSON du Doctor, version `DOCTOR_SCHEMA_VERSION` — lu par ApexSetup
        (`RuntimeContractParser.ParseDoctor`, qui connaît la version 1 et traite ces trois champs comme
        optionnels pour rester compatible avec les runtimes antérieurs)."""
        from datetime import datetime, timezone

        from apex.version import __version__

        return {
            "schema_version": DOCTOR_SCHEMA_VERSION,
            "apex_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": self.worst.value,
            "counts": self.counts,
            "duration_ms": round(self.duration_ms, 1),
            "results": [r.to_dict() for r in self.results],
        }


def run_checks(
    ctx: CheckContext,
    checks: Iterable[tuple[str, CheckFunc]] | None = None,
) -> DoctorReport:
    started = time.perf_counter()
    results: list[CheckResult] = []

    for name, func in checks if checks is not None else ALL_CHECKS:
        check_start = time.perf_counter()
        try:
            outcome = func(ctx)
        except Exception as exc:  # le doctor ne plante jamais
            log.exception("check en erreur", check=name)
            outcome = CheckResult(
                name=name,
                category="interne",
                severity=Severity.FAIL,
                message=f"vérification en erreur : {exc.__class__.__name__}: {exc}",
                remediation="Anomalie du doctor lui-même — à corriger avant de se fier au rapport.",
            )
        duration = (time.perf_counter() - check_start) * 1000
        outcome = CheckResult(
            name=outcome.name,
            category=outcome.category,
            severity=outcome.severity,
            message=outcome.message,
            remediation=outcome.remediation,
            facts=outcome.facts,
            duration_ms=duration,
        )
        REGISTRY.observe(
            "apex_doctor_check_duration_seconds",
            duration / 1000,
            help_text="Durée d'une vérification apex doctor",
            check=outcome.name,
            severity=outcome.severity.value,
        )
        results.append(outcome)

    return DoctorReport(tuple(results), (time.perf_counter() - started) * 1000)


def build_context(
    *, config_path: str | None = None, probe: Probe | None = None, timeout_s: float = 5.0
) -> CheckContext:
    ctx = CheckContext(probe=probe or SystemProbe(), timeout_s=timeout_s)
    try:
        ctx.config = load_config(config_path)
    except ApexError as exc:
        ctx.config = None
        ctx.config_error = str(exc)
    return ctx


# -- rendu -----------------------------------------------------------------

_GLYPH = {
    Severity.PASS: "  OK  ",
    Severity.WARN: " WARN ",
    Severity.FAIL: " FAIL ",
    Severity.SKIP: " SKIP ",
}


def render_text(report: DoctorReport, *, verbose: bool = False) -> str:
    lines: list[str] = ["", "APEX DOCTOR", "=" * 72]
    category = None
    for item in report.results:
        if item.category != category:
            category = item.category
            lines.append(f"\n[{category}]")
        lines.append(f"  [{_GLYPH[item.severity]}] {item.name:<24} {item.message}")
        if item.remediation and (verbose or item.severity in (Severity.FAIL, Severity.WARN)):
            for line in item.remediation.splitlines():
                lines.append(f"                  -> {line}")

    counts = report.counts
    lines.append("")
    lines.append("-" * 72)
    lines.append(
        f"  {counts['pass']} ok · {counts['warn']} avertissements · "
        f"{counts['fail']} échecs · {counts['skip']} ignorés "
        f"({report.duration_ms:.0f} ms)"
    )
    if report.worst is Severity.FAIL:
        lines.append("  État : BLOQUANT — corriger les échecs avant de poursuivre.")
    elif report.worst is Severity.WARN:
        lines.append("  État : UTILISABLE avec réserves.")
    else:
        lines.append("  État : SAIN.")
    lines.append("")
    lines.append("  Ce diagnostic est en lecture seule : aucune modification du système.")
    lines.append("")
    return "\n".join(lines)


def render_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
