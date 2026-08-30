"""Tests du registre backend des Specialty — apex.specialties.

Vérifie surtout l'honnêteté du registre : une seule Specialty ("research")
est déclarée supportée, cohérente avec ce qu'AgentRuntime exerce réellement
de bout en bout (voir apex/agents/runtime.py) — et que Domain et Specialty
restent deux concepts non fusionnés (aucun import croisé).
"""

from __future__ import annotations

import unittest

from apex.interfaces.registry import ImplStatus
from apex.policy.model import Subject
from apex.specialties import SPECIALTIES, by_id, supported
from apex.specialties.model import Specialty


class TestRegistry(unittest.TestCase):
    def test_unique_ids(self) -> None:
        ids = [s.id for s in SPECIALTIES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_by_id_finds_a_known_specialty(self) -> None:
        spec = by_id("research")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.name, "Research")

    def test_by_id_returns_none_for_unknown(self) -> None:
        self.assertIsNone(by_id("does-not-exist"))

    def test_only_research_is_supported(self) -> None:
        """Ne pas créer artificiellement 15 implémentations — une seule
        Specialty a un moteur réellement exercé (RESEARCH, AgentRuntime)."""
        ids = {s.id for s in supported()}
        self.assertEqual(ids, {"research"})

    def test_every_non_research_specialty_is_planned(self) -> None:
        for spec in SPECIALTIES:
            if spec.id == "research":
                continue
            with self.subTest(specialty=spec.id):
                self.assertEqual(spec.dev_stage, ImplStatus.PLANNED)
                self.assertFalse(spec.is_supported)

    def test_research_is_stub_not_implemented(self) -> None:
        """STUB, pas IMPLEMENTED : même honnêteté que le registre principal
        pour AgentRuntime — aucune isolation OS réelle n'existe encore."""
        spec = by_id("research")
        self.assertEqual(spec.dev_stage, ImplStatus.STUB)
        self.assertTrue(spec.is_supported)

    def test_compatible_agent_types_are_real_subjects(self) -> None:
        for spec in SPECIALTIES:
            for agent_type in spec.compatible_agent_types:
                with self.subTest(specialty=spec.id, agent_type=agent_type):
                    self.assertIsInstance(agent_type, Subject)


class TestSpecialtyIsSupported(unittest.TestCase):
    def test_defined_is_not_supported(self) -> None:
        spec = Specialty(id="x", name="X", description="", dev_stage=ImplStatus.DEFINED)
        self.assertFalse(spec.is_supported)

    def test_implemented_is_supported(self) -> None:
        spec = Specialty(id="x", name="X", description="", dev_stage=ImplStatus.IMPLEMENTED)
        self.assertTrue(spec.is_supported)


if __name__ == "__main__":
    unittest.main()
