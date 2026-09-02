#!/usr/bin/env bash
# APEX — launcher Linux/WSL officiel.
#
# Couche d'orchestration UNIQUEMENT : ce script ne contient et ne doit
# jamais contenir de logique métier APEX. Il appelle des commandes qui
# existent déjà (docker compose, apex doctor, apex gate) et relaie
# fidèlement leurs résultats — il ne les réinterprète jamais.
#
# Portable par construction : aucune dépendance à WSL. La racine du projet
# est résolue depuis l'emplacement de CE script, jamais depuis un chemin
# utilisateur codé en dur — fonctionne identiquement sous WSL2, Linux
# natif, ou une future machine Linux dédiée APEX.
#
# Interdits, appliqués strictement dans ce script :
#   - aucune installation automatique (Docker, Python, Ollama, Postgres,
#     Redis, MinIO) ;
#   - aucun démarrage automatique de Docker Engine, WSL ou Ollama ;
#   - aucun téléchargement de modèle (`ollama pull` n'apparaît jamais ici) ;
#   - aucune création/écrasement de `.env` ou `.venv` — seulement détectés ;
#   - aucun affichage de valeur issue de `.env` (seuls les NOMS de
#     variables sont journalisés, jamais leur contenu).
#
# Codes de sortie (documentés une fois ici, source unique pour ce script
# et les commits suivants qui les utiliseront) :
#   0   - préflight réussi
#   1   - .env absent
#   2   - Docker/Compose indisponible ou `docker compose up` en échec
#   3   - aucun interpréteur Python >= 3.12 utilisable trouvé
#   4   - `apex doctor` a renvoyé FAIL
#   5   - `apex gate` a renvoyé FAIL
#   130 - interrompu (Ctrl+C / SIGINT)

set -euo pipefail

readonly EXIT_OK=0
readonly EXIT_ENV_MISSING=1
readonly EXIT_DOCKER_FAIL=2
readonly EXIT_PYTHON_MISSING=3
readonly EXIT_DOCTOR_FAIL=4
readonly EXIT_GATE_FAIL=5
readonly EXIT_INTERRUPTED=130

readonly MIN_PYTHON_MAJOR=3
readonly MIN_PYTHON_MINOR=12

# Toujours sur stderr, jamais stdout : plusieurs fonctions (detect_python,
# resolve_project_root) renvoient leur résultat via stdout pour être
# capturées par `$(...)` — un log sur stdout s'y mélangerait silencieusement
# et corromprait la valeur capturée (constaté en test manuel avant ce
# correctif). stdout reste réservé aux valeurs de retour, jamais aux logs.
log_info() { printf '[apex-launch] %s\n' "$*" >&2; }
log_warn() { printf '[apex-launch] AVERTISSEMENT : %s\n' "$*" >&2; }
log_err()  { printf '[apex-launch] ERREUR : %s\n' "$*" >&2; }

on_interrupt() {
    log_warn "interrompu (Ctrl+C) — aucun processus externe n'a été arrêté par ce script."
    exit "${EXIT_INTERRUPTED}"
}
trap on_interrupt INT

# -- Racine du projet --------------------------------------------------------
#
# Résolue depuis l'emplacement de CE script (scripts/apex-launch.sh est
# toujours à un niveau sous la racine), jamais depuis $PWD ni un chemin en
# dur : le script reste correct quel que soit le répertoire d'appel.
resolve_project_root() {
    # Résout d'abord le lien symbolique éventuel vers son fichier physique —
    # sinon un appel via un lien (PATH, raccourci) donnerait le répertoire
    # du LIEN, pas celui du script réel, et la racine calculée serait fausse.
    local source="${BASH_SOURCE[0]}"
    if command -v readlink >/dev/null 2>&1; then
        source="$(readlink -f -- "${source}" 2>/dev/null || printf '%s' "${source}")"
    fi
    local script_dir
    script_dir="$(cd -- "$(dirname -- "${source}")" >/dev/null 2>&1 && pwd -P)"
    (cd -- "${script_dir}/.." >/dev/null 2>&1 && pwd -P)
}

# -- Chargement contrôlé de .env ---------------------------------------------
#
# Analyseur volontairement minimal : accepte uniquement des lignes
# `NOM=valeur` (NOM = identifiant shell valide), rejette tout le reste sans
# jamais évaluer la valeur comme du code — ni `source`, ni `eval`. Une
# valeur de `.env` ne peut donc jamais exécuter de commande, même si le
# fichier est malformé ou corrompu.
load_env_file() {
    local env_path="$1"
    local loaded=0
    local line key value

    if [[ ! -e "${env_path}" ]]; then
        return 1
    fi

    # .env peut porter des secrets d'infrastructure : un avertissement
    # (jamais bloquant, jamais une correction automatique des permissions)
    # si le fichier est lisible au-delà du propriétaire.
    if command -v stat >/dev/null 2>&1; then
        local mode
        mode="$(stat -c '%a' "${env_path}" 2>/dev/null || stat -f '%Lp' "${env_path}" 2>/dev/null || true)"
        if [[ -n "${mode}" && "${mode}" =~ ^[0-7][0-7][0-7]$ ]]; then
            local group_other="${mode:1:2}"
            if [[ "${group_other}" != "00" ]]; then
                log_warn ".env est lisible au-delà du propriétaire (permissions ${mode}) — envisager 'chmod 600 .env'."
            fi
        fi
    fi

    while IFS= read -r line || [[ -n "${line}" ]]; do
        # Commentaire ou ligne vide.
        [[ "${line}" =~ ^[[:space:]]*(#.*)?$ ]] && continue
        # Doit être NOM=valeur, NOM = identifiant shell valide.
        if [[ "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            # Guillemets englobants optionnels, retirés littéralement —
            # jamais d'expansion (pas de $(), pas de guillemets inverses,
            # pas de glob) : la valeur reste une chaîne opaque.
            if [[ "${value}" =~ ^\"(.*)\"$ ]]; then
                value="${BASH_REMATCH[1]}"
            elif [[ "${value}" =~ ^\'(.*)\'$ ]]; then
                value="${BASH_REMATCH[1]}"
            fi
            export "${key}=${value}"
            loaded=$((loaded + 1))
        else
            log_warn "ligne .env ignorée (format inattendu, ni commentaire ni NOM=valeur)"
        fi
    done < "${env_path}"

    log_info "${loaded} variable(s) chargée(s) depuis .env (valeurs jamais journalisées)."
    return 0
}

# -- Détection Python / venv --------------------------------------------------
#
# Le launcher active un `.venv` existant s'il en trouve un, sinon se
# rabat sur un `python3` système compatible. Il n'en crée, n'en installe
# et n'en modifie AUCUN.
python_version_ok() {
    local candidate="$1"
    [[ -x "${candidate}" ]] || return 1
    "${candidate}" -c "
import sys
raise SystemExit(0 if sys.version_info[:2] >= (${MIN_PYTHON_MAJOR}, ${MIN_PYTHON_MINOR}) else 1)
" 2>/dev/null
}

detect_python() {
    local project_root="$1"
    local venv_python="${project_root}/.venv/bin/python3"

    if python_version_ok "${venv_python}"; then
        log_info "venv détecté et compatible : .venv/bin/python3"
        printf '%s\n' "${venv_python}"
        return 0
    fi

    if [[ -x "${venv_python}" ]]; then
        log_warn ".venv présent mais version Python insuffisante (${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ requis) — non utilisé."
    fi

    local system_python
    if system_python="$(command -v python3 2>/dev/null)" && python_version_ok "${system_python}"; then
        log_warn "aucun .venv utilisable — python3 système utilisé (${system_python})."
        printf '%s\n' "${system_python}"
        return 0
    fi

    return 1
}

# -- Préflight environnement (commit 1 : rien au-delà) -----------------------

main() {
    local project_root
    project_root="$(resolve_project_root)"
    log_info "racine du projet : ${project_root}"

    local env_file="${project_root}/.env"
    if ! load_env_file "${env_file}"; then
        log_err ".env introuvable (${env_file})."
        log_err "remédiation : cp .env.example .env, puis éditer les valeurs avant de relancer."
        exit "${EXIT_ENV_MISSING}"
    fi

    local python_bin
    if ! python_bin="$(detect_python "${project_root}")"; then
        log_err "aucun interpréteur Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ utilisable (ni .venv, ni python3 système)."
        log_err "remédiation : créer .venv vous-même (python3.12 -m venv .venv && .venv/bin/pip install -e .) — ce script n'installe rien."
        exit "${EXIT_PYTHON_MISSING}"
    fi
    log_info "interpréteur retenu : ${python_bin}"

    log_info "préflight environnement OK."
    exit "${EXIT_OK}"
}

# Exécuté seulement en tant que script (`./apex-launch.sh`), jamais quand ce
# fichier est `source`é — permet aux tests d'importer les fonctions
# individuellement (resolve_project_root, load_env_file, detect_python,
# on_interrupt) sans déclencher le préflight complet.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
