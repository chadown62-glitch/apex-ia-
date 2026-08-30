"""Tests de apex.artifacts.model — identité de contenu (SHA-256), aucune DB,
aucun filesystem.
"""

from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone

from apex.artifacts.model import (
    ArtifactMetadata,
    ArtifactStoreCapabilityReport,
    compute_content_hash,
    content_hash_hex,
    is_valid_content_hash,
)
from apex.obs.capability import CapabilityStatus


class TestComputeContentHash(unittest.TestCase):
    def test_matches_real_sha256(self) -> None:
        data = b"hello world"
        expected = f"sha256:{hashlib.sha256(data).hexdigest()}"
        self.assertEqual(compute_content_hash(data), expected)

    def test_format_is_sha256_colon_64_hex(self) -> None:
        h = compute_content_hash(b"anything")
        self.assertTrue(h.startswith("sha256:"))
        self.assertEqual(len(h), len("sha256:") + 64)
        hex_part = h.split(":", 1)[1]
        int(hex_part, 16)  # lève ValueError si ce n'est pas de l'hexadécimal

    def test_identical_content_produces_the_same_hash(self) -> None:
        a = compute_content_hash(b"identique")
        b = compute_content_hash(b"identique")
        self.assertEqual(a, b)

    def test_different_content_produces_different_hashes(self) -> None:
        contents = [b"a", b"b", b"aa", b"", b"\x00", b"\x00\x01", b"a" * 1000]
        hashes = {compute_content_hash(c) for c in contents}
        self.assertEqual(len(hashes), len(contents))

    def test_empty_content_has_a_real_hash(self) -> None:
        h = compute_content_hash(b"")
        self.assertEqual(h, f"sha256:{hashlib.sha256(b'').hexdigest()}")

    def test_binary_content(self) -> None:
        data = bytes(range(256)) * 4
        h = compute_content_hash(data)
        self.assertTrue(is_valid_content_hash(h))


class TestIsValidContentHash(unittest.TestCase):
    def test_a_real_computed_hash_is_valid(self) -> None:
        self.assertTrue(is_valid_content_hash(compute_content_hash(b"x")))

    def test_wrong_prefix_is_invalid(self) -> None:
        digest = hashlib.sha256(b"x").hexdigest()
        self.assertFalse(is_valid_content_hash(f"sha512:{digest}"))
        self.assertFalse(is_valid_content_hash(digest))  # sans préfixe du tout

    def test_wrong_length_is_invalid(self) -> None:
        self.assertFalse(is_valid_content_hash("sha256:abc"))
        self.assertFalse(is_valid_content_hash("sha256:" + "a" * 63))
        self.assertFalse(is_valid_content_hash("sha256:" + "a" * 65))

    def test_uppercase_hex_is_invalid(self) -> None:
        """Format canonique STRICT — pas de tolérance de casse."""
        digest = hashlib.sha256(b"x").hexdigest().upper()
        self.assertFalse(is_valid_content_hash(f"sha256:{digest}"))

    def test_path_traversal_like_strings_are_invalid(self) -> None:
        for bad in ("../../../etc/passwd", "sha256:../../../etc/passwd", "sha256:" + "../" * 20):
            with self.subTest(bad=bad):
                self.assertFalse(is_valid_content_hash(bad))

    def test_empty_string_is_invalid(self) -> None:
        self.assertFalse(is_valid_content_hash(""))


class TestContentHashHex(unittest.TestCase):
    def test_extracts_the_hex_part_of_a_valid_hash(self) -> None:
        digest = hashlib.sha256(b"x").hexdigest()
        self.assertEqual(content_hash_hex(f"sha256:{digest}"), digest)

    def test_raises_value_error_on_malformed_input(self) -> None:
        for bad in ("not-a-hash", "sha256:short", "../../etc/passwd", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    content_hash_hex(bad)


class TestArtifactStoreCapabilityReport(unittest.TestCase):
    def test_is_available_true_only_for_available_status(self) -> None:
        available = ArtifactStoreCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend="x", detail="d",
        )
        self.assertTrue(available.is_available)
        for status in CapabilityStatus:
            if status is CapabilityStatus.AVAILABLE:
                continue
            with self.subTest(status=status):
                report = ArtifactStoreCapabilityReport(status=status, backend="x", detail="d")
                self.assertFalse(report.is_available)


class TestArtifactMetadata(unittest.TestCase):
    def test_never_carries_provenance_fields(self) -> None:
        """Consigne étape 10 : ArtifactMetadata ne duplique jamais la
        provenance de MemoryProvider — seuls les faits intrinsèques et
        `memory_record_id` existent."""
        fields = ArtifactMetadata.__dataclass_fields__
        for forbidden in ("source", "confidence", "verification_status", "evidence_for", "evidence_against"):
            self.assertNotIn(forbidden, fields)
        self.assertIn("memory_record_id", fields)

    def test_construction(self) -> None:
        meta = ArtifactMetadata(
            content_hash=compute_content_hash(b"x"), size_bytes=1,
            created_at=datetime.now(timezone.utc), content_type="text/plain",
        )
        self.assertIsNone(meta.memory_record_id)


if __name__ == "__main__":
    unittest.main()
