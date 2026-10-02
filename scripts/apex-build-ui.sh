#!/usr/bin/env bash
# APEX — construit l'interface web (frontend/ -> frontend/dist), servie ensuite par `apex serve`.
#
# Étape d'installation, lancée UNE fois (puis après chaque mise à jour du dépôt), comme la création
# de .venv : jamais appelée par launch.sh (qui n'installe rien). Elle télécharge les dépendances
# JavaScript verrouillées par frontend/package-lock.json (`npm ci`) puis construit (`npm run build`).
# Le build ne contient aucun secret : le jeton d'API n'y est jamais inscrit (l'interface s'ouvre
# depuis le Panel avec une session, voir apex/api/ui.py).
#
# Codes de sortie : 0 construit ; 1 Node.js/npm absent ou trop ancien ; 2 `npm ci` en échec ;
# 3 `npm run build` en échec.

set -euo pipefail

readonly MIN_NODE_MAJOR=18

log() { printf '[apex-build-ui] %s\n' "$*" >&2; }

root="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/.." && pwd -P)"
frontend="${root}/frontend"

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    log "Node.js (${MIN_NODE_MAJOR}+) et npm sont nécessaires pour construire l'interface."
    log "Ubuntu : sudo apt install nodejs npm   (ou https://nodejs.org) — puis relancez ce script."
    exit 1
fi
node_major="$(node -p 'process.versions.node.split(".")[0]')"
if (( node_major < MIN_NODE_MAJOR )); then
    log "Node.js $(node --version) trop ancien : ${MIN_NODE_MAJOR}+ requis."
    exit 1
fi

log "dépendances (npm ci, versions verrouillées par package-lock.json)..."
(cd -- "${frontend}" && npm ci --no-audit --no-fund) || { log "npm ci a échoué."; exit 2; }
log "construction (npm run build)..."
(cd -- "${frontend}" && npm run build) || { log "npm run build a échoué."; exit 3; }
log "interface construite : ${frontend}/dist — servie par APEX au prochain démarrage (ou tout de suite s'il tourne)."
