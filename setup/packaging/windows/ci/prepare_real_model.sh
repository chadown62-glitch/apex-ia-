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
# Mode choisi sur la mémoire MESURÉE de la distribution (run 22 : 2 processeurs, 3,9 Go — le modèle de
# référence 7B (~4,7 Go) n'y tient pas : aucune réponse en 15 min, puis arrêt du service WSL) :
#   reference  (>= 7 Go) : les modèles de la machine cible ;
#   reduced    (< 7 Go)  : un VRAI modèle plus petit, qwen2.5-coder:1.5b-instruct, déclaré par une configuration
#                          de déploiement CI dérivée de config.yaml (APEX_CONFIG) — jamais présenté comme le
#                          modèle de référence ; gemma4 (juge, 8 Go) et qwen3.6 restent « incompatibles avec la
#                          VRAM déclarée » (WARN) comme sur la machine cible.
MEM_MB=$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)
if (( MEM_MB >= 7000 )); then
  MODE=reference; MODELS=(qwen2.5-coder:7b-instruct gemma4:12b bge-m3)
else
  MODE=reduced; MODELS=(qwen2.5-coder:1.5b-instruct bge-m3)
fi
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

log "capacité MESURÉE de la distribution (run 21 : API sans réponse pendant l'inférence CPU) :"
echo "  processeurs : $(nproc) ; $(free -m | awk '/^Mem:/ {print "mémoire " $2 " Mo, disponible " $7 " Mo"} /^Swap:/ {print "; swap " $2 " Mo"}' | tr '\n' ' ')"
log "vitesse MESURÉE d'Ollama sur ce CPU (${MODELS[0]}, 32 jetons ; charge aussi le modèle en mémoire) :"
curl -fsS --max-time 900 http://127.0.0.1:11434/api/generate \
  -d "{\"model\": \"${MODELS[0]}\", \"prompt\": \"def add(a, b):\", \"stream\": false, \"options\": {\"num_predict\": 32}}" \
  | python3 -c 'import json, sys
r = json.load(sys.stdin)
s = lambda k: r.get(k, 0) / 1e9
print("  chargement %.1f s ; prompt %s jetons en %.1f s ; génération %s jetons en %.1f s ; total %.1f s" % (
    s("load_duration"), r.get("prompt_eval_count"), s("prompt_eval_duration"),
    r.get("eval_count"), s("eval_duration"), s("total_duration")))' \
  || log "mesure impossible (voir ci-dessus)"
echo "  après chargement : $(free -m | awk '/^Mem:/ {print "disponible " $7 " Mo"}')"

log "image de la sandbox conteneur : $IMAGE"
docker pull -q "$IMAGE"

ENV_FILE="$APEX_HOME_REPO/.env"
log "déclaration matérielle de la machine cible dans $ENV_FILE (le runner n'a PAS de GPU) :"
sed -i -E 's/^APEX_GPU_MODEL=.*/APEX_GPU_MODEL=NVIDIA GeForce RTX 4060 Ti/; s/^APEX_GPU_VRAM_GB=.*/APEX_GPU_VRAM_GB=8/' "$ENV_FILE"
grep -E '^APEX_GPU_(MODEL|VRAM_GB)=' "$ENV_FILE"

if [[ "$MODE" == reduced ]]; then
  CI_CONFIG="$APEX_HOME_REPO/config.ci-reduced.yaml"
  log "mode reduced : $CI_CONFIG (qwen2.5-coder 7b-instruct -> 1.5b-instruct, rôles coder_main et fast), APEX_CONFIG dans .env"
  "$APEX_HOME_REPO/.venv/bin/python" - "$APEX_HOME_REPO/config.yaml" "$CI_CONFIG" <<'PY'
import sys, yaml
src, dst = sys.argv[1:3]
doc = yaml.safe_load(open(src, encoding="utf-8"))
changed = 0
for role in doc["models"]["roles"].values():
    for cand in role.get("candidates", []):
        if cand.get("family") == "qwen2.5-coder" and cand.get("version") == "7b-instruct":
            cand["version"] = "1.5b-instruct"; changed += 1
for ep in doc["models"]["endpoints"]:
    if ep.get("served_model") == "qwen2.5-coder:7b-instruct":
        ep["served_model"] = "qwen2.5-coder:1.5b-instruct"; ep["vram_gb"] = 1.5; changed += 1
assert changed >= 3, f"configuration inattendue ({changed} remplacements)"
yaml.safe_dump(doc, open(dst, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
print(f"  {changed} remplacements")
PY
  chown apex:apex "$CI_CONFIG" 2>/dev/null || true
  sed -i '/^APEX_CONFIG=/d' "$ENV_FILE"; echo "APEX_CONFIG=$CI_CONFIG" >> "$ENV_FILE"
fi

log "prêt (mode $MODE) : Ollama sur 127.0.0.1:11434 avec ${MODELS[*]} ; image $IMAGE"
