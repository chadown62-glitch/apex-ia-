# ruff: noqa: S603, S607 — banc de mesure : il n'exécute que des programmes locaux de confiance (le Panel
# compilé depuis ce dépôt, ses scripts, dotnet, python), jamais une commande reçue de l'extérieur.
"""Capacité DESKTOP DISTRIBUTION du Continuous Improvement Engine (Gauntlet loop).

Question mesurée : la distribution Windows d'APEX (installateur + Panel `APEX.exe`) dit-elle la
vérité sur l'état d'APEX, et s'installe-t-elle / se met-elle à jour / se désinstalle-t-elle comme
annoncé ?

Tout est RÉEL, rien n'est présenté comme Windows :
- le Panel est COMPILÉ depuis les sources au moment de la mesure (binaire linux-x64, même code que
  `APEX.exe`, hôte Linux natif) et interrogé par `--status-json` ;
- le backend est la vraie application FastAPI (`apex.api.app.create_app`), servie par uvicorn dans
  un processus séparé, avec la vraie découverte (`apex.discovery`, phase `serving`, nettoyage à
  l'arrêt) et le vrai jeton — SANS le Gate, qui est l'affaire de `launch.sh`/`apex serve` et n'a pas
  lieu d'être ici (ce conteneur n'est pas la machine cible) ;
- la phase `starting` vient du VRAI `scripts/apex-launch.sh serve` (seul `docker` est un double) ;
- l'installateur est construit par `setup/packaging/build_desktop_release.sh` et son cycle de vie est
  exécuté par `setup/packaging/wine_installer_e2e.sh` (WINE E2E : binaire Windows réel sous Wine,
  jamais une preuve Windows).
Les chemins propres à Windows réel (vrai `wsl.exe`, redirection localhost WSL2, raccourcis du vrai
shell, SmartScreen, redémarrage) restent hors de portée de cette mesure : NOT VERIFIED.
"""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apex.improvement.judge import ExternalJudge, FunctionProbe, ProbeOutcome
from apex.improvement.model import BenchmarkSnapshot, CapabilitySpec, CycleKind, MetricKind, MetricSpec
from apex.metalearner.judge_model import JudgeDimension, JudgeSeverity

CAPABILITY_ID = "desktop_distribution"


#: Arbre source MESURÉ (défaut : ce dépôt). `APEX_CI_DESKTOP_REPO` permet de mesurer une autre
#: révision (worktree) avec la MÊME définition de banc — ex. la baseline d'avant la distribution.
#: Le juge peut être chargé depuis une révision git dans un fichier temporaire (`--judge-ref`) : la
#: racine est donc cherchée (module, puis répertoire courant), jamais déduite d'une profondeur fixe
#: (constaté : `parents[3]` -> IndexError dans le juge en processus séparé, cycle 1).
def _harness_root() -> Path:
    for base in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for candidate in (base, *base.parents):
            if (candidate / "setup" / "ApexSetup.sln").exists():
                return candidate
    return Path.cwd().resolve()


def _repo_root() -> Path:
    configured = os.environ.get("APEX_CI_DESKTOP_REPO")
    return Path(configured) if configured else _harness_root()


REPO = _repo_root()
SETUP = REPO / "setup"
#: Arbre du BANC (jamais `APEX_CI_DESKTOP_REPO`) : le script E2E Wine est un instrument de mesure, pas
#: le produit mesuré. Pris dans l'arbre mesuré, il changeait l'empreinte de définition d'une révision
#: à l'autre (absent à e84f9f1) et rendait toute comparaison INCONCLUSIVE (constaté, cycle 1).
WINE_E2E = _harness_root() / "setup" / "packaging" / "wine_installer_e2e.sh"
PANEL_PROJECT = SETUP / "src" / "ApexSetup.Dashboard" / "ApexSetup.Dashboard.csproj"

_PANEL_METRICS: dict[str, str] = {
    "offline_detected": "aucun backend : APEX NOT READY / BackendNotStarted (code 2)",
    "starting_detected": "launch.sh serve en préflight : Backend STARTING",
    "online_detected": "backend en marche : Backend et API READY, jamais NOT READY",
    "services_mirror_backend": "chaque service affiché = l'état mesuré par le backend lui-même",
    "partial_never_ready": "un service non prêt au backend => jamais APEX READY",
    "token_rejected_detected": "jeton refusé par le backend : TokenRejected",
    "timeout_detected": "backend figé (SIGSTOP) : ApiTimeout, puis rétabli (SIGCONT)",
    "crash_detected": "kill -9 du backend : BackendStopped (enregistrement périmé, pid mort)",
    "clean_stop_detected": "arrêt par SIGINT : enregistrement retiré, BackendNotStarted",
    "port_conflict_detected": "autre programme sur le port : PortConflict",
    "no_secret_in_outputs": "le jeton n'apparaît dans aucun journal, sortie JSON ni diagnostic",
}
#: Chemins Windows impossibles à exécuter ici : mesurés par les tests du Panel (MOCK : wsl.exe simulé).
_WINDOWS_MOCK_METRICS: dict[str, tuple[str, str]] = {
    "wsl_any_language": (
        "WslLocalizationTests",
        "MOCK : WSL en marche vu en marche quelle que soit la langue de Windows (sortie traduite)",
    ),
    "wsl_backend_in_other_distro": (
        "MultiDistroDiscoveryTests",
        "MOCK : backend lancé dans une distribution non par défaut, trouvé sans aucun réglage",
    ),
}
_INSTALLER_METRICS: dict[str, str] = {
    "installer_builds": "build_desktop_release.sh produit l'installateur, son SHA256 et son manifeste",
    "installer_reproducible": "deux builds successifs donnent le même SHA256 d'installateur",
    "installer_lifecycle": "fraction des vérifications WINE E2E réussies (cycle de vie de l'installateur)",
}

#: Coût au repos du Panel FENÊTRÉ (définition d5, ajoutée AVANT le changement du cycle 3) : % d'un cœur,
#: stochastique (bruit de l'ordonnanceur), comparé par bootstrap apparié — jamais un gain construit
#: sur du bruit. Rendu logiciel sous Xvfb : le seul rendu disponible ici, pas celui d'un poste Windows.
IDLE_CPU_METRIC = "idle_cpu_percent"
IDLE_WINDOWS = 6
IDLE_WINDOW_S = 10.0
IDLE_WARMUP_S = 30.0

SPEC = CapabilitySpec(
    capability_id=CAPABILITY_ID,
    name="Desktop Distribution",
    version="1",
    description=(
        "Le Panel APEX.exe affiche l'état RÉEL d'APEX (jamais READY sans réponse authentifiée) et "
        "l'installateur Windows s'installe, se met à jour et se désinstalle comme annoncé."
    ),
    components=(
        "setup/src/ApexSetup.Dashboard (Panel)",
        "setup/packaging/ApexSetup.iss (installateur)",
        "apex/api/routes/status.py",
        "apex/discovery.py",
        "scripts/apex-launch.sh (annonce au Panel)",
    ),
    dependencies=(
        "dotnet 8 SDK",
        "Wine 9 (wine64 + wine32) et Xvfb pour l'installateur",
        "python du dépôt",
    ),
    preconditions=(),
    tests=(
        "setup/tests/ApexSetup.Dashboard.Tests",
        "tests/test_panel_status.py",
        "tests/test_launcher_panel_announce.py",
    ),
    metrics=(
        *(MetricSpec(name, description, True) for name, description in _PANEL_METRICS.items()),
        *(MetricSpec(name, description, True) for name, description in _INSTALLER_METRICS.items()),
        *(MetricSpec(name, description, True) for name, (_, description) in _WINDOWS_MOCK_METRICS.items()),
        MetricSpec(
            IDLE_CPU_METRIC,
            f"CPU (% d'un cœur) du Panel fenêtré au repos contre un backend réel stable : {IDLE_WINDOWS} "
            f"fenêtres de {IDLE_WINDOW_S:.0f} s après {IDLE_WARMUP_S:.0f} s (Xvfb, rendu logiciel)",
            higher_is_better=False,
            kind=MetricKind.STOCHASTIC,
            min_samples=IDLE_WINDOWS,
        ),
    ),
    success_criteria=("toutes les métriques déterministes à 1.0", "aucune régression"),
    risks=(
        "Windows réel non disponible : wsl.exe, localhost WSL2, raccourcis du vrai shell NOT VERIFIED",
        "sondes et correctifs écrits par le même agent",
    ),
    maturity="release-candidate",
)


# -- outillage ------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _listening(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _source_digest() -> str:
    h = hashlib.sha256()
    for path in sorted((SETUP / "src" / "ApexSetup.Dashboard").rglob("*")):
        if path.is_file() and "bin" not in path.parts and "obj" not in path.parts:
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


_panel_cache: dict[str, Path] = {}


def build_panel() -> Path:
    """Compile le Panel depuis les sources ACTUELLES (mis en cache par empreinte des sources)."""
    digest = _source_digest()
    if digest in _panel_cache and _panel_cache[digest].exists():
        return _panel_cache[digest]
    out = Path(tempfile.mkdtemp(prefix="apex-ci-panel-"))
    # Retiré à la sortie du processus : sans cela, chaque mesure laissait ~90 Mo dans /tmp et a fini par
    # remplir le disque (constaté avant le cycle 3 : installateur à 0/17 faute d'espace pour Wine).
    atexit.register(shutil.rmtree, out, ignore_errors=True)
    subprocess.run(
        [
            "dotnet",
            "publish",
            str(PANEL_PROJECT),
            "-c",
            "Release",
            "-r",
            "linux-x64",
            "--self-contained",
            "true",
            "-o",
            str(out),
            "-nologo",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"},
    )
    _panel_cache[digest] = out / "APEX"
    return out / "APEX"


@dataclass
class Sandbox:
    """Un « poste » isolé : répertoire personnel (découverte), projet (jeton), journaux du Panel."""

    root: Path

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def project(self) -> Path:
        return self.root / "project"

    @property
    def logs(self) -> Path:
        return self.root / "panel-logs"

    @property
    def record(self) -> Path:
        return self.home / ".apex" / "runtime" / "backend.json"

    def panel_env(self, port: int) -> dict[str, str]:
        config = self.root / "panel-config"
        config.mkdir(parents=True, exist_ok=True)
        (config / "dashboard-settings.json").write_text(json.dumps({"ApiPort": port}), encoding="utf-8")
        return {
            **os.environ,
            "HOME": str(self.home),
            "APEX_PANEL_LOG_DIR": str(self.logs),
            "APEX_PANEL_CONFIG_DIR": str(config),
        }


@contextlib.contextmanager
def sandbox() -> Iterator[Sandbox]:
    root = Path(tempfile.mkdtemp(prefix="apex-ci-desktop-"))
    box = Sandbox(root)
    box.home.mkdir()
    box.project.mkdir()
    (box.project / "config.yaml").symlink_to(REPO / "config.yaml")
    try:
        yield box
    finally:
        shutil.rmtree(root, ignore_errors=True)


def panel_status(panel: Path, box: Sandbox, port: int, *, diagnostic: bool = False) -> tuple[int, Any]:
    args = [str(panel), "--diagnostic" if diagnostic else "--status-json"]
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=60, env=box.panel_env(port))
    except subprocess.TimeoutExpired:
        return -1, "" if diagnostic else {"headline": "aucune réponse du Panel sous 60 s", "components": []}
    if diagnostic:
        return done.returncode, done.stdout
    try:
        return done.returncode, json.loads(done.stdout)
    except ValueError:
        return done.returncode, {
            "headline": "sortie illisible",
            "cause": done.stdout[:200],
            "components": [],
        }


_BACKEND = """
import os, sys
from pathlib import Path
import uvicorn
from apex.api.app import create_app
from apex.discovery import cleanup_on_shutdown, write_discovery
port = int(sys.argv[1])
write_discovery("127.0.0.1", port, Path.cwd().resolve(), phase="serving")
uvicorn.run(cleanup_on_shutdown(create_app()), host="127.0.0.1", port=port, log_level="warning")
"""


@contextlib.contextmanager
def backend(box: Sandbox, port: int, *, env_token: str | None = None) -> Iterator[subprocess.Popen[str]]:
    """Vraie API APEX (processus séparé) ; jeton réel créé dans `<projet>/.apex/api-token`."""
    env = {**os.environ, "HOME": str(box.home), "PYTHONPATH": str(REPO)}
    env.pop("APEX_API_TOKEN", None)
    env.pop("APEX_BRAIN_WORKDIR", None)
    if env_token:
        env["APEX_API_TOKEN"] = env_token
    proc = subprocess.Popen(
        [sys.executable, "-c", _BACKEND, str(port)],
        cwd=box.project,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not _listening(port):
            if proc.poll() is not None:
                raise RuntimeError(f"le backend s'est arrêté au démarrage (code {proc.returncode})")
            time.sleep(0.2)
        yield proc
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(proc.pid, signal.SIGCONT)
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def backend_status(box: Sandbox, port: int) -> dict[str, str]:
    token = (box.project / ".apex" / "api-token").read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/status", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - boucle locale
        data = json.loads(response.read())["data"]
    return {c["component"]: c["state"] for c in data["components"]}


def _states(result: Any) -> dict[str, str]:
    return {c["key"]: c["status"] for c in result.get("components", [])}


# -- scénarios Panel ------------------------------------------------------------------------------


def panel_scenarios(panel: Path) -> tuple[dict[str, float], list[str], list[float]]:
    """Chaque scénario non exécutable (ex. une révision sans `/status` ni découverte) vaut 0, avec
    sa raison — jamais une exception qui interromprait la mesure, jamais un succès par défaut."""
    values: dict[str, float] = {}
    notes: list[str] = []
    latencies: list[float] = []
    try:
        _panel_scenarios_body(panel, values, notes, latencies)
    except Exception as exc:
        notes.append(f"scénarios interrompus : {exc.__class__.__name__}: {exc}"[:400])
    for metric in _PANEL_METRICS:
        if metric not in values:
            values[metric] = 0.0
            notes.append(f"{metric}: ÉCHEC — scénario non exécutable sur cette révision")
    return values, notes, latencies


def _panel_scenarios_body(
    panel: Path, values: dict[str, float], notes: list[str], latencies: list[float]
) -> None:
    outputs: list[str] = []

    def note(metric: str, ok: bool, detail: str) -> None:
        values[metric] = 1.0 if ok else 0.0
        notes.append(f"{metric}: {'OK' if ok else 'ÉCHEC'} — {detail}")

    with sandbox() as box:
        port = _free_port()
        code, out = panel_status(panel, box, port)
        note(
            "offline_detected",
            code == 2 and out.get("cause") == "BackendNotStarted",
            f"code {code}, {out.get('cause')}",
        )

        code, out = _starting_scenario(panel, box, port)
        note(
            "starting_detected",
            _states(out).get("Backend") == "Starting",
            f"code {code}, {out.get('cause')}",
        )

        with backend(box, port) as proc:
            backend_status(box, port)  # préchauffe (détection du sandbox)
            code, out = panel_status(panel, box, port)
            outputs.append(json.dumps(out))
            states = _states(out)
            note(
                "online_detected",
                states.get("Backend") == "Ready"
                and states.get("Api") == "Ready"
                and out.get("verdict") in ("Ready", "Degraded"),
                f"code {code}, {out.get('headline')}",
            )
            measured = backend_status(box, port)
            mapping = {
                "database": "Database",
                "redis": "Redis",
                "ollama": "Ollama",
                "sandbox": "Sandbox",
                "brain": "Brain",
            }
            mismatches = [
                (k, v, states.get(mapping[k]))
                for k, v in measured.items()
                if k in mapping and states.get(mapping[k], "").lower() != v
            ]
            note(
                "services_mirror_backend",
                not mismatches,
                f"écarts {mismatches}" if mismatches else f"{measured}",
            )
            any_not_ready = any(v != "ready" for v in measured.values())
            note(
                "partial_never_ready",
                not (any_not_ready and out.get("verdict") == "Ready"),
                f"backend non prêts : {sorted(k for k, v in measured.items() if v != 'ready')}, "
                f"verdict {out.get('verdict')}",
            )
            for _ in range(5):
                t0 = time.perf_counter()
                panel_status(panel, box, port)
                latencies.append(round(time.perf_counter() - t0, 3))
            outputs.append(panel_status(panel, box, port, diagnostic=True)[1])

            os.kill(proc.pid, signal.SIGSTOP)
            try:
                code, frozen = panel_status(panel, box, port)
            finally:
                os.kill(proc.pid, signal.SIGCONT)
            _, back = panel_status(panel, box, port)
            note(
                "timeout_detected",
                frozen.get("cause") == "ApiTimeout" and _states(back).get("Api") == "Ready",
                f"figé : {frozen.get('cause')} ; repris : {back.get('headline')}",
            )

            proc.kill()
            proc.wait(timeout=20)
            code, out = panel_status(panel, box, port)
            note(
                "crash_detected",
                code == 2 and out.get("cause") == "BackendStopped" and box.record.exists(),
                f"code {code}, {out.get('cause')}, enregistrement présent : {box.record.exists()}",
            )

        with backend(box, port) as proc:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=30)
            code, out = panel_status(panel, box, port)
            note(
                "clean_stop_detected",
                out.get("cause") == "BackendNotStarted" and not box.record.exists(),
                f"{out.get('cause')}, enregistrement présent : {box.record.exists()}",
            )

        wrong_token = "jeton-du-backend-different-0123456789"  # noqa: S105 - volontairement faux (scénario)
        with backend(box, port, env_token=wrong_token):
            code, out = panel_status(panel, box, port)
            outputs.append(json.dumps(out))
            note(
                "token_rejected_detected",
                out.get("cause") == "TokenRejected",
                f"code {code}, {out.get('cause')}",
            )

        foreign = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=box.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                if _listening(port):
                    break
                time.sleep(0.1)
            code, out = panel_status(panel, box, port)
            note(
                "port_conflict_detected",
                out.get("cause") == "PortConflict",
                f"code {code}, {out.get('cause')}",
            )
        finally:
            foreign.terminate()
            foreign.wait(timeout=10)

        token_file = box.project / ".apex" / "api-token"
        token = token_file.read_text(encoding="utf-8").strip() if token_file.exists() else None
        logs = "".join(p.read_text(encoding="utf-8") for p in box.logs.glob("*.log"))
        leaked = token is not None and any(token in text for text in [logs, *outputs])
        note(
            "no_secret_in_outputs",
            token is not None and not leaked,
            "jeton absent des journaux, JSON et diagnostic"
            if not leaked
            else "JETON TROUVÉ dans une sortie",
        )


def _starting_scenario(panel: Path, box: Sandbox, port: int) -> tuple[int, Any]:
    """Vrai launch.sh (copié) + vrai python ; `docker` : double qui attend 20 s pendant `compose up`."""
    project = box.root / "launch-project"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "apex-launch.sh", project / "scripts" / "apex-launch.sh")
    (project / "apex").symlink_to(REPO / "apex")
    (project / ".env").write_text("FOO=bar\n", encoding="utf-8")
    (project / ".env").chmod(0o600)
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").symlink_to(sys.executable)
    bin_dir = box.root / "launch-bin"
    bin_dir.mkdir()
    for tool in (
        "dirname",
        "basename",
        "stat",
        "cat",
        "grep",
        "mktemp",
        "env",
        "bash",
        "sh",
        "readlink",
        "mkdir",
        "mv",
        "rm",
        "date",
        "sleep",
        "touch",
        "tr",
        "head",
        "kill",
    ):
        real = shutil.which(tool)
        if real:
            (bin_dir / tool).symlink_to(real)
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\nif [[ "$1 $2" == "compose version" ]]; then exit 0; fi\n'
        'if [[ "$1 $2" == "compose up" ]]; then sleep 20; exit 1; fi\nexit 0\n'
    )
    docker.chmod(0o755)
    launcher = subprocess.Popen(
        [str(project / "scripts" / "apex-launch.sh"), "serve", "--port", str(port)],
        cwd=project,
        env={"PATH": str(bin_dir), "HOME": str(box.home)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if box.record.exists():
                break
            time.sleep(0.1)
        return panel_status(panel, box, port)
    finally:
        launcher.terminate()
        launcher.wait(timeout=30)


# -- chemins Windows (MOCK) -------------------------------------------------------------------------


def dotnet_test_class(test_class: str) -> tuple[float, str]:
    """Exécute une classe de tests du Panel (dans l'arbre MESURÉ) : 1.0 si elle passe entièrement."""
    project = SETUP / "tests" / "ApexSetup.Dashboard.Tests"
    selector = f"FullyQualifiedName~ApexSetup.Dashboard.Tests.{test_class}"
    done = subprocess.run(
        ["dotnet", "test", str(project), "--nologo", "--filter", selector],
        capture_output=True,
        text=True,
        timeout=1800,
        env={**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"},
    )
    lines = done.stdout.splitlines()
    summary = next((line.strip() for line in lines if "Passed!" in line or "Failed!" in line), "")
    ok = done.returncode == 0 and summary.startswith("Passed!")
    return (1.0 if ok else 0.0), summary or (done.stdout + done.stderr)[-300:]


# -- coût au repos (Panel fenêtré) ----------------------------------------------------------------


def _free_display() -> str | None:
    for number in range(90, 130):
        if not Path(f"/tmp/.X{number}-lock").exists() and not Path(f"/tmp/.X11-unix/X{number}").exists():  # noqa: S108
            return f":{number}"
    return None


def _cpu_ticks(pid: int) -> int:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])  # utime + stime (champs 14 et 15 de /proc/pid/stat)


def idle_cpu_samples(panel: Path) -> tuple[list[float], str]:
    """Échantillons (% d'un cœur), ou ([], raison) si la mesure est impossible : jamais inventés."""
    display = _free_display() if shutil.which("Xvfb") else None
    if display is None:
        return [], "Xvfb absent ou aucun écran virtuel libre"
    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1000x780x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1)
        if xvfb.poll() is not None:
            return [], f"Xvfb {display} ne démarre pas (code {xvfb.returncode})"
        with sandbox() as box:
            port = _free_port()
            with backend(box, port):
                backend_status(box, port)
                env = {**box.panel_env(port), "DISPLAY": display}
                gui = subprocess.Popen(
                    [str(panel), "--software-rendering"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    time.sleep(IDLE_WARMUP_S)
                    ticks = os.sysconf("SC_CLK_TCK")
                    samples: list[float] = []
                    for _ in range(IDLE_WINDOWS):
                        if gui.poll() is not None:
                            return [], f"le Panel fenêtré s'est arrêté (code {gui.returncode})"
                        before = _cpu_ticks(gui.pid)
                        time.sleep(IDLE_WINDOW_S)
                        used = (_cpu_ticks(gui.pid) - before) / ticks
                        samples.append(round(100 * used / IDLE_WINDOW_S, 3))
                    return samples, f"{IDLE_WINDOWS} fenêtres de {IDLE_WINDOW_S:.0f} s : {samples}"
                finally:
                    gui.terminate()
                    try:
                        gui.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        gui.kill()
    except (OSError, RuntimeError) as exc:
        return [], f"mesure impossible : {type(exc).__name__}: {exc}"
    finally:
        xvfb.terminate()
        xvfb.wait(timeout=10)


# -- installateur (WINE E2E) ----------------------------------------------------------------------


#: Préfixe Wine (~1,4 Gio) + deux builds de l'installateur + publication : en dessous, BLOQUÉ.
INSTALLER_MIN_FREE_BYTES = 4 * 2**30


def installer_measure() -> tuple[dict[str, float], dict[str, str], list[str], float | None]:
    missing = [tool for tool in ("dotnet", "wine", "Xvfb", "winepath") if shutil.which(tool) is None]
    if missing:
        reason = f"outils absents : {missing}"
        return {}, dict.fromkeys(_INSTALLER_METRICS, reason), [reason], None
    free = shutil.disk_usage(tempfile.gettempdir()).free
    if free < INSTALLER_MIN_FREE_BYTES:
        # Environnement saturé : ni un succès ni un échec du produit — la mesure n'a pas eu lieu.
        needed = INSTALLER_MIN_FREE_BYTES / 2**30
        reason = f"espace disque insuffisant : {free / 2**30:.1f} Gio libres < {needed:.0f} Gio"
        return {}, dict.fromkeys(_INSTALLER_METRICS, reason), [reason], None
    values: dict[str, float] = {}
    notes: list[str] = []
    work = Path(tempfile.mkdtemp(prefix="apex-ci-installer-"))
    try:
        hashes = []
        for index in (1, 2):
            out = work / f"release{index}"
            done = subprocess.run(
                ["bash", str(SETUP / "packaging" / "build_desktop_release.sh"), str(out)],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            manifest = out / "manifest.json"
            if done.returncode != 0 or not manifest.exists():
                # Pas d'installateur : capacités qui en dépendent ABSENTES (0), pas « non mesurées ».
                values.update(dict.fromkeys(_INSTALLER_METRICS, 0.0))
                notes.append(
                    f"installer_builds: ÉCHEC (code {done.returncode}) {(done.stdout + done.stderr)[-300:]}"
                )
                return values, {}, notes, None
            hashes.append(json.loads(manifest.read_text(encoding="utf-8"))["installer"]["sha256"])
        values["installer_builds"] = 1.0
        values["installer_reproducible"] = 1.0 if hashes[0] == hashes[1] else 0.0
        notes.append(f"installer_builds: OK ; installer_reproducible : {hashes[0][:16]} / {hashes[1][:16]}")
        installer = next((work / "release1").glob("APEX-Setup-x64-v*.exe"))
        done = subprocess.run(
            [
                "bash",
                str(WINE_E2E),
                str(installer),
                str(work / "wine"),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        results = json.loads((work / "wine" / "results.json").read_text(encoding="utf-8"))
        values["installer_lifecycle"] = results["passed"] / results["total"]
        failed = [c["id"] for c in results["checks"] if c["status"] != "PASS"]
        notes.append(f"installer_lifecycle: {results['passed']}/{results['total']} (échecs : {failed})")
        if failed:
            notes.append(
                f"installer_lifecycle: fin de sortie WINE E2E : {(done.stdout + done.stderr)[-600:]}"
            )
        seconds_file = work / "wine" / "install_seconds"
        seconds = float(seconds_file.read_text()) if seconds_file.exists() else None
        return values, {}, notes, seconds
    finally:
        shutil.rmtree(work, ignore_errors=True)


class DesktopDistributionAdapter:
    spec = SPEC

    def benchmark_fingerprint(self) -> str:
        parts = [inspect_source(f) for f in (WINE_E2E, Path(__file__))]
        return "desktop=" + hashlib.sha256("".join(parts).encode()).hexdigest()[:16]

    def measure(self, version: str) -> BenchmarkSnapshot:
        samples: dict[str, tuple[float, ...]] = {}
        blocked: dict[str, str] = {}
        notes: list[str] = []
        if shutil.which("dotnet") is None:
            blocked.update(dict.fromkeys([*_PANEL_METRICS, IDLE_CPU_METRIC], "dotnet absent"))
        else:
            panel = build_panel()
            values, panel_notes, latencies = panel_scenarios(panel)
            samples.update({k: (v,) for k, v in values.items()})
            notes.extend(panel_notes)
            # Durée de --status-json : information, pas une métrique (non mesurable sans backend, elle
            # rendrait BLOQUÉ par construction tout cycle dont la baseline n'a pas de backend).
            if latencies:
                notes.append(f"durée de --status-json (backend en marche, 5 mesures) : {latencies} s")
            idle, idle_note = idle_cpu_samples(panel)
            if idle:
                samples[IDLE_CPU_METRIC] = tuple(idle)
            else:
                blocked[IDLE_CPU_METRIC] = idle_note
            notes.append(f"{IDLE_CPU_METRIC}: {idle_note}")
        for metric, (test_class, _) in _WINDOWS_MOCK_METRICS.items():
            if shutil.which("dotnet") is None:
                blocked[metric] = "dotnet absent"
                continue
            value, observed = dotnet_test_class(test_class)
            samples[metric] = (value,)
            notes.append(f"{metric}: {'OK' if value else 'ÉCHEC'} — {observed}")
        values, installer_blocked, installer_notes, seconds = installer_measure()
        samples.update({k: (v,) for k, v in values.items()})
        blocked.update(installer_blocked)
        notes.extend(installer_notes)
        if seconds is not None:
            notes.append(f"installation silencieuse sous Wine : {seconds} s")
        return BenchmarkSnapshot(
            capability_id=CAPABILITY_ID,
            version=version,
            samples=samples,
            blocked=blocked,
            notes=tuple(notes),
            environment={
                "host": "linux (conteneur)",
                "windows": "Wine 9 (pas Windows réel)",
                "wsl": "absent",
            },
        )


def inspect_source(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


# -- juge : sondes de réfutation ------------------------------------------------------------------


def _probe_ready_without_backend() -> ProbeOutcome:
    panel = build_panel()
    with sandbox() as box:
        port = _free_port()
        with backend(box, port) as proc:
            proc.kill()
            proc.wait(timeout=20)
        _, out = panel_status(panel, box, port)
    ready = out.get("verdict") == "Ready" or _states(out).get("Api") == "Ready"
    return ProbeOutcome(
        ready,
        f"backend tué : {out.get('headline')} ({out.get('cause')})",
        "le Panel ne dit jamais READY quand le backend ne répond pas",
        JudgeSeverity.CRITICAL,
    )


def _probe_hostile_record() -> ProbeOutcome:
    panel = build_panel()
    with sandbox() as box:
        port = _free_port()
        box.record.parent.mkdir(parents=True)
        box.record.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "service": "apex",
                    "phase": "serving",
                    "apex_version": "0.1.2",
                    "api_version": "v1",
                    "panel_protocol": 1,
                    "project_root": "/etc",
                    "token_file": "../etc/shadow",
                    "host": "127.0.0.1",
                    "port": port,
                    "pid": os.getpid(),
                    "started_at": "2026-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        _, out = panel_status(panel, box, port)
    accepted = out.get("cause") != "DiscoveryInvalid"
    return ProbeOutcome(
        accepted,
        f"enregistrement altéré : {out.get('cause')}",
        "un enregistrement de découverte aux chemins hostiles est refusé",
        JudgeSeverity.CRITICAL,
    )


def _probe_version_mismatch() -> ProbeOutcome:
    """Serveur HTTP réel qui répond comme un backend d'un protocole futur (2)."""
    panel = build_panel()
    body = json.dumps(
        {
            "status": "real",
            "data": {
                "service": "apex",
                "versions": {"apex": "9.9.9", "api": "v1", "panel_protocol": 2},
                "components": [{"component": "database", "state": "ready", "detail": ""}],
            },
        }
    )
    script = (
        "import http.server,sys\nB=sys.argv[2].encode()\nclass H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(s):\n        s.send_response(200); s.end_headers(); s.wfile.write(B)\n"
        "    def log_message(s,*a): pass\n"
        "http.server.HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()\n"
    )
    with sandbox() as box:
        port = _free_port()
        server = subprocess.Popen([sys.executable, "-c", script, str(port), body])
        try:
            for _ in range(50):
                if _listening(port):
                    break
                time.sleep(0.1)
            _, out = panel_status(panel, box, port)
        finally:
            server.terminate()
            server.wait(timeout=10)
    interpreted = out.get("verdict") != "VersionMismatch" or _states(out).get("Database") != "Unknown"
    return ProbeOutcome(
        interpreted,
        f"protocole 2 : {out.get('headline')} ({out.get('cause')})",
        "un protocole inconnu n'est jamais interprété (VERSION_MISMATCH)",
        JudgeSeverity.HIGH,
    )


def _probe_corrupted_settings_and_logs() -> ProbeOutcome:
    panel = build_panel()
    with sandbox() as box:
        port = _free_port()
        env = box.panel_env(port)
        Path(env["APEX_PANEL_CONFIG_DIR"], "dashboard-settings.json").write_text(
            "{ corrompu", encoding="utf-8"
        )
        blocker = box.root / "fichier"
        blocker.write_text("x", encoding="utf-8")
        env["APEX_PANEL_LOG_DIR"] = str(blocker / "logs")
        done = subprocess.run(
            [str(panel), "--diagnostic"], capture_output=True, text=True, timeout=60, env=env
        )
    reported = "illisibles" in done.stdout and "inaccessible" in done.stdout
    return ProbeOutcome(
        done.returncode not in (0, 1, 2, 3) or not reported,
        f"code {done.returncode} ; problèmes signalés au diagnostic : {reported}",
        "paramètres corrompus et journaux inaccessibles : le Panel répond et le dit",
        JudgeSeverity.HIGH,
    )


def _probe_no_shell_in_wsl_commands() -> ProbeOutcome:
    source = (SETUP / "src" / "ApexSetup.Dashboard" / "Services" / "BackendHost.cs").read_text(
        encoding="utf-8"
    )
    code_lines = [
        line for line in source.splitlines() if not line.strip().startswith("//") and "///" not in line
    ]
    offending = [
        line.strip() for line in code_lines if any(s in line for s in ('"bash"', '"-c"', '"-lc"', '"sh"'))
    ]
    uses_exec = '"--exec"' in source
    return ProbeOutcome(
        bool(offending) or not uses_exec,
        f"shell : {offending or 'aucun'} ; --exec : {uses_exec}",
        "aucune commande WSL ne passe par un shell",
        JudgeSeverity.CRITICAL,
    )


def _probe_installer_policy() -> ProbeOutcome:
    # Lignes de script seulement : un commentaire Inno Setup (« ; ») peut citer une section absente.
    iss = "\n".join(
        line
        for line in (SETUP / "packaging" / "ApexSetup.iss").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith(";")
    )
    manifest = (SETUP / "src" / "ApexSetup.Dashboard" / "app.manifest").read_text(encoding="utf-8")
    problems = []
    if "PrivilegesRequired=lowest" not in iss:
        problems.append("élévation demandée")
    if 'level="asInvoker"' not in manifest:
        problems.append("APEX.exe demande l'élévation")
    if "[UninstallDelete]" in iss or "[InstallDelete]" in iss:
        problems.append("suppression hors fichiers installés")
    if '{autodesktop}\\APEX"' not in iss or "Tasks: desktopicon" in iss:
        problems.append("raccourci Bureau non automatique")
    return ProbeOutcome(
        bool(problems),
        f"problèmes : {problems or 'aucun'}",
        "sans administrateur, raccourci Bureau automatique, aucune donnée utilisateur supprimée",
        JudgeSeverity.HIGH,
    )


def _mock_probe(metric: str) -> Callable[[], ProbeOutcome]:
    test_class, description = _WINDOWS_MOCK_METRICS[metric]

    def run() -> ProbeOutcome:
        value, observed = dotnet_test_class(test_class)
        return ProbeOutcome(value < 1.0, observed, description, JudgeSeverity.HIGH)

    return run


_F, _A, _I = CycleKind.FUNCTIONALITY, CycleKind.ADVERSARIAL, CycleKind.IMPROVEMENT
_ALL = frozenset({_F, _A, _I})

_PROBES: tuple[tuple[str, JudgeDimension, str, Callable[[], ProbeOutcome], str], ...] = (
    (
        "ready_without_backend",
        JudgeDimension.CORRECTNESS,
        "READY affiché sans backend",
        _probe_ready_without_backend,
        "setup/tests/ApexSetup.Dashboard.Tests/StatusEngineTests.cs",
    ),
    (
        "hostile_discovery_record",
        JudgeDimension.SECURITY,
        "enregistrement de découverte hostile accepté",
        _probe_hostile_record,
        "setup/tests/ApexSetup.Dashboard.Tests/StatusClientTests.cs",
    ),
    (
        "unknown_protocol_interpreted",
        JudgeDimension.CORRECTNESS,
        "protocole inconnu interprété",
        _probe_version_mismatch,
        "setup/tests/ApexSetup.Dashboard.Tests/StatusEngineTests.cs",
    ),
    (
        "corrupted_settings_or_logs",
        JudgeDimension.ROBUSTNESS,
        "paramètres corrompus / journaux inaccessibles mal gérés",
        _probe_corrupted_settings_and_logs,
        "setup/tests/ApexSetup.Dashboard.Tests/LogsAndDiagnosticTests.cs",
    ),
    (
        "shell_in_wsl_commands",
        JudgeDimension.SECURITY,
        "commande WSL passée par un shell",
        _probe_no_shell_in_wsl_commands,
        "setup/tests/ApexSetup.Dashboard.Tests/BackendHostTests.cs",
    ),
    (
        "wsl_localized_output",
        JudgeDimension.EDGE_CASES,
        "WSL mal lu sur un Windows non anglais (MOCK)",
        _mock_probe("wsl_any_language"),
        "setup/tests/ApexSetup.Dashboard.Tests/WslRobustnessTests.cs",
    ),
    (
        "backend_in_other_distro",
        JudgeDimension.EDGE_CASES,
        "backend d'une autre distribution introuvable (MOCK)",
        _mock_probe("wsl_backend_in_other_distro"),
        "setup/tests/ApexSetup.Dashboard.Tests/WslRobustnessTests.cs",
    ),
    (
        "installer_policy",
        JudgeDimension.SECURITY,
        "élévation, raccourci ou suppression de données non conformes",
        _probe_installer_policy,
        "setup/packaging/wine_installer_e2e.sh",
    ),
)


def build_judge(judge_id: str = "ci-judge:desktop-distribution") -> ExternalJudge:
    return ExternalJudge(
        judge_id,
        tuple(
            FunctionProbe(
                probe_id=name,
                dimension=dimension,
                kinds=_ALL,
                description=f"échec : {description}",
                remediation="corriger le Panel, l'installateur ou le backend concerné",
                regression_test=test,
                fn=fn,
            )
            for name, dimension, description, fn, test in _PROBES
        ),
    )
