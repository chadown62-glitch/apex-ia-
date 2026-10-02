"""Contrat « démarrage d'APEX » contre « autorisation de phase 2 » (audit du 02/10/2026).

Constaté (reproduit sur une vraie machine et en CI Windows Server + WSL2, run 11) : `launch.sh serve`,
`apex serve` et `apex start` exigeaient `phase_2_authorized` — l'autorisation de la PHASE 2 de la feuille
de route (machine entièrement mesurée) — pour simplement démarrer le backend. Or `launch.sh` n'accorde
jamais les sondes à consentement : GATE-15/17/18/22 restent UNKNOWN (`not_authorized`) sur TOUTE machine,
et une machine sans GPU garde GATE-08/09/10 UNKNOWN (`not_applicable`) à vie. Le backend ne démarrait donc
sur aucune machine sans dérogation périssable à renouveler indéfiniment, et un « non mesuré » bloquait
plus qu'un « mesuré dégradé » (ces contrôles n'ont aucun chemin FAIL).

Règles exprimées ici :
  1. un FAIL mesuré bloque TOUJOURS le démarrage, même excusé ; jamais réécrit en PASS ;
  2. un UNKNOWN n'est jamais présenté comme un FAIL ;
  3. un contrôle non autorisé reste identifiable comme non vérifié ;
  4. les dérogations sont datées, limitées aux contrôles nommés, n'excusent jamais un FAIL au démarrage ;
  5. une machine sans GPU n'est pas défectueuse pour le démarrage ;
  6. MinIO absent reste compatible avec le démarrage ;
  7. une vraie précondition bloque le démarrage (FAIL, sonde non réelle, UNKNOWN d'une précondition) ;
  8. les codes de sortie distinguent succès, précondition réelle, environnement non vérifié, erreur interne.
L'autorisation de phase 2 (`phase_2_authorized`, code de sortie d'`apex gate`) est INCHANGÉE.
"""

from __future__ import annotations

import inspect
import io
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

import pytest

from apex.errors import ConfigError
from apex.gate import checks as gate_checks
from apex.gate.model import GateCheckResult, GateStatus
from apex.gate.report import EnvironmentReport, GateException

REPO = Path(__file__).resolve().parents[1]

PASS, FAIL, DEGRADED, UNKNOWN = GateStatus.PASS, GateStatus.FAIL, GateStatus.DEGRADED, GateStatus.UNKNOWN


def _r(check_id: str, status: GateStatus, reason: str = "") -> GateCheckResult:
    return GateCheckResult(check_id=check_id, title=check_id, status=status, message=f"{check_id} {status.value}",
                           unknown_reason=reason)


def _baseline() -> dict[str, GateCheckResult]:
    """Une machine WSL2 saine telle que launch.sh la mesure : tout concluant sauf les sondes à
    consentement, que launch.sh n'accorde jamais (run 12 réel : exactement cet ensemble)."""
    rows = {f"GATE-{i:02d}": _r(f"GATE-{i:02d}", PASS) for i in range(1, 23)}
    for gid in ("GATE-15", "GATE-17", "GATE-18", "GATE-22"):
        rows[gid] = _r(gid, UNKNOWN, "not_authorized")
    return rows


def _report(rows: dict[str, GateCheckResult], *, real: bool = True, exceptions=()) -> EnvironmentReport:
    return EnvironmentReport(checks=tuple(rows.values()), probe_is_real=real, exceptions=tuple(exceptions))


def _exc(check_id: str, days: int = 30) -> GateException:
    return GateException(check_id, "test", "audit", date.today() + timedelta(days=days))


def _ids(results) -> list[str]:
    return [c.check_id for c in results]


# -- constat de départ (reproduit en réel) : la phase 2 reste refusée, le démarrage ne l'est plus ---------

def test_baseline_machine_starts_but_phase_2_stays_unauthorized() -> None:
    report = _report(_baseline())
    assert report.startup_authorized is True
    assert report.phase_2_authorized is False          # feuille de route : inchangée
    assert _ids(report.unresolved) == ["GATE-15", "GATE-17", "GATE-18", "GATE-22"]


# -- 1. un FAIL réel n'est jamais transformé en PASS -----------------------------------------------------

def test_1_a_measured_fail_blocks_startup_and_stays_fail_in_every_view() -> None:
    rows = _baseline(); rows["GATE-05"] = _r("GATE-05", FAIL)
    report = _report(rows)
    assert report.startup_authorized is False
    assert _ids(report.startup_blocking) == ["GATE-05"]
    as_dict = report.to_dict()
    assert {c["check_id"]: c["status"] for c in as_dict["checks"]}["GATE-05"] == "FAIL"
    assert as_dict["startup"]["authorized"] is False
    assert as_dict["startup"]["blocking"] == ["GATE-05"]


# -- 2. un UNKNOWN n'est jamais présenté comme un FAIL ---------------------------------------------------

def test_2_unknown_is_never_listed_as_a_blocking_failure() -> None:
    report = _report(_baseline())
    assert report.startup_blocking == ()
    as_dict = report.to_dict()
    assert as_dict["startup"]["blocking"] == []
    assert all(c["status"] == "UNKNOWN" for c in as_dict["checks"] if c["check_id"] in {"GATE-15", "GATE-17"})


def test_2_text_report_separates_measured_failures_from_unverified_checks() -> None:
    from apex.gate.runner import render_text

    rows = _baseline(); rows["GATE-05"] = _r("GATE-05", FAIL)
    text = render_text(_report(rows))
    section = text[text.index("Démarrage d'APEX"):]
    blocking_line = next(line for line in section.splitlines() if "FAIL mesurés" in line)
    unverified_line = next(line for line in section.splitlines() if "Non vérifiés" in line)
    assert "GATE-05" in blocking_line and "GATE-15" not in blocking_line
    assert "GATE-15" in unverified_line and "GATE-05" not in unverified_line


# -- 3. un contrôle non autorisé reste identifiable comme non vérifié ------------------------------------

def test_3_unauthorized_probe_stays_identified_as_unverified_with_its_reason() -> None:
    report = _report(_baseline())
    unverified = {item["check_id"]: item["reason"] for item in report.to_dict()["startup"]["unverified"]}
    assert unverified == {"GATE-15": "not_authorized", "GATE-17": "not_authorized",
                          "GATE-18": "not_authorized", "GATE-22": "not_authorized"}
    assert report.phase_2_authorized is False   # jamais compté comme vérifié


def test_3_an_excused_unknown_is_still_listed_as_unverified() -> None:
    """Une dérogation accepte l'absence de preuve ; elle ne la fournit pas."""
    rows = _baseline(); rows["GATE-21"] = _r("GATE-21", UNKNOWN, "dependency_missing")
    report = _report(rows, exceptions=[_exc("GATE-15"), _exc("GATE-21")])
    assert report.startup_authorized is True
    assert {"GATE-15", "GATE-21"} <= set(_ids(report.startup_unverified))
    assert report.startup_unverified_blocking == ()


# -- 4. dérogations : datées, ciblées, jamais pour un FAIL au démarrage -----------------------------------

def test_4_an_exception_without_expiry_is_rejected() -> None:
    with pytest.raises(ConfigError):
        GateException.parse({"check_id": "GATE-21", "reason": "x", "approved_by": "y"})


def test_4_an_exception_never_lets_a_measured_fail_start_apex() -> None:
    rows = _baseline(); rows["GATE-06"] = _r("GATE-06", FAIL)
    excused = [_exc(g) for g in ("GATE-06", "GATE-15", "GATE-17", "GATE-18", "GATE-22")]
    report = _report(rows, exceptions=excused)
    assert report.phase_2_authorized is True          # mécanisme de phase existant, inchangé
    assert report.startup_authorized is False          # mais APEX ne démarre pas sur un FAIL mesuré
    assert _ids(report.startup_blocking) == ["GATE-06"]


def test_4_an_exception_only_covers_the_named_check_and_expires() -> None:
    rows = _baseline(); rows["GATE-21"] = _r("GATE-21", UNKNOWN, "dependency_missing")
    assert _report(rows, exceptions=[_exc("GATE-22")]).startup_authorized is False
    assert _report(rows, exceptions=[_exc("GATE-21")]).startup_authorized is True
    assert _report(rows, exceptions=[_exc("GATE-21", days=-1)]).startup_authorized is False


# -- 5. sans GPU : pas défectueuse pour démarrer ---------------------------------------------------------

def test_5_a_machine_without_gpu_can_start() -> None:
    rows = _baseline()
    rows["GATE-07"] = _r("GATE-07", DEGRADED)
    for gid in ("GATE-08", "GATE-09", "GATE-10", "GATE-18"):
        rows[gid] = _r(gid, UNKNOWN, "not_applicable")
    report = _report(rows)
    assert report.startup_authorized is True
    assert report.startup_blocking == ()
    assert {"GATE-08", "GATE-09", "GATE-10"} <= set(_ids(report.startup_unverified))


# -- 6. MinIO absent reste compatible avec le démarrage --------------------------------------------------

def test_6_no_gate_check_can_fail_because_minio_is_absent() -> None:
    source = inspect.getsource(gate_checks)
    for call in re.findall(r"_fail\((?:[^()]|\([^()]*\))*\)", source, re.S):
        assert "minio" not in call.lower()
    from apex.config.loader import load_config

    assert load_config(str(REPO / "config.yaml")).services["minio"].required is False


# -- 7. une vraie précondition bloque toujours -----------------------------------------------------------

@pytest.mark.parametrize("check_id", ["GATE-02", "GATE-05", "GATE-06", "GATE-14", "GATE-16", "GATE-19", "GATE-21"])
def test_7_every_measured_fail_blocks_startup(check_id: str) -> None:
    rows = _baseline(); rows[check_id] = _r(check_id, FAIL)
    assert _report(rows).startup_authorized is False


def test_7_a_consented_probe_that_fails_blocks_startup() -> None:
    rows = _baseline(); rows["GATE-17"] = _r("GATE-17", FAIL)
    assert _report(rows).startup_authorized is False


def test_7_unknown_on_a_precondition_blocks_startup_as_unverified() -> None:
    rows = _baseline(); rows["GATE-21"] = _r("GATE-21", UNKNOWN, "dependency_missing")
    report = _report(rows)
    assert report.startup_authorized is False
    assert report.startup_blocking == ()                       # pas un FAIL…
    assert _ids(report.startup_unverified_blocking) == ["GATE-21"]   # …mais une précondition non vérifiée


def test_7_an_unclassified_check_is_a_precondition_by_default() -> None:
    rows = _baseline(); rows["GATE-99"] = _r("GATE-99", UNKNOWN, "not_authorized")
    assert _report(rows).startup_authorized is False


def test_7_measurements_from_a_non_real_probe_never_start_apex() -> None:
    rows = {k: _r(k, PASS) for k in _baseline()}
    assert _report(rows, real=False).startup_authorized is False


# -- classification explicite des 22 contrôles -----------------------------------------------------------

def test_every_gate_check_has_an_explicit_startup_role() -> None:
    from apex.gate.report import STARTUP_ROLES

    assert set(STARTUP_ROLES) == {cid for cid, _ in gate_checks.ALL_GATE_CHECKS}


def test_a_tolerated_unknown_never_hides_a_possible_fail_except_runtime_enforced_ones() -> None:
    """Un UNKNOWN n'est toléré au démarrage que si le contrôle ne peut produire aucun FAIL (son pire
    résultat mesuré est DEGRADED, qui ne bloque pas) — sauf garantie revérifiée à l'exécution."""
    from apex.gate.report import RUNTIME_ENFORCED, STARTUP_ROLES, StartupRole

    for check_id, func in gate_checks.ALL_GATE_CHECKS:
        if STARTUP_ROLES[check_id] is StartupRole.PRECONDITION:
            continue
        has_fail_path = "_fail(" in inspect.getsource(func)
        assert not has_fail_path or check_id in RUNTIME_ENFORCED, check_id
    assert RUNTIME_ENFORCED == {"GATE-17"}


def test_gate_17_guarantee_is_rechecked_fail_closed_at_execution_time() -> None:
    """Pourquoi GATE-17 non autorisé ne bloque pas le démarrage : la sandbox refuse d'exécuter tant que
    réseau coupé + non-root + racine en lecture seule ne sont pas prouvés sur un vrai conteneur."""
    from apex.errors import IsolationUnavailable
    from apex.sandbox.container import ContainerSandbox
    from apex.sandbox.model import IsolationLevel, ResourceLimits, SandboxCapabilityReport, SandboxSpec

    report = SandboxCapabilityReport(
        level=IsolationLevel.RLIMIT_ONLY, namespaces_available=False, cgroups_available=False,
        privilege_drop_available=False, network_isolation_available=False, docker_daemon_reachable=True,
        seccomp_available=False, detail="garanties non prouvées",
    )
    sandbox = ContainerSandbox(image="x", capability_report=report)
    limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5.0, disk_mb=16, network="none")
    spec = SandboxSpec(mission_id="m", clone_id="c", command=("true",), limits=limits)
    with pytest.raises(IsolationUnavailable):
        sandbox.execute(spec)


# -- 8. codes de sortie distincts --------------------------------------------------------------------------

def _run_gate_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, report: EnvironmentReport, *args: str) -> int:
    import apex.gate.runner as runner
    from apex.cli.main import main

    monkeypatch.setattr(runner, "run_gate", lambda **_: report)
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        return main(["gate", "--output", str(tmp_path / "env.json"), *args])


def test_8_gate_startup_mode_exit_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from apex.cli.main import GATE_STARTUP_BLOCKED, GATE_STARTUP_UNVERIFIED

    ok = _report(_baseline())
    failed_rows = _baseline(); failed_rows["GATE-05"] = _r("GATE-05", FAIL)
    unverified_rows = _baseline(); unverified_rows["GATE-21"] = _r("GATE-21", UNKNOWN, "dependency_missing")
    assert _run_gate_cli(monkeypatch, tmp_path, ok, "--startup") == 0
    assert _run_gate_cli(monkeypatch, tmp_path, _report(failed_rows), "--startup") == GATE_STARTUP_BLOCKED
    assert _run_gate_cli(monkeypatch, tmp_path, _report(unverified_rows), "--startup") == GATE_STARTUP_UNVERIFIED
    assert len({0, 1, 2, GATE_STARTUP_BLOCKED, GATE_STARTUP_UNVERIFIED}) == 5   # 1/2 = crash Python / argparse


def test_8_plain_apex_gate_still_answers_phase_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _run_gate_cli(monkeypatch, tmp_path, _report(_baseline())) == 1


def test_8_apex_serve_refuses_a_measured_fail_and_never_starts_the_server(monkeypatch: pytest.MonkeyPatch,
                                                                           tmp_path: Path) -> None:
    import apex.gate.runner as runner
    from apex.cli.main import main

    rows = _baseline(); rows["GATE-05"] = _r("GATE-05", FAIL)
    monkeypatch.setattr(runner, "run_gate", lambda **_: _report(rows))
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("HOME", str(tmp_path))
    started = []
    monkeypatch.setitem(sys.modules, "uvicorn", type(sys)("uvicorn"))
    sys.modules["uvicorn"].run = lambda *a, **k: started.append(True)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        code = main(["serve", "--port", "0"])
    assert code == 1 and started == []
    assert "GATE-05" in err.getvalue()


def test_8_apex_serve_starts_when_only_tolerated_checks_are_unverified(monkeypatch: pytest.MonkeyPatch,
                                                                        tmp_path: Path) -> None:
    import apex.gate.runner as runner
    import apex.obs.component_checks as component_checks
    from apex.cli.main import main

    monkeypatch.setattr(runner, "run_gate", lambda **_: _report(_baseline()))
    monkeypatch.setattr(component_checks, "warm_up", lambda: None)
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("HOME", str(tmp_path))
    started = []
    monkeypatch.setitem(sys.modules, "uvicorn", type(sys)("uvicorn"))
    sys.modules["uvicorn"].run = lambda *a, **k: started.append(True)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        code = main(["serve", "--port", "0"])
    assert started == [True] and code == 0
    assert "non vérifiés" in err.getvalue() and "GATE-17" in err.getvalue()
