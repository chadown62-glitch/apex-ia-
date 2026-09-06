"""Tests de `apex.genome.clone_config.resolve_resource_limits` — la
première influence réelle du contenu d'un Genome sur l'exécution d'un
clone (passe « Real Clone Population + Evolution Loop »). Fonction PURE,
aucune DB — voir sa docstring pour le raisonnement de sécurité complet.
"""

from __future__ import annotations

import unittest

from apex.genome.clone_config import resolve_resource_limits
from apex.sandbox.model import ResourceLimits


def _base() -> ResourceLimits:
    return ResourceLimits(cpu_cores=2.0, memory_mb=512, pids_limit=32, timeout_s=60.0, disk_mb=64, network="none")


class TestNoGenomeContent(unittest.TestCase):
    def test_none_content_returns_base_unchanged(self) -> None:
        base = _base()
        self.assertEqual(resolve_resource_limits(None, base), base)

    def test_empty_content_returns_base_unchanged(self) -> None:
        base = _base()
        self.assertEqual(resolve_resource_limits({}, base), base)

    def test_content_without_execution_key_returns_base_unchanged(self) -> None:
        base = _base()
        self.assertEqual(resolve_resource_limits({"strategy": "x"}, base), base)

    def test_malformed_execution_value_returns_base_unchanged(self) -> None:
        base = _base()
        self.assertEqual(resolve_resource_limits({"execution": "not-a-dict"}, base), base)


class TestOverridesApplied(unittest.TestCase):
    def test_valid_override_is_applied(self) -> None:
        base = _base()
        resolved = resolve_resource_limits({"execution": {"timeout_s": 10.0}}, base)
        self.assertEqual(resolved.timeout_s, 10.0)
        self.assertEqual(resolved.cpu_cores, base.cpu_cores)  # champs non couverts inchangés

    def test_two_different_genomes_produce_two_different_clone_configurations(self) -> None:
        """Le test explicitement exigé par la mission (§3) : deux Genomes
        différents doivent produire des `ResourceLimits` différentes
        lorsque les paramètres concernés diffèrent."""
        base = _base()
        genome_a = {"execution": {"timeout_s": 10.0, "cpu_cores": 0.5}}
        genome_b = {"execution": {"timeout_s": 45.0, "cpu_cores": 1.5}}
        resolved_a = resolve_resource_limits(genome_a, base)
        resolved_b = resolve_resource_limits(genome_b, base)
        self.assertNotEqual(resolved_a, resolved_b)
        self.assertEqual(resolved_a.timeout_s, 10.0)
        self.assertEqual(resolved_b.timeout_s, 45.0)

    def test_never_mutates_base(self) -> None:
        base = _base()
        resolve_resource_limits({"execution": {"timeout_s": 1.0}}, base)
        self.assertEqual(base, _base())

    def test_network_is_never_overridable(self) -> None:
        base = _base()
        resolved = resolve_resource_limits({"execution": {"network": "proxy"}}, base)
        self.assertEqual(resolved.network, "none")


class TestSecurityNeverElevatesLimits(unittest.TestCase):
    def test_override_above_ceiling_is_rejected(self) -> None:
        base = _base()
        resolved = resolve_resource_limits({"execution": {"memory_mb": 999_999}}, base)
        self.assertEqual(resolved.memory_mb, base.memory_mb)

    def test_negative_override_is_rejected(self) -> None:
        base = _base()
        resolved = resolve_resource_limits({"execution": {"timeout_s": -5.0}}, base)
        self.assertEqual(resolved.timeout_s, base.timeout_s)

    def test_zero_override_is_rejected(self) -> None:
        base = _base()
        resolved = resolve_resource_limits({"execution": {"cpu_cores": 0}}, base)
        self.assertEqual(resolved.cpu_cores, base.cpu_cores)

    def test_non_numeric_override_is_rejected(self) -> None:
        base = _base()
        resolved = resolve_resource_limits({"execution": {"timeout_s": "fast"}}, base)
        self.assertEqual(resolved.timeout_s, base.timeout_s)

    def test_boolean_override_is_rejected(self) -> None:
        """`bool` est une sous-classe d'`int` en Python — jamais traité
        comme un nombre valide ici (même garde que `apex.genome.mutation`)."""
        base = _base()
        resolved = resolve_resource_limits({"execution": {"timeout_s": True}}, base)
        self.assertEqual(resolved.timeout_s, base.timeout_s)

    def test_override_exactly_at_ceiling_is_accepted(self) -> None:
        base = _base()
        resolved = resolve_resource_limits({"execution": {"timeout_s": base.timeout_s}}, base)
        self.assertEqual(resolved.timeout_s, base.timeout_s)


if __name__ == "__main__":
    unittest.main()
