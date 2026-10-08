#!/usr/bin/env bash
# Régénère requirements-windows.lock : les bibliothèques du runtime Windows d'APEX (python.exe 3.12 win_amd64),
# versions figées et empreintes SHA-256 (pip --require-hashes). Résolution POUR Windows (uv, plateforme
# x86_64-pc-windows-msvc) — pip sous Linux évaluerait les marqueurs de plateforme de Linux.
# Les versions sont alignées sur l'environnement de développement (.venv) quand il existe : le runtime Windows
# exécute les mêmes versions que celles testées ici.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
command -v uv >/dev/null || { echo "uv introuvable (https://docs.astral.sh/uv/)" >&2; exit 1; }
CONSTRAINTS=()
if [[ -x "$REPO/.venv/bin/pip" ]]; then
  tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
  "$REPO/.venv/bin/pip" freeze 2>/dev/null | grep -v '^-e\|@ file' > "$tmp"
  CONSTRAINTS=(--constraint "$tmp")
fi
uv pip compile "$HERE/requirements-windows.in" --python-platform x86_64-pc-windows-msvc --python-version 3.12 \
  --only-binary :all: --generate-hashes --no-header --quiet "${CONSTRAINTS[@]}" -o "$HERE/requirements-windows.lock"
sed -i '1i # Généré par setup/packaging/runtime/lock_runtime.sh depuis requirements-windows.in — ne pas éditer.\n# Installé par le Panel (« Préparer APEX ») : pip install --require-hashes --only-binary=:all: --no-deps -r ce fichier.' \
  "$HERE/requirements-windows.lock"
echo "requirements-windows.lock : $(grep -cE '^[a-z0-9]' "$HERE/requirements-windows.lock") paquets"
