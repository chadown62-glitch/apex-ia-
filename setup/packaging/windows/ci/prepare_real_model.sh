#!/usr/bin/env bash
# CI UNIQUEMENT (runner GitHub Windows Server + WSL2) : installe, DANS la distribution, ce que la machine cible
# a déjà — Ollama et qwen2.5-coder:7b-instruct (config.yaml, rôles coder_main/fast) — plus l'image de la
# sandbox conteneur (sandbox.container_image) pour que la première exécution ne dépende pas d'un
# téléchargement pendant la sonde. Rien n'est changé dans APEX : le modèle est servi par le vrai Ollama, à
# l'adresse déclarée par config.yaml (127.0.0.1:11434). Jamais utilisé sur le PC de l'utilisateur.
#
# Usage (en root dans la distribution) : prepare_real_model.sh [modèle]
set -euo pipefail
MODEL="${1:-qwen2.5-coder:7b-instruct}"
IMAGE="python:3.12-slim"
log() { echo "[prepare_real_model] $(date -u +%H:%M:%S) $*"; }

export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq curl zstd ca-certificates >/dev/null

if ! command -v ollama >/dev/null 2>&1; then
  log "installation d'Ollama (script officiel ollama.com)"
  curl -fsSL https://ollama.com/install.sh | sh
fi
ollama --version || true

if ! curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  if pidof systemd >/dev/null 2>&1 && systemctl start ollama 2>/dev/null; then
    log "ollama démarré par systemd"
  else
    (OLLAMA_HOST=127.0.0.1:11434 nohup ollama serve >/var/log/ollama.log 2>&1 &)
    log "ollama serve lancé hors systemd"
  fi
fi
for _ in $(seq 1 60); do curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null || { log "Ollama ne répond pas"; tail -20 /var/log/ollama.log 2>/dev/null; exit 1; }

log "téléchargement du modèle $MODEL"
ollama pull "$MODEL" 2>&1 | tail -3
ollama list

log "image de la sandbox conteneur : $IMAGE"
docker pull -q "$IMAGE"

log "prêt : Ollama sur 127.0.0.1:11434 avec $MODEL ; image $IMAGE"
