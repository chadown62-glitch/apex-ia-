"""Capacités du Continuous Improvement Engine dédiées à l'intelligence
native : structure, déterminisme et cohérence juge/adaptateur. Les attentes
de chaque scénario sont vérifiées par les tests de régression des
composants (tests/test_knowledge_agent.py)."""

from __future__ import annotations

from apex.improvement.__main__ import CAPABILITIES
from apex.improvement.capabilities import knowledge_curation as kc
from apex.improvement.capabilities import native_learning as nl
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


def test_native_learning_is_registered_and_measures_every_metric() -> None:
    adapter = CAPABILITIES["native_learning"].adapter()
    assert adapter.spec.capability_id == nl.CAPABILITY_ID
    snapshot = adapter.measure("t")
    names = {m.name for m in nl.SPEC.metrics}
    assert names == set(nl.SCENARIOS)
    # Mesurée (PyTorch présent) ou BLOCKED (absent) — jamais les deux, jamais rien.
    assert set(snapshot.samples) | set(snapshot.blocked) == names
    assert not set(snapshot.samples) & set(snapshot.blocked)
    assert all(v in ((0.0,), (1.0,)) for v in snapshot.samples.values())


def test_native_learning_synthetic_corpus_does_not_leak() -> None:
    """Le corpus des scénarios doit passer le contrôle de fuite lui-même."""
    from apex.neural.dataset import build_dataset

    build_dataset(nl._candidates(), validation_ratio=0.1, test_ratio=0.2, max_overlap=0.5)


def test_native_learning_guard_scenarios_hold() -> None:
    import pytest

    pytest.importorskip("torch")
    for name in (
        "backprop_updates_weights",
        "heldout_gain_accepted",
        "corrupted_training_rejected",
        "leakage_blocked",
        "unconfirmed_experience_refused",
    ):
        assert nl.run_scenario(name)[0] == 1.0, name
