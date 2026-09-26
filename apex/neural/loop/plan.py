"""Faiblesse observée -> hypothèse falsifiable -> plan d'expérience.

Une `Weakness` n'existe que mesurée : benchmark, valeur, référence, écart et
nombre de documents cités. Une `LoopHypothesis` est refusée si elle ne relie
pas une faiblesse mesurée, UNE modification déclarée, une métrique et un
benchmark existant, et une direction prédite qu'une mesure peut contredire.
Un `ExperimentPlan` fixe tout ce que l'exécution fera : point de départ
(champion ou initialisation), recette complète, budget, benchmarks protégés,
critères d'acceptation et de rejet ; son empreinte identifie l'expérience
(même point de départ + même recette = même expérience, jamais relancée)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

METRIC = "bits_per_byte"
DIRECTION_DECREASE = "decrease"


class NonFalsifiableHypothesisError(ValueError):
    """Hypothèse vague ou impossible à contredire par une mesure."""


class InvalidPlanError(ValueError):
    """Plan incomplet, hors budget ou incohérent avec le monde."""


def plan_fingerprint(world_fingerprint: str, baseline_artifact: str | None, recipe: Recipe) -> str:
    """Identité d'une expérience : même monde, même point de départ, même recette."""
    payload = json.dumps(
        {"world": world_fingerprint, "baseline": baseline_artifact, "recipe": recipe.to_dict()},
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class Weakness:
    benchmark: str
    domain: str | None
    value: float
    reference: float
    gap: float
    documents: int
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Recipe:
    """Recette d'entraînement complète — rien n'est implicite."""

    init: str  # "champion" | "scratch"
    mix: dict[str, float]
    steps: int
    lr: float
    tokens: int
    warmup_steps: int = 20
    weight_decay: float = 0.1
    batch_size: int = 16
    seed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recipe:
        return cls(**{**data, "mix": dict(data["mix"])})

    def diff(self, other: Recipe) -> list[dict[str, Any]]:
        """Champs qui diffèrent de `other` (la recette de référence)."""
        mine, theirs = self.to_dict(), other.to_dict()
        return [{"field": k, "from": theirs[k], "to": v} for k, v in mine.items() if theirs[k] != v]


@dataclass(frozen=True)
class LoopHypothesis:
    weakness: Weakness
    claim: str
    modification: str
    target_benchmark: str
    direction: str
    protected: tuple[str, ...]
    falsification: str
    strategy: str

    def validate(self, benchmarks: set[str]) -> None:
        problems = []
        if self.target_benchmark not in benchmarks:
            problems.append(f"benchmark cible « {self.target_benchmark} » inexistant")
        if self.direction != DIRECTION_DECREASE:
            problems.append(f"direction « {self.direction} » non mesurable ({METRIC} doit baisser)")
        if not self.modification.strip():
            problems.append("aucune modification déclarée")
        if self.weakness.documents < 5:
            problems.append(
                f"faiblesse citée sur {self.weakness.documents} document(s) : preuve insuffisante"
            )
        if not set(self.protected) <= benchmarks or not self.protected:
            problems.append("benchmarks protégés absents ou inconnus")
        if self.weakness.benchmark not in benchmarks:
            problems.append(f"faiblesse citée sur un benchmark inconnu « {self.weakness.benchmark} »")
        if not self.falsification.strip() or not self.claim.strip():
            problems.append("affirmation ou critère de réfutation vide")
        if problems:
            raise NonFalsifiableHypothesisError("; ".join(problems))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "weakness": self.weakness.to_dict(), "protected": list(self.protected)}


@dataclass(frozen=True)
class Budget:
    steps: int
    tokens: int
    timeout_s: float


@dataclass(frozen=True)
class ExperimentPlan:
    world_fingerprint: str
    baseline: dict[str, Any]  # {"name", "index", "artifact"} ou {"name": "init"} au démarrage
    baseline_recipe: Recipe | None
    recipe: Recipe
    hypothesis: LoopHypothesis
    budget: Budget
    parent_experiment_id: str | None = None
    experiment_id: str = field(default_factory=lambda: f"exp-{uuid.uuid4().hex[:12]}")

    @property
    def modifications(self) -> list[dict[str, Any]]:
        if self.baseline_recipe is None:
            return [{"field": "init", "from": None, "to": self.recipe.init}]
        return self.recipe.diff(self.baseline_recipe)

    @property
    def fingerprint(self) -> str:
        return plan_fingerprint(self.world_fingerprint, self.baseline.get("artifact"), self.recipe)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentPlan:
        """Reconstruit un plan journalisé (reprise après crash) et vérifie que
        l'empreinte recalculée est celle qui a été enregistrée."""
        hyp = data["hypothesis"]
        hypothesis = LoopHypothesis(
            weakness=Weakness(**hyp["weakness"]),
            claim=hyp["claim"],
            modification=hyp["modification"],
            target_benchmark=hyp["target_benchmark"],
            direction=hyp["direction"],
            protected=tuple(hyp["protected"]),
            falsification=hyp["falsification"],
            strategy=hyp["strategy"],
        )
        plan = cls(
            world_fingerprint=data["world_fingerprint"],
            baseline=dict(data["baseline"]),
            baseline_recipe=Recipe.from_dict(data["baseline_recipe"]) if data["baseline_recipe"] else None,
            recipe=Recipe.from_dict(data["recipe"]),
            hypothesis=hypothesis,
            budget=Budget(**data["budget"]),
            parent_experiment_id=data["parent_experiment_id"],
            experiment_id=data["experiment_id"],
        )
        if plan.fingerprint != data["fingerprint"]:
            raise InvalidPlanError(f"plan {data['experiment_id']} : empreinte journalisée incohérente")
        return plan

    def validate(self, benchmarks: set[str], *, max_modifications: int, domains: set[str]) -> None:
        self.hypothesis.validate(benchmarks)
        problems = []
        if self.recipe.steps <= 0 or self.recipe.tokens <= 0 or self.recipe.lr <= 0:
            problems.append("recette vide (pas, tokens ou taux d'apprentissage nuls)")
        if self.recipe.steps > self.budget.steps or self.recipe.tokens > self.budget.tokens:
            problems.append(f"recette hors budget ({self.recipe.steps} pas / {self.recipe.tokens} tokens)")
        if set(self.recipe.mix) - domains:
            problems.append(f"domaines inconnus : {sorted(set(self.recipe.mix) - domains)}")
        if self.recipe.init == "champion" and not self.baseline.get("artifact"):
            problems.append("départ du champion demandé sans champion")
        if self.recipe.init not in ("champion", "scratch"):
            problems.append(f"initialisation « {self.recipe.init} » inconnue")
        mods = self.modifications
        if not mods:
            problems.append("aucune modification par rapport à la référence : expérience sans objet")
        if len(mods) > max_modifications:
            problems.append(
                f"{len(mods)} modifications (maximum {max_modifications}) : attribution impossible"
            )
        if problems:
            raise InvalidPlanError("; ".join(problems))

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "parent_experiment_id": self.parent_experiment_id,
            "fingerprint": self.fingerprint,
            "world_fingerprint": self.world_fingerprint,
            "objective": self.hypothesis.claim,
            "baseline": self.baseline,
            "baseline_recipe": self.baseline_recipe.to_dict() if self.baseline_recipe else None,
            "recipe": self.recipe.to_dict(),
            "modifications": self.modifications,
            "hypothesis": self.hypothesis.to_dict(),
            "metrics": [METRIC],
            "target_benchmark": self.hypothesis.target_benchmark,
            "protected_benchmarks": list(self.hypothesis.protected),
            "budget": asdict(self.budget),
            "stop_conditions": [
                "perte non finie",
                "délai dépassé",
                "arrêt demandé",
                "budget de pas atteint",
            ],
            "acceptance_criteria": [
                f"gain significatif (IC 95 % > 0) sur « heldout » ({METRIC}, bootstrap apparié)",
                "aucune régression significative ni au-delà de la tolérance sur une suite protégée",
                "checkpoint intègre, entraîné sur le dataset du monde, sans contamination",
                "mesures du Judge identiques à celles déclarées par l'exécuteur et reproductibles",
            ],
            "rejection_criteria": [
                "régression significative sur une suite protégée",
                "gain non distinguable du bruit (INCONCLUSIVE)",
                "contamination, checkpoint invalide, mesure falsifiée, candidat identique au champion",
            ],
        }
