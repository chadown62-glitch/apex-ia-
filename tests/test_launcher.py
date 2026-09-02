"""Tests du launcher Linux/WSL (`scripts/apex-launch.sh`).

Commit 1 : squelette — résolution de racine, chargement contrôlé de `.env`,
détection Python/venv.
Commit 2 : démarrage de l'infrastructure existante via
`docker compose up -d --wait`. Ni `apex doctor` ni `apex gate` n'existent
encore dans le script à ce stade (voir son en-tête).

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
    "sleep",  # requis par le faux `docker` du test SIGINT — jamais par le launcher lui-même
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

        if with_python3:
            self._write_fake_python(self.bin_dir / "python3", compatible=True)

        self.docker_calls_log = self.root / "docker_calls.log"

    def _write_fake_python(self, path: Path, *, compatible: bool) -> None:
        """Faux interpréteur déterministe : réussit ou échoue le test de
        version selon `compatible`, sans dépendre de la version réelle de
        Python installée sur la machine qui exécute les tests."""
        path.write_text(f"#!/usr/bin/env bash\nexit {0 if compatible else 1}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def write_env(self, content: str, *, mode: int = 0o600) -> Path:
        env_path = self.root / ".env"
        env_path.write_text(content)
        env_path.chmod(mode)
        return env_path

    def write_venv_python(self, *, compatible: bool) -> Path:
        venv_bin = self.root / ".venv" / "bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        python_path = venv_bin / "python3"
        self._write_fake_python(python_path, compatible=compatible)
        return python_path

    def write_fake_docker(
        self,
        *,
        compose_version_ok: bool = True,
        compose_up_exit: int = 0,
        compose_up_output: str = "fake: containers started",
    ) -> Path:
        """Faux `docker` déterministe : répond à `compose version` et
        `compose up -d --wait` sans dépendre d'un vrai démon Docker.
        Journalise chaque invocation (args + cwd) dans `self.docker_calls_log`
        pour vérifier ce que le launcher a réellement exécuté."""
        docker_path = self.bin_dir / "docker"
        version_exit = 0 if compose_version_ok else 1
        docker_path.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$* (cwd=$(pwd))" >> "{self.docker_calls_log}"\n'
            'if [[ "$1 $2" == "compose version" ]]; then\n'
            f"  exit {version_exit}\n"
            "fi\n"
            'if [[ "$1 $2 $3 $4" == "compose up -d --wait" ]]; then\n'
            f'  echo "{compose_up_output}"\n'
            f"  exit {compose_up_exit}\n"
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
            "sudo", "compose down", "compose rm", "compose kill", "system prune",
        )
        for pattern in forbidden:
            self.assertNotIn(pattern, code_lines, f"motif interdit trouvé dans le launcher : {pattern!r}")


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


class TestInterruptHandlerIsIsolated(LauncherTestCase):
    """`on_interrupt` doit produire l'exit code 130 sans agir sur autre chose
    que le process courant — testé en isolation (source sans exécuter
    `main`) car le préflight de ce commit est trop rapide pour un vrai
    SIGINT concurrent fiable ; un test de bout en bout viendra naturellement
    avec `docker compose up -d --wait` (commit 2), point d'attente réel."""

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
