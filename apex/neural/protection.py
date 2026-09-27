"""Protection des capacités ACQUISES et mesure formelle de l'OUBLI.

Un groupe protégé (le texte, puis chaque capacité) est ancré sur une entrée ATTESTÉE du registre :
celle qui a démontré le meilleur niveau sur la suite-clé du groupe, avec les mêmes benchmarks
(empreintes identiques). L'ancre conserve ses mesures brutes par élément, la version du modèle,
les empreintes et l'historique des versions promues.

Oubli d'une suite (métriques « plus bas = mieux ») : `après - avant`, positif = dégradation.
Statut, sur le bootstrap apparié par élément entre l'ancre et le candidat :
- FORGOTTEN : dégradation significative, ou dégradation agrégée au-delà de la tolérance ;
- IMPROVED : amélioration significative ;
- NOISE : différence non distinguable du bruit, dans la tolérance.

Comparer à l'ANCRE (et non au seul champion courant) empêche une dérive cumulée : une suite de
promotions dont chacune perd un peu, dans la tolérance, reste bornée par le niveau démontré."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apex.neural.evaluation import EvalReport
from apex.stattest import StatTest, StatTestVerdict

FORGOTTEN = "forgotten"
IMPROVED = "improved"
NOISE = "noise"


@dataclass(frozen=True)
class Protection:
    group: str  # "text" ou nom de la capacité
    suites: tuple[str, ...]  # la première est la suite-clé qui désigne l'ancre
    anchor_index: int
    anchor_name: str
    anchor_artifact: str | None
    reports: dict[str, EvalReport]  # mesures brutes de l'ancre, par élément
    history: tuple[dict[str, Any], ...]  # {index, name, values} de chaque version comparable

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "suites": list(self.suites),
            "anchor_index": self.anchor_index,
            "anchor_name": self.anchor_name,
            "anchor_artifact": self.anchor_artifact,
            "anchor_values": {s: round(self.reports[s].value, 5) for s in self.suites},
            "benchmarks": {s: self.reports[s].benchmark_fingerprint for s in self.suites},
            "history": list(self.history),
        }


def protections(entries: list[dict[str, Any]], groups: dict[str, tuple[str, ...]]) -> list[Protection]:
    """Ancres des groupes, depuis les entrées PROMUES du registre. Seules comptent les versions
    mesurées sur les benchmarks de la version en service (empreintes identiques) : une ancre
    mesurée sur un autre benchmark ne serait pas comparable."""
    promoted = [e for e in entries if e["promoted"]]
    if not promoted:
        return []
    current = promoted[-1]["reports"]
    out = []
    for group, suites in groups.items():
        if not all(s in current for s in suites):
            continue
        comparable = [
            e
            for e in promoted
            if all(
                s in e["reports"]
                and e["reports"][s]["benchmark_fingerprint"] == current[s]["benchmark_fingerprint"]
                for s in suites
            )
        ]
        key = suites[0]
        # Meilleur niveau démontré sur la suite-clé ; à égalité, la version la plus récente.
        anchor = min(reversed(comparable), key=lambda e: e["reports"][key]["value"])
        out.append(
            Protection(
                group=group,
                suites=suites,
                anchor_index=int(anchor["index"]),
                anchor_name=str(anchor["name"]),
                anchor_artifact=anchor.get("artifact"),
                reports={s: EvalReport.from_dict(anchor["reports"][s]) for s in suites},
                history=tuple(
                    {
                        "index": e["index"],
                        "name": e["name"],
                        "values": {s: round(e["reports"][s]["value"], 5) for s in suites},
                    }
                    for e in comparable
                ),
            )
        )
    return out


def forgetting(
    protection: Protection,
    candidate: dict[str, EvalReport],
    tolerances: dict[str, float],
    *,
    stat: StatTest | None = None,
) -> list[dict[str, Any]]:
    """Une ligne par suite du groupe ; mesures brutes conservées (valeurs, IC, n)."""
    stat = stat or StatTest(n_bootstrap=2000, confidence_level=0.95, seed=0)
    rows = []
    for suite in protection.suites:
        before, after = protection.reports[suite], candidate[suite]
        if before.benchmark_fingerprint != after.benchmark_fingerprint:
            raise ValueError(f"« {suite} » : benchmark de l'ancre différent de celui du candidat")
        base = {s.doc_id: s.value for s in before.scores}
        cand = {s.doc_id: s.value for s in after.scores}
        if set(base) != set(cand):
            raise ValueError(f"« {suite} » : éléments évalués différents entre l'ancre et le candidat")
        ids = sorted(base)
        # Gain = avant - après (positif si le candidat est meilleur), comme le Judge.
        gain = stat.compare_paired([cand[i] for i in ids], [base[i] for i in ids])
        delta = after.value - before.value
        tolerance = tolerances[before.metric]
        significant = gain.verdict is StatTestVerdict.SIGNIFICANT
        if (significant and (gain.ci_upper or 0.0) < 0.0) or delta > tolerance:
            status = FORGOTTEN
        elif significant and (gain.ci_lower or 0.0) > 0.0:
            status = IMPROVED
        else:
            status = NOISE
        rows.append(
            {
                "group": protection.group,
                "suite": suite,
                "metric": before.metric,
                "anchor_index": protection.anchor_index,
                "before": round(before.value, 5),
                "after": round(after.value, 5),
                "forgetting": round(delta, 5),
                "gain_ci": [gain.ci_lower, gain.ci_upper],
                "n": gain.sample_size,
                "tolerance": tolerance,
                "status": status,
            }
        )
    return rows
