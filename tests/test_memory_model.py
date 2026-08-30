"""Tests de apex.memory.model — dataclasses pures, aucune DB.

Vérifie les vocabulaires fermés (VerificationStatus/MemoryVisibility/
RelationType/MemoryKind), les validations à la construction (confiance
bornée, contenu/source/provenance non vides, relation non réflexive), et
l'état initial correct d'un nouvel enregistrement (OWN, jamais SHARED par
défaut).
"""

from __future__ import annotations

import unittest

from apex.memory.model import (
    MemoryKind,
    MemoryRecord,
    MemoryRelation,
    MemoryVisibility,
    RelationType,
    VerificationStatus,
)


class TestVerificationStatusVocabulary(unittest.TestCase):
    def test_exactly_five_values(self) -> None:
        """Consigne étape 9 : VERIFIED/LIKELY/UNCERTAIN/DISPUTED/FALSE, ni
        plus ni moins."""
        actual = {s.value for s in VerificationStatus}
        self.assertEqual(actual, {"verified", "likely", "uncertain", "disputed", "false"})

    def test_new_record_defaults_to_uncertain_never_verified(self) -> None:
        """Une absence de preuve ne devient jamais une certitude."""
        record = MemoryRecord.new(
            kind=MemoryKind.SEMANTIC, content="x", source="s", created_by="research",
        )
        self.assertIs(record.verification_status, VerificationStatus.UNCERTAIN)


class TestMemoryKindVocabulary(unittest.TestCase):
    def test_exactly_four_values(self) -> None:
        actual = {k.value for k in MemoryKind}
        self.assertEqual(actual, {"episodic", "semantic", "procedural", "experimental"})


class TestMemoryVisibilityVocabulary(unittest.TestCase):
    def test_exactly_two_values(self) -> None:
        actual = {v.value for v in MemoryVisibility}
        self.assertEqual(actual, {"own", "shared"})

    def test_new_record_is_always_own_never_shared_by_default(self) -> None:
        record = MemoryRecord.new(
            kind=MemoryKind.EPISODIC, content="x", source="s", created_by="research",
        )
        self.assertIs(record.visibility, MemoryVisibility.OWN)


class TestRelationTypeVocabulary(unittest.TestCase):
    def test_exactly_two_values(self) -> None:
        actual = {r.value for r in RelationType}
        self.assertEqual(actual, {"supports", "contradicts"})


class TestVocabulariesAreNeverMerged(unittest.TestCase):
    """Consigne étape 9 : confidence / VerificationStatus / DataStatus /
    ExecutionStatus / ImplStatus restent des vocabulaires séparés."""

    def test_verification_status_and_data_status_share_no_values(self) -> None:
        from apex.obs.status import DataStatus

        verification_values = {s.value for s in VerificationStatus}
        data_status_values = {s.value for s in DataStatus}
        self.assertEqual(verification_values & data_status_values, set())

    def test_confidence_is_a_float_not_a_member_of_verification_status(self) -> None:
        record = MemoryRecord.new(
            kind=MemoryKind.SEMANTIC, content="x", source="s", created_by="research", confidence=0.7,
        )
        self.assertIsInstance(record.confidence, float)
        self.assertNotIsInstance(record.verification_status, float)


class TestMemoryRecordConstruction(unittest.TestCase):
    def test_empty_content_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MemoryRecord.new(kind=MemoryKind.SEMANTIC, content="   ", source="s", created_by="research")

    def test_empty_source_rejected(self) -> None:
        """Une connaissance sans source n'est jamais anonyme."""
        with self.assertRaises(ValueError):
            MemoryRecord.new(kind=MemoryKind.SEMANTIC, content="x", source="  ", created_by="research")

    def test_confidence_out_of_bounds_rejected(self) -> None:
        for bad in (-0.1, 1.1, 2.0, -5.0):
            with self.subTest(confidence=bad):
                with self.assertRaises(ValueError):
                    MemoryRecord.new(
                        kind=MemoryKind.SEMANTIC, content="x", source="s", created_by="research",
                        confidence=bad,
                    )

    def test_confidence_boundaries_are_accepted(self) -> None:
        for ok in (0.0, 1.0, 0.5):
            with self.subTest(confidence=ok):
                record = MemoryRecord.new(
                    kind=MemoryKind.SEMANTIC, content="x", source="s", created_by="research",
                    confidence=ok,
                )
                self.assertEqual(record.confidence, ok)

    def test_evidence_against_carries_contradicting_sources(self) -> None:
        record = MemoryRecord.new(
            kind=MemoryKind.SEMANTIC, content="x", source="s", created_by="research",
            evidence_for=("https://a.example",), evidence_against=("https://b.example",),
        )
        self.assertEqual(record.evidence_for, ("https://a.example",))
        self.assertEqual(record.evidence_against, ("https://b.example",))

    def test_external_provenance_defaults_to_false(self) -> None:
        record = MemoryRecord.new(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by="research")
        self.assertFalse(record.external_provenance)
        self.assertIsNone(record.external_source)

    def test_ids_are_unique(self) -> None:
        a = MemoryRecord.new(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by="research")
        b = MemoryRecord.new(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by="research")
        self.assertNotEqual(a.id, b.id)


class TestMemoryRelationConstruction(unittest.TestCase):
    def test_self_relation_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MemoryRelation.new(
                source_id="a", target_id="a", relation=RelationType.SUPPORTS,
                provenance="p", created_by="research",
            )

    def test_empty_provenance_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MemoryRelation.new(
                source_id="a", target_id="b", relation=RelationType.SUPPORTS,
                provenance="   ", created_by="research",
            )

    def test_confidence_out_of_bounds_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MemoryRelation.new(
                source_id="a", target_id="b", relation=RelationType.CONTRADICTS,
                provenance="p", created_by="research", confidence=1.5,
            )

    def test_confidence_is_optional(self) -> None:
        rel = MemoryRelation.new(
            source_id="a", target_id="b", relation=RelationType.SUPPORTS,
            provenance="p", created_by="research",
        )
        self.assertIsNone(rel.confidence)


if __name__ == "__main__":
    unittest.main()
