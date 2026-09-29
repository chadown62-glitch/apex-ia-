#!/usr/bin/env bash
# Génère apex.ico (16 à 256 px) depuis apex.svg — icône de l'exécutable, du raccourci Bureau, de la
# fenêtre et de l'installateur. Nécessite ImageMagick. Résultat commité : le build n'en dépend pas.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/../../src/ApexSetup.Dashboard/Assets/apex.ico"
convert -background none "$HERE/apex.svg" -define icon:auto-resize=256,128,64,48,32,24,16 "$OUT"
echo "icône écrite : $OUT"
