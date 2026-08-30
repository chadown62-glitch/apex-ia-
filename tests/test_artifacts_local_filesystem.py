"""Tests de LocalFilesystemArtifactStore — intégration réelle du filesystem
(pas de mock : de vrais fichiers, un vrai répertoire temporaire par test).

Couvre la liste exigée par la consigne étape 10 : stockage, SHA-256, format,
récupération, intégrité, déduplication, binaire, vide, gros contenu, chemin
inexistant, identifiant invalide, path traversal, écriture atomique,
comportement backend indisponible, détection DEV réelle.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.artifacts.model import compute_content_hash
from apex.errors import ArtifactIntegrityError
from apex.obs.capability import CapabilityStatus


class _TempStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="apex_artifacts_test_")
        self.root = os.path.join(self.tmpdir, "artifacts")
        self.store = LocalFilesystemArtifactStore(root=self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestLazyDirectoryCreation(_TempStoreTestCase):
    def test_directory_does_not_exist_right_after_construction(self) -> None:
        self.assertFalse(os.path.isdir(self.root))

    def test_detect_never_creates_the_directory(self) -> None:
        self.store.detect()
        self.assertFalse(os.path.isdir(self.root))

    def test_directory_is_created_only_on_first_real_put(self) -> None:
        self.assertFalse(os.path.isdir(self.root))
        self.store.put(b"x")
        self.assertTrue(os.path.isdir(self.root))


class TestPutAndGetRoundTrip(_TempStoreTestCase):
    def test_stores_and_retrieves_content(self) -> None:
        meta = self.store.put(b"hello world")
        self.assertEqual(self.store.get(meta.content_hash), b"hello world")

    def test_content_hash_matches_real_sha256(self) -> None:
        data = "contenu vérifiable".encode("utf-8")
        meta = self.store.put(data)
        self.assertEqual(meta.content_hash, compute_content_hash(data))

    def test_content_hash_format_is_canonical(self) -> None:
        meta = self.store.put(b"x")
        self.assertTrue(meta.content_hash.startswith("sha256:"))
        self.assertEqual(len(meta.content_hash), len("sha256:") + 64)

    def test_identical_content_yields_the_same_identifier(self) -> None:
        a = self.store.put("même contenu".encode("utf-8"))
        b = self.store.put("même contenu".encode("utf-8"))
        self.assertEqual(a.content_hash, b.content_hash)

    def test_different_contents_yield_different_identifiers(self) -> None:
        hashes = {self.store.put(c).content_hash for c in (b"a", b"b", b"aa", b"", b"\x00")}
        self.assertEqual(len(hashes), 5)

    def test_binary_content_round_trips_exactly(self) -> None:
        data = bytes(range(256)) * 100
        meta = self.store.put(data)
        self.assertEqual(self.store.get(meta.content_hash), data)

    def test_empty_content(self) -> None:
        meta = self.store.put(b"")
        self.assertEqual(meta.size_bytes, 0)
        self.assertEqual(self.store.get(meta.content_hash), b"")

    def test_reasonably_large_content(self) -> None:
        data = os.urandom(8 * 1024 * 1024)  # 8 Mo — réaliste, pas excessif
        meta = self.store.put(data)
        self.assertEqual(meta.size_bytes, len(data))
        self.assertEqual(self.store.get(meta.content_hash), data)

    def test_metadata_size_bytes_matches_content_length(self) -> None:
        meta = self.store.put(b"12345")
        self.assertEqual(meta.size_bytes, 5)

    def test_content_type_and_memory_record_id_are_stored(self) -> None:
        meta = self.store.put(b"x", content_type="image/png", memory_record_id="mem-1")
        self.assertEqual(meta.content_type, "image/png")
        self.assertEqual(meta.memory_record_id, "mem-1")
        fetched = self.store.get_metadata(meta.content_hash)
        assert fetched is not None
        self.assertEqual(fetched.content_type, "image/png")
        self.assertEqual(fetched.memory_record_id, "mem-1")

    def test_put_is_idempotent_no_duplicate_write_needed(self) -> None:
        first = self.store.put(b"dedup")
        second = self.store.put(b"dedup")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(self.store.get(first.content_hash), b"dedup")


class TestExistsAndMetadata(_TempStoreTestCase):
    def test_exists_true_after_put(self) -> None:
        meta = self.store.put(b"x")
        self.assertTrue(self.store.exists(meta.content_hash))

    def test_exists_false_for_a_valid_but_unknown_hash(self) -> None:
        self.assertFalse(self.store.exists(compute_content_hash(b"never stored")))

    def test_get_metadata_none_for_unknown_content(self) -> None:
        self.assertIsNone(self.store.get_metadata(compute_content_hash(b"never stored")))


class TestNonexistentAndInvalidIdentifiers(_TempStoreTestCase):
    def test_get_nonexistent_valid_hash_raises_lookup_error(self) -> None:
        with self.assertRaises(LookupError):
            self.store.get(compute_content_hash(b"never stored"))

    def test_get_malformed_identifier_raises_value_error_not_lookup_error(self) -> None:
        with self.assertRaises(ValueError):
            self.store.get("not-a-valid-hash")

    def test_exists_malformed_identifier_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.store.exists("not-a-valid-hash")

    def test_get_metadata_malformed_identifier_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.store.get_metadata("not-a-valid-hash")

    def test_various_malformed_identifiers_all_rejected(self) -> None:
        bad_identifiers = (
            "", "sha256:", "sha256:short", "md5:" + "a" * 32,
            "sha256:" + "g" * 64,  # caractères non hexadécimaux
            "sha256:" + "A" * 64,  # majuscules — non canonique
        )
        for bad in bad_identifiers:
            with self.subTest(identifier=bad):
                with self.assertRaises(ValueError):
                    self.store.get(bad)


class TestPathTraversalIsStructurallyImpossible(_TempStoreTestCase):
    def test_traversal_like_identifier_is_rejected_before_touching_the_filesystem(self) -> None:
        malicious = "sha256:" + "../" * 20 + "etc/passwd"
        with self.assertRaises(ValueError):
            self.store.get(malicious)
        with self.assertRaises(ValueError):
            self.store.exists(malicious)

    def test_no_file_ever_created_outside_the_store_root(self) -> None:
        outside_marker = os.path.join(self.tmpdir, "should-never-exist")
        malicious = "sha256:" + ("../" * 10 + "should-never-exist").ljust(64, "0")
        try:
            self.store.put(b"x")  # crée le répertoire légitimement
            self.store.get(malicious)
        except ValueError:
            pass
        self.assertFalse(os.path.exists(outside_marker))


class TestIntegrityVerificationOnRead(_TempStoreTestCase):
    def test_tampered_content_is_detected_on_read(self) -> None:
        meta = self.store.put(b"contenu original")
        hex_digest = meta.content_hash.split(":", 1)[1]
        content_path = self.store._content_path(hex_digest)
        with open(content_path, "wb") as fh:
            fh.write(b"contenu corrompu, taille differente")
        with self.assertRaises(ArtifactIntegrityError):
            self.store.get(meta.content_hash)

    def test_tampered_content_never_returned_to_the_caller(self) -> None:
        meta = self.store.put(b"original")
        hex_digest = meta.content_hash.split(":", 1)[1]
        content_path = self.store._content_path(hex_digest)
        with open(content_path, "wb") as fh:
            fh.write(b"falsifie")
        try:
            self.store.get(meta.content_hash)
            self.fail("aurait dû lever ArtifactIntegrityError")
        except ArtifactIntegrityError:
            pass  # le contenu falsifié n'a jamais atteint l'appelant


class TestAtomicWrite(_TempStoreTestCase):
    def test_no_temp_file_survives_a_successful_write(self) -> None:
        self.store.put(b"x")
        for _root, _dirs, files in os.walk(self.root):
            for name in files:
                self.assertFalse(name.startswith(".tmp-"), f"fichier temporaire résiduel : {name}")

    def test_no_partial_file_survives_a_simulated_write_failure(self) -> None:
        import unittest.mock

        original_replace = os.replace

        def failing_replace(*args, **kwargs):
            raise OSError("échec simulé pendant l'écriture")

        with unittest.mock.patch("os.replace", side_effect=failing_replace):
            with self.assertRaises(OSError):
                self.store.put(b"contenu jamais visible")

        # Aucun fichier final ni fichier temporaire ne doit subsister.
        found_any_file = False
        for _root, _dirs, files in os.walk(self.root):
            found_any_file = found_any_file or bool(files)
        self.assertFalse(found_any_file, "un fichier partiellement écrit a survécu à l'échec")

    def test_content_is_only_visible_after_the_atomic_rename(self) -> None:
        meta = self.store.put(b"contenu complet")
        hex_digest = meta.content_hash.split(":", 1)[1]
        content_path = self.store._content_path(hex_digest)
        with open(content_path, "rb") as fh:
            self.assertEqual(fh.read(), b"contenu complet")


class TestDetectionOnDevMachine(_TempStoreTestCase):
    """DEV_MACHINE : ce qui est réellement testable ici, testé réellement —
    jamais une prétention de validation pour TARGET_MACHINE."""

    def test_available_when_directory_not_yet_created_but_parent_writable(self) -> None:
        report = self.store.detect()
        self.assertIs(report.status, CapabilityStatus.AVAILABLE)
        self.assertFalse(report.facts["existed"])

    def test_available_when_directory_already_exists_and_writable(self) -> None:
        self.store.put(b"x")  # crée réellement le répertoire
        report = self.store.detect()
        self.assertIs(report.status, CapabilityStatus.AVAILABLE)
        self.assertTrue(report.facts["existed"])
        self.assertTrue(report.facts["writable"])

    def _make_root_structurally_uncreatable(self) -> None:
        """`chmod` seul ne suffit PAS ici : cette suite tourne en root dans
        ce conteneur, et root contourne les bits de permission Unix — un
        test qui se fierait à `chmod` passerait pour une mauvaise raison.
        On rend le parent structurellement impossible à traverser à la
        place : un FICHIER ordinaire là où un répertoire est attendu — ni
        `os.path.isdir` ni `os.makedirs` ne peuvent le contourner, quel que
        soit le privilège de l'appelant."""
        blocker = os.path.join(self.tmpdir, "not_a_directory")
        with open(blocker, "w") as fh:
            fh.write("x")
        self.root = os.path.join(blocker, "artifacts")
        self.store = LocalFilesystemArtifactStore(root=self.root)

    def test_unavailable_on_dev_when_directory_is_not_writable(self) -> None:
        """Backend réellement indisponible ICI — UNAVAILABLE_ON_DEV, jamais
        UNSUPPORTED (consigne étape 10)."""
        self._make_root_structurally_uncreatable()
        report = self.store.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)
        self.assertNotEqual(report.status, CapabilityStatus.UNSUPPORTED)

    def test_unavailable_on_dev_never_becomes_unsupported_even_when_put_fails(self) -> None:
        self._make_root_structurally_uncreatable()
        with self.assertRaises(OSError):
            self.store.put(b"x")
        report = self.store.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)

    def test_report_backend_name(self) -> None:
        report = self.store.detect()
        self.assertEqual(report.backend, "local_filesystem")


class TestDefaultRootFromConfig(unittest.TestCase):
    def test_no_root_given_resolves_lazily_from_config(self) -> None:
        store = LocalFilesystemArtifactStore()
        self.assertIsNone(store._root)  # rien résolu avant le premier accès
        root = store._resolve_root()
        self.assertTrue(root.endswith("artifacts"))


if __name__ == "__main__":
    unittest.main()
