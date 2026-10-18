#!/usr/bin/env bash
# CI UNIQUEMENT, après les étapes qui se servent des modèles : rend la place prise dans la distribution WSL avant
# une étape qui écrit beaucoup (la construction de MinIO). La distribution vit sur le disque C: du runner.
# Mesuré (runs 39150876458 et 39155790253) : « There is not enough space on the disk » pendant la construction,
# SIGBUS de Go, puis `getpwnam(root) failed` : WSL ne lisait plus son propre /etc/passwd. Les modèles Ollama du
# mode « référence » (prepare_real_model.sh : qwen2.5-coder 7B, gemma4 12B, bge-m3) en sont la plus grosse part.
# Rien de ce qui est retiré ici ne sert aux étapes suivantes. Jamais exécuté chez l'utilisateur.
set -uo pipefail
used() { df -BM --output=used / | tail -1 | tr -dc 0-9; }
before=$(used)
for dir in /usr/share/ollama/.ollama/models /root/.ollama/models /home/apex/.ollama/models \
           /root/.cache/pip /home/apex/.cache/pip /root/.cache/huggingface /home/apex/.cache/huggingface; do
  [ -d "$dir" ] || continue
  echo "[free_wsl_space] $dir : $(du -sh "$dir" 2>/dev/null | cut -f1) retirés"
  rm -rf "$dir"
done
docker builder prune -af >/dev/null 2>&1 || true
# Rend aussi les blocs libérés au disque de Windows quand la distribution le permet (VHD clairsemé).
fstrim -v / 2>&1 || true
echo "[free_wsl_space] libéré : $(( before - $(used) )) Mo ; reste $(df -h --output=avail / | tail -1 | tr -d ' ')"
