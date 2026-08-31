"""`WeaknessDetector` — classification pure d'un ensemble d'`EvalRun`.

Aucune dépendance DB : prend une séquence d'`apex.evalrunner.model.EvalRun`
déjà rassemblée par l'appelant (`apex.weakness.service.WeaknessDetectorService`
pour l'usage réel, ou directement injectée en test) — même séparation
logique/DB qu'`apex.oracles.exit_code.ExitCodeOracle`. Ce découplage
délibéré permet à un futur appelant (`CurriculumStrategy`, `TaskGenerator`)
de fournir n'importe quel regroupement pertinent (par spécialité, par clone,
par spécialité+clone) sans que ce module n'impose une seule granularité.

Seuils heuristiques, documentés, jamais présentés comme un `StatTest` réel
(`PLANNED`, phase 16) : ce module ne fait qu'un comptage déterministe et
reproductible, pas une preuve statistique rigoureuse.
"""

from __future__ import annotations

from collections.abc import Sequence

from apex.evalrunner.model import EvalRun
from apex.oracles.model import OracleVerdict
from apex.weakness.model import WeaknessCategory, WeaknessReport

#: Sous ce seuil de résultats PASS/FAIL exploitables, aucune conclusion
#: statistiquement crédible n'est tentée — quel que soit le taux d'échec
#: observé. Valeur arbitraire mais documentée, à ajuster seulement sur
#: preuve, jamais en fonction d'un résultat souhaité.
MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS = 5

#: Taux d'échec (sur l'échantillon exploitable) au-dessus duquel une
#: faiblesse est jugée « statistiquement crédible » par cette heuristique.
CREDIBLE_FAILURE_RATE_THRESHOLD = 0.5


class WeaknessDetector:
    def detect(self, eval_runs: Sequence[EvalRun]) -> WeaknessReport:
        pass_count = sum(1 for r in eval_runs if r.oracle_verdict is OracleVerdict.PASS)
        fail_count = sum(1 for r in eval_runs if r.oracle_verdict is OracleVerdict.FAIL)
        error_count = sum(1 for r in eval_runs if r.oracle_verdict is OracleVerdict.ERROR)
        timeout_count = sum(1 for r in eval_runs if r.oracle_verdict is OracleVerdict.TIMEOUT)
        unknown_count = sum(1 for r in eval_runs if r.oracle_verdict is OracleVerdict.UNKNOWN)
        evidence = tuple(r.id for r in eval_runs)
        meaningful = pass_count + fail_count

        if meaningful == 0:
            return WeaknessReport(
                category=WeaknessCategory.INSUFFICIENT_DATA,
                pass_count=pass_count, fail_count=fail_count, error_count=error_count,
                timeout_count=timeout_count, unknown_count=unknown_count,
                failure_rate=None, evidence_run_ids=evidence,
                reason=(
                    f"aucun résultat PASS/FAIL exploitable (n={len(eval_runs)}, dont "
                    f"{error_count} ERROR, {timeout_count} TIMEOUT, {unknown_count} UNKNOWN "
                    "— exclus du calcul de faiblesse, jamais confondus avec un échec de tâche)"
                ),
            )

        failure_rate = fail_count / meaningful

        if fail_count == 0:
            category = WeaknessCategory.NO_WEAKNESS
            reason = f"{meaningful} résultat(s) exploitable(s), aucun échec"
        elif meaningful < MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS:
            if fail_count == 1:
                category = WeaknessCategory.ISOLATED_FAILURE
                reason = (
                    f"1 échec sur {meaningful} résultat(s) exploitable(s) — échantillon "
                    "trop petit pour conclure à un motif répété"
                )
            else:
                category = WeaknessCategory.REPEATED_WEAKNESS
                reason = (
                    f"{fail_count} échecs sur {meaningful} résultats exploitables — motif "
                    f"répété mais échantillon sous le seuil ({MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS}) "
                    "requis avant toute conclusion statistiquement crédible"
                )
        elif failure_rate >= CREDIBLE_FAILURE_RATE_THRESHOLD:
            category = WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS
            reason = (
                f"taux d'échec {failure_rate:.0%} sur {meaningful} résultats exploitables "
                f"(seuil heuristique {CREDIBLE_FAILURE_RATE_THRESHOLD:.0%}) — HEURISTIQUE "
                "documentée, PAS un StatTest réel (PLANNED, phase 16)"
            )
        else:
            category = WeaknessCategory.REPEATED_WEAKNESS
            reason = (
                f"{fail_count} échecs sur {meaningful} résultats exploitables "
                f"(taux {failure_rate:.0%}, sous le seuil heuristique "
                f"{CREDIBLE_FAILURE_RATE_THRESHOLD:.0%}) — motif répété, pas encore crédible"
            )

        return WeaknessReport(
            category=category, pass_count=pass_count, fail_count=fail_count,
            error_count=error_count, timeout_count=timeout_count, unknown_count=unknown_count,
            failure_rate=failure_rate, evidence_run_ids=evidence, reason=reason,
        )
