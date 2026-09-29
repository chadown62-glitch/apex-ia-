#!/usr/bin/env bash
# Récupère Inno Setup 6.4.3 (compilateur ISCC.exe) depuis NuGet — version et empreinte FIGÉES :
# un build reproductible utilise exactement le même compilateur. Sous Linux, ISCC s'exécute avec
# Wine (paquets wine64 + wine32). Usage : fetch_innosetup.sh <dossier de destination>
set -euo pipefail

VERSION="6.4.3"
SHA256="9d67a2b1155ca5bc825475ceb907aa357c5feb42e474cfd5347eddeef5ad92a5"
DEST="${1:?usage: fetch_innosetup.sh <dossier>}"
URL="https://api.nuget.org/v3-flatcontainer/tools.innosetup/${VERSION}/tools.innosetup.${VERSION}.nupkg"

mkdir -p "$DEST"
pkg="$DEST/tools.innosetup.${VERSION}.nupkg"
if [[ ! -f "$pkg" ]]; then
  curl -fsSL -o "$pkg.tmp" "$URL"
  mv "$pkg.tmp" "$pkg"
fi
actual=$(sha256sum "$pkg" | cut -d' ' -f1)
if [[ "$actual" != "$SHA256" ]]; then
  echo "[fetch_innosetup.sh] ERREUR : empreinte inattendue ($actual) — paquet refusé" >&2
  rm -f "$pkg"
  exit 1
fi
rm -rf "$DEST/innosetup"
mkdir -p "$DEST/innosetup"
python3 -m zipfile -e "$pkg" "$DEST/innosetup"
echo "$DEST/innosetup/tools/ISCC.exe"
