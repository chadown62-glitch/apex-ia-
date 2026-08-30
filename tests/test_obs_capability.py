"""Tests de apex.obs.capability.CapabilityStatus — étape 10.

Vérifie le vocabulaire fermé exact et l'absence de fusion avec les autres
vocabulaires fermés du dépôt (DataStatus, VerificationStatus, ImplStatus).
"""

from __future__ import annotations

import unittest

from apex.obs.capability import CapabilityStatus


class TestCapabilityStatusVocabulary(unittest.TestCase):
    def test_exactly_six_values(self) -> None:
        actual = {s.value for s in CapabilityStatus}
        self.assertEqual(
            actual,
            {
                "available", "unavailable_on_dev", "available_on_target",
                "required", "planned", "unsupported",
            },
        )

    def test_unavailable_on_dev_and_unsupported_are_distinct_values(self) -> None:
        """Consigne étape 10 : UNAVAILABLE_ON_DEV != UNSUPPORTED, jamais
        interchangeables."""
        self.assertNotEqual(CapabilityStatus.UNAVAILABLE_ON_DEV, CapabilityStatus.UNSUPPORTED)
        self.assertNotEqual(
            CapabilityStatus.UNAVAILABLE_ON_DEV.value, CapabilityStatus.UNSUPPORTED.value,
        )


class TestCapabilityStatusNeverMergedWithOtherVocabularies(unittest.TestCase):
    def test_no_shared_values_with_data_status(self) -> None:
        from apex.obs.status import DataStatus

        self.assertEqual(
            {s.value for s in CapabilityStatus} & {s.value for s in DataStatus}, set(),
        )

    def test_no_shared_values_with_verification_status(self) -> None:
        from apex.memory.model import VerificationStatus

        self.assertEqual(
            {s.value for s in CapabilityStatus} & {s.value for s in VerificationStatus}, set(),
        )

    def test_impl_status_is_a_distinct_enum_type_never_interchanged(self) -> None:
        """`ImplStatus.PLANNED` (le CODE est-il écrit ?) et
        `CapabilityStatus.PLANNED` (la CAPACITÉ est-elle prévue ?) partagent
        légitimement le mot « planned » — deux questions distinctes posent
        parfois la même réponse en anglais, ce n'est pas une fusion. Ce qui
        compte : ce sont deux classes `Enum` distinctes, jamais un seul
        vocabulaire, et les valeurs propres à CapabilityStatus (DEV/TARGET)
        n'existent pas dans ImplStatus."""
        from apex.interfaces.registry import ImplStatus

        self.assertIsNot(CapabilityStatus, ImplStatus)
        self.assertFalse(issubclass(CapabilityStatus, ImplStatus))
        self.assertFalse(issubclass(ImplStatus, CapabilityStatus))
        dev_target_specific = {"unavailable_on_dev", "available_on_target", "unsupported"}
        self.assertEqual(dev_target_specific & {s.value for s in ImplStatus}, set())


if __name__ == "__main__":
    unittest.main()
