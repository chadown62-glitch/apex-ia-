"""Tests du launcher Linux/WSL (`scripts/apex-launch.sh`).

Commit 1 : squelette — résolution de racine, chargement contrôlé de `.env`,
détection Python/venv.
Commit 2 : démarrage de l'infrastructure existante via
`docker compose up -d --wait`.
Commit 3 : exécution de `apex doctor` puis `apex gate` (dans cet ordre),
relais fidèle de leurs codes de sortie — jamais réinterprétés.
Commit 4 : gestion robuste de SIGINT (trap exécuté une seule fois) et
sous-commande `stop`, explicite et séparée de Ctrl+C, qui n'arrête que
l'infrastructure que ce launcher a lui-même fait démarrer.

Chaque scénario construit un projet factice isolé (`tempfile`) contenant
uniquement `scripts/apex-launch.sh` — jamais le vrai `.env`/`.venv` du
dépôt — et contrôle `PATH` explicitement, dans le même esprit que
`FakeProbe` pour `apex doctor` : déterministe, aucune dépendance à la
machine qui exécute les tests.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parent.parent / "scripts" / "apex-launch.sh"

# Un PATH minimal portant seulement les utilitaires dont le script a besoin
# pour SE LANCER lui-même (bash, coreutils) — jamais un `python3` réel, pour
# que les tests décident précisément quel interpréteur est "vu" par le script.
_COREUTILS = (
    "dirname", "basename", "stat", "cat", "grep", "mktemp", "env", "bash", "sh", "readlink",
    "mkdir", "mv", "rm", "date",  # requis par le launcher pour l'état d'ownership compose (commit 4)
    "sleep", "touch",  # requis par le faux `docker` (test SIGINT, marqueur d'état) — jamais par le launcher
)


class LauncherFixture:
    """Un projet factice minimal : <root>/scripts/apex-launch.sh."""

    def __init__(self, tmp: Path, *, with_python3: bool = False) -> None:
        self.root = tmp
        self.scripts_dir = tmp / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.launcher = self.scripts_dir / "apex-launch.sh"
        shutil.copy2(LAUNCHER, self.launcher)
        self.launcher.chmod(self.launcher.stat().st_mode | stat.S_IEXEC)

        self.bin_dir = tmp / "_bin"
        self.bin_dir.mkdir(exist_ok=True)
        for tool in _COREUTILS:
            real = shutil.which(tool)
            if real:
                link = self.bin_dir / tool
                if not link.exists():
                    link.symlink_to(real)

        self.docker_calls_log = self.root / "docker_calls.log"
        self.docker_running_flag = self.root / "_docker_running.flag"
        self.compose_state_file = self.root / ".apex" / "launcher-compose-state.json"
        self.python_calls_log = self.root / "python_calls.log"

        if with_python3:
            self._write_fake_python(self.bin_dir / "python3", compatible=True)

    def _write_fake_python(
        self,
        path: Path,
        *,
        compatible: bool,
        doctor_exit: int = 0,
        doctor_output: str = "APEX DOCTOR (fake): 0 fail",
        gate_exit: int = 0,
        gate_output: str = "APEX GATE (fake): authorized",
    ) -> None:
        """Faux interpréteur déterministe — même esprit que `FakeProbe` côté
        `apex doctor` : répond au test de version (`-c ...`) et, invoqué
        comme `-m apex.cli.main doctor|gate`, simule ces commandes sans
        dépendre d'une vraie installation APEX ni de Docker/PostgreSQL/GPU
        réels. Chaque invocation `-m ...` est journalisée dans
        `self.python_calls_log` pour vérifier l'ordre doctor → gate."""
        script = (
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "-c" ]]; then\n'
            f"  exit {0 if compatible else 1}\n"
            "fi\n"
            f'echo "$*" >> "{self.python_calls_log}"\n'
            'if [[ "$1 $2 $3" == "-m apex.cli.main doctor" ]]; then\n'
            f'  echo "{doctor_output}"\n'
            f"  exit {doctor_exit}\n"
            "fi\n"
            'if [[ "$1 $2 $3" == "-m apex.cli.main gate" ]]; then\n'
            f'  echo "{gate_output}"\n'
            f"  exit {gate_exit}\n"
            "fi\n"
            'echo "unexpected fake python invocation: $*" >&2\n'
            "exit 99\n"
        )
        path.write_text(script)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def write_env(self, content: str, *, mode: int = 0o600) -> Path:
        env_path = self.root / ".env"
        env_path.write_text(content)
        env_path.chmod(mode)
        return env_path

    def write_venv_python(self, *, compatible: bool, **apex_behavior: object) -> Path:
        venv_bin = self.root / ".venv" / "bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        python_path = venv_bin / "python3"
        self._write_fake_python(python_path, compatible=compatible, **apex_behavior)
        return python_path

    def write_system_python(self, *, compatible: bool = True, **apex_behavior: object) -> Path:
        python_path = self.bin_dir / "python3"
        self._write_fake_python(python_path, compatible=compatible, **apex_behavior)
        return python_path

    def write_fake_docker(
        self,
        *,
        compose_version_ok: bool = True,
        compose_up_exit: int = 0,
        compose_up_output: str = "fake: containers started",
        compose_down_exit: int = 0,
        initially_running: bool = False,
    ) -> Path:
        """Faux `docker` déterministe, avec un état MUTABLE de « conteneurs
        en cours » (fichier marqueur `self.docker_running_flag`) pour
        simuler `compose ps --status running`, `compose up -d --wait` et
        `compose down` de façon cohérente entre plusieurs invocations
        successives du launcher (nécessaire pour tester l'ownership et
        `stop`) — sans dépendre d'un vrai démon Docker. Journalise chaque
        invocation (args + cwd) dans `self.docker_calls_log`."""
        docker_path = self.bin_dir / "docker"
        if initially_running:
            self.docker_running_flag.write_text("1")
        elif self.docker_running_flag.exists():
            self.docker_running_flag.unlink()
        version_exit = 0 if compose_version_ok else 1
        docker_path.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$* (cwd=$(pwd))" >> "{self.docker_calls_log}"\n'
            'if [[ "$1 $2" == "compose version" ]]; then\n'
            f"  exit {version_exit}\n"
            "fi\n"
            'if [[ "$1 $2 $3 $4" == "compose ps --status running" ]]; then\n'
            f'  if [[ -f "{self.docker_running_flag}" ]]; then echo "fake-container-id"; fi\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$1 $2 $3 $4" == "compose up -d --wait" ]]; then\n'
            f'  echo "{compose_up_output}"\n'
            f'  if [[ {compose_up_exit} -eq 0 ]]; then : > "{self.docker_running_flag}"; fi\n'
            f"  exit {compose_up_exit}\n"
            "fi\n"
            'if [[ "$1 $2" == "compose down" ]]; then\n'
            '  echo "fake: containers removed"\n'
            f'  if [[ {compose_down_exit} -eq 0 ]]; then rm -f "{self.docker_running_flag}"; fi\n'
            f"  exit {compose_down_exit}\n"
            "fi\n"
            'echo "unexpected docker invocation: $*" >&2\n'
            "exit 99\n"
        )
        docker_path.chmod(docker_path.stat().st_mode | stat.S_IEXEC)
        return docker_path

    def run(self, *, cwd: Path | None = None, extra_path: str = "") -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{extra_path}:{self.bin_dir}" if extra_path else str(self.bin_dir),
            "HOME": str(self.root),
        }
        return subprocess.run(
            [str(self.launcher)],
            cwd=str(cwd or self.root),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def run_stop(self, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {"PATH": str(self.bin_dir), "HOME": str(self.root)}
        return subprocess.run(
            [str(self.launcher), "stop"],
            cwd=str(cwd or self.root),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def source_and_call(self, expression: str) -> subprocess.CompletedProcess[str]:
        """Source le script (le garde `BASH_SOURCE == 0` empêche `main` de
        s'exécuter) puis évalue `expression` — pour tester une fonction
        individuelle sans déclencher le préflight complet."""
        script = f'source "{self.launcher}"; {expression}'
        env = {"PATH": str(self.bin_dir), "HOME": str(self.root)}
        return subprocess.run(
            ["bash", "-c", script],
            cwd=str(self.root),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )


class LauncherTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()


class TestMissingEnv(LauncherTestCase):
    def test_missing_env_exits_1_with_remediation(self) -> None:
        fx = LauncherFixture(self.tmp)
        result = fx.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn(".env introuvable", result.stderr)
        self.assertIn("cp .env.example .env", result.stderr)
        # stdout doit rester réservé aux valeurs de retour, jamais aux logs.
        self.assertEqual(result.stdout, "")


class TestPythonDetection(LauncherTestCase):
    def test_no_venv_no_system_python_exits_3(self) -> None:
        fx = LauncherFixture(self.tmp)  # pas de python3 dans _bin
        fx.write_env("FOO=bar\n")
        result = fx.run()
        self.assertEqual(result.returncode, 3)
        self.assertIn("aucun interpréteur Python", result.stderr)
        self.assertNotIn("installer", result.stderr.lower())  # jamais une offre d'installation

    def test_compatible_venv_is_used(self) -> None:
        fx = LauncherFixture(self.tmp)
        fx.write_env("FOO=bar\n")
        venv_python = fx.write_venv_python(compatible=True)
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn(str(venv_python), result.stderr)
        self.assertIn("venv détecté et compatible", result.stderr)

    def test_incompatible_venv_falls_back_to_system_python(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_venv_python(compatible=False)
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("version Python insuffisante", result.stderr)
        self.assertIn("python3 système utilisé", result.stderr)

    def test_incompatible_venv_and_no_system_python_exits_3(self) -> None:
        fx = LauncherFixture(self.tmp)
        fx.write_env("FOO=bar\n")
        fx.write_venv_python(compatible=False)
        result = fx.run()
        self.assertEqual(result.returncode, 3)

    def test_system_python_used_when_no_venv(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("python3 système utilisé", result.stderr)


class TestEnvLoadingIsSafe(LauncherTestCase):
    def test_secret_value_never_appears_in_output(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("POSTGRES_PASSWORD=correct-horse-battery-staple\n")
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("correct-horse-battery-staple", result.stdout)
        self.assertNotIn("correct-horse-battery-staple", result.stderr)
        self.assertIn("1 variable(s) chargée(s)", result.stderr)

    def test_malformed_line_is_warned_and_skipped_without_crashing(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("VALID=ok\nthis is not a valid line\nOTHER=2\n")
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("ligne .env ignorée", result.stderr)
        self.assertIn("2 variable(s) chargée(s)", result.stderr)

    def test_quoted_values_are_unwrapped_literally_never_evaluated(self) -> None:
        fx = LauncherFixture(self.tmp)
        fx.write_env('DANGEROUS="$(touch pwned)"\n')
        canary = self.tmp / "pwned"
        fx.write_venv_python(compatible=True)
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(canary.exists(), "la valeur .env a été évaluée comme du code — injection possible")

    def test_world_readable_env_warns_but_does_not_block(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n", mode=0o644)
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("lisible au-delà du propriétaire", result.stderr)

    def test_strict_env_permissions_produce_no_warning(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n", mode=0o600)
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("lisible au-delà du propriétaire", result.stderr)


class TestPathsWithSpaces(LauncherTestCase):
    """Revue de sécurité §12 : un chemin contenant des espaces ne doit
    jamais casser le quoting ni être scindé en plusieurs arguments."""

    def test_project_root_with_spaces_resolves_correctly(self) -> None:
        spaced_root = self.tmp / "apex project (dev)"
        spaced_root.mkdir()
        fx = LauncherFixture(spaced_root, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"racine du projet : {spaced_root}", result.stderr)


class TestRootResolution(LauncherTestCase):
    def test_root_is_the_launcher_project_regardless_of_cwd(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker()
        other_cwd = Path(tempfile.mkdtemp())
        try:
            result = fx.run(cwd=other_cwd)
        finally:
            shutil.rmtree(other_cwd, ignore_errors=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"racine du projet : {self.tmp}", result.stderr)

    def test_root_resolution_via_symlinked_invocation(self) -> None:
        """La racine doit rester correcte même si le script est invoqué par
        un lien symbolique (cas réel : `PATH` ou raccourci)."""
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker()
        link = Path(tempfile.mkdtemp()) / "apex-launch-link.sh"
        try:
            link.symlink_to(fx.launcher)
            result = subprocess.run(
                [str(link)],
                cwd=str(self.tmp),
                env={"PATH": str(fx.bin_dir), "HOME": str(self.tmp)},
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            shutil.rmtree(link.parent, ignore_errors=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"racine du projet : {self.tmp}", result.stderr)


class TestIdempotence(LauncherTestCase):
    def test_running_twice_does_not_modify_env_or_venv(self) -> None:
        fx = LauncherFixture(self.tmp)
        env_path = fx.write_env("FOO=bar\n")
        venv_python = fx.write_venv_python(compatible=True)
        fx.write_fake_docker()
        env_before = env_path.read_bytes()
        venv_before = venv_python.read_bytes()
        env_mtime_before = env_path.stat().st_mtime_ns
        venv_mtime_before = venv_python.stat().st_mtime_ns

        first = fx.run()
        second = fx.run()

        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(env_path.read_bytes(), env_before)
        self.assertEqual(venv_python.read_bytes(), venv_before)
        self.assertEqual(env_path.stat().st_mtime_ns, env_mtime_before)
        self.assertEqual(venv_python.stat().st_mtime_ns, venv_mtime_before)

        # `docker compose up -d --wait` est nativement idempotent : deux
        # exécutions appellent la même commande deux fois, jamais `down`.
        calls = fx.docker_calls_log.read_text()
        self.assertEqual(calls.count("compose up -d --wait"), 2)
        self.assertNotIn("compose down", calls)


class TestNoUnauthorizedInstallOrStart(LauncherTestCase):
    """Le launcher ne doit ni installer ni démarrer quoi que ce soit au-delà
    de `docker compose up` sur le compose déjà versionné — aucune commande
    `apt`, `curl | sh`, `ollama`, `sudo`, `dockerd`, ni un `docker compose
    down`/`rm`/`prune` n'apparaît dans le script."""

    def test_script_never_mentions_install_or_service_start_commands(self) -> None:
        # Ignore les commentaires : le script DOCUMENTE volontairement ces
        # interdictions dans son en-tête (ex. "`ollama pull` n'apparaît
        # jamais ici") — seule une occurrence en code réel serait un bug.
        code_lines = "\n".join(
            line for line in LAUNCHER.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        forbidden = (
            "apt-get install", "apt install", "ollama pull", "ollama serve",
            "curl -fsSL", "wsl --shutdown", "systemctl start docker", "dockerd",
            "sudo", "compose rm", "compose kill", "system prune",
            "killall", "pkill",
            # down oui (via `stop`) mais suppression de volumes jamais.
            "compose down -v", "compose down --volumes",
        )
        for pattern in forbidden:
            self.assertNotIn(pattern, code_lines, f"motif interdit trouvé dans le launcher : {pattern!r}")

    def test_no_bare_kill_command_anywhere(self) -> None:
        """Ni `kill`, ni `killall`, ni `pkill` : ce launcher ne cible jamais
        un PID pour lui envoyer un signal — SIGINT est géré par un `trap`
        sur le process courant, jamais en signalant un autre process."""
        import re

        code_lines = "\n".join(
            line for line in LAUNCHER.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        self.assertIsNone(re.search(r"\bkill\b", code_lines), "commande 'kill' trouvée dans le launcher")


class TestDockerComposeIntegration(LauncherTestCase):
    """Commit 2 : `docker compose up -d --wait` sur le compose existant
    (postgres/redis/minio) — jamais Ollama, jamais un arrêt, jamais une
    installation de Docker lui-même."""

    def test_docker_not_found_exits_2_without_offering_to_install(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)  # pas de docker dans _bin
        fx.write_env("FOO=bar\n")
        result = fx.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("docker introuvable", result.stderr)
        self.assertIn("ce script n'installe rien", result.stderr)

    def test_compose_plugin_unavailable_exits_2(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker(compose_version_ok=False)
        result = fx.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("plugin 'docker compose' est indisponible", result.stderr)

    def test_compose_up_success_exits_0_and_runs_from_project_root(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker(compose_up_output="fake: containers started")
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("infrastructure démarrée", result.stderr)
        self.assertIn("préflight OK", result.stderr)
        calls = fx.docker_calls_log.read_text()
        self.assertIn(f"compose up -d --wait (cwd={self.tmp})", calls)

    def test_compose_up_failure_exits_2_and_relays_docker_stderr(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker(compose_up_exit=1, compose_up_output="Error response from daemon: boom")
        result = fx.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Error response from daemon: boom", result.stderr)
        self.assertIn("docker compose up a échoué", result.stderr)

    def test_never_invokes_compose_down_or_destructive_commands(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker()
        fx.run()
        calls = fx.docker_calls_log.read_text()
        for destructive in ("compose down", "compose rm", "compose kill", "system prune", " rm "):
            self.assertNotIn(destructive, calls)

    def test_docker_not_invoked_at_all_when_env_missing(self) -> None:
        """Un échec avant l'étape Docker (`.env` absent) ne doit déclencher
        aucun appel `docker`, même en présence d'un `docker` fonctionnel."""
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_fake_docker()
        result = fx.run()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(fx.docker_calls_log.exists() and fx.docker_calls_log.read_text().strip())

    def test_sigint_during_compose_up_exits_130_end_to_end(self) -> None:
        """Test de bout en bout promis au commit 1 : `docker compose up
        -d --wait` est le premier point d'attente réel du launcher — un
        Ctrl+C pendant cette étape doit produire l'exit code 130, sans que
        le launcher n'exécute quoi que ce soit après l'interruption.

        Nuance constatée en écrivant ce test : ce script signale le seul PID
        du launcher (`Popen.send_signal`), pas tout le groupe de processus —
        bash défère alors le trap jusqu'à la fin de la substitution de
        commande en cours (`$(docker compose up ...)`), ici les 5 s de
        `sleep` du faux docker. Un vrai Ctrl+C terminal envoie SIGINT à tout
        le groupe : `docker compose` le recevrait aussi et s'arrêterait de
        lui-même, rendant la substitution quasi immédiate. Le test reste
        valide dans les deux cas — seul le délai diffère — d'où le timeout
        généreux ci-dessous plutôt qu'une assertion de latence."""
        import signal
        import time

        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        docker_path = fx.bin_dir / "docker"
        docker_path.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$* (cwd=$(pwd))" >> "{fx.docker_calls_log}"\n'
            'if [[ "$1 $2" == "compose version" ]]; then exit 0; fi\n'
            'if [[ "$1 $2 $3 $4" == "compose up -d --wait" ]]; then\n'
            "  sleep 5\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n"
        )
        docker_path.chmod(docker_path.stat().st_mode | stat.S_IEXEC)

        proc = subprocess.Popen(
            [str(fx.launcher)],
            cwd=str(fx.root),
            env={"PATH": str(fx.bin_dir), "HOME": str(fx.root)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Laisse le temps au launcher d'atteindre `docker compose up`
            # (résolution racine + .env + venv sont quasi instantanés).
            time.sleep(0.5)
            proc.send_signal(signal.SIGINT)
            _stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        self.assertEqual(proc.returncode, 130)
        self.assertIn("interrompu (Ctrl+C)", stderr)


class TestDoctorGateIntegration(LauncherTestCase):
    """Commit 3 : `apex doctor` puis `apex gate`, dans cet ordre, relais
    fidèle de leurs codes de sortie — jamais un FAIL contourné ou
    réinterprété en succès."""

    def _fixture(self, **apex_behavior: object) -> LauncherFixture:
        fx = LauncherFixture(self.tmp)
        fx.write_env("FOO=bar\n")
        fx.write_venv_python(compatible=True, **apex_behavior)
        fx.write_fake_docker()
        return fx

    def test_doctor_and_gate_success_exits_0(self) -> None:
        fx = self._fixture(doctor_exit=0, gate_exit=0)
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("APEX DOCTOR (fake): 0 fail", result.stdout)
        self.assertIn("APEX GATE (fake): authorized", result.stdout)
        self.assertIn("préflight OK (environnement + infrastructure + doctor + gate)", result.stderr)

    def test_doctor_failure_exits_4_and_never_runs_gate(self) -> None:
        fx = self._fixture(doctor_exit=1, doctor_output="APEX DOCTOR (fake): 1 fail")
        result = fx.run()
        self.assertEqual(result.returncode, 4)
        self.assertIn("APEX DOCTOR (fake): 1 fail", result.stdout)
        self.assertIn("apex doctor a signalé un échec", result.stderr)
        calls = fx.python_calls_log.read_text() if fx.python_calls_log.exists() else ""
        self.assertNotIn("gate", calls)

    def test_gate_failure_exits_5(self) -> None:
        fx = self._fixture(gate_exit=1, gate_output="APEX GATE (fake): not authorized")
        result = fx.run()
        self.assertEqual(result.returncode, 5)
        self.assertIn("APEX GATE (fake): not authorized", result.stdout)
        self.assertIn("apex gate a signalé un échec", result.stderr)

    def test_doctor_runs_before_gate_in_order(self) -> None:
        fx = self._fixture()
        fx.run()
        calls = fx.python_calls_log.read_text().splitlines()
        doctor_calls = [c for c in calls if "doctor" in c]
        gate_calls = [c for c in calls if "gate" in c]
        self.assertEqual(len(doctor_calls), 1)
        self.assertEqual(len(gate_calls), 1)
        self.assertLess(calls.index(doctor_calls[0]), calls.index(gate_calls[0]))

    def test_doctor_and_gate_invoked_from_project_root(self) -> None:
        """`apex doctor`/`gate` doivent trouver `config.yaml` relatif à la
        racine — vérifié indirectement : le launcher ne passe aucun chemin
        codé en dur, `cd` explicitement dans project_root avant l'appel."""
        text = LAUNCHER.read_text()
        self.assertIn('cd -- "${project_root}" && "${python_bin}" -m apex.cli.main doctor', text)
        self.assertIn('cd -- "${project_root}" && "${python_bin}" -m apex.cli.main gate', text)

    def test_no_extra_gate_flags_are_added(self) -> None:
        """Le launcher ne doit jamais ajouter --allow-container-exec ni
        --allow-network-probe : ce serait contourner le consentement
        explicite qu'exige le Gate pour ses vérifications les plus
        invasives."""
        code_lines = "\n".join(
            line for line in LAUNCHER.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        self.assertNotIn("--allow-container-exec", code_lines)
        self.assertNotIn("--allow-network-probe", code_lines)

    def test_secret_value_never_appears_in_doctor_gate_output(self) -> None:
        fx = self._fixture()
        fx.write_env("POSTGRES_PASSWORD=correct-horse-battery-staple\n")
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("correct-horse-battery-staple", result.stdout)
        self.assertNotIn("correct-horse-battery-staple", result.stderr)

    def test_sigint_between_doctor_and_gate_exits_130(self) -> None:
        """Ctrl+C pendant `apex doctor` (lui-même lent ici) doit interrompre
        avant que `apex gate` ne soit jamais invoqué."""
        import signal
        import time

        fx = LauncherFixture(self.tmp)
        fx.write_env("FOO=bar\n")
        fx.write_venv_python(compatible=True)
        fx.write_fake_docker()
        python_path = fx.root / ".venv" / "bin" / "python3"
        python_path.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "-c" ]]; then exit 0; fi\n'
            f'echo "$*" >> "{fx.python_calls_log}"\n'
            'if [[ "$1 $2 $3" == "-m apex.cli.main doctor" ]]; then\n'
            "  sleep 5\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n"
        )
        python_path.chmod(python_path.stat().st_mode | stat.S_IEXEC)

        proc = subprocess.Popen(
            [str(fx.launcher)],
            cwd=str(fx.root),
            env={"PATH": str(fx.bin_dir), "HOME": str(fx.root)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(1.0)  # laisse compose_up (fake, instantané) + démarrage doctor
            proc.send_signal(signal.SIGINT)
            _stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        self.assertEqual(proc.returncode, 130)
        self.assertIn("interrompu (Ctrl+C)", stderr)
        calls = fx.python_calls_log.read_text() if fx.python_calls_log.exists() else ""
        self.assertNotIn("gate", calls)


class TestStopSubcommand(LauncherTestCase):
    """Commit 4 : `./apex-launch.sh stop` — arrêt EXPLICITE, séparé de
    Ctrl+C, de l'infrastructure que CE launcher a lui-même fait démarrer.
    Idempotent, jamais destructeur d'un état qu'il n'a pas créé, jamais
    dépendant de la vivacité d'un PID."""

    def _launched_fixture(self, **docker_kwargs: object) -> LauncherFixture:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker(**docker_kwargs)
        result = fx.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        return fx

    def test_stop_with_no_state_is_noop_exit_0(self) -> None:
        fx = LauncherFixture(self.tmp)
        fx.write_fake_docker()
        result = fx.run_stop()
        self.assertEqual(result.returncode, 0)
        self.assertIn("rien à arrêter", result.stderr)
        calls = fx.docker_calls_log.read_text() if fx.docker_calls_log.exists() else ""
        self.assertNotIn("down", calls)

    def test_stop_after_launch_tears_down_infra(self) -> None:
        fx = self._launched_fixture()
        self.assertTrue(fx.compose_state_file.exists())
        self.assertTrue(fx.docker_running_flag.exists())

        result = fx.run_stop()
        self.assertEqual(result.returncode, 0)
        self.assertIn("infrastructure arrêtée", result.stderr)
        self.assertFalse(fx.compose_state_file.exists())
        self.assertFalse(fx.docker_running_flag.exists())
        calls = fx.docker_calls_log.read_text()
        self.assertIn("compose down", calls)

    def test_double_stop_is_idempotent(self) -> None:
        fx = self._launched_fixture()
        first = fx.run_stop()
        second = fx.run_stop()
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertIn("infrastructure arrêtée", first.stderr)
        self.assertIn("rien à arrêter", second.stderr)
        calls = fx.docker_calls_log.read_text()
        self.assertEqual(calls.count("compose down"), 1, "un seul appel réel à 'compose down' attendu")

    def test_stop_never_touches_infra_it_did_not_start(self) -> None:
        """Infrastructure déjà active AVANT le launcher (jamais démarrée
        par lui) : aucune ownership enregistrée, 'stop' ne doit jamais
        l'arrêter — c'est exactement le cas d'une infra externe/partagée."""
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("FOO=bar\n")
        fx.write_fake_docker(initially_running=True)
        result = fx.run()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(fx.compose_state_file.exists())
        self.assertTrue(fx.docker_running_flag.exists(), "l'infra externe doit rester active")

        stop_result = fx.run_stop()
        self.assertEqual(stop_result.returncode, 0)
        self.assertIn("rien à arrêter", stop_result.stderr)
        self.assertTrue(fx.docker_running_flag.exists(), "'stop' ne doit pas arrêter une infra externe")
        calls = fx.docker_calls_log.read_text()
        self.assertNotIn("compose down", calls)

    def test_stop_ignores_state_not_affirming_ownership(self) -> None:
        fx = LauncherFixture(self.tmp)
        fx.write_fake_docker(initially_running=True)
        fx.compose_state_file.parent.mkdir(parents=True, exist_ok=True)
        fx.compose_state_file.write_text('{"compose_started_by_launcher": false, "pid": 999999}\n')

        result = fx.run_stop()
        self.assertEqual(result.returncode, 0)
        self.assertIn("rien à arrêter", result.stderr)
        self.assertFalse(fx.compose_state_file.exists(), "l'état non-affirmatif doit être nettoyé")
        self.assertTrue(fx.docker_running_flag.exists())

    def test_stop_with_stale_or_reused_pid_still_works(self) -> None:
        """Le PID enregistré est purement informatif : un PID périmé (ex.
        1, presque certainement un process totalement différent aujourd'hui)
        ne doit ni bloquer 'stop' ni provoquer un signal vers ce PID."""
        fx = LauncherFixture(self.tmp)
        fx.write_fake_docker(initially_running=True)
        fx.compose_state_file.parent.mkdir(parents=True, exist_ok=True)
        fx.compose_state_file.write_text(
            '{"compose_started_by_launcher": true, "pid": 1, "started_at": "1970-01-01T00:00:00Z"}\n'
        )

        result = fx.run_stop()
        self.assertEqual(result.returncode, 0)
        self.assertIn("infrastructure arrêtée", result.stderr)
        self.assertFalse(fx.compose_state_file.exists())
        self.assertFalse(fx.docker_running_flag.exists())

    def test_stop_docker_unavailable_preserves_state_for_retry(self) -> None:
        fx = self._launched_fixture()
        (fx.bin_dir / "docker").unlink()  # docker disparaît entre le lancement et le stop

        result = fx.run_stop()
        self.assertEqual(result.returncode, 2)  # EXIT_DOCKER_FAIL
        self.assertTrue(fx.compose_state_file.exists(), "l'état doit être conservé pour un nouvel essai")

    def test_stop_failure_preserves_state_for_retry(self) -> None:
        fx = self._launched_fixture(compose_down_exit=1)
        result = fx.run_stop()
        self.assertEqual(result.returncode, 2)  # EXIT_DOCKER_FAIL
        self.assertTrue(fx.compose_state_file.exists(), "un échec de 'down' ne doit pas effacer l'état")

    def test_stop_state_file_content_never_leaks_secrets(self) -> None:
        fx = LauncherFixture(self.tmp, with_python3=True)
        fx.write_env("POSTGRES_PASSWORD=correct-horse-battery-staple\n")
        fx.write_fake_docker()
        fx.run()
        content = fx.compose_state_file.read_text()
        self.assertNotIn("correct-horse-battery-staple", content)
        self.assertIn("compose_started_by_launcher", content)

    def test_compose_down_only_appears_within_cmd_stop_never_in_main(self) -> None:
        """`docker compose down` est une action EXPLICITE de `stop` — elle
        ne doit jamais apparaître dans `main()` (le préflight normal),
        seulement dans `cmd_stop()`."""
        text = LAUNCHER.read_text()
        main_start = text.index("\nmain() {")
        main_end = text.index("\n}\n", main_start)
        main_body = text[main_start:main_end]
        self.assertNotIn("compose down", main_body)

        stop_start = text.index("\ncmd_stop() {")
        stop_end = text.index("\n}\n", stop_start)
        stop_body = text[stop_start:stop_end]
        self.assertIn("compose down", stop_body)


class TestInterruptHandlerIsIsolated(LauncherTestCase):
    """`on_interrupt` doit produire l'exit code 130 sans agir sur autre chose
    que le process courant — testé en isolation (source sans exécuter
    `main`) et de bout en bout (vrai SIGINT, vrai sous-processus)."""

    def test_on_interrupt_exits_130(self) -> None:
        fx = LauncherFixture(self.tmp)
        result = fx.source_and_call("on_interrupt")
        self.assertEqual(result.returncode, 130)
        self.assertIn("interrompu (Ctrl+C)", result.stderr)
        self.assertIn("aucun processus externe", result.stderr)

    def test_sigint_trap_is_registered_for_int_signal(self) -> None:
        fx = LauncherFixture(self.tmp)
        result = fx.source_and_call("trap -p INT")
        self.assertIn("on_interrupt", result.stdout)

    def test_trap_disables_further_int_as_first_action(self) -> None:
        """`on_interrupt` doit désarmer INT en toute première ligne — la
        garantie structurelle qui rend son exécution unique, vérifiée
        directement sur le source plutôt que par une course de signaux
        (fiable, contrairement à un timing de délivrance de signal)."""
        text = LAUNCHER.read_text()
        start = text.index("on_interrupt() {")
        end = text.index("\n}", start)
        body = text[start:end]
        body_lines = body.splitlines()[1:]
        first_statement = next(
            line.strip() for line in body_lines if line.strip() and not line.strip().startswith("#")
        )
        self.assertEqual(first_statement, "trap '' INT")

    def test_double_sigint_produces_exactly_one_interrupt_message_and_exit_130(self) -> None:
        """Test de bout en bout : deux SIGINT envoyés coup sur coup pendant
        un `apex doctor` volontairement lent ne doivent produire qu'UNE
        seule exécution du handler — un seul message, un seul exit 130,
        jamais une sortie dupliquée ou un état incohérent."""
        import signal
        import time

        fx = LauncherFixture(self.tmp)
        fx.write_env("FOO=bar\n")
        fx.write_venv_python(compatible=True)
        fx.write_fake_docker()
        python_path = fx.root / ".venv" / "bin" / "python3"
        python_path.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "-c" ]]; then exit 0; fi\n'
            'if [[ "$1 $2 $3" == "-m apex.cli.main doctor" ]]; then\n'
            "  sleep 5\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n"
        )
        python_path.chmod(python_path.stat().st_mode | stat.S_IEXEC)

        proc = subprocess.Popen(
            [str(fx.launcher)],
            cwd=str(fx.root),
            env={"PATH": str(fx.bin_dir), "HOME": str(fx.root)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(1.0)
            proc.send_signal(signal.SIGINT)
            proc.send_signal(signal.SIGINT)  # second Ctrl+C immédiat
            _stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        self.assertEqual(proc.returncode, 130)
        self.assertEqual(
            stderr.count("interrompu (Ctrl+C)"), 1,
            f"le handler doit s'exécuter exactement une fois — sortie : {stderr!r}",
        )


class TestBashSyntax(unittest.TestCase):
    def test_script_passes_bash_syntax_check(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(LAUNCHER)], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_is_executable(self) -> None:
        mode = LAUNCHER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "scripts/apex-launch.sh doit être exécutable (chmod +x)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
