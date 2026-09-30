"""Capacité REAL WINDOWS du Continuous Improvement Engine (cycles REAL-WINDOWS-n).

Question mesurée : sur un Windows RÉEL (+ WSL2 réel), l'installateur, le raccourci, APEX.exe et le Panel
font-ils ce qu'ils annoncent face au vrai `wsl.exe`, au vrai `launch.sh` et au vrai backend ?

Rien n'est mesuré ici : l'adaptateur LIT les résultats produits sur la machine Windows par
`setup/packaging/windows/Test-ApexWindows.ps1` (le même script sur le runner GitHub Windows Server et sur
le PC de l'utilisateur) :
- `APEX_REAL_WINDOWS_RESULTS` : `results.json` d'une exécution `-Phase Full` (ou `PreReboot`) ;
- `APEX_REAL_WINDOWS_REBOOT` (facultatif) : un ou plusieurs `results.json` de `-Phase PostReboot`,
  séparés par « ; ».
Règles (jamais un PASS construit) :
- une métrique = la fraction de vérifications PASS de son groupe ; une vérification BLOCKED rend la
  métrique BLOQUÉE (avec la raison) — jamais ignorée, jamais comptée comme réussie ; NOT_APPLICABLE est
  exclu ;
- reproductibilité : le moteur mesure deux fois ; la mesure n°k lit l'exécution réelle n°k du script
  (`-Repeat 2`). Avec une seule exécution, la seconde mesure est bloquée : la reproductibilité n'est
  pas vérifiée, donc les comparaisons déterministes restent non concluantes ;
- redémarrage : un seul redémarrage réel ne prouve pas la reproductibilité — le groupe « restart » exige
  deux fichiers PostReboot, sinon il est BLOQUÉ ;
- provenance : l'environnement (Windows, version, serveur ou non, distribution) fait partie du snapshot :
  deux machines différentes ne sont jamais comparées (INCONCLUSIVE) ; le juge refuse des résultats Wine
  ou produits par un script différent du script versionné.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apex.improvement.judge import ExternalJudge, FunctionProbe, ProbeOutcome
from apex.improvement.model import BenchmarkSnapshot, CapabilitySpec, CycleKind, MetricSpec
from apex.metalearner.judge_model import JudgeDimension, JudgeSeverity

CAPABILITY_ID = "real_windows"


def _harness_root() -> Path:
    for base in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for candidate in (base, *base.parents):
            if (candidate / "setup" / "packaging" / "windows" / "Test-ApexWindows.ps1").exists():
                return candidate
    return Path.cwd().resolve()


SCRIPT = _harness_root() / "setup" / "packaging" / "windows" / "Test-ApexWindows.ps1"

#: Groupes de la mission (phase 14) -> vérifications du script qui les établissent.
GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "install": (
        "installation, seconde installation, refus Panel ouvert, interruption",
        ("I01", "I02", "I06", "I09", "I10", "I11"),
    ),
    "uninstall": ("désinstallation : programme et raccourcis retirés, données conservées", ("I12",)),
    "reinstall": ("réinstallation après désinstallation et mise à jour", ("I13", "I14")),
    "shortcut": ("icône, raccourcis Bureau et Menu Démarrer", ("I03", "I04", "I05")),
    "launch": ("double-clic réel, instance unique, Menu Démarrer", ("L01", "L02", "L03")),
    "wsl_detection": (
        "vrai WSL : détection, distribution absente/arrêtée/redémarrée, multi-distribution",
        ("W01", "W02", "W03", "W04", "W05"),
    ),
    "backend_discovery": (
        "backend réel découvert, composants = état du backend, Panel ouvert après",
        ("B03", "B05", "B06"),
    ),
    "ready": ("APEX READY dans une configuration réellement opérationnelle", ("B04",)),
    "offline": ("backend non lancé, crash, arrêt propre", ("B01", "B08", "B10")),
    "starting": ("préflight de launch.sh, interruption pendant le démarrage", ("B02", "B11")),
    "error": ("backend figé, jeton refusé", ("B07", "B12")),
    "recovery": (
        "relance du Panel et du backend, fermeture propre, Panel ouvert avant le backend",
        ("L04", "L05", "B09", "B13"),
    ),
    "diagnostic": ("--diagnostic UTF-8 sans secret, bouton Copier, journaux", ("I08", "S03")),
    "security": (
        "processus lancés, arguments hostiles, enregistrement hostile, port occupé",
        ("S01", "S02", "S04", "S05"),
    ),
    "restart": ("redémarrage réel de Windows", ("R01", "R02", "R03", "R04")),
}

SPEC = CapabilitySpec(
    capability_id=CAPABILITY_ID,
    name="Real Windows",
    version="1",
    description="APEX Desktop sur un Windows réel avec un vrai WSL2, le vrai launch.sh et le vrai backend.",
    components=(
        "setup/packaging/ApexSetup.iss",
        "setup/src/ApexSetup.Dashboard",
        "scripts/apex-launch.sh",
        "apex/api/routes/status.py",
    ),
    dependencies=(
        "un Windows réel",
        "WSL2 et une distribution avec le dépôt APEX pour les groupes backend",
    ),
    preconditions=("résultats de Test-ApexWindows.ps1 (version versionnée du script)",),
    tests=("setup/packaging/windows/Test-ApexWindows.ps1",),
    metrics=tuple(MetricSpec(name, description, True) for name, (description, _) in GROUPS.items()),
    success_criteria=("chaque groupe à 1.0 sur un Windows réel", "aucune vérification FAIL"),
    risks=(
        "runner GitHub = Windows Server hébergé, sans redémarrage : pas le PC de l'utilisateur",
        "script, correctifs et juge écrits par le même agent",
    ),
    maturity="validation",
)


def load_results(path: str | Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("schema") != 1:
        raise ValueError(f"schéma de résultats inconnu : {data.get('schema')!r}")
    return data


def group_values(
    checks: list[dict[str, Any]], run: int
) -> tuple[dict[str, float], dict[str, str], list[str]]:
    """(valeurs, bloqués, notes) pour l'exécution `run` — pure, testée."""
    by_id = {c["id"]: c for c in checks if c.get("run") == run}
    values: dict[str, float] = {}
    blocked: dict[str, str] = {}
    notes: list[str] = []
    for name, (_, ids) in GROUPS.items():
        present = [by_id[i] for i in ids if i in by_id]
        missing = [i for i in ids if i not in by_id]
        applicable = [c for c in present if c["status"] != "NOT_APPLICABLE"]
        stuck = [c for c in applicable if c["status"] == "BLOCKED"]
        if missing or stuck or not applicable:
            reasons = [f"{c['id']} : {c['detail']}" for c in stuck] + [
                f"{i} : non exécuté" for i in missing
            ]
            blocked[name] = "; ".join(reasons) or "aucune vérification applicable"
            continue
        passed = sum(1 for c in applicable if c["status"] == "PASS")
        values[name] = passed / len(applicable)
        failed = [c["id"] for c in applicable if c["status"] != "PASS"]
        notes.append(f"{name}: {passed}/{len(applicable)}" + (f" (échecs : {failed})" if failed else ""))
    return values, blocked, notes


def _environment(results: dict[str, Any]) -> dict[str, str]:
    env = results.get("environment", {})
    return {
        "machine": str(env.get("label")),
        "windows": f"{env.get('os_caption')} {env.get('os_build')}",
        "server": str(env.get("os_is_server")),
        "wine": str(env.get("is_wine")),
        "distribution": str(env.get("distro_under_test")),
    }


class RealWindowsAdapter:
    spec = SPEC

    def __init__(self) -> None:
        self._calls = 0

    def benchmark_fingerprint(self) -> str:
        parts = [
            hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""
            for p in (SCRIPT, Path(__file__))
        ]
        return "realwin=" + hashlib.sha256("".join(parts).encode()).hexdigest()[:16]

    def measure(self, version: str) -> BenchmarkSnapshot:
        self._calls += 1
        path = os.environ.get("APEX_REAL_WINDOWS_RESULTS")
        if not path:
            reason = "aucun résultat Windows réel fourni (APEX_REAL_WINDOWS_RESULTS)"
            return BenchmarkSnapshot(
                CAPABILITY_ID, version, {}, dict.fromkeys(GROUPS, reason), notes=(reason,)
            )
        results = load_results(path)
        run = self._calls
        notes = [
            f"résultats : {Path(path).name}, script {results.get('script_sha256', '?')[:12]}, "
            f"installateur {results.get('environment', {}).get('installer', {}).get('sha256', '?')[:12]}"
        ]
        if run > int(results.get("repeat", 1)):
            reason = f"une seule exécution réelle disponible : reproductibilité non vérifiée (mesure {run})"
            return BenchmarkSnapshot(
                CAPABILITY_ID,
                version,
                {},
                dict.fromkeys(GROUPS, reason),
                notes=(reason,),
                environment=_environment(results),
            )
        values, blocked, group_notes = group_values(list(results["checks"]), run)
        notes.extend(group_notes)
        reboot_paths = [p for p in os.environ.get("APEX_REAL_WINDOWS_REBOOT", "").split(";") if p]
        blocked.pop("restart", None)
        values.pop("restart", None)
        if len(reboot_paths) >= 2:
            reboot = load_results(reboot_paths[run - 1])
            r_values, r_blocked, r_notes = group_values(list(reboot["checks"]), 1)
            if "restart" in r_values:
                values["restart"] = r_values["restart"]
            else:
                blocked["restart"] = r_blocked.get("restart", "vérifications de redémarrage absentes")
            notes.extend(r_notes[-1:])
        else:
            blocked["restart"] = (
                f"{len(reboot_paths)} redémarrage(s) réel(s) fourni(s) ; deux sont nécessaires "
                "pour vérifier la reproductibilité"
            )
        return BenchmarkSnapshot(
            capability_id=CAPABILITY_ID,
            version=version,
            samples={k: (v,) for k, v in values.items()},
            blocked=blocked,
            notes=tuple(notes),
            environment=_environment(results),
        )


# -- juge : sondes de réfutation ------------------------------------------------------------------


def _results() -> dict[str, Any] | None:
    path = os.environ.get("APEX_REAL_WINDOWS_RESULTS")
    return load_results(path) if path else None


def _probe_provenance() -> ProbeOutcome:
    results = _results()
    if results is None:
        return ProbeOutcome(None, "aucun résultat fourni")
    env = results.get("environment", {})
    expected = hashlib.sha256(SCRIPT.read_bytes()).hexdigest() if SCRIPT.exists() else ""
    problems = []
    if env.get("is_wine") is not False:
        problems.append(f"Wine détecté ou inconnu ({env.get('is_wine')!r})")
    if not str(env.get("os_caption", "")).startswith("Microsoft Windows"):
        problems.append(f"système non Windows ({env.get('os_caption')!r})")
    if results.get("script_sha256") != expected:
        problems.append(
            f"script différent du script versionné "
            f"({str(results.get('script_sha256'))[:12]} ≠ {expected[:12]})"
        )
    if problems:
        return ProbeOutcome(True, " ; ".join(problems), severity=JudgeSeverity.CRITICAL)
    return ProbeOutcome(False, f"{env.get('os_caption')} {env.get('os_build')}, script versionné, pas Wine")


def _probe_check_not_failed(check_ids: tuple[str, ...], what: str) -> Callable[[], ProbeOutcome]:
    def run() -> ProbeOutcome:
        results = _results()
        if results is None:
            return ProbeOutcome(None, "aucun résultat fourni")
        relevant = [c for c in results["checks"] if c["id"] in check_ids]
        failed = [
            f"{c['id']} (exécution {c['run']}) : {c['detail']}" for c in relevant if c["status"] == "FAIL"
        ]
        if failed:
            return ProbeOutcome(True, f"{what} : " + " | ".join(failed), severity=JudgeSeverity.CRITICAL)
        if not any(c["status"] == "PASS" for c in relevant):
            return ProbeOutcome(None, f"{what} : non vérifié sur cette machine (BLOCKED)")
        return ProbeOutcome(False, f"{what} : aucune réfutation ({len(relevant)} vérifications)")

    return run


def _probe_bundle_has_no_secret() -> ProbeOutcome:
    bundle = os.environ.get("APEX_REAL_WINDOWS_BUNDLE")
    if not bundle:
        return ProbeOutcome(None, "archive non fournie (APEX_REAL_WINDOWS_BUNDLE)")
    leaks = []
    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            if info.filename.endswith((".txt", ".json", ".log", ".err")):
                text = archive.read(info).decode("utf-8", errors="replace")
                if "[SECRET MASQUÉ]" in text:
                    leaks.append(info.filename)
    if leaks:
        return ProbeOutcome(
            True,
            f"un secret apparaissait dans : {leaks} (masqué dans l'archive)",
            severity=JudgeSeverity.CRITICAL,
        )
    return ProbeOutcome(False, "aucune sortie ne contenait le jeton ni un mot de passe")


_ALL = frozenset({CycleKind.FUNCTIONALITY, CycleKind.ADVERSARIAL, CycleKind.IMPROVEMENT})

_PROBES: tuple[tuple[str, JudgeDimension, str, Callable[[], ProbeOutcome]], ...] = (
    (
        "results_from_real_windows",
        JudgeDimension.CORRECTNESS,
        "résultats non produits sur un Windows réel par le script versionné",
        _probe_provenance,
    ),
    (
        "panel_never_starts_wsl",
        JudgeDimension.SECURITY,
        "le Panel a démarré WSL",
        _probe_check_not_failed(("W03",), "WSL jamais démarré par le Panel"),
    ),
    (
        "only_wsl_children",
        JudgeDimension.SECURITY,
        "APEX.exe a lancé autre chose que wsl.exe",
        _probe_check_not_failed(("S01", "S02", "S04", "S05"), "processus lancés par APEX.exe"),
    ),
    (
        "no_secret_exposed",
        JudgeDimension.SECURITY,
        "secret visible dans une sortie",
        _probe_check_not_failed(("I08", "S03"), "secrets"),
    ),
    (
        "bundle_has_no_secret",
        JudgeDimension.SECURITY,
        "secret dans l'archive de résultats",
        _probe_bundle_has_no_secret,
    ),
    (
        "never_ready_without_backend",
        JudgeDimension.CORRECTNESS,
        "READY sans backend réel",
        _probe_check_not_failed(("B01", "B08", "B10", "R04"), "READY fantôme"),
    ),
)


def build_judge(judge_id: str = "ci-judge:real-windows") -> ExternalJudge:
    return ExternalJudge(
        judge_id,
        tuple(
            FunctionProbe(
                probe_id=name,
                dimension=dimension,
                kinds=_ALL,
                description=f"échec : {description}",
                remediation="reproduire sur Windows réel, corriger, ajouter le test de régression, "
                "relancer le script",
                regression_test="setup/packaging/windows/Test-ApexWindows.ps1",
                fn=fn,
            )
            for name, dimension, description, fn in _PROBES
        ),
    )
