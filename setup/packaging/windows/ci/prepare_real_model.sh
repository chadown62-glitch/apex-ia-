#!/usr/bin/env bash
# CI UNIQUEMENT (runner GitHub Windows Server + WSL2) : reproduit, DANS la distribution, la configuration de
# la machine cible déclarée par le projet (tests/test_config_hardware_catalog.py, _REAL_HARDWARE_ENVIRON et
# _REAL_INSTALLED_MODELS) — Ollama avec qwen2.5-coder:7b-instruct, gemma4:12b et bge-m3, et la déclaration
# matérielle de son .env (RTX 4060 Ti, 8 Go de VRAM) — plus l'image de la sandbox conteneur
# (sandbox.container_image). Rien n'est changé dans APEX, ni dans apex doctor, ni dans le Startup Gate.
#
# Constaté au run 19 : avec la déclaration par défaut de .env.example (RTX 4090, 24 Go) et un seul modèle,
# `apex doctor` refuse à juste titre le démarrage (« modèles de référence absents ») — la CI n'était alors
# pas la machine cible. Le GPU, lui, est ABSENT du runner : rien ne le mesure (gpu.config_match « VRAM
# réelle inconnue »), et la génération se fait sur CPU. Jamais utilisé sur le PC de l'utilisateur.
#
# Usage (en root dans la distribution) : prepare_real_model.sh <dépôt APEX de l'utilisateur>
set -euo pipefail
APEX_HOME_REPO="${1:?dépôt APEX attendu (ex. /home/apex/apex)}"
MODELS=(qwen2.5-coder:7b-instruct gemma4:12b bge-m3)
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

for model in "${MODELS[@]}"; do
  log "téléchargement du modèle $model"
  ollama pull "$model" 2>&1 | tail -2
done
ollama list

log "image de la sandbox conteneur : $IMAGE"
docker pull -q "$IMAGE"

ENV_FILE="$APEX_HOME_REPO/.env"
log "déclaration matérielle de la machine cible dans $ENV_FILE (le runner n'a PAS de GPU) :"
sed -i -E 's/^APEX_GPU_MODEL=.*/APEX_GPU_MODEL=NVIDIA GeForce RTX 4060 Ti/; s/^APEX_GPU_VRAM_GB=.*/APEX_GPU_VRAM_GB=8/' "$ENV_FILE"
grep -E '^APEX_GPU_(MODEL|VRAM_GB)=' "$ENV_FILE"

log "prêt : Ollama sur 127.0.0.1:11434 avec ${MODELS[*]} ; image $IMAGE"
