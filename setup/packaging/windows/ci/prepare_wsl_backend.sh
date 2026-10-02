#!/usr/bin/env bash
# CI UNIQUEMENT (runner GitHub Windows Server + WSL2) : prépare, DANS la distribution, ce qu'un utilisateur
# a déjà sur son poste avant de lancer APEX — dépôt APEX, Docker, .venv, .env — sans rien changer à APEX
# lui-même. Jamais utilisé sur le PC de l'utilisateur (le script de validation Windows n'installe rien).
#
# Usage (en root dans la distribution) : prepare_wsl_backend.sh <copie du dépôt vue depuis WSL> <utilisateur>
# Résultat : ~<utilisateur>/apex prêt pour `bash scripts/apex-launch.sh serve`, Docker démarré.
# Les mots de passe de .env sont générés ici, jamais affichés.
set -euo pipefail
SRC="$1"; USER_NAME="$2"
log() { echo "[prepare_wsl_backend] $(date -u +%H:%M:%S) $*"; }

export DEBIAN_FRONTEND=noninteractive
log "paquets : docker, compose, python venv"
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 python3-venv python3-pip rsync openssl >/dev/null

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$USER_NAME"
fi
usermod -aG docker "$USER_NAME"

log "/etc/wsl.conf : systemd (Docker en service) et $USER_NAME utilisateur par défaut — comme sur un poste où"
log "l'utilisateur lance launch.sh sous son propre compte (le Panel lit ~ de l'utilisateur par défaut)"
printf '[boot]\nsystemd=true\n\n[user]\ndefault=%s\n' "$USER_NAME" > /etc/wsl.conf

log "démarrage de Docker"
if pidof systemd >/dev/null 2>&1 && systemctl start docker 2>/dev/null; then
  log "docker démarré par systemd"
else
  (dockerd >/var/log/dockerd.log 2>&1 &)
fi
for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
docker info >/dev/null 2>&1 || { log "Docker ne démarre pas"; tail -20 /var/log/dockerd.log 2>/dev/null; exit 1; }

# MinIO est FACULTATIF (profil Compose « minio ») : aucune image MinIO n'est préparée ici, exactement comme
# sur une machine neuve — la validation prouve qu'APEX démarre SANS elle. Le profil est testé à part
# (build_minio_from_source.sh, étape CI dédiée).

HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
DEST="$HOME_DIR/apex"
log "copie du dépôt vers $DEST (sans .venv ni artefacts)"
rsync -a --delete --exclude .venv --exclude node_modules --exclude 'setup/**/bin' --exclude 'setup/**/obj' \
  --exclude .apex --exclude .env "$SRC/" "$DEST/"

log ".env : copie de .env.example avec mots de passe générés (jamais affichés)"
python3 - "$DEST/.env.example" "$DEST/.env" <<'PY'
import re, secrets, sys
src, dst = sys.argv[1:3]
out = []
for line in open(src, encoding="utf-8"):
    m = re.match(r"^([A-Z0-9_]*PASSWORD)=", line)
    out.append(f"{m.group(1)}={secrets.token_hex(16)}\n" if m else line)
open(dst, "w", encoding="utf-8").writelines(out)
PY
chmod 600 "$DEST/.env"

log ".venv : python3 -m venv + pip install -e .[db,api]"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip >/dev/null
"$DEST/.venv/bin/pip" install -q -e "$DEST[db,api]" >/dev/null

chown -R "$USER_NAME:$USER_NAME" "$DEST"

# Interface web : étape d'installation d'un poste (comme .venv), par le script fourni à l'utilisateur. Un échec
# n'arrête pas la préparation : la validation le montrera (C03 : interface non construite = FAIL).
log "interface web : Node.js (paquets Ubuntu) puis scripts/apex-build-ui.sh sous le compte $USER_NAME"
apt-get install -y -qq nodejs npm >/dev/null
if su - "$USER_NAME" -c "cd '$DEST' && bash scripts/apex-build-ui.sh" > /tmp/apex-build-ui.log 2>&1; then
  log "interface construite : $DEST/frontend/dist"
else
  log "interface NON construite (fin du journal ci-dessous)"; tail -15 /tmp/apex-build-ui.log
fi

# Aucune dérogation du Gate (gate.exceptions.yaml) : depuis l'audit du 02/10/2026, `launch.sh serve`
# décide sur `apex gate --startup` — les sondes à consentement non accordées et l'absence de GPU y sont
# « non vérifiés », pas des échecs. La CI prouve ainsi le vrai parcours d'un poste, sans contournement.

log "prêt : $DEST (utilisateur $USER_NAME)"
