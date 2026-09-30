"""MinIO facultatif (décision « option C », docs/MINIO_IMPACT_ANALYSIS.md).

Défaut corrigé : l'image `minio/minio` n'est plus distribuée ; MinIO étant déclaré obligatoire (Compose
sans profil + `required: true`), `launch.sh` échouait sur toute machine sans l'image en cache, alors
qu'aucune fonction d'APEX n'utilise MinIO (`MinioArtifactStore` : opérations `NotImplementedYet`).
Ces tests portent sur les VRAIS fichiers du dépôt (`config.yaml`, `docker-compose.yml`) et, quand le
binaire est présent, sur le VRAI `docker compose config` — jamais un double du comportement de Compose.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

from apex.config.loader import load_config
from apex.doctor.check import CheckContext, Severity
from apex.doctor.checks import services
from apex.util.probe import FakeProbe

REPO = Path(__file__).resolve().parents[1]
_ENV = {"POSTGRES_PASSWORD": "x", "MINIO_ROOT_PASSWORD": "x"}


def _real_config():  # type: ignore[no-untyped-def]
    return load_config(REPO / "config.yaml", environ=_ENV)


class TestDeclaredOptional(unittest.TestCase):
    def test_minio_is_optional_and_postgres_redis_stay_required(self) -> None:
        config = _real_config()
        self.assertFalse(config.services["minio"].required)
        self.assertTrue(config.services["postgres"].required)
        self.assertTrue(config.services["redis"].required)

    def test_compose_puts_only_minio_behind_a_profile_and_keeps_its_volume(self) -> None:
        compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(compose["services"]["minio"].get("profiles"), ["minio"])
        self.assertIn("minio-data:/data", compose["services"]["minio"]["volumes"])
        self.assertIn("minio-data", compose["volumes"])  # volume jamais supprimé
        for name in ("postgres", "redis"):
            self.assertNotIn("profiles", compose["services"][name])


class TestDoctorWithoutMinio(unittest.TestCase):
    def _ctx(self, open_ports: set[tuple[str, int]]) -> CheckContext:
        return CheckContext(probe=FakeProbe(open_ports=open_ports), config=_real_config(), timeout_s=0.5)

    def test_absent_minio_is_an_optional_warning_not_a_blocking_failure(self) -> None:
        outcome = services.check_minio(self._ctx(set()))
        self.assertIs(outcome.severity, Severity.WARN)
        self.assertIn("optionnel", outcome.message)

    def test_present_minio_still_passes(self) -> None:
        self.assertIs(services.check_minio(self._ctx({("127.0.0.1", 9000)})).severity, Severity.PASS)

    def test_absent_required_service_is_still_a_blocking_failure(self) -> None:
        outcome = services.check_postgres(self._ctx({("127.0.0.1", 9000)}))
        self.assertIs(outcome.severity, Severity.FAIL)
        self.assertNotIn("optionnel", outcome.message)


@unittest.skipUnless(shutil.which("docker"), "binaire docker absent : `docker compose config` non exécutable ici")
class TestRealComposeProfiles(unittest.TestCase):
    """Le VRAI Compose décide quels services démarrent (aucun démon requis : `config` seulement)."""

    def _services(self, profiles: str | None) -> list[str]:
        env = {**os.environ, **_ENV}
        env.pop("COMPOSE_PROFILES", None)
        if profiles is not None:
            env["COMPOSE_PROFILES"] = profiles
        done = subprocess.run(
            ["docker", "compose", "-f", str(REPO / "docker-compose.yml"), "config", "--services"],
            capture_output=True, text=True, env=env, cwd=REPO, timeout=60, check=False,
        )
        if done.returncode != 0 and "compose" in done.stderr and "is not a docker command" in done.stderr:
            self.skipTest("plugin docker compose absent")
        self.assertEqual(done.returncode, 0, done.stderr)
        return sorted(done.stdout.split())

    def test_default_start_does_not_include_minio(self) -> None:
        self.assertEqual(self._services(None), ["postgres", "redis"])

    def test_the_minio_profile_starts_it_explicitly(self) -> None:
        self.assertEqual(self._services("minio"), ["minio", "postgres", "redis"])
