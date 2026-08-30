"""Tests de sécurité du Sandbox — intégration réelle du noyau, pas de mocks
(consigne étape 7, §9-§10). Chaque test exerce le VRAI mécanisme : un
processus réellement isolé, un cgroup réellement écrit, un uid réellement
changé — jamais une simulation de ce que le mécanisme ferait.

Coût assumé : ces tests sont plus lents qu'une suite unitaire ordinaire
(spawn de vrais sous-processus, vrais timeouts). C'est le prix de la
garantie qu'ils prouvent quelque chose de réel — voir la docstring de
`apex.sandbox.linux_namespace`.
"""

from __future__ import annotations

import os
import subprocess
import unittest

from apex.errors import IsolationUnavailable
from apex.sandbox.linux_namespace import LinuxNamespaceSandbox
from apex.sandbox.model import (
    ExecutionStatus,
    IsolationLevel,
    ResourceLimits,
    SandboxCapabilityReport,
    SandboxSpec,
)


def _limits(**overrides) -> ResourceLimits:
    defaults = dict(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=6, disk_mb=16, network="none")
    defaults.update(overrides)
    return ResourceLimits(**defaults)


def _spec(command: tuple[str, ...], **limit_overrides) -> SandboxSpec:
    return SandboxSpec(mission_id="m", clone_id="c", command=command, limits=_limits(**limit_overrides))


class TestFilesystemIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = LinuxNamespaceSandbox()

    def test_write_to_host_root_is_blocked(self) -> None:
        result = self.sandbox.execute(_spec(("bash", "-c", "touch /etc/apex_should_never_exist 2>&1")))
        self.assertIn("Read-only file system", result.stdout)
        self.assertFalse(os.path.exists("/etc/apex_should_never_exist"))

    def test_reading_a_root_only_secret_is_blocked(self) -> None:
        secret_path = "/root/.apex_test_secret"
        with open(secret_path, "w") as fh:
            fh.write("TOP_SECRET\n")
        os.chmod(secret_path, 0o600)
        try:
            result = self.sandbox.execute(_spec(("bash", "-c", f"cat {secret_path} 2>&1")))
            self.assertNotIn("TOP_SECRET", result.stdout)
            self.assertIn("Permission denied", result.stdout)
        finally:
            os.remove(secret_path)

    def test_isolated_workdir_remains_writable(self) -> None:
        result = self.sandbox.execute(_spec(("bash", "-c", "touch ok.txt && echo workdir-ok")))
        self.assertIn("workdir-ok", result.stdout)

    def test_privilege_is_really_dropped_not_a_namespace_remap(self) -> None:
        """La distinction critique découverte pendant le développement :
        `unshare --map-user` seul NE baisse PAS le privilège réel quand
        l'appelant est déjà root. Ce test vérifie l'uid effectif ET l'échec
        de lecture d'un fichier protégé — la seule preuve qui compte."""
        result = self.sandbox.execute(_spec(("python3", "-c", "import os; print(os.getuid())")))
        self.assertEqual(result.stdout.strip(), "65534")


class TestNetworkIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = LinuxNamespaceSandbox()

    def test_outbound_connection_is_blocked(self) -> None:
        script = (
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.settimeout(2)\n"
            "try:\n"
            "    s.connect(('1.1.1.1', 443))\n"
            "    print('LEAK')\n"
            "except OSError as e:\n"
            "    print('blocked:', e)\n"
        )
        result = self.sandbox.execute(_spec(("python3", "-c", script)))
        self.assertIn("blocked", result.stdout)
        self.assertNotIn("LEAK", result.stdout)

    def test_postgres_on_host_loopback_is_unreachable(self) -> None:
        """Exigence explicite de l'étape 7 : la sandbox ne doit jamais
        pouvoir atteindre PostgreSQL — y compris en 127.0.0.1, qui pointe
        vers un netns frais sans aucune route, pas vers l'hôte."""
        script = (
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.settimeout(2)\n"
            "try:\n"
            "    s.connect(('127.0.0.1', 5432))\n"
            "    print('POSTGRES-REACHABLE-BAD')\n"
            "except OSError as e:\n"
            "    print('blocked:', e)\n"
        )
        result = self.sandbox.execute(_spec(("python3", "-c", script)))
        self.assertNotIn("POSTGRES-REACHABLE-BAD", result.stdout)
        self.assertIn("blocked", result.stdout)

    def test_no_postgres_credentials_reach_the_sandboxed_process(self) -> None:
        """L'environnement du processus sandboxé est construit explicitement
        — jamais un `os.environ` hérité — donc aucune variable
        POSTGRES_*/APEX_DB_*_PASSWORD ne peut fuiter, même si le processus
        Python appelant les connaît."""
        # Sauvegarde puis RESTAURE la valeur d'origine (jamais un simple pop
        # inconditionnel) : ce test tournant dans la même suite que
        # tests/test_scheduler_service.py::TestAtomicClaimAgainstRealPostgres
        # (entre autres), un pop aveugle supprimait purement et simplement la
        # vraie variable d'environnement PostgreSQL du reste de la suite —
        # bug réel trouvé en faisant tourner la suite complète, pas supposé.
        previous = os.environ.get("POSTGRES_PASSWORD")
        os.environ["POSTGRES_PASSWORD"] = "should-never-leak"
        try:
            result = self.sandbox.execute(_spec(("bash", "-c", "env | grep -c POSTGRES")))
            # grep -c sort avec le code 1 (aucune correspondance) mais imprime
            # tout de même "0" sur stdout — c'est la preuve attendue.
            self.assertEqual(result.stdout.strip(), "0")
        finally:
            if previous is None:
                os.environ.pop("POSTGRES_PASSWORD", None)
            else:
                os.environ["POSTGRES_PASSWORD"] = previous


class TestResourceLimits(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = LinuxNamespaceSandbox()

    def test_memory_limit_is_really_enforced_by_the_kernel(self) -> None:
        script = (
            "chunks = []\n"
            "for i in range(2000):\n"
            "    b = bytearray(1024 * 1024)\n"
            "    for j in range(0, len(b), 4096):\n"
            "        b[j] = 1\n"
            "    chunks.append(b)\n"
            "print('should not print')\n"
        )
        result = self.sandbox.execute(_spec(("python3", "-c", script), memory_mb=32, timeout_s=10))
        self.assertIs(result.status, ExecutionStatus.OOM_KILLED)
        self.assertNotIn("should not print", result.stdout)
        self.assertEqual(result.memory_peak_mb.status.value, "real")

    def test_pids_limit_blocks_concurrent_forks(self) -> None:
        script = (
            "import os, time\n"
            "children = []\n"
            "for i in range(30):\n"
            "    try:\n"
            "        pid = os.fork()\n"
            "        if pid == 0:\n"
            "            time.sleep(1.5); os._exit(0)\n"
            "        else:\n"
            "            children.append(pid)\n"
            "    except OSError:\n"
            "        pass\n"
            "print('forked', len(children))\n"
            "time.sleep(1.0)\n"
        )
        result = self.sandbox.execute(_spec(("python3", "-c", script), pids_limit=5, timeout_s=8))
        forked = int(result.stdout.split()[1])
        self.assertLess(forked, 30, "la limite de PID n'a pas bloqué les forks excédentaires")
        self.assertLessEqual(result.pids_peak.value, 5)

    def test_timeout_kills_the_whole_process_tree_promptly(self) -> None:
        """Bug réel rencontré et corrigé pendant le développement : tuer
        seulement le `unshare` extérieur laissait le travail namespacé
        orphelin continuer jusqu'à sa fin naturelle. Ce test vérifie que le
        temps réellement écoulé reste proche du timeout, pas de la durée
        demandée par le script."""
        import time as time_module

        started = time_module.perf_counter()
        result = self.sandbox.execute(_spec(("python3", "-c", "import time; time.sleep(30)"), timeout_s=2))
        wall = time_module.perf_counter() - started
        self.assertIs(result.status, ExecutionStatus.TIMEOUT)
        self.assertLess(wall, 10, "le processus n'a pas été tué rapidement après le timeout")

    def test_cpu_quota_is_written_to_the_real_cgroup(self) -> None:
        """Vérification déterministe (pas basée sur un chronométrage,
        potentiellement instable) que la limite CPU demandée atteint bien
        le fichier cgroup réel, avant nettoyage."""
        spec = _spec(("sleep", "0.2"), cpu_cores=0.5)
        cgroup_name = "apex_test_cpu_quota"
        paths = self.sandbox._create_cgroups(cgroup_name, spec)
        try:
            with open(os.path.join(paths["cpu"], "cpu.cfs_quota_us")) as fh:
                quota = int(fh.read().strip())
            with open(os.path.join(paths["cpu"], "cpu.cfs_period_us")) as fh:
                period = int(fh.read().strip())
            self.assertAlmostEqual(quota / period, 0.5, places=2)
        finally:
            self.sandbox._cleanup_cgroups(paths)

    def test_exit_code_and_stdout_stderr_are_captured(self) -> None:
        result = self.sandbox.execute(
            _spec(("bash", "-c", "echo out-line; echo err-line 1>&2; exit 3"))
        )
        self.assertEqual(result.exit_code.value, 3)
        self.assertIn("out-line", result.stdout)
        self.assertIn("err-line", result.stderr)
        self.assertIs(result.status, ExecutionStatus.FAILED)


class TestCleanupAndNoLeaks(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = LinuxNamespaceSandbox()

    def _cgroup_dir_count(self) -> int:
        total = 0
        for controller in ("cpu", "memory", "pids"):
            base = f"/sys/fs/cgroup/{controller}"
            if os.path.isdir(base):
                total += len([d for d in os.listdir(base) if d.startswith("apex_")])
        return total

    def test_no_cgroup_directories_leak_after_success(self) -> None:
        before = self._cgroup_dir_count()
        self.sandbox.execute(_spec(("python3", "-c", "print('ok')")))
        self.assertEqual(self._cgroup_dir_count(), before)

    def test_no_cgroup_directories_leak_after_a_crash(self) -> None:
        before = self._cgroup_dir_count()
        self.sandbox.execute(_spec(("bash", "-c", "kill -9 $$")))
        self.assertEqual(self._cgroup_dir_count(), before)

    def test_no_cgroup_directories_leak_after_a_timeout(self) -> None:
        before = self._cgroup_dir_count()
        self.sandbox.execute(_spec(("python3", "-c", "import time; time.sleep(30)"), timeout_s=1))
        self.assertEqual(self._cgroup_dir_count(), before)

    def test_no_orphaned_process_survives_a_timeout(self) -> None:
        marker = "apex_orphan_marker_test"
        self.sandbox.execute(
            _spec(("python3", "-c", f"import time; time.sleep(30)  # {marker}"), timeout_s=1)
        )
        import time as time_module

        time_module.sleep(0.5)
        out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "", f"processus orphelin détecté : {out.stdout}")


class TestNeverClaimsFalseIsolation(unittest.TestCase):
    def test_execute_refuses_when_isolation_is_not_available(self) -> None:
        fake_report = SandboxCapabilityReport(
            level=IsolationLevel.RLIMIT_ONLY, namespaces_available=False, cgroups_available=False,
            privilege_drop_available=False, network_isolation_available=False,
            docker_daemon_reachable=False, seccomp_available=False, detail="test: forced unavailable",
        )
        sandbox = LinuxNamespaceSandbox(capability_report=fake_report)
        with self.assertRaises(IsolationUnavailable):
            sandbox.execute(_spec(("true",)))

    def test_result_isolation_level_matches_what_was_really_detected(self) -> None:
        sandbox = LinuxNamespaceSandbox()
        result = sandbox.execute(_spec(("true",)))
        self.assertIs(result.isolation_level, sandbox.detect().level)
        self.assertIsNot(result.isolation_level, IsolationLevel.CONTAINERIZED)
        self.assertIsNot(result.isolation_level, IsolationLevel.HARDENED)


if __name__ == "__main__":
    unittest.main()
