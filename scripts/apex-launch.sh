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
# `APEX_LAUNCHER_RECLAIM_ORPHAN_PORTS=1` (variable d'environnement, jamais
# un drapeau CLI — cohérent avec la convention `APEX_*` déjà utilisée par
# `.env`) : cas réel diagnostiqué — un arrêt brutal du démon Docker peut
# laisser un `docker-proxy` orphelin tenir un port publié par APEX (5432/
# 6379/9000/9001) alors que le conteneur compose correspondant n'est plus
# `running` (`docker compose ps` ne le voit plus, il apparaît seulement
# comme `Created`) : `docker compose up -d --wait` échoue alors avec
# « address already in use », sans diagnostic utile. Voir
# `reconcile_stale_ports`. DÉSACTIVÉ PAR DÉFAUT : ce script ne termine
# JAMAIS un processus de sa propre initiative sans que l'utilisateur l'ait
# explicitement demandé — le diagnostic (port, PID, nature du processus)
# est TOUJOURS affiché, la commande exacte de remédiation manuelle
# (`kill <PID>`) TOUJOURS proposée ; seule l'action elle-même est
# conditionnée par cette variable. Un port occupé par autre chose qu'un
# `docker-proxy` (service réellement externe à APEX) n'est JAMAIS touché,
# avec ou sans cette variable.
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
# Sous-commande `serve` (`./apex-launch.sh serve [args...]`) — N+15 : exécute
# le même préflight que ci-dessus puis `exec python -m apex.cli.main serve`
# (le code de sortie devient alors celui d'`apex serve` lui-même). Sans
# argument, ce script s'arrête après le préflight (exit 0) — comportement
# inchangé, `serve` est purement additif.
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
# processus, donc à l'éventuel enfant en cours aussi — ce SIGINT n'est donc
# jamais relayé explicitement à un PID d'enfant par ce script.
#
# Exception UNIQUE au reste de ce fichier (N+23, `reconcile_stale_ports`) :
# un signal explicite vers UN PID individuellement vérifié comme un
# `docker-proxy` orphelin réel (jamais un nom/motif de processus — cette
# forme de cible reste et restera absente de ce fichier).
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

# -- Conflits de port : docker-proxy orphelin vs service réellement externe --
#
# Cas réel diagnostiqué : un arrêt brutal du démon Docker peut laisser un
# `docker-proxy` orphelin tenir l'un des ports publiés par APEX alors que
# `docker compose ps` ne connaît plus aucun conteneur `running` — le
# conteneur correspondant reste visible mais à l'état `Created`. Sans
# détection, `docker compose up -d --wait` échoue avec « address already in
# use », un message qui ne dit jamais QUI tient le port ni si c'est sûr d'y
# toucher. Les trois fonctions ci-dessous répondent chacune à une question
# précise, aucune ne devine :
#   - quels ports APEX est sur le point de publier (lus dans
#     `docker-compose.yml`, jamais dupliqués/codés en dur ici) ;
#   - un port est-il réellement occupé (connexion TCP réelle, jamais une
#     supposition) ;
#   - le processus qui le tient est-il RÉELLEMENT `docker-proxy` (préfixe
#     `argv[0]` via `/proc/<pid>/cmdline`, jamais un nom de commande deviné
#     — un service externe qui tourne sous un autre nom n'est jamais pris
#     pour un `docker-proxy`).

# Lit les ports HÔTE déclarés dans `docker-compose.yml` (`"ip:host:container"`)
# — source UNIQUE de vérité, jamais une liste de ports recopiée ici : si le
# fichier compose change, cette fonction suit sans modification.
apex_declared_ports() {
    local project_root="$1"
    local compose_file="${project_root}/docker-compose.yml"
    [[ -f "${compose_file}" ]] || return 0

    local line
    while IFS= read -r line; do
        if [[ "${line}" =~ \"[0-9.]+:([0-9]+):[0-9]+\" ]]; then
            printf '%s\n' "${BASH_REMATCH[1]}"
        fi
    done < "${compose_file}"
}

# Connexion TCP réelle (`/dev/tcp`, fonctionnalité bash native — aucun outil
# externe requis) — jamais une supposition sur l'état d'un port.
port_is_open() {
    local host="$1" port="$2"
    (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null
}

# Cherche, parmi les process RÉELS (`/proc`), un `docker-proxy` dont la
# ligne de commande porte `-host-port <port>` EXACTEMENT — jamais un préfixe
# de port ambigu (ex. le port 543 ne doit jamais matcher 5432). Le nom du
# processus est vérifié sur `argv[0]` (`/proc/<pid>/cmdline`, premier champ,
# préfixe de chemin retiré) : c'est ce que porte réellement l'invocation de
# `docker-proxy` par le démon Docker, contrairement à `/proc/<pid>/comm`
# (tronqué à 15 caractères et sensible à la façon dont le processus a été
# exec'é — un test double fidèle peut le contrôler précisément via
# `exec -a`, voir `tests/test_launcher.py`). Retourne le PID sur stdout,
# rien et un statut non nul si introuvable — jamais un PID inventé.
find_docker_proxy_pid_for_port() {
    local port="$1"
    local pid_dir pid argv0 cmdline
    for pid_dir in /proc/[0-9]*; do
        pid="${pid_dir#/proc/}"
        [[ -r "${pid_dir}/cmdline" ]] || continue
        cmdline="$(tr '\0' '\n' < "${pid_dir}/cmdline" 2>/dev/null || true)"
        [[ -n "${cmdline}" ]] || continue
        argv0="$(head -n1 <<< "${cmdline}")"
        [[ "${argv0##*/}" == "docker-proxy" ]] || continue
        if grep -qxF -- "-host-port" <<< "${cmdline}"; then
            local next_is_port=false line
            while IFS= read -r line; do
                if [[ "${next_is_port}" == "true" ]]; then
                    if [[ "${line}" == "${port}" ]]; then
                        printf '%s\n' "${pid}"
                        return 0
                    fi
                    next_is_port=false
                elif [[ "${line}" == "-host-port" ]]; then
                    next_is_port=true
                fi
            done <<< "${cmdline}"
        fi
    done
    return 1
}

# Pour chaque port qu'APEX est sur le point de publier : s'il est libre,
# rien à faire. S'il est occupé par un `docker-proxy` réellement identifié
# (voir `find_docker_proxy_pid_for_port`) : diagnostic clair TOUJOURS
# affiché, action de libération SEULEMENT si
# `APEX_LAUNCHER_RECLAIM_ORPHAN_PORTS=1` (désactivé par défaut, voir
# l'en-tête du fichier). S'il est occupé par autre chose : diagnostic
# « service externe » — jamais touché, avec ou sans cette variable.
reconcile_stale_ports() {
    local project_root="$1"
    local port pid

    while IFS= read -r port; do
        [[ -n "${port}" ]] || continue
        port_is_open "127.0.0.1" "${port}" || continue

        if pid="$(find_docker_proxy_pid_for_port "${port}")"; then
            log_warn "port ${port} (infrastructure APEX) occupé par un docker-proxy orphelin (PID ${pid})."
            log_warn "  aucun conteneur APEX 'running' ne le revendique — état Docker incohérent, généralement après un arrêt brutal du démon."
            if [[ "${APEX_LAUNCHER_RECLAIM_ORPHAN_PORTS:-0}" == "1" ]]; then
                log_warn "  APEX_LAUNCHER_RECLAIM_ORPHAN_PORTS=1 : libération du port ${port} (arrêt du docker-proxy orphelin PID ${pid})."
                kill "${pid}" 2>/dev/null || true
                local waited=0
                while port_is_open "127.0.0.1" "${port}" && (( waited < 20 )); do
                    sleep 0.1
                    waited=$((waited + 1))
                done
            else
                log_warn "  remédiation manuelle sûre : kill ${pid}"
                log_warn "  ou relancer avec APEX_LAUNCHER_RECLAIM_ORPHAN_PORTS=1 pour une libération automatique de ce cas précis uniquement."
            fi
        else
            log_warn "port ${port} (infrastructure APEX) déjà occupé par un processus qui n'est PAS un docker-proxy — probablement un service externe à APEX."
            log_warn "  ce launcher ne touche jamais un processus qu'il n'a pas identifié avec certitude comme lui appartenant."
            log_warn "  diagnostic : ss -ltnp | grep ':${port} '   (ou : lsof -i :${port})"
        fi
    done <<< "$(apex_declared_ports "${project_root}")"
}

# `docker compose up -d --wait` peut réussir (code 0, healthchecks
# satisfaits) alors qu'un conteneur EXISTANT — créé par une exécution
# antérieure — n'a jamais enregistré le mapping de port actuellement
# déclaré dans `docker-compose.yml` : Compose ne recrée un conteneur que si
# le hash de SA configuration a changé depuis sa création ; un conteneur
# resté dans cet état reste indéfiniment « sain » (healthcheck exécuté DANS
# le réseau Docker, jamais affecté par l'absence de publication côté hôte)
# sans jamais republier son port. Cas RÉEL diagnostiqué (mission « Debug
# physique du launcher / postgres healthy mais port 5432 absent », N+24) :
# `docker compose ps` reportait `apex-postgres` "Up (healthy)" avec une
# colonne PORTS entièrement VIDE, alors que redis/minio affichaient
# correctement la leur. `reconcile_stale_ports` (N+23) ne détecte JAMAIS ce
# cas : aucun processus, orphelin ou non, ne tient le port — le conteneur
# ne l'a jamais demandé au système d'exploitation.
#
# « healthy » n'est donc jamais une preuve qu'un port hôte est publié —
# seule une connexion TCP réelle (`port_is_open`, déjà réutilisée telle
# quelle) l'est.
verify_declared_ports_after_up() {
    local project_root="$1"
    local port all_open=true
    while IFS= read -r port; do
        [[ -n "${port}" ]] || continue
        port_is_open "127.0.0.1" "${port}" || all_open=false
    done <<< "$(apex_declared_ports "${project_root}")"
    [[ "${all_open}" == "true" ]]
}

# Démarre l'infrastructure existante. N'exécute jamais `docker compose
# down`/`rm`/`prune` : seul `up -d --wait` est utilisé, jamais un arrêt —
# à UNE exception près, documentée et bornée : `up -d --force-recreate
# --wait`, tenté UNE SEULE fois (jamais une boucle) si un port déclaré
# reste injoignable après un `up` pourtant réussi (voir
# `verify_declared_ports_after_up`). `--force-recreate` remplace
# l'INSTANCE de conteneur, jamais un volume nommé (`postgres-data`/
# `redis-data`/`minio-data`) — les données survivent par construction,
# c'est la remédiation standard `docker compose` pour un conteneur dont la
# configuration réelle a divergé. Automatique (contrairement à
# `APEX_LAUNCHER_RECLAIM_ORPHAN_PORTS`) : cette opération ne détruit jamais
# de donnée, à la différence d'un signal envoyé à un PID.
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

    # Seulement quand RIEN de compose n'est actif (sinon les ports occupés
    # le sont légitimement par l'infrastructure déjà en cours — rien à
    # réconcilier) : le cas réel diagnostiqué est exactement celui-ci, un
    # `docker compose ps` qui ne voit plus rien tourner alors qu'un port
    # reste occupé au niveau du système d'exploitation.
    if [[ "${already_running}" == "false" ]]; then
        reconcile_stale_ports "${project_root}"
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

    if ! verify_declared_ports_after_up "${project_root}"; then
        log_warn "docker compose up a réussi (conteneurs sains) mais au moins un port déclaré d'APEX n'est pas réellement en écoute."
        log_warn "  cause probable : un conteneur créé par une exécution antérieure n'a jamais enregistré ce mapping de port (Compose ne recrée que si sa configuration a changé)."
        log_warn "  remédiation : docker compose up -d --force-recreate --wait (une seule tentative, aucun volume touché)."
        local recreate_output
        local recreate_status=0
        recreate_output="$(cd -- "${project_root}" && docker compose up -d --force-recreate --wait 2>&1)" || recreate_status=$?
        if [[ -n "${recreate_output}" ]]; then
            while IFS= read -r line; do
                log_info "  docker: ${line}"
            done <<< "${recreate_output}"
        fi
        if (( recreate_status != 0 )); then
            log_err "docker compose up --force-recreate a échoué (code ${recreate_status}) — voir les lignes ci-dessus."
            return 1
        fi
        if ! verify_declared_ports_after_up "${project_root}"; then
            log_err "au moins un port déclaré d'APEX reste injoignable même après --force-recreate."
            log_err "  cause probablement hors de portée de ce launcher (pare-feu, réseau hôte) — diagnostic manuel requis."
            return 1
        fi
        log_info "port(s) réconcilié(s) avec succès après --force-recreate."
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

# -- Annonce au Panel APEX (sous-commande `serve` seulement) -----------------
#
# Le Panel Windows (`APEX.exe`) lit `~/.apex/runtime/backend.json` pour savoir qu'un backend
# démarre, sur quel port, et où se trouve le jeton (jamais le jeton lui-même). `serve` l'écrit en
# phase `starting` dès que Python est trouvé — AVANT compose/doctor/gate, les étapes longues — sinon
# le Panel dirait « non démarré » pendant tout le préflight. Le format a UN seul écrivain
# (`python -m apex.discovery`, jamais recopié ici). Meilleur effort : un échec n'arrête jamais le
# lancement. Retiré si le préflight échoue (trap EXIT) ; après `exec`, `apex serve` (même pid) le
# réécrit en phase `serving` et le retire à l'arrêt.
ANNOUNCE_STARTING=0
SERVE_HOST="127.0.0.1"
SERVE_PORT="8000"

# Mêmes défauts qu'`apex serve` ; seules --host/--port sont lues, le reste est ignoré ici.
parse_serve_address() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --port) SERVE_PORT="${2:-${SERVE_PORT}}"; shift 2 || shift ;;
            --port=*) SERVE_PORT="${1#--port=}"; shift ;;
            --host) SERVE_HOST="${2:-${SERVE_HOST}}"; shift 2 || shift ;;
            --host=*) SERVE_HOST="${1#--host=}"; shift ;;
            *) shift ;;
        esac
    done
}

announce_starting() {
    [[ "${ANNOUNCE_STARTING}" == "1" ]] || return 0
    if (cd -- "${PROJECT_ROOT}" && "${PYTHON_BIN}" -m apex.discovery starting \
            --pid "$$" --host "${SERVE_HOST}" --port "${SERVE_PORT}") >/dev/null 2>&1; then
        trap withdraw_starting EXIT
        log_info "démarrage annoncé au Panel APEX (~/.apex/runtime/backend.json)."
    else
        log_warn "annonce du démarrage au Panel impossible — il indiquera « non démarré » jusqu'à l'écoute de l'API."
    fi
}

withdraw_starting() {
    (cd -- "${PROJECT_ROOT}" && "${PYTHON_BIN}" -m apex.discovery remove --pid "$$") >/dev/null 2>&1 || true
}

# -- Préflight environnement --------------------------------------------------
#
# Factorisé hors de `main` (N+15) pour être réutilisé identiquement par
# `cmd_serve` (sous-commande `serve`, voir plus bas) — jamais dupliqué :
# les deux chemins partagent EXACTEMENT la même résolution .env/venv/Python,
# le même `compose_up`, le même `apex doctor`/`apex gate`. Renseigne
# `PROJECT_ROOT`/`PYTHON_BIN` (globales, sans `local`, intentionnellement)
# pour que l'appelant les réutilise sans avoir à les recalculer.
run_preflight() {
    PROJECT_ROOT="$(resolve_project_root)"
    log_info "racine du projet : ${PROJECT_ROOT}"

    local env_file="${PROJECT_ROOT}/.env"
    if ! load_env_file "${env_file}"; then
        log_err ".env introuvable (${env_file})."
        log_err "remédiation : cp .env.example .env, puis éditer les valeurs avant de relancer."
        exit "${EXIT_ENV_MISSING}"
    fi

    if ! PYTHON_BIN="$(detect_python "${PROJECT_ROOT}")"; then
        log_err "aucun interpréteur Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ utilisable (ni .venv, ni python3 système)."
        log_err "remédiation : créer .venv vous-même (python3.12 -m venv .venv && .venv/bin/pip install -e .) — ce script n'installe rien."
        exit "${EXIT_PYTHON_MISSING}"
    fi
    log_info "interpréteur retenu : ${PYTHON_BIN}"
    announce_starting

    if ! compose_up "${PROJECT_ROOT}"; then
        exit "${EXIT_DOCKER_FAIL}"
    fi

    if ! run_doctor "${PROJECT_ROOT}" "${PYTHON_BIN}"; then
        log_err "apex doctor a signalé un échec (FAIL) — voir le rapport ci-dessus."
        exit "${EXIT_DOCTOR_FAIL}"
    fi

    if ! run_gate "${PROJECT_ROOT}" "${PYTHON_BIN}"; then
        log_err "apex gate a signalé un échec — voir le rapport ci-dessus."
        exit "${EXIT_GATE_FAIL}"
    fi
}

main() {
    run_preflight
    log_info "préflight OK (environnement + infrastructure + doctor + gate)."
    exit "${EXIT_OK}"
}

# Sous-commande `serve` (`./apex-launch.sh serve [-- arguments...]`) — N+15 :
# la SEULE façon dont ce launcher démarre réellement APEX (`apex serve`,
# commande CLI additive, voir apex/cli/main.py). Ne change RIEN au
# comportement par défaut (`./apex-launch.sh` sans argument continue de
# s'arrêter après « préflight OK », exit 0, exactement comme avant) — un
# appelant qui veut le préflight seul (ex. `make launch`) n'est jamais
# affecté. `exec` remplace le processus shell par `apex serve` : le code de
# sortie renvoyé par ce script est alors EXACTEMENT celui d'`apex serve`,
# jamais réinterprété, et aucun processus bash superflu ne reste en
# mémoire — le futur launcher Windows (ApexSetup.Launcher) qui invoque
# `wsl.exe -- bash apex-launch.sh serve` attend ainsi la fin réelle
# d'`apex serve`, pas seulement celle du préflight.
cmd_serve() {
    ANNOUNCE_STARTING=1
    parse_serve_address "$@"
    run_preflight
    log_info "préflight OK — démarrage d'APEX (apex serve)."
    cd -- "${PROJECT_ROOT}"
    trap - EXIT  # l'enregistrement passe à `apex serve` (même pid après `exec`)
    exec "${PYTHON_BIN}" -m apex.cli.main serve "$@"
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
        serve)
            shift
            cmd_serve "$@"
            ;;
        *)
            main "$@"
            ;;
    esac
fi
