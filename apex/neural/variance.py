"""Verdict MULTI-GRAINES : distinguer une régression reproductible du bruit d'entraînement.

Règles pré-enregistrées dans `docs/P3_7_VARIANCE_AND_BINDING.md` §2 (commit fca0ae3), implémentées
ici telles quelles. Une recette est entraînée avec k graines ; chaque graine est un artefact réel
jugé par le Judge ; seules ses mesures par élément entrent ici. Pour chaque suite protégée, la
référence est l'ANCRE de son groupe (mécanisme existant) :

- D_i = valeur(graine i) - valeur(ancre) (plus bas = mieux ; > 0 = pire), agrégation d'`EvalReport` ;
- SE_item : bootstrap apparié sur les éléments de la moyenne des D_i ;
- sigma = max(sigma historique, écart-type observé des D_i) ; U = 1,96 · √(SE_item² + sigma²/k) ;
- marge acceptable m = max(tolérance du monde, sigma historique).

Ce module ne mesure rien et ne décide d'aucune promotion : il calcule, depuis des mesures, un
verdict que le contrôleur consigne et que le registre RECALCULE."""

from __future__ import annotations

import math
from typing import Any

import torch

from apex.neural.evaluation import BPB, EvalReport
from apex.neural.protection import Protection

REGRESSION = "REGRESSION"
IMPROVEMENT = "IMPROVEMENT"
NO_REGRESSION = "NO_REGRESSION"
VARIANCE_INCONCLUSIVE = "VARIANCE_INCONCLUSIVE"
MEMORIZATION = "MEMORIZATION"
INVALID_EXPERIMENT = "INVALID_EXPERIMENT"

ACCEPT = "accept"
REJECT = "reject"
INCONCLUSIVE = "inconclusive"
INVALID = "invalid"

Z = 1.96
BOOTSTRAP = 2000
#: Exactitude held-out démontrée (1 - borne haute de l'erreur) exigée d'une NOUVELLE capacité.
ACQUISITION_MAX_ERROR = 0.90

#: Contrôles du Judge qui rendent une graine INVALIDE (intégrité, pas une mauvaise hypothèse).
INTEGRITY_CHECKS = frozenset(
    {
        "candidate_integrity",
        "no_contamination",
        "candidate_reproducible",
        "claimed_metrics_match",
        "baseline_integrity",
        "baseline_unchanged",
        "candidate_differs",
        "declared_data_matches_recipe",
        "training_replayed",
    }
)


def failed_integrity(checks: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        name
        for name, check in checks.items()
        if not check.get("ok") and (name in INTEGRITY_CHECKS or name.startswith("anchor_"))
    )


def pooled_sigma(groups: list[dict[str, list[float]]]) -> dict[str, float]:
    """sigma groupé par suite : somme des carrés des écarts à la moyenne des graines / somme (k_j - 1)."""
    squares: dict[str, float] = {}
    freedom: dict[str, int] = {}
    for group in groups:
        for suite, values in group.items():
            if len(values) < 2:
                continue
            mean = sum(values) / len(values)
            squares[suite] = squares.get(suite, 0.0) + sum((v - mean) ** 2 for v in values)
            freedom[suite] = freedom.get(suite, 0) + len(values) - 1
    return {s: math.sqrt(squares[s] / freedom[s]) for s in squares}


def _aligned(
    reference: EvalReport, seeds: list[EvalReport]
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    ids = [s.doc_id for s in reference.scores]
    ref = torch.tensor([s.value for s in reference.scores], dtype=torch.float64)
    weights = torch.tensor(
        [float(s.n_bytes) if reference.metric == BPB else 1.0 for s in reference.scores],
        dtype=torch.float64,
    )
    rows = []
    for report in seeds:
        by_id = {s.doc_id: s.value for s in report.scores}
        if set(by_id) != set(ids):
            raise ValueError(f"« {reference.benchmark} » : éléments évalués différents (graine, ancre)")
        if report.benchmark_fingerprint != reference.benchmark_fingerprint:
            raise ValueError(f"« {reference.benchmark} » : benchmark différent (graine, ancre)")
        rows.append([by_id[i] for i in ids])
    return ref, weights, torch.tensor(rows, dtype=torch.float64)


def _bootstrap_se(ref: torch.Tensor, weights: torch.Tensor, cand: torch.Tensor) -> float:
    n = ref.shape[0]
    generator = torch.Generator().manual_seed(0)
    idx = torch.randint(n, (BOOTSTRAP, n), generator=generator)
    w = weights[idx]
    total = w.sum(dim=1)
    ref_agg = (ref[idx] * w).sum(dim=1) / total
    cand_agg = (cand[:, idx] * w.unsqueeze(0)).sum(dim=2) / total.unsqueeze(0)
    mean_delta = (cand_agg - ref_agg.unsqueeze(0)).mean(dim=0)
    return float(mean_delta.std(unbiased=True))


def suite_row(
    group: str,
    reference: EvalReport,
    seeds: list[EvalReport],
    *,
    sigma_prior: float | None,
    tolerance: float,
) -> dict[str, Any]:
    ref, weights, cand = _aligned(reference, seeds)
    k = len(seeds)
    deltas = [s.value - reference.value for s in seeds]
    mean = sum(deltas) / k
    observed = math.sqrt(sum((d - mean) ** 2 for d in deltas) / (k - 1)) if k > 1 else None
    se_item = _bootstrap_se(ref, weights, cand)
    sigma = max(x for x in (sigma_prior, observed, 0.0) if x is not None)
    uncertainty = Z * math.sqrt(se_item**2 + sigma**2 / k)
    margin = max(tolerance, sigma_prior or 0.0)
    if mean - uncertainty > margin and all(d > 0 for d in deltas):
        status = REGRESSION
    elif mean + uncertainty < 0:
        status = IMPROVEMENT
    elif mean + uncertainty <= margin:
        status = NO_REGRESSION
    else:
        status = VARIANCE_INCONCLUSIVE
    se_artifact = _bootstrap_se(ref, weights, cand[:1])
    return {
        "group": group,
        "suite": reference.benchmark,
        "metric": reference.metric,
        "reference": round(reference.value, 5),
        "seeds": [round(s.value, 5) for s in seeds],
        "delta": round(mean, 5),
        "deltas": [round(d, 5) for d in deltas],
        "se_item": round(se_item, 5),
        "sigma": round(sigma, 5),
        "sigma_prior": None if sigma_prior is None else round(sigma_prior, 5),
        "sigma_observed": None if observed is None else round(observed, 5),
        "uncertainty": round(uncertainty, 5),
        "margin": round(margin, 5),
        "status": status,
        # L'artefact promu est celui de la graine planifiée (la première), jamais la meilleure graine.
        "artifact_beyond_margin": bool(deltas[0] - Z * se_artifact > margin),
    }


def seed_verdict(
    *,
    seeds: list[dict[str, EvalReport]],
    protections: list[Protection],
    tolerances: dict[str, float],
    prior: dict[str, float],
    target: str,
    memorization_suite: str | None,
    new_capability: bool,
    invalid: list[str],
    max_seeds: int = 3,
) -> dict[str, Any]:
    """Verdict d'une recette sur k graines (première graine = artefact candidat à la promotion)."""
    k = len(seeds)
    base = {"k": k, "target": target, "max_seeds": max_seeds}
    if invalid:
        return {
            **base,
            "decision": INVALID,
            "status": INVALID_EXPERIMENT,
            "reasons": invalid,
            "rows": [],
            "need_more_seeds": False,
        }
    rows = [
        suite_row(
            p.group,
            p.reports[suite],
            [s[suite] for s in seeds],
            sigma_prior=prior.get(suite),
            tolerance=tolerances[p.reports[suite].metric],
        )
        for p in protections
        for suite in p.suites
    ]
    by_suite = {r["suite"]: r for r in rows}
    reasons: list[str] = []
    target_row = by_suite.get(target)
    if target_row is None:
        return {
            **base,
            "decision": INVALID,
            "status": INVALID_EXPERIMENT,
            "rows": rows,
            "reasons": [f"cible « {target} » sans ancre protégée : invérifiable"],
            "need_more_seeds": False,
        }
    regressions = [r["suite"] for r in rows if r["status"] == REGRESSION]
    memorized = (
        memorization_suite is not None
        and by_suite.get(memorization_suite, {}).get("status") == IMPROVEMENT
        and target_row["status"] != IMPROVEMENT
    )
    inconclusive = [r["suite"] for r in rows if r["status"] == VARIANCE_INCONCLUSIVE]
    no_gain = target_row["status"] != IMPROVEMENT and target_row["delta"] - target_row["uncertainty"] >= 0
    acquired = not new_capability or (
        target_row["reference"] + target_row["delta"] + target_row["uncertainty"] < ACQUISITION_MAX_ERROR
    )
    beyond = [r["suite"] for r in rows if r["artifact_beyond_margin"]]
    status = target_row["status"]
    if regressions:
        decision = REJECT
        reasons.append(f"régression reproduite au-delà de la marge : {regressions}")
    elif memorized:
        decision, status = REJECT, MEMORIZATION
        reasons.append(f"gain sur « {memorization_suite} » sans gain held-out : mémorisation")
    elif no_gain:
        decision = REJECT
        reasons.append(f"aucun gain démontré sur « {target} »")
    elif inconclusive:
        decision = INCONCLUSIVE
        reasons.append(f"variance : conclusion impossible sur {inconclusive}")
    elif target_row["status"] != IMPROVEMENT:
        decision = INCONCLUSIVE
        reasons.append(f"gain sur « {target} » non démontré à cette variance")
    elif not acquired:
        decision = REJECT
        reasons.append(
            f"capacité non acquise : erreur held-out {target_row['reference'] + target_row['delta']:.3f} "
            f"± {target_row['uncertainty']:.3f}, seuil {ACQUISITION_MAX_ERROR}"
        )
    elif beyond:
        decision = REJECT
        reasons.append(f"l'artefact promu dépasse lui-même la marge sur {beyond}")
    else:
        decision = ACCEPT
        reasons.append(f"gain démontré sur « {target} » sur {k} graines, aucune régression hors marge")
    return {
        **base,
        "decision": decision,
        "status": status,  # statut de la CIBLE (MEMORIZATION si mémorisée)
        "reasons": reasons,
        "rows": rows,
        "need_more_seeds": decision == INCONCLUSIVE and k < max_seeds,
    }


def reports_group(seeds: list[dict[str, EvalReport]]) -> dict[str, list[float]]:
    """Valeurs par suite des graines d'une même recette (entrée de l'historique de réplication)."""
    suites = set.intersection(*(set(s) for s in seeds)) if seeds else set()
    return {suite: [s[suite].value for s in seeds] for suite in sorted(suites)}
