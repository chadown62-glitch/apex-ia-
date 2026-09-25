"""Debugger adversarial — transforme les réfutations du juge en `Finding`.

OBSERVED (la sonde a réfuté) -> HYPOTHESIS (`ProbeOutcome.hypothesis`) ->
TEST (une ré-exécution INDÉPENDANTE de la sonde, jamais une relecture du
premier résultat) -> CONFIRMED (le défaut se reproduit) / REJECTED (il ne se
reproduit pas : bruit, non-déterminisme, faux positif du juge) / PROBABLE
(la reproduction n'a pas pu conclure — jamais promu en CONFIRMED faute de
preuve).

Un résultat de sonde INCONCLUSIVE ou en panne ne produit JAMAIS de finding
« confirmé » : il reste un finding PROBABLE dont l'hypothèse est
explicitement « le juge n'a pas pu conclure », pas une cause inventée.

Le correctif est une PROPOSITION (`RefutationProbe.remediation`) — ce module
ne modifie jamais de code, n'applique jamais rien."""

from __future__ import annotations

from apex.improvement.judge import JudgedProbe
from apex.improvement.model import Finding, HypothesisStatus
from apex.metalearner.judge_model import JudgeDecision


def _reproduce(judged: JudgedProbe) -> tuple[HypothesisStatus, str]:
    try:
        again = judged.probe.run()
    except Exception as exc:  # une reproduction impossible reste PROBABLE
        return HypothesisStatus.PROBABLE, f"reproduction impossible : {exc.__class__.__name__}: {exc}"
    if again.refuted is True:
        return HypothesisStatus.CONFIRMED, f"reproduit par ré-exécution indépendante : {again.observed}"
    if again.refuted is False:
        return HypothesisStatus.REJECTED, f"non reproduit par ré-exécution indépendante : {again.observed}"
    return HypothesisStatus.PROBABLE, f"reproduction non concluante : {again.observed}"


def investigate(judged_probes: tuple[JudgedProbe, ...], *, cycle_id: str) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for index, judged in enumerate(judged_probes):
        decision = judged.result.decision
        if decision is JudgeDecision.PASS:
            continue
        probe = judged.probe
        finding_id = f"{cycle_id}:{probe.probe_id}:{index}"
        if decision is JudgeDecision.INCONCLUSIVE or judged.outcome is None:
            observed = judged.error or (
                judged.outcome.observed if judged.outcome else judged.result.rationale
            )
            findings.append(
                Finding(
                    finding_id=finding_id,
                    probe_id=probe.probe_id,
                    severity="inconclusive",
                    observed=observed,
                    hypothesis="le juge n'a pas pu conclure — aucune cause n'est affirmée",
                    status=HypothesisStatus.PROBABLE,
                    reproduction="non tentée (aucun défaut démontré)",
                    proposed_fix="rendre la précondition de la sonde disponible puis ré-évaluer",
                    regression_test=probe.regression_test,
                )
            )
            continue
        status, reproduction = _reproduce(judged)
        severity = judged.result.findings[0].severity.value if judged.result.findings else "high"
        findings.append(
            Finding(
                finding_id=finding_id,
                probe_id=probe.probe_id,
                severity=severity,
                observed=judged.outcome.observed,
                hypothesis=judged.outcome.hypothesis or "cause non formulée par la sonde",
                status=status,
                reproduction=reproduction,
                proposed_fix=probe.remediation,
                regression_test=probe.regression_test,
            )
        )
    return tuple(findings)
