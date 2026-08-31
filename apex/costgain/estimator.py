"""`CostGainEstimator` — coût réellement mesuré, gain estimé sur preuve réelle.

Compose `apex.stattest.StatTest` (Phase 16, réel) pour le gain — jamais une
seconde logique statistique — et lit directement les champs déjà réels
d'`apex.evalrunner.model.EvalRun` (Phase 5) pour le coût. Aucune dépendance
DB propre : prend une séquence d'`EvalRun` déjà rassemblée par l'appelant
(même découplage qu'`apex.weakness.detector.WeaknessDetector`).

**Ce qui est honnêtement `UNKNOWN` aujourd'hui, et pourquoi** :
- `memory_peak_mb` : réellement mesuré (Phase 7, cgroups v1
  `memory.max_usage_in_bytes`) et désormais copié par
  `EvalRunnerService._build_eval_run` dans `EvalRun.facts["memory_peak_mb"]`
  — le sac JSON libre déjà prévu par le contrat Phase 5, aucune colonne
  ajoutée. `UNKNOWN` seulement quand la clé est absente d'un run donné
  (mesure indisponible pour ce run précis), jamais inventé.
- `cpu_time_s` : reste `UNKNOWN` — `cpu.stat` (cgroups v1) n'expose que du
  throttling, pas le temps CPU réellement consommé (limite réelle du
  backend, voir `apex.sandbox.linux_namespace`), jamais copié faute de
  valeur réelle à copier.
- `token_count` : exigerait un raccordement `ModelProvider` (Phase 6) ->
  `EvalRunner` qui n'existe pas (décision de périmètre explicite depuis la
  Phase 5 : `task.command` reste toujours déterministe, jamais généré).

Aucun de ces trois n'est un oubli — chacun est une limite réelle et
documentée, jamais transformée en zéro.
"""

from __future__ import annotations

from collections.abc import Sequence

from apex.costgain.model import CostGainEstimate, ResourceCost
from apex.evalrunner.model import EvalRun
from apex.obs.status import DataStatus, Measurement
from apex.stattest.comparator import StatTest
from apex.stattest.model import StatTestResult, StatTestVerdict


class CostGainEstimator:
    def __init__(self, *, stat_test: StatTest | None = None) -> None:
        self._stat_test = stat_test or StatTest()

    def estimate_cost(self, eval_runs: Sequence[EvalRun]) -> ResourceCost:
        """`elapsed_s` est la somme réelle SEULEMENT si AUCUN run de
        `eval_runs` n'a un `elapsed_s` manquant — additionner les seules
        valeurs connues et ignorer les absentes sous-compterait silencieusement
        le coût réel, exactement l'erreur que ce module doit éviter.
        `memory_peak_mb` : le MAXIMUM (pas la somme — un pic n'est pas
        additif entre runs séparés) des pics réels, même garde de
        complétude que `elapsed_s`."""
        elapsed_values = [run.elapsed_s for run in eval_runs]
        if eval_runs and all(v is not None for v in elapsed_values):
            elapsed = Measurement.real(sum(elapsed_values))
        else:
            elapsed = Measurement.unknown()

        memory_values = [run.facts.get("memory_peak_mb") for run in eval_runs]
        if eval_runs and all(v is not None for v in memory_values):
            memory_peak = Measurement.real(max(memory_values))
        else:
            memory_peak = Measurement.unknown()

        return ResourceCost(
            elapsed_s=elapsed,
            cpu_time_s=Measurement.unknown(),
            memory_peak_mb=memory_peak,
            token_count=Measurement.unknown(),
            experiment_count=len(eval_runs),
        )

    def estimate_gain(self, before: Sequence[float], after: Sequence[float]) -> StatTestResult:
        """`before`/`after` : issues appariées (0.0/1.0, ex. échec/succès)
        sur les MÊMES tâches avant/après une intervention (curriculum,
        stratégie, mutation de Genome, ...) — délègue entièrement à
        `StatTest.compare_paired`, aucune logique statistique dupliquée."""
        return self._stat_test.compare_paired(before, after)

    def estimate(
        self, *, specialty: str, eval_runs: Sequence[EvalRun], before: Sequence[float], after: Sequence[float],
    ) -> CostGainEstimate:
        cost = self.estimate_cost(eval_runs)
        gain = self.estimate_gain(before, after)
        ratio = self._ratio(gain, cost)
        reason = self._reason(gain, cost, ratio)
        return CostGainEstimate(specialty=specialty, cost=cost, gain=gain, ratio=ratio, reason=reason)

    def _ratio(self, gain: StatTestResult, cost: ResourceCost) -> Measurement:
        """`Measurement.real` UNIQUEMENT si le gain est `SIGNIFICANT`
        (distinguable du bruit) ET que le coût en temps est réellement
        mesuré et strictement positif — sinon `UNKNOWN`, jamais un ratio
        construit sur du bruit statistique ou une division par une valeur
        absente."""
        if gain.verdict is not StatTestVerdict.SIGNIFICANT:
            return Measurement.unknown()
        if cost.elapsed_s.status is not DataStatus.REAL:
            return Measurement.unknown()
        if cost.elapsed_s.value <= 0:
            return Measurement.unknown()
        return Measurement.real(gain.observed_diff / cost.elapsed_s.value)

    def _reason(self, gain: StatTestResult, cost: ResourceCost, ratio: Measurement) -> str:
        if ratio.status is DataStatus.REAL:
            return f"gain significatif ({gain.reason}) rapporté au coût temps mesuré ({cost.elapsed_s.value:.3f}s)"
        if gain.verdict is StatTestVerdict.INSUFFICIENT_DATA:
            return "gain UNKNOWN — données insuffisantes pour une décision statistique (voir StatTest)"
        if gain.verdict is StatTestVerdict.NOT_SIGNIFICANT:
            return "ratio UNKNOWN — gain non distinguable du bruit (StatTestVerdict.NOT_SIGNIFICANT)"
        return "ratio UNKNOWN — coût temps non mesuré (voir ResourceCost.elapsed_s)"
