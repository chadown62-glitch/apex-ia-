#!/usr/bin/env bash
# APEX — point d'entrée UNIQUE de validation (N+26).
#
# Compose des briques déjà réelles, n'en réimplémente AUCUNE :
#   - scripts/apex-launch.sh (sourcé, jamais dupliqué) pour la résolution
#     .env/Python/Docker Compose (resolve_project_root/load_env_file/
#     detect_python/compose_up, N+23/N+24 inclus) ;
#   - apex.doctor.launcher.run_preflight_and_repair (Python, réel depuis
#     N+19, AXE B) pour Diagnostic -> Auto-repair (liste blanche FERMÉE,
#     `apex/doctor/repair.py` — jamais rien d'autre qu'un `mkdir -p`, un
#     `docker compose up -d <service>` ou un `service docker start`) ->
#     Verify -> Gate ;
#   - `apex doctor --full` (N+26) pour la visibilité sandbox (namespaces/
#     cgroups/privilège/réseau/sonde de sécurité Docker/backend réellement
#     sélectionnable — voir apex/doctor/checks/sandbox.py) ;
#   - un sous-ensemble curaté de `pytest` pour la validation "quick"
#     (rapide, aucune dépendance Docker/Postgres réelle requise), ou la
#     suite complète avec `--full`.
#
# AUCUNE logique de réparation ou de vérification n'est dupliquée ici —
# orchestration UNIQUEMENT, exactement le principe qu'apex-launch.sh
# applique déjà à lui-même.
#
# Ce script NE DÉMARRE JAMAIS le serveur APEX : il s'arrête toujours après
# le diagnostic, quel que soit le résultat. Lancer réellement APEX reste
# une action explicite et séparée (`./scripts/apex-launch.sh serve` ou
# `apex start`).
#
# Usage :
#   ./scripts/apex-preflight.sh [--dry-run] [--full] [--skip-tests]
#
#   --dry-run     n'applique AUCUN safe-fix — diagnostique et affiche
#                 uniquement le plan de réparation (par défaut : les
#                 safe-fixes, liste blanche fermée et non destructive,
#                 SONT appliqués — c'est le sens même d'un « une seule
#                 commande demain »).
#   --full        suite de tests complète au lieu du sous-ensemble rapide,
#                 ET active la sonde sandbox coûteuse (apex doctor --full).
#   --skip-tests  n'exécute aucun test (diagnostic + safe-fixes seuls).
#
# Codes de sortie :
#   0  - READY ou READY AVEC AVERTISSEMENTS (jamais bloquant)
#   1  - BLOCKED (au moins un problème bloquant identifié — voir le
#        rapport pour ce qui empêche READY exactement)
#   2  - FAILED (une étape de CE script a échoué de façon inattendue —
#        anomalie de l'orchestration elle-même, distincte d'un
#        environnement simplement incomplet)
#
# Interdits, comme pour apex-launch.sh — jamais dans ce script :
#   - `docker system prune` / `docker volume prune` / suppression de
#     volume / suppression arbitraire de conteneur externe ;
#   - kill arbitraire d'un processus (PostgreSQL compris) ;
#   - réinstallation de Docker/CUDA, modification de la configuration
#     GPU, suppression de fichier utilisateur.
# Les SEULES actions à effet de bord possibles sont celles de la liste
# blanche fermée `apex.doctor.repair._SAFE_REPAIR_RULES` — jamais une
# commande construite ici.

set -uo pipefail

_script_dir() {
    local source="${BASH_SOURCE[0]}"
    if command -v readlink >/dev/null 2>&1; then
        source="$(readlink -f -- "${source}" 2>/dev/null || printf '%s' "${source}")"
    fi
    cd -- "$(dirname -- "${source}")" >/dev/null 2>&1 && pwd -P
}

# shellcheck source=apex-launch.sh
source "$(_script_dir)/apex-launch.sh"

readonly PREFLIGHT_READY=0
readonly PREFLIGHT_BLOCKED=1
readonly PREFLIGHT_FAILED=2

preflight_log() { printf '[apex-preflight] %s\n' "$*" >&2; }
preflight_section() { printf '\n=== %s ===\n' "$*" >&2; }

main_preflight() {
    local dry_run=0
    local full=0
    local skip_tests=0
    for arg in "$@"; do
        case "${arg}" in
            --dry-run) dry_run=1 ;;
            --full) full=1 ;;
            --skip-tests) skip_tests=1 ;;
            *)
                log_err "argument inconnu : ${arg}"
                log_err "usage : $0 [--dry-run] [--full] [--skip-tests]"
                exit "${PREFLIGHT_FAILED}"
                ;;
        esac
    done

    local blocked=0
    local warnings=0

    preflight_section "1/5 — ENVIRONNEMENT"
    local project_root
    project_root="$(resolve_project_root)"
    preflight_log "racine du projet : ${project_root}"

    local env_file="${project_root}/.env"
    if ! load_env_file "${env_file}"; then
        log_warn ".env introuvable (${env_file}) — variables APEX_* non chargées."
        log_warn "remédiation : cp .env.example .env, puis éditer les valeurs."
        warnings=1
    fi

    local python_bin=""
    if ! python_bin="$(detect_python "${project_root}")"; then
        log_err "aucun interpréteur Python 3.12+ utilisable (ni .venv, ni python3 système)."
        log_err "remédiation : python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev,db,api]'"
        blocked=1
    else
        preflight_log "interpréteur retenu : ${python_bin}"
    fi

    if [[ "${blocked}" -eq 1 ]]; then
        preflight_log "environnement Python bloquant — étapes suivantes sautées."
        _print_verdict "${blocked}" "${warnings}"
        exit "${PREFLIGHT_BLOCKED}"
    fi

    preflight_section "2/5 — INFRASTRUCTURE (Docker)"
    if ! compose_up "${project_root}"; then
        log_err "infrastructure Docker indisponible — voir les messages ci-dessus."
        blocked=1
    fi

    preflight_section "3/5 — DIAGNOSTIC + SAFE-FIXES + GATE"
    # `run_preflight_and_repair` (Python, apex.doctor.launcher, réel depuis
    # N+19) EST la seule couche de ce dépôt autorisée à produire un effet
    # de bord depuis un diagnostic — liste blanche fermée
    # (apex.doctor.repair._SAFE_REPAIR_RULES). `dry_run` ici correspond
    # EXACTEMENT à son propre paramètre `dry_run` : jamais réinterprété.
    local doctor_status=0
    (
        cd -- "${project_root}" && "${python_bin}" - "${dry_run}" <<'PYEOF'
import sys

from apex.doctor.launcher import StartupStatus, render_text, run_preflight_and_repair

dry_run = sys.argv[1] == "1"
outcome = run_preflight_and_repair(dry_run=dry_run, history_path=None)
print(render_text(outcome, verbose=False))

if outcome.status is StartupStatus.READY_TO_SERVE:
    sys.exit(0)
if outcome.status is StartupStatus.DRY_RUN:
    sys.exit(3)  # ni prêt ni bloqué — juste jamais vérifié (dry-run)
sys.exit(1)  # BLOCKED_DOCTOR ou BLOCKED_GATE
PYEOF
    ) || doctor_status=$?

    case "${doctor_status}" in
        0) preflight_log "doctor + safe-fixes + gate : OK" ;;
        3) preflight_log "--dry-run : aucun safe-fix appliqué, gate non consultée (rien exécuté)." ; warnings=1 ;;
        *) log_err "doctor/gate bloquant après tentative de réparation — voir le rapport ci-dessus." ; blocked=1 ;;
    esac

    preflight_section "4/5 — SANDBOX (namespaces/cgroups/Docker/provider réellement sélectionnable)"
    local sandbox_status=0
    if [[ "${full}" -eq 1 ]]; then
        (cd -- "${project_root}" && "${python_bin}" -m apex.cli.main doctor --full) || sandbox_status=$?
        if [[ "${sandbox_status}" -ne 0 ]]; then
            log_err "apex doctor --full a signalé un FAIL — voir le rapport ci-dessus."
            blocked=1
        fi
    else
        preflight_log "sautée (sonde coûteuse — relancer avec --full pour l'inclure)."
    fi

    preflight_section "5/5 — TESTS"
    if [[ "${skip_tests}" -eq 1 ]]; then
        preflight_log "sautés (--skip-tests)."
    else
        local test_status=0
        if [[ "${full}" -eq 1 ]]; then
            preflight_log "suite complète (--full)..."
            (cd -- "${project_root}" && "${python_bin}" -m pytest --ignore=tests/test_runtime_packaging.py -q) \
                || test_status=$?
        else
            preflight_log "sous-ensemble rapide (config/sandbox/recovery/doctor/taskloop/learning)..."
            (
                cd -- "${project_root}" && "${python_bin}" -m pytest -q \
                    tests/test_config.py \
                    tests/test_conftest_data_root.py \
                    tests/test_sandbox_model.py \
                    tests/test_sandbox_detect.py \
                    tests/test_recovery_policy.py \
                    tests/test_taskloop_foundations.py \
                    tests/test_learning_recommendation.py \
                    tests/test_doctor.py \
                    tests/test_doctor_repair.py \
                    tests/test_doctor_recovery_additions.py
            ) || test_status=$?
        fi
        if [[ "${test_status}" -ne 0 ]]; then
            log_err "au moins un test a échoué (code ${test_status}) — voir la sortie ci-dessus."
            blocked=1
        else
            preflight_log "tests : OK"
        fi
    fi

    _print_verdict "${blocked}" "${warnings}"
    if [[ "${blocked}" -eq 1 ]]; then
        exit "${PREFLIGHT_BLOCKED}"
    fi
    exit "${PREFLIGHT_READY}"
}

_print_verdict() {
    local blocked="$1"
    local warnings="$2"
    preflight_section "VERDICT"
    if [[ "${blocked}" -eq 1 ]]; then
        preflight_log "BLOCKED — au moins un problème bloquant ci-dessus empêche READY."
        preflight_log "corriger les points marqués ERREUR puis relancer ce script."
    elif [[ "${warnings}" -eq 1 ]]; then
        preflight_log "READY WITH WARNINGS — utilisable, mais voir les avertissements ci-dessus."
    else
        preflight_log "READY — environnement, infrastructure, sandbox et tests validés."
    fi
}

main_preflight "$@"
