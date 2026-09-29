#!/usr/bin/env bash
# N+30 — publication win-x64 autonome (multi-fichiers) d'ApexSetup.Dashboard (APEX.exe, le Panel).
#
# Script SÉPARÉ de publish.sh (Cli/Gui/Launcher) plutôt qu'une extension de
# ce dernier : publish.sh est déjà testé/documenté pour exactement ces
# trois exécutables (voir docs/PACKAGING.md) — un quatrième export ajouté
# à un script existant qui fonctionne aurait été une modification non
# nécessaire d'une partie qui marche déjà (règle explicite de la mission
# N+30, §23). Même discipline de vérification empirique que publish.sh :
# chaque binaire produit est contrôlé comme un VRAI PE32+ Windows, jamais
# une simple confiance dans le succès de `dotnet publish`.
#
# Usage : setup/packaging/publish_dashboard.sh [chemin de sortie]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ROOT="${1:-$SETUP_ROOT/dist}"

VERSION=$(grep -oP '(?<=<SetupProductVersion>)[^<]+' "$SETUP_ROOT/Directory.Build.props")
if [[ -z "$VERSION" ]]; then
  echo "[publish_dashboard.sh] ERREUR : impossible de lire SetupProductVersion depuis Directory.Build.props" >&2
  exit 1
fi

DIST_DIR="$OUT_ROOT/apex-dashboard-${VERSION}-win-x64"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

echo "[publish_dashboard.sh] Version : $VERSION"
# MULTI-FICHIERS (plus un fichier unique) : mesuré — le fichier unique ne démarre pas sous Wine
# (CoreCLR ne projette pas System.Private.CoreLib depuis le bundle, 0x8007046C) alors que le
# multi-fichiers démarre ; l'installateur copie de toute façon un dossier, et un démarrage sans
# extraction est plus rapide. Avalonia recommande aussi cette forme (DLL natives Skia/ANGLE).
echo "[publish_dashboard.sh] Publication ApexSetup.Dashboard (win-x64, autonome, multi-fichiers, APEX.exe)..."
dotnet publish "$SETUP_ROOT/src/ApexSetup.Dashboard/ApexSetup.Dashboard.csproj" \
  -c Release -r win-x64 --self-contained true \
  -p:PublishSingleFile=false \
  -o "$DIST_DIR"

EXE="$DIST_DIR/APEX.exe"
if [[ ! -f "$EXE" ]]; then
  echo "[publish_dashboard.sh] ERREUR : $EXE n'a pas été produit" >&2
  exit 1
fi

kind=$(file -b "$EXE")
if [[ "$kind" != *"PE32+"* || "$kind" != *"MS Windows"* ]]; then
  echo "[publish_dashboard.sh] ERREUR : $EXE n'est pas un exécutable PE Windows valide ($kind)" >&2
  exit 1
fi
echo "[publish_dashboard.sh] Vérifié : $EXE -> $kind"

if command -v strings >/dev/null 2>&1; then
  # BUG RÉEL trouvé par revue (RELEASE LOCK 2/2) : ce script tourne sous
  # `set -o pipefail` (voir l'en-tête). `strings "$EXE" | grep -qE ...`
  # avec `-q` fait quitter `grep` dès la première correspondance trouvée —
  # `strings`, dont la sortie n'est alors plus lue, reçoit SIGPIPE et
  # termine avec un statut non nul. Sous `pipefail`, le statut du pipeline
  # devient celui de CE `strings` mort par SIGPIPE (non nul) plutôt que
  # celui de `grep` (0 = trouvé) — `if pipeline; then` évalue alors la
  # condition comme FAUSSE alors qu'une correspondance existe réellement :
  # le contrôle « aucun chemin de développement » passait silencieusement
  # même en présence d'une vraie correspondance (vérifié empiriquement).
  # Capturer la sortie de `strings` dans une variable puis grep via un
  # here-string (`<<<`, pas un pipe `|`) élimine tout risque de SIGPIPE :
  # un here-string écrit le contenu déjà entièrement disponible avant
  # d'invoquer `grep`, aucun processus producteur concurrent ne peut donc
  # recevoir SIGPIPE quand `grep -q` s'arrête à la première correspondance.
  #
  # Le motif est aussi resserré à $SETUP_ROOT et $HOME (la VRAIE machine de
  # build) : l'ancien `/home/[a-zA-Z0-9._-]+/` générique matchait aussi des
  # chemins PDB tiers légitimement embarqués par des dépendances NuGet
  # d'Avalonia elles-mêmes compilées ailleurs (ex. MicroCom.Runtime,
  # Tmds.DBus) — un faux positif permanent, sans rapport avec une fuite du
  # chemin de CE dépôt ou de CETTE machine.
  # Tous les binaires compilés depuis CE dépôt (l'exécutable et les assemblies APEX/ApexSetup).
  for built in "$EXE" "$DIST_DIR"/APEX.dll "$DIST_DIR"/ApexSetup.*.dll; do
    [[ -f "$built" ]] || continue
    strings_output=$(strings "$built")
    if grep -qE "$SETUP_ROOT|$HOME/|/root/" <<< "$strings_output"; then
      echo "[publish_dashboard.sh] ERREUR : $built embarque un chemin absolu de développement :" >&2
      grep -E "$SETUP_ROOT|$HOME/|/root/" <<< "$strings_output" >&2
      exit 1
    fi
    echo "[publish_dashboard.sh] Vérifié : $(basename "$built") ne contient aucun chemin absolu de développement"
  done
fi

if find "$DIST_DIR" -name "*.pdb" | grep -q .; then
  echo "[publish_dashboard.sh] ERREUR : des fichiers .pdb ont été inclus dans la distribution :" >&2
  find "$DIST_DIR" -name "*.pdb" >&2
  exit 1
fi
echo "[publish_dashboard.sh] Vérifié : aucun .pdb dans la distribution"

echo "[publish_dashboard.sh] Distribution prête : $DIST_DIR"
echo "[publish_dashboard.sh] AUCUNE signature appliquée — voir docs/PACKAGING.md."
