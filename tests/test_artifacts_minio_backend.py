"""Tests de MinioArtifactStore — détection RÉELLE (sonde TCP réelle sur
cette machine, jamais simulée), opérations de stockage honnêtement
PLANNED (NotImplementedYet). Consigne étape 10, exigence explicite : MinIO
ne doit JAMAIS être déclaré UNSUPPORTED uniquement parce qu'il est absent de
DEV_MACHINE.
"""

from __future__ import annotations

import socket
import unittest

from apex.artifacts.minio_backend import MinioArtifactStore
from apex.errors import NotImplementedYet
from apex.obs.capability import CapabilityStatus


def _port_is_actually_free(host: str, port: int) -> bool:
    """Constat réel — jamais supposé — que rien n'écoute sur ce port,
    utilisé pour choisir un port sûrement injoignable dans les tests."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return False
    except OSError:
        return True


class TestDetectionAgainstThisRealMachine(unittest.TestCase):
    def test_default_port_9000_is_unreachable_on_this_dev_machine(self) -> None:
        """Fait vérifié empiriquement (audit étape 10) : aucun démon MinIO
        n'écoute sur cette machine de développement."""
        store = MinioArtifactStore()
        report = store.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)

    def test_unreachable_minio_is_never_declared_unsupported(self) -> None:
        """Le champ qui compte est `status` — pas le texte libre de
        `detail`, qui explique légitimement pourquoi ce N'EST PAS
        UNSUPPORTED et mentionne donc le mot en le niant."""
        store = MinioArtifactStore()
        report = store.detect()
        self.assertNotEqual(report.status, CapabilityStatus.UNSUPPORTED)
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)

    def test_detail_mentions_target_machine_not_a_permanent_limitation(self) -> None:
        store = MinioArtifactStore()
        report = store.detect()
        self.assertIn("TARGET_MACHINE", report.detail)

    def test_facts_report_the_real_probed_host_and_port(self) -> None:
        store = MinioArtifactStore(host="127.0.0.1", port=9000)
        report = store.detect()
        self.assertEqual(report.facts["host"], "127.0.0.1")
        self.assertEqual(report.facts["port"], 9000)
        self.assertFalse(report.facts["reachable"])

    def test_a_definitely_closed_port_is_also_reported_unavailable_on_dev(self) -> None:
        port = 18765
        self.assertTrue(_port_is_actually_free("127.0.0.1", port), "port de test déjà occupé")
        store = MinioArtifactStore(host="127.0.0.1", port=port)
        report = store.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)

    def test_a_reachable_tcp_endpoint_is_reported_available(self) -> None:
        """Preuve que detect() sonde RÉELLEMENT (pas un résultat câblé en
        dur) : on ouvre nous-mêmes un vrai socket d'écoute et on vérifie que
        detect() le constate."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        _, port = server.getsockname()
        try:
            store = MinioArtifactStore(host="127.0.0.1", port=port)
            report = store.detect()
            self.assertIs(report.status, CapabilityStatus.AVAILABLE)
            self.assertTrue(report.facts["reachable"])
        finally:
            server.close()


class TestOperationsAreHonestlyNotImplemented(unittest.TestCase):
    """Aucune opération ne doit jamais prétendre fonctionner — jamais un
    faux succès, jamais une écriture locale déguisée en MinIO."""

    def setUp(self) -> None:
        self.store = MinioArtifactStore()

    def test_put_raises_not_implemented_yet(self) -> None:
        with self.assertRaises(NotImplementedYet):
            self.store.put(b"x")

    def test_get_raises_not_implemented_yet(self) -> None:
        with self.assertRaises(NotImplementedYet):
            self.store.get("sha256:" + "0" * 64)

    def test_get_metadata_raises_not_implemented_yet(self) -> None:
        with self.assertRaises(NotImplementedYet):
            self.store.get_metadata("sha256:" + "0" * 64)

    def test_exists_raises_not_implemented_yet(self) -> None:
        with self.assertRaises(NotImplementedYet):
            self.store.exists("sha256:" + "0" * 64)

    def test_not_implemented_yet_names_the_real_fallback(self) -> None:
        with self.assertRaises(NotImplementedYet) as ctx:
            self.store.put(b"x")
        self.assertEqual(ctx.exception.fallback, "LocalFilesystemArtifactStore")


if __name__ == "__main__":
    unittest.main()
