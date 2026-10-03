#!/usr/bin/env bash
# PHASE 6 — Windows packaging infrastructure.
#
# Produit un répertoire de distribution Windows self-contained pour
# ApexSetup.Cli et ApexSetup.Gui — RID win-x64 exclusivement (seule cible
# de production de Setup, voir architecture verrouillée « Windows-native »).
# Chaque exécutable produit est vérifié comme un VRAI binaire PE Windows
# (`file` doit rapporter "PE32+ ... for MS Windows") — jamais une
# affirmation non vérifiée.
#
# Self-contained : choix délibéré — Setup s'adresse à des machines qui
# n'ont potentiellement AUCUN runtime .NET installé ; exiger .NET
# pré-installé pour lancer un installeur serait absurde.
#
# AUCUNE signature n'est appliquée ici (aucun certificat disponible dans
# cet environnement — voir docs/PACKAGING.md). Ce script prépare
# uniquement la structure que `signtool` pourrait signer plus tard.
#
# Usage : setup/packaging/publish.sh [chemin de sortie]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ROOT="${1:-$SETUP_ROOT/dist}"

VERSION=$(grep -oP '(?<=<SetupProductVersion>)[^<]+' "$SETUP_ROOT/Directory.Build.props")
if [[ -z "$VERSION" ]]; then
  echo "[publish.sh] ERREUR : impossible de lire SetupProductVersion depuis Directory.Build.props" >&2
  exit 1
fi

DIST_DIR="$OUT_ROOT/apex-setup-${VERSION}-win-x64"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR/cli" "$DIST_DIR/gui"

echo "[publish.sh] Version : $VERSION"
echo "[publish.sh] Publication ApexSetup.Cli (win-x64, self-contained, fichier unique)..."
dotnet publish "$SETUP_ROOT/src/ApexSetup.Cli/ApexSetup.Cli.csproj" \
  -c Release -r win-x64 --self-contained true \
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true \
  -o "$DIST_DIR/cli"

echo "[publish.sh] Publication ApexSetup.Gui (win-x64, self-contained)..."
dotnet publish "$SETUP_ROOT/src/ApexSetup.Gui/ApexSetup.Gui.csproj" \
  -c Release -r win-x64 --self-contained true \
  -o "$DIST_DIR/gui"

# ApexSetup.Launcher n'est PLUS publié (audit « première boucle d'exécution réelle », 03/10/2026) : il
# décide sur `GateSummary.Phase2Authorized` et refuserait donc de démarrer toute machine dont la phase 2
# n'est pas autorisée — l'état normal (le démarrage relève de `startup_authorized`, docs/PHASE_1_GATE.md
# §10). Le seul lanceur livré est le Panel `APEX.exe` (publish_dashboard.sh -> launch.sh). Source et
# tests restent dans la solution, à titre historique (setup/docs/LAUNCHER_ARCHITECTURE.md).

# Vérification empirique — jamais une simple confiance dans le succès de
# `dotnet publish` : le fichier produit est-il RÉELLEMENT un exécutable
# Windows valide ?
for exe in "$DIST_DIR/cli/apex-setup.exe" "$DIST_DIR/gui/apex-setup-gui.exe"; do
  if [[ ! -f "$exe" ]]; then
    echo "[publish.sh] ERREUR : $exe n'a pas été produit" >&2
    exit 1
  fi
  kind=$(file -b "$exe")
  if [[ "$kind" != *"PE32+"* || "$kind" != *"MS Windows"* ]]; then
    echo "[publish.sh] ERREUR : $exe n'est pas un exécutable PE Windows valide ($kind)" >&2
    exit 1
  fi
  echo "[publish.sh] Vérifié : $exe -> $kind"

  # RÉGRESSION (trouvée en Phase 6 : un build Release embarquait le chemin
  # absolu du PDB de la machine de développement dans le binaire distribué —
  # voir Directory.Build.props, DebugType=none) — revérifié à CHAQUE
  # publication, jamais supposé résolu une fois pour toutes.
  if command -v strings >/dev/null 2>&1; then
    if strings "$exe" | grep -qE "$SETUP_ROOT|/home/[a-zA-Z0-9._-]+/|/root/"; then
      echo "[publish.sh] ERREUR : $exe embarque un chemin absolu de développement :" >&2
      strings "$exe" | grep -E "$SETUP_ROOT|/home/[a-zA-Z0-9._-]+/|/root/" >&2
      exit 1
    fi
    echo "[publish.sh] Vérifié : $exe ne contient aucun chemin absolu de développement"
  fi
done

# Aucun .pdb ne doit être distribué (DebugType=none) — vérifié directement,
# jamais seulement supposé depuis la configuration du projet.
if find "$DIST_DIR" -name "*.pdb" | grep -q .; then
  echo "[publish.sh] ERREUR : des fichiers .pdb ont été inclus dans la distribution :" >&2
  find "$DIST_DIR" -name "*.pdb" >&2
  exit 1
fi
echo "[publish.sh] Vérifié : aucun .pdb dans la distribution"

echo "[publish.sh] Distribution prête : $DIST_DIR"
echo "[publish.sh] AUCUNE signature appliquée — voir docs/PACKAGING.md."
