"""Capacités du Continuous Improvement Engine dédiées à l'intelligence
native : structure, déterminisme et cohérence juge/adaptateur. Les attentes
de chaque scénario sont vérifiées par les tests de régression des
composants (tests/test_knowledge_agent.py)."""

from __future__ import annotations

from apex.improvement.__main__ import CAPABILITIES
from apex.improvement.capabilities import knowledge_curation as kc
from apex.improvement.model import CycleKind


def test_knowledge_curation_is_registered_and_deterministic() -> None:
    adapter = CAPABILITIES["knowledge_curation"].adapter()
    assert adapter.spec.capability_id == kc.CAPABILITY_ID
    first, second = adapter.measure("t"), adapter.measure("t")
    assert first.samples == second.samples
    assert set(first.samples) == {m.name for m in kc.SPEC.metrics} == set(kc.SCENARIOS)
    assert all(v in ((0.0,), (1.0,)) for v in first.samples.values())
    assert not first.blocked
    assert adapter.benchmark_fingerprint() == kc.KnowledgeCurationAdapter().benchmark_fingerprint()


def test_guard_scenarios_hold() -> None:
    """Garde-fous : un contenu sain reste accepté, un doublon exact rejeté."""
    for name in (
        "clean_accepted",
        "distinct_document_accepted",
        "exact_duplicate_rejected",
        "plain_injection_quarantined",
        "unknown_license_review",
    ):
        assert kc.run_scenario(name)[0] == 1.0, name


def test_judge_has_one_probe_per_scenario_for_every_cycle_kind() -> None:
    judge = kc.build_judge()
    probes = judge._probes
    assert [p.probe_id for p in probes] == list(kc.SCENARIOS)
    assert all(p.kinds == frozenset(CycleKind) for p in probes)
    assert all(p.regression_test == "tests/test_knowledge_agent.py" for p in probes)
