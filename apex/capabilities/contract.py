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
