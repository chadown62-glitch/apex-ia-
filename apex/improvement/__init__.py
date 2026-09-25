"""Continuous Improvement Engine — voir `docs/CONTINUOUS_IMPROVEMENT.md`."""

from apex.improvement.engine import CapabilityAdapter, ContinuousImprovementEngine, combine_verdict
from apex.improvement.judge import (
    ExternalJudge,
    FunctionProbe,
    ProbeOutcome,
    RefutationProbe,
    SelfConfirmationError,
)
from apex.improvement.ledger import ImprovementLedger
from apex.improvement.measure import compare_snapshots
from apex.improvement.model import (
    BenchmarkSnapshot,
    CapabilitySpec,
    CycleKind,
    CycleRecord,
    Evidence,
    EvidenceLevel,
    Finding,
    HypothesisStatus,
    ImplementerClaim,
    ImprovementVerdict,
    MetricKind,
    MetricSpec,
)

__all__ = [
    "BenchmarkSnapshot",
    "CapabilityAdapter",
    "CapabilitySpec",
    "ContinuousImprovementEngine",
    "CycleKind",
    "CycleRecord",
    "Evidence",
    "EvidenceLevel",
    "ExternalJudge",
    "Finding",
    "FunctionProbe",
    "HypothesisStatus",
    "ImplementerClaim",
    "ImprovementLedger",
    "ImprovementVerdict",
    "MetricKind",
    "MetricSpec",
    "ProbeOutcome",
    "RefutationProbe",
    "SelfConfirmationError",
    "combine_verdict",
    "compare_snapshots",
]
