"""Contrat d'une CAPACITÉ apprenable, et interface que la boucle autonome
(Priority 1) utilise pour en faire une cible — sans rien savoir de la
capacité elle-même.

Une capacité fournit :
- un contrat explicite (entrée, sortie, environnement, outils permis,
  critères de succès / d'échec, contraintes de sécurité, provenance,
  exigences de reproductibilité, métrique et tolérance de garde) ;
- un benchmark GELÉ (sérialisable, avec empreinte), stocké dans le monde ;
- une évaluation qui produit des `EvalReport` (une suite par split, un score
  par élément, « plus bas = mieux ») et des détails par tâche ;
- ses textes PROTÉGÉS (solutions d'évaluation : jamais dans l'entraînement)
  et ses documents d'entraînement propres (split train seulement).

Le monde (`apex.neural.loop.world`) gèle les données, la boucle choisit les
expériences, le Judge indépendant mesure : la capacité ne décide jamais de
son propre succès."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from apex.neural.dataset import TrainingCandidate
from apex.neural.evaluation import EvalReport
from apex.neural.model import ApexLM
from apex.neural.tokenizer import ByteBPETokenizer
from apex.sandbox.provider import SandboxProvider


@dataclass(frozen=True)
class CapabilityContract:
    name: str
    version: str
    description: str
    input: str
    output: str
    environment: str
    allowed_tools: tuple[str, ...]
    safety_constraints: tuple[str, ...]
    success_criteria: tuple[str, ...]
    failure_criteria: tuple[str, ...]
    reproducibility: tuple[str, ...]
    provenance: str
    metric: str  # valeur par élément, plus bas = mieux
    guard_tolerance: float  # régression tolérée de la valeur agrégée quand la suite est une garde
    target_value: float  # au-dessus : faiblesse observée (cible de la boucle)
    training_domains: tuple[str, ...]  # domaines du monde qui nourrissent la capacité
    acceptance_suite: str  # seule suite de la capacité qui peut fonder une acceptation
    suites: tuple[str, ...]
    limits: tuple[str, ...] = field(default_factory=tuple)  # ce que la capacité NE fait PAS
    #: Suite des tâches dont les solutions peuvent être VUES à l'entraînement (sonde de mémorisation).
    memorization_suite: str | None = None
    #: Suites d'une famille jamais vue à l'entraînement (sonde de transfert).
    transfer_suites: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityEvaluation:
    reports: dict[str, EvalReport]
    details: dict[str, list[dict[str, Any]]]  # suite -> résultats par tâche (JSON)


class Capability(ABC):
    contract: CapabilityContract

    @abstractmethod
    def load(self, payload: dict[str, Any]) -> Any:
        """Relit un benchmark gelé (vérifie son empreinte)."""

    @abstractmethod
    def dump(self, data: Any) -> dict[str, Any]: ...

    @abstractmethod
    def fingerprint(self, data: Any) -> str: ...

    @abstractmethod
    def suite_fingerprints(self, data: Any) -> dict[str, str]: ...

    @abstractmethod
    def protected_texts(self, data: Any) -> list[str]:
        """Textes qui ne doivent JAMAIS apparaître dans l'entraînement."""

    @abstractmethod
    def train_documents(self, data: Any) -> list[TrainingCandidate]:
        """Documents d'entraînement issus du split train de la capacité."""

    @abstractmethod
    def longest_input(self, data: Any, tokenizer: ByteBPETokenizer) -> int:
        """Plus longue entrée (en jetons, BOS/EOS compris) que le modèle devra lire."""

    @abstractmethod
    def evaluate(
        self,
        model: ApexLM,
        tokenizer: ByteBPETokenizer,
        data: Any,
        *,
        provider: SandboxProvider,
    ) -> CapabilityEvaluation: ...


def _significant_gain(c: dict[str, Any]) -> bool:
    return bool(c.get("verdict") == "significant" and c["bpb_gain_ci"][0] > 0)


def _significant_loss(c: dict[str, Any]) -> bool:
    return bool(c.get("verdict") == "significant" and c["bpb_gain_ci"][1] < 0)


def classify_learning(contract: CapabilityContract, comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """MÉMORISATION / GÉNÉRALISATION / TRANSFERT, établis par les comparaisons
    appariées du Judge (jamais déclarés) :

    - GENERALIZATION : gain significatif sur la suite d'acceptation (fonctions jamais vues) ;
    - TRANSFER : gain significatif sur une famille jamais vue à l'entraînement ;
    - MEMORIZATION : gain significatif sur les tâches VUES, sans gain held-out ou nettement
      supérieur à lui (intervalles disjoints) : une part du gain ne se transfère pas ;
    - NONE : aucun gain significatif ; les régressions significatives sont listées à part."""
    comps = {c["suite"]: c for c in comparisons}
    kinds: list[str] = []
    evidence: list[str] = []

    def note(suite: str) -> None:
        c = comps[suite]
        low, high = c["bpb_gain_ci"]
        values = f"{c.get('baseline_bpb')} -> {c.get('candidate_bpb')}"
        evidence.append(f"{suite} : {values} (gain IC 95 % [{low:.3f}, {high:.3f}])")

    heldout = comps.get(contract.acceptance_suite)
    if heldout is not None and _significant_gain(heldout):
        kinds.append("GENERALIZATION")
        note(contract.acceptance_suite)
    for suite in contract.transfer_suites:
        if suite in comps and _significant_gain(comps[suite]):
            kinds.append("TRANSFER")
            note(suite)
    memo = comps.get(contract.memorization_suite or "")
    exceeds_heldout = (
        memo is not None and heldout is not None and memo["bpb_gain_ci"][0] > heldout["bpb_gain_ci"][1]
    )
    if memo is not None and _significant_gain(memo) and ("GENERALIZATION" not in kinds or exceeds_heldout):
        kinds.append("MEMORIZATION")
        note(contract.memorization_suite or "")
    regressions = sorted(s for s in contract.suites if s in comps and _significant_loss(comps[s]))
    return {
        "kinds": kinds or ["NONE"],
        "evidence": evidence,
        "regressions": regressions,
        "status": "ESTABLISHED",  # bootstrap apparié par fonction, sur les mesures du Judge
    }
