#!/usr/bin/env bash
# =====================================================================================================
# Entraînement du Cerveau APEX v2 sur le GPU de ce PC (WSL Ubuntu + carte NVIDIA) — de zéro, par APEX.
#
#   cd ~/apex && git pull && bash scripts/wsl/apex-brain-train.sh
#
# Chaque étape écrit ses résultats dans $APEX_BRAIN_WORK (défaut ~/apex-brain) et n'est jamais refaite :
# relancer le script après une interruption (fermeture, coupure, redémarrage) REPREND là où il s'était arrêté,
# y compris au milieu de l'entraînement (point de reprise toutes les 10 minutes).
#
# Étapes : vérifications -> environnement Python (PyTorch CUDA) -> corpus (textes réels vérifiés) ->
# tokenizer -> tokenisation -> exercices d'instruction -> pré-entraînement -> instruction -> export APEX-Q5
# -> évaluation -> installation dans APEX sous Windows (%LOCALAPPDATA%\APEX\brain\apex-brain-v2).
#
# Réglages (variables d'environnement) : APEX_BRAIN_WORK, APEX_BRAIN_PRESET (rtx4060ti-8gb),
# APEX_BRAIN_PROFILE (gpu | validation), APEX_BRAIN_VOCAB (16384), APEX_BRAIN_TASKS (400000),
# APEX_BRAIN_MAX_STEPS / APEX_BRAIN_SFT_STEPS (0 = préréglage), APEX_BRAIN_ALLOW_CPU=1 (essai sans GPU),
# APEX_BRAIN_SKIP_INSTALL=1, APEX_BRAIN_PYTHON (python3), APEX_BRAIN_VENV (environnement Python existant à
# réutiliser), APEX_BRAIN_MIN_FREE_GB (40), APEX_BRAIN_EVAL_LIMIT (0 = tous les exercices d'évaluation),
# APEX_BRAIN_STABILITY (100 générations).
# =====================================================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${APEX_BRAIN_WORK:-$HOME/apex-brain}"
PRESET="${APEX_BRAIN_PRESET:-rtx4060ti-8gb}"
PROFILE="${APEX_BRAIN_PROFILE:-gpu}"
VOCAB="${APEX_BRAIN_VOCAB:-16384}"
TASKS="${APEX_BRAIN_TASKS:-400000}"
MAX_STEPS="${APEX_BRAIN_MAX_STEPS:-0}"
SFT_STEPS="${APEX_BRAIN_SFT_STEPS:-0}"
PYTHON="${APEX_BRAIN_PYTHON:-python3}"
mkdir -p "$WORK"
LOG="$WORK/apex-brain-train.log"
exec > >(tee -a "$LOG") 2>&1

say() { printf '\n[%s] %s\n' "$(date '+%d/%m %H:%M:%S')" "$*"; }
fail() { say "ÉCHEC : $*"; say "Journal complet : $LOG"; exit 1; }
done_mark() { touch "$WORK/.done-$1"; }
is_done() { [[ -f "$WORK/.done-$1" ]]; }

say "Cerveau APEX v2 — entraînement de zéro (dépôt : $REPO, travail : $WORK, préréglage : $PRESET)"

# ------------------------------------------------------------------------------------------ vérifications
free_gb=$(df -BG --output=avail "$WORK" | tail -1 | tr -dc '0-9')
MIN_FREE_GB="${APEX_BRAIN_MIN_FREE_GB:-40}"
if [[ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]] && ! is_done corpus; then
  fail "espace disque insuffisant dans $WORK : ${free_gb} Go libres, ${MIN_FREE_GB} Go nécessaires (données + points de reprise)"
fi
if [[ "${APEX_BRAIN_ALLOW_CPU:-0}" != "1" ]]; then
  command -v nvidia-smi >/dev/null || fail "nvidia-smi introuvable : le GPU NVIDIA n'est pas visible dans WSL (pilote NVIDIA Windows récent requis, puis « wsl --update »)"
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || fail "nvidia-smi ne répond pas"
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), sys.version' || fail "Python 3.10 ou plus récent requis ($PYTHON)"

# ------------------------------------------------------------------------------- environnement Python
VENV="${APEX_BRAIN_VENV:-$WORK/venv}"
if [[ -n "${APEX_BRAIN_VENV:-}" ]]; then
  [[ -x "$VENV/bin/python" ]] || fail "APEX_BRAIN_VENV=$VENV ne contient pas bin/python"
elif ! is_done venv; then
  say "Environnement Python (PyTorch avec CUDA depuis PyPI, NumPy, safetensors, pyarrow) — plusieurs minutes…"
  "$PYTHON" -m venv "$VENV" || fail "création de l'environnement impossible (sudo apt install python3-venv)"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install torch numpy safetensors pyarrow pyyaml || fail "installation des bibliothèques"
  done_mark venv
fi
PY="$VENV/bin/python"
DEVICE=$("$PY" -c 'import torch; print("cuda" if torch.cuda.is_available() else "cpu")')
if [[ "$DEVICE" != "cuda" && "${APEX_BRAIN_ALLOW_CPU:-0}" != "1" ]]; then
  fail "PyTorch ne voit pas le GPU (torch.cuda.is_available() = False) : mettre à jour le pilote NVIDIA Windows et WSL"
fi
"$PY" -c 'import torch; print("PyTorch", torch.__version__, "CUDA", torch.version.cuda, "GPU", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "aucun")'

BUILD=("$PY" "$REPO/scripts/apex_brain_build.py")
cd "$REPO"

# ----------------------------------------------------------------------------------------------- données
if ! is_done corpus; then
  say "Corpus : textes réels vérifiés (documentation, code sous licence permissive, textes publics anglais et français)…"
  "${BUILD[@]}" corpus --profile "$PROFILE" --out "$WORK/corpus" --cache "$WORK/cache" || fail "corpus"
  done_mark corpus
fi
if ! is_done tokenizer; then
  say "Tokenizer d'APEX ($VOCAB jetons), appris sur un échantillon équilibré du corpus…"
  "${BUILD[@]}" tokenizer --corpus "$WORK/corpus" --out "$WORK/tokenizer.json" --vocab "$VOCAB" \
    --budget-mb "en=60,fr=60,code=40,translation=10" || fail "tokenizer"
  done_mark tokenizer
fi
if ! is_done tokenize; then
  say "Tokenisation du corpus…"
  rm -rf "$WORK/data"
  "${BUILD[@]}" tokenize --corpus "$WORK/corpus" --tokenizer "$WORK/tokenizer.json" --out "$WORK/data" \
    --workers "$(nproc)" || fail "tokenisation"
  done_mark tokenize
fi
if ! is_done tasks; then
  say "Exercices d'instruction vérifiables ($TASKS conversations)…"
  rm -rf "$WORK/sft"
  window=$("$PY" -c "import sys; sys.path.insert(0, '$REPO'); from scripts.apex_brain_build import PRESETS; print(PRESETS['$PRESET']['sft']['window'])" 2>/dev/null || echo 2048)
  "${BUILD[@]}" tasks --tokenizer "$WORK/tokenizer.json" --out "$WORK/sft" --train "$TASKS" --window "$window" \
    --corpus "$WORK/corpus" || fail "exercices"
  done_mark tasks
fi

# ------------------------------------------------------------------------------------------ entraînement
say "Pré-entraînement (reprise automatique ; Ctrl+C l'arrête proprement, relancer le script pour continuer)…"
"${BUILD[@]}" train --stage pretrain --preset "$PRESET" --data "$WORK/data" --run "$WORK/runs/pretrain" \
  --device "$DEVICE" --max-steps "$MAX_STEPS" || fail "pré-entraînement"
"$PY" - "$WORK/runs/pretrain" <<'EOF' || { say "Pré-entraînement interrompu avant la fin : relancer le script pour continuer."; exit 0; }
import json, sys
from pathlib import Path
run = Path(sys.argv[1])
config = json.loads((run / "run.json").read_text())
stops = [json.loads(l) for l in (run / "metrics.jsonl").read_text().splitlines() if '"stop"' in l]
sys.exit(0 if stops and stops[-1]["step"] >= config["max_steps"] else 1)
EOF

say "Instruction : apprendre à converser et à répondre juste…"
"${BUILD[@]}" train --stage sft --preset "$PRESET" --data "$WORK/sft" --run "$WORK/runs/sft" \
  --init-from "$WORK/runs/pretrain/checkpoint.pt" --replay-data "$WORK/data" --device "$DEVICE" \
  --max-steps "$SFT_STEPS" || fail "instruction"
"$PY" - "$WORK/runs/sft" <<'EOF' || { say "Instruction interrompue avant la fin : relancer le script pour continuer."; exit 0; }
import json, sys
from pathlib import Path
run = Path(sys.argv[1])
config = json.loads((run / "run.json").read_text())
stops = [json.loads(l) for l in (run / "metrics.jsonl").read_text().splitlines() if '"stop"' in l]
sys.exit(0 if stops and stops[-1]["step"] >= config["max_steps"] else 1)
EOF

# -------------------------------------------------------------------------- export, évaluation, installation
BRAIN="$WORK/brain/apex-brain-v2"
if ! is_done export; then
  say "Export des poids en APEX-Q5…"
  rm -rf "$BRAIN"
  "${BUILD[@]}" export --run "$WORK/runs/sft" --run-pretrain "$WORK/runs/pretrain" --data "$WORK/data" \
    --tokenizer "$WORK/tokenizer.json" --out "$BRAIN" --quantize apex-q5 || fail "export"
  done_mark export
fi
if ! is_done evaluate; then
  say "Évaluation (exercices jamais vus, coût de la quantification, vitesse, stabilité)…"
  "${BUILD[@]}" evaluate --brain "$BRAIN" --tasks "$WORK/sft/eval.jsonl" --data "$WORK/data" \
    --checkpoint "$WORK/runs/sft/checkpoint.pt" --limit "${APEX_BRAIN_EVAL_LIMIT:-0}" \
    --stability "${APEX_BRAIN_STABILITY:-100}" || fail "évaluation"
  done_mark evaluate
fi
if [[ "${APEX_BRAIN_SKIP_INSTALL:-0}" != "1" ]]; then
  if command -v cmd.exe >/dev/null && command -v wslpath >/dev/null; then
    win_local=$(cmd.exe /c 'echo %LOCALAPPDATA%' 2>/dev/null | tr -d '\r')
    target=$(wslpath "$win_local")/APEX/brain
    say "Installation dans APEX (Windows) : $win_local\\APEX\\brain\\apex-brain-v2"
    "${BUILD[@]}" install --brain "$BRAIN" --to "$target" || fail "installation"
    say "Terminé. Dans APEX : Paramètres > Moteur > « Cerveau APEX », puis une nouvelle conversation."
  else
    say "Pas sous WSL : Cerveau prêt dans $BRAIN (à copier dans le dossier brain d'APEX)."
  fi
fi
say "Rapport d'évaluation : $BRAIN/evaluation.json"
say "Converser avec lui dans ce terminal : $PY $REPO/scripts/apex_brain_build.py chat --brain $BRAIN"
