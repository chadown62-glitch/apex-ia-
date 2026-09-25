"""`FailureCategory`/`classify_failure` — distingue POURQUOI une tentative de
solution générée (`EvalRunnerService.run_generated_solution`, N+19) n'a pas
réussi, à partir des PREUVES déjà réellement produites sur l'`EvalRun`
(`oracle_verdict`, `sandbox_status`, `model_id`, `oracle_detail`, et
`facts["candidate_execution"]` — voir `EvalRunnerService._build_eval_run`) —
jamais une déduction du type « fitness mauvais = stratégie mauvaise » (§ de
la mission N+20, boucle d'apprentissage).

Chaque branche cite la preuve exacte qui la distingue des autres, pour
qu'aucune catégorie ne soit un choix par défaut déguisé :

- `generation_failure` : le `ModelProvider` lui-même a échoué avant toute
  tentative de sandbox — preuve : `model_id is None` (aucun modèle n'a
  réellement répondu, voir la docstring d'`EvalRun`).
- `invalid_candidate` : un modèle a répondu et le code a été exécuté, mais
  le script complet n'a jamais produit sa sortie JSON de résumé (preuve :
  `candidate_execution.stdout` absent ou non parseable en JSON avec une clé
  `"failures"`) — un candidat syntaxiquement/structurellement invalide
  (`SyntaxError`, fonction non définie et non captée par le harness, etc.),
  distinct d'une réponse simplement incorrecte.
- `execution_failure` : le sandbox a réellement démarré et un
  `ExecutionStatus.FAILED`/`ExecutionStatus.PERMISSION_DENIED`/
  `ExecutionStatus.NETWORK_DENIED` documente une preuve d'exécution
  anormale non couverte par les catégories plus spécifiques ci-dessous.
- `timeout` : `oracle_verdict is TIMEOUT` — le délai imparti a réellement
  été dépassé (preuve noyau, voir `apex.sandbox.linux_namespace`).
- `resource_failure` : `sandbox_status` documente une limite RESSOURCE
  réellement dépassée (`OOM_KILLED`/`RESOURCE_LIMIT_EXCEEDED`/
  `PROCESS_LIMIT_EXCEEDED`/`DISK_LIMIT_EXCEEDED`).
- `policy_security_rejection` : `sandbox_status` est `NETWORK_DENIED` ou
  `PERMISSION_DENIED` ET la génération a réellement abouti (`model_id` posé)
  ET ce n'est PAS le chemin « sandbox indisponible avant même de démarrer »
  (voir `infrastructure_failure`) — preuve que c'est le CANDIDAT qui a
  réellement tenté une action refusée par le sandbox, pas une panne
  d'infrastructure.
- `infrastructure_failure` : `oracle_detail` porte le préfixe littéral
  produit par `EvalRunnerService.run_generated_solution` quand
  `IsolationUnavailable` est levée (« sandbox indisponible : ») — preuve
  que le sandbox n'a jamais pu démarrer, indépendamment du candidat — ou
  `oracle_verdict is UNKNOWN` (exécution annulée avant complétion, jamais
  imputable au candidat).
- `test_oracle_failure` : réservée à un futur `OracleProvider` qui pourrait
  lui-même échouer indépendamment du candidat (ex. jugement impossible) —
  `ExitCodeOracle` (seul oracle réel à ce jour) ne produit jamais cette
  situation ; catégorie documentée mais actuellement inatteignable par ce
  classifieur (voir tests).
- `incorrect_solution` : le script s'est réellement exécuté jusqu'au bout
  (JSON de résumé du harness valide) et au moins un cas caché a échoué SANS
  qu'aucune entrée ne porte de clé `"error"` — la preuve la plus forte
  qu'il s'agit d'une solution fonctionnellement incorrecte, pas d'un
  candidat invalide.
- `success` : `oracle_verdict is PASS`.
"""

from __future__ import annotations

import json
from enum import Enum

from apex.evalrunner.model import EvalRun
from apex.oracles.model import OracleVerdict
from apex.oracles.result_contract import CONTRACT_VIOLATION, RESULT_LINE_PREFIX, read_published_results
from apex.sandbox.model import ExecutionStatus

#: Préfixe littéral posé par `EvalRunnerService.run_generated_solution` sur
#: `oracle_detail` quand `IsolationUnavailable` empêche le sandbox de
#: démarrer — voir ce module, branche `except IsolationUnavailable`. Utilisé
#: ici comme PREUVE textuelle réelle, jamais reconstruite/devinée.
_SANDBOX_UNAVAILABLE_PREFIX = "sandbox indisponible :"

_RESOURCE_STATUSES = frozenset(
    {
        ExecutionStatus.OOM_KILLED,
        ExecutionStatus.RESOURCE_LIMIT_EXCEEDED,
        ExecutionStatus.PROCESS_LIMIT_EXCEEDED,
        ExecutionStatus.DISK_LIMIT_EXCEEDED,
    }
)
_POLICY_STATUSES = frozenset({ExecutionStatus.NETWORK_DENIED, ExecutionStatus.PERMISSION_DENIED})


class FailureCategory(str, Enum):
    GENERATION_FAILURE = "generation_failure"
    INVALID_CANDIDATE = "invalid_candidate"
    EXECUTION_FAILURE = "execution_failure"
    TIMEOUT = "timeout"
    RESOURCE_FAILURE = "resource_failure"
    TEST_ORACLE_FAILURE = "test_oracle_failure"
    INCORRECT_SOLUTION = "incorrect_solution"
    POLICY_SECURITY_REJECTION = "policy_security_rejection"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    SUCCESS = "success"


def _harness_summary(eval_run: EvalRun) -> dict | None:
    """Tente de parser `facts["candidate_execution"]["stdout"]` comme le
    résumé JSON produit par un harness (`apex.taskgen.solution_tasks`, voir
    `_HARNESS_TEMPLATE`) — `None` si absent ou non parseable comme un objet
    portant une clé `"failures"` (jamais une exception propagée : un stdout
    malformé EST la preuve d'un candidat invalide, pas une erreur de ce
    classifieur)."""
    candidate_execution = eval_run.facts.get("candidate_execution")
    if not isinstance(candidate_execution, dict):
        return None
    stdout = candidate_execution.get("stdout")
    if not isinstance(stdout, str) or not stdout.strip():
        return None
    try:
        parsed = json.loads(stdout.strip().splitlines()[-1] if "\n" in stdout.strip() else stdout)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or "failures" not in parsed:
        return None
    return parsed


def _contract_category(eval_run: EvalRun) -> FailureCategory | None:
    """Harness au contrat de résultat (`apex.oracles.result_contract`) :
    `None` si le stdout ne porte aucune ligne de contrat (ancien format,
    lu par `_harness_summary`). Contrat violé ou exception levée par le
    candidat -> INVALID_CANDIDATE ; résultats publiés mais faux ->
    INCORRECT_SOLUTION."""
    if (eval_run.oracle_detail or "").startswith(CONTRACT_VIOLATION):
        return FailureCategory.INVALID_CANDIDATE
    candidate_execution = eval_run.facts.get("candidate_execution")
    stdout = candidate_execution.get("stdout") if isinstance(candidate_execution, dict) else None
    if not isinstance(stdout, str) or RESULT_LINE_PREFIX not in stdout:
        return None
    published = read_published_results(stdout)
    if published.results is None:
        return FailureCategory.INVALID_CANDIDATE
    if any(isinstance(r, dict) and "error" in r for r in published.results):
        return FailureCategory.INVALID_CANDIDATE
    return FailureCategory.INCORRECT_SOLUTION


def classify_failure(eval_run: EvalRun) -> FailureCategory:
    """Classification PURE — aucun accès DB, aucune inférence au-delà des
    champs déjà réellement persistés sur `eval_run`. Toujours une valeur
    fermée de `FailureCategory` (jamais `None`) : même un cas non prévu
    retombe sur `infrastructure_failure`, jamais deviné comme une réponse
    incorrecte du candidat (§ mission N+20 : ne jamais confondre les
    causes)."""
    if eval_run.oracle_verdict is OracleVerdict.PASS:
        return FailureCategory.SUCCESS
    if eval_run.oracle_verdict is OracleVerdict.TIMEOUT:
        return FailureCategory.TIMEOUT
    if eval_run.oracle_verdict is OracleVerdict.UNKNOWN:
        return FailureCategory.INFRASTRUCTURE_FAILURE

    if eval_run.model_id is None and eval_run.sandbox_status is None:
        # Aucune génération n'a réellement abouti (voir la branche
        # `except (ModelProviderError, TimeoutExceeded)` de
        # `run_generated_solution`) — jamais atteint le sandbox.
        return FailureCategory.GENERATION_FAILURE

    detail = eval_run.oracle_detail or ""
    if detail.startswith(_SANDBOX_UNAVAILABLE_PREFIX):
        return FailureCategory.INFRASTRUCTURE_FAILURE

    if eval_run.sandbox_status in _RESOURCE_STATUSES:
        return FailureCategory.RESOURCE_FAILURE
    if eval_run.sandbox_status in _POLICY_STATUSES:
        return FailureCategory.POLICY_SECURITY_REJECTION

    if eval_run.oracle_verdict is OracleVerdict.FAIL:
        contract = _contract_category(eval_run)
        if contract is not None:
            return contract
        summary = _harness_summary(eval_run)
        if summary is None:
            # Le script n'a jamais produit son résumé JSON — preuve d'un
            # candidat structurellement invalide (SyntaxError, sortie
            # anticipée, etc.), distinct d'une réponse simplement fausse.
            return FailureCategory.INVALID_CANDIDATE
        failures = summary.get("failures")
        if isinstance(failures, list) and any(
            isinstance(entry, dict) and "error" in entry for entry in failures
        ):
            # Le harness a réellement capté une exception LEVÉE PAR le
            # candidat sur au moins un cas — voir `_HARNESS_TEMPLATE`
            # (`try/except Exception`) : preuve directe d'un candidat
            # invalide (fonction absente, type erroné, ...), pas d'une
            # simple différence de valeur retournée.
            return FailureCategory.INVALID_CANDIDATE
        return FailureCategory.INCORRECT_SOLUTION

    if eval_run.sandbox_status is ExecutionStatus.FAILED:
        return FailureCategory.EXECUTION_FAILURE

    # `ExecutionStatus`/`OracleVerdict` sont des vocabulaires fermés — cette
    # branche ne serait atteinte que par une combinaison non prévue par les
    # branches ci-dessus (ex. nouvelle valeur ajoutée sans mise à jour de ce
    # classifieur). Jamais une catégorie de candidat devinée dans ce cas.
    return FailureCategory.INFRASTRUCTURE_FAILURE
