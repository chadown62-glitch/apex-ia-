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
#   0   - préflight réussi (ou `stop` : rien à arrêter / arrêt propre)
#   1   - .env absent
#   2   - Docker/Compose indisponible ou `docker compose up`/`down` en échec
#   3   - aucun interpréteur Python >= 3.12 utilisable trouvé
#   4   - `apex doctor` a renvoyé FAIL
#   5   - `apex gate` a renvoyé FAIL
#   130 - interrompu (Ctrl+C / SIGINT)
#
# Sous-commande `stop` (`./apex-launch.sh stop`) : arrêt EXPLICITE et
# séparé de Ctrl+C — jamais déclenché automatiquement par une interruption
# (l'infrastructure reste up après un Ctrl+C, exactement comme après
# `make up` : seul un arrêt volontaire l'arrête). N'arrête que
# l'infrastructure que CE launcher a lui-même fait démarrer depuis un état
# éteint — jamais une infrastructure déjà active avant son passage, jamais
# un service externe (Ollama compris). Voir `compose_up`/`cmd_stop`.

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

# `trap '' INT` en toute première ligne : un second Ctrl+C reçu pendant le
# nettoyage (le court instant entre l'entrée dans le handler et l'`exit`)
# est ignoré plutôt que de réentrer le handler — garantit une exécution
# UNIQUE, jamais un message dupliqué ni un `exit` concurrent. Ce script ne
# background jamais rien (docker compose up/down, doctor, gate tournent
# tous au premier plan) : un Ctrl+C réel envoie SIGINT à tout le groupe de
# processus, donc à l'éventuel enfant en cours aussi — ce script n'a donc
# aucun PID d'enfant à cibler lui-même, et ne tue jamais rien explicitement
# (aucun `kill`/`pkill`/`killall` nulle part dans ce fichier).
on_interrupt() {
    trap '' INT
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

# -- Infrastructure via docker compose ---------------------------------------
#
# N'utilise QUE le `docker-compose.yml` déjà versionné du dépôt (postgres,
# redis, minio) — Ollama n'y figure jamais et ce script ne l'y ajoute pas.
# `up -d --wait` est nativement idempotent (Compose ne recrée un conteneur
# que si sa configuration a changé) et attend les healthchecks déjà déclarés
# dans le fichier compose — rien de cela n'est réimplémenté ici. Ce script ne
# démarre JAMAIS Docker Engine lui-même (dockerd) : si le démon n'est pas
# joignable, `docker compose` échoue avec son propre message, relayé tel
# quel — jamais une tentative de le démarrer (encore moins via `sudo`).
docker_cli_available() {
    command -v docker >/dev/null 2>&1
}

compose_plugin_available() {
    docker compose version >/dev/null 2>&1
}

# -- Ownership de l'infrastructure (pour `stop`) ------------------------------
#
# `stop` ne doit arrêter QUE l'infrastructure que ce launcher a lui-même
# fait démarrer — jamais une infrastructure déjà active avant son passage
# (démarrée manuellement, par `make up`, ou par un autre outil). Détection :
# si AUCUN service n'était `running` avant `docker compose up`, et que
# l'`up` réussit, cette exécution est la seule responsable du démarrage —
# l'ownership est enregistrée. Si au moins un service tournait déjà
# (partiellement ou totalement), l'ownership n'est PAS enregistrée : `stop`
# refusera alors d'y toucher, par prudence, même sur un état partiel.
#
# L'état est stocké dans `.apex/` (répertoire déjà utilisé par le Gate pour
# son propre rapport machine-lisible, `.apex/environment.json`) sous un nom
# distinct. Écriture ATOMIQUE (fichier temporaire puis `mv`) : un Ctrl+C
# pendant l'écriture ne peut jamais laisser un état partiellement écrit —
# soit l'ancien fichier reste en place, soit le nouveau y est intégralement.
#
# Le PID enregistré est PUREMENT informatif (diagnostic humain) : ce script
# ne le relit JAMAIS pour décider quoi que ce soit, et ne l'utilise JAMAIS
# pour cibler un signal — un PID est réutilisable par le système
# d'exploitation, s'y fier pour une décision serait dangereux (on pourrait
# viser un processus totalement différent qui a hérité du même PID entre
# temps). `stop` ne dépend que de la présence/du contenu du fichier d'état,
# jamais de la vivacité d'un PID.
readonly _COMPOSE_STATE_FILE_NAME="launcher-compose-state.json"

compose_state_file() {
    printf '%s/.apex/%s\n' "$1" "${_COMPOSE_STATE_FILE_NAME}"
}

compose_has_running_services() {
    local project_root="$1"
    local running
    running="$(cd -- "${project_root}" && docker compose ps --status running --quiet 2>/dev/null)"
    [[ -n "${running}" ]]
}

record_compose_ownership() {
    local project_root="$1"
    local state_dir="${project_root}/.apex"
    local state_file
    state_file="$(compose_state_file "${project_root}")"

    mkdir -p -- "${state_dir}" 2>/dev/null || {
        log_warn "impossible de créer ${state_dir} — 'stop' ne pourra pas arrêter cette infrastructure automatiquement."
        return 0
    }
    local tmp_file
    tmp_file="$(mktemp "${state_dir}/.launcher-compose-state.XXXXXX" 2>/dev/null)" || {
        log_warn "impossible d'enregistrer l'état de démarrage — 'stop' ne pourra pas arrêter cette infrastructure automatiquement."
        return 0
    }
    printf '{"compose_started_by_launcher": true, "pid": %d, "started_at": "%s"}\n' \
        "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || printf 'unknown')" > "${tmp_file}"
    mv -f -- "${tmp_file}" "${state_file}"
}

# Démarre l'infrastructure existante. N'exécute jamais `docker compose
# down`/`rm`/`prune` : seul `up -d --wait` est utilisé, jamais un arrêt.
compose_up() {
    local project_root="$1"

    if ! docker_cli_available; then
        log_err "docker introuvable dans PATH."
        log_err "remédiation : installer Docker vous-même — ce script n'installe rien."
        return 1
    fi

    if ! compose_plugin_available; then
        log_err "le plugin 'docker compose' est indisponible (Docker installé sans, ou démon injoignable)."
        log_err "remédiation : vérifier votre installation Docker — ce script ne la modifie pas."
        return 1
    fi

    local already_running=false
    if compose_has_running_services "${project_root}"; then
        already_running=true
    fi

    log_info "démarrage de l'infrastructure (docker compose up -d --wait)..."
    local output
    local status=0
    output="$(cd -- "${project_root}" && docker compose up -d --wait 2>&1)" || status=$?
    if [[ -n "${output}" ]]; then
        while IFS= read -r line; do
            log_info "  docker: ${line}"
        done <<< "${output}"
    fi

    if (( status != 0 )); then
        log_err "docker compose up a échoué (code ${status}) — voir les lignes ci-dessus."
        return 1
    fi

    if [[ "${already_running}" == "false" ]]; then
        record_compose_ownership "${project_root}"
        log_info "infrastructure démarrée par ce launcher (postgres, redis, minio) — 'stop' pourra l'arrêter."
    else
        log_info "infrastructure déjà active avant ce launcher — 'stop' ne l'arrêtera pas automatiquement."
    fi
    return 0
}

# -- Sous-commande `stop` ------------------------------------------------------
#
# Arrêt EXPLICITE et VOLONTAIRE — jamais une conséquence d'un Ctrl+C (voir
# `on_interrupt`). Idempotent et sans ambiguïté :
#   - aucun fichier d'état -> rien à faire, EXIT_OK, aucun appel docker ;
#   - fichier d'état présent mais n'affirmant pas l'ownership -> rien à
#     faire, le fichier est nettoyé, EXIT_OK ;
#   - ownership affirmée -> `docker compose down` (jamais `-v` : les
#     volumes nommés — donc les données — ne sont jamais supprimés), puis
#     le fichier d'état est retiré. Un second `stop` derrière celui-ci ne
#     retrouve plus de fichier -> no-op, EXIT_OK : idempotent par
#     construction, sans avoir à mémoriser quoi que ce soit d'autre.
cmd_stop() {
    local project_root
    project_root="$(resolve_project_root)"
    local state_file
    state_file="$(compose_state_file "${project_root}")"

    if [[ ! -f "${state_file}" ]]; then
        log_info "rien à arrêter — aucune infrastructure démarrée par ce launcher n'est enregistrée."
        exit "${EXIT_OK}"
    fi

    if ! grep -q '"compose_started_by_launcher"[[:space:]]*:[[:space:]]*true' -- "${state_file}" 2>/dev/null; then
        log_info "rien à arrêter — l'état enregistré n'affirme pas que ce launcher a démarré l'infrastructure."
        rm -f -- "${state_file}"
        exit "${EXIT_OK}"
    fi

    if ! docker_cli_available || ! compose_plugin_available; then
        log_err "docker/le plugin compose est indisponible — impossible d'arrêter proprement l'infrastructure."
        log_err "l'état enregistré est conservé ; relancer 'stop' une fois Docker à nouveau disponible."
        exit "${EXIT_DOCKER_FAIL}"
    fi

    log_info "arrêt de l'infrastructure démarrée par ce launcher (docker compose down)..."
    local output
    local status=0
    output="$(cd -- "${project_root}" && docker compose down 2>&1)" || status=$?
    if [[ -n "${output}" ]]; then
        while IFS= read -r line; do
            log_info "  docker: ${line}"
        done <<< "${output}"
    fi

    if (( status != 0 )); then
        log_err "docker compose down a échoué (code ${status}) — état conservé, voir les lignes ci-dessus."
        exit "${EXIT_DOCKER_FAIL}"
    fi

    rm -f -- "${state_file}"
    log_info "infrastructure arrêtée, état nettoyé."
    exit "${EXIT_OK}"
}

# -- apex doctor / apex gate --------------------------------------------------
#
# Relais fidèle, jamais un jugement propre : ce launcher ne réimplémente
# aucun seuil matériel, aucune règle de compatibilité, aucune notion de
# « environnement sain » — `apex doctor` et `apex gate` restent l'unique
# source de vérité. Leur sortie est héritée telle quelle (jamais capturée
# puis reformatée) : ce que l'utilisateur voit est exactement ce que
# `python -m apex.cli.main doctor`/`gate` afficherait exécuté directement.
# Un FAIL de l'un ou l'autre arrête le launcher sans exécuter la suite —
# jamais recatégorisé en WARN, jamais ignoré, jamais contourné par un
# drapeau (aucune option `--allow-container-exec`/`--allow-network-probe`
# n'est ajoutée par ce script : leurs valeurs par défaut, les plus
# conservatrices, sont conservées).
run_doctor() {
    local project_root="$1"
    local python_bin="$2"
    log_info "exécution de apex doctor..."
    local status=0
    (cd -- "${project_root}" && "${python_bin}" -m apex.cli.main doctor) || status=$?
    return "${status}"
}

run_gate() {
    local project_root="$1"
    local python_bin="$2"
    log_info "exécution de apex gate..."
    local status=0
    (cd -- "${project_root}" && "${python_bin}" -m apex.cli.main gate) || status=$?
    return "${status}"
}

# -- Préflight environnement --------------------------------------------------

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

    if ! compose_up "${project_root}"; then
        exit "${EXIT_DOCKER_FAIL}"
    fi

    if ! run_doctor "${project_root}" "${python_bin}"; then
        log_err "apex doctor a signalé un échec (FAIL) — voir le rapport ci-dessus."
        exit "${EXIT_DOCTOR_FAIL}"
    fi

    if ! run_gate "${project_root}" "${python_bin}"; then
        log_err "apex gate a signalé un échec — voir le rapport ci-dessus."
        exit "${EXIT_GATE_FAIL}"
    fi

    log_info "préflight OK (environnement + infrastructure + doctor + gate)."
    exit "${EXIT_OK}"
}

# Exécuté seulement en tant que script (`./apex-launch.sh`), jamais quand ce
# fichier est `source`é — permet aux tests d'importer les fonctions
# individuellement (resolve_project_root, load_env_file, detect_python,
# on_interrupt, cmd_stop, ...) sans déclencher le préflight complet.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        stop)
            cmd_stop
            ;;
        *)
            main "$@"
            ;;
    esac
fi
