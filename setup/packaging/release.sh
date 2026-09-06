#!/usr/bin/env bash
# PHASE 8 — Release Build Pipeline.
#
# Chaîne : Source -> Tests -> Sécurité -> Build Runtime -> Anti-fuite ->
#          Reproductibilité -> Build Setup -> Hash+Manifeste -> Validation finale.
#
# Règle absolue de cette phase : ne JAMAIS retirer ni contourner les
# mécanismes de release déjà établis pour le Runtime (`runtime_build/`,
# inchangé, invoqué tel quel). Ne JAMAIS déclarer une reproductibilité non
# vérifiée empiriquement.
#
# Usage : setup/packaging/release.sh [répertoire de sortie]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SETUP_ROOT/.." && pwd)"
OUT_ROOT="${1:-$SETUP_ROOT/release}"
rm -rf "$OUT_ROOT"
mkdir -p "$OUT_ROOT"

PY="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

log() { echo "[release.sh] $*"; }
section() { echo; echo "[release.sh] ============ $* ============"; }

# ---------------------------------------------------------------------------
section "1/8 — SOURCE"
# ---------------------------------------------------------------------------
# Provenance RÉELLE, jamais supposée. Deux faits indépendants et tous deux
# nécessaires pour savoir si un `build_commit_sha` a un sens dans le
# manifeste Setup (Phase 7) :
#   (a) le dépôt entier a-t-il des modifications non committées (git status) ?
#   (b) le répertoire `setup/` LUI-MÊME est-il seulement suivi par Git ?
# Constat empirique de cette exécution : `git ls-files setup | wc -l` vaut
# 0 — `setup/` entier est NON SUIVI par Git à ce stade. Attacher
# `build_commit_sha=HEAD` au manifeste de l'artefact Setup serait un
# mensonge de provenance : le commit HEAD ne contient même pas le code
# qui a produit cet artefact. On ne fabrique jamais une traçabilité qui
# n'existe pas (voir docs/RELEASE_MANIFEST.md, "Never invent provenance").
HEAD_SHA=$(git -C "$REPO_ROOT" rev-parse HEAD)
REPO_DIRTY=$([[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]] && echo true || echo false)
SETUP_TRACKED_FILES=$(git -C "$REPO_ROOT" ls-files setup | wc -l | tr -d ' ')

log "HEAD = $HEAD_SHA"
log "dépôt entier sale (git status --porcelain non vide) = $REPO_DIRTY"
log "fichiers de setup/ suivis par Git = $SETUP_TRACKED_FILES"

if [[ "$SETUP_TRACKED_FILES" -eq 0 ]]; then
  SETUP_COMMIT_SHA=""
  log "setup/ est ENTIÈREMENT non committé -> build_commit_sha du manifeste Setup sera OMIS (null), jamais fabriqué"
elif [[ "$REPO_DIRTY" == "true" ]]; then
  SETUP_COMMIT_SHA=""
  log "dépôt sale -> build_commit_sha du manifeste Setup sera OMIS (null) : HEAD ne reflète pas nécessairement le contenu réellement construit"
else
  SETUP_COMMIT_SHA="$HEAD_SHA"
  log "dépôt propre et setup/ suivi -> build_commit_sha = $SETUP_COMMIT_SHA"
fi

# ---------------------------------------------------------------------------
section "2/8 — TESTS"
# ---------------------------------------------------------------------------
log "dotnet test (solution Setup complète : Core/Application/Cli/Gui/Release/Release.Cli)..."
(cd "$SETUP_ROOT" && dotnet test ApexSetup.sln --nologo -v minimal)

log "pytest (suite Runtime Python complète)..."
(cd "$REPO_ROOT" && "$PY" -m pytest -q)

# ---------------------------------------------------------------------------
section "3/8 — SÉCURITÉ (pré-build)"
# ---------------------------------------------------------------------------
"$SCRIPT_DIR/test_security_scan.sh"
"$SCRIPT_DIR/security_scan.sh" source "$SETUP_ROOT/src"
"$SCRIPT_DIR/security_scan.sh" source "$SETUP_ROOT/tests"
"$SCRIPT_DIR/security_scan.sh" source "$SETUP_ROOT/packaging"
"$SCRIPT_DIR/security_scan.sh" source "$SETUP_ROOT/docs"

# ---------------------------------------------------------------------------
section "4/8 — BUILD RUNTIME (mécanisme existant, inchangé)"
# ---------------------------------------------------------------------------
# Invoque runtime_build/ TEL QUEL — aucune modification, aucun contournement.
# Mode "commit" (RELEASE, reproductible par construction : git archive sur
# un SHA résolu) — jamais le répertoire de travail pour un build de release.
RUNTIME_DIST="$OUT_ROOT/runtime"
log "python -m runtime_build.build --source commit --commit $HEAD_SHA --out $RUNTIME_DIST"
(cd "$REPO_ROOT" && "$PY" -m runtime_build.build --source commit --commit "$HEAD_SHA" --out "$RUNTIME_DIST")
RUNTIME_COMMIT_SHA=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['source_commit'])" "$RUNTIME_DIST/RUNTIME_BUILD_MANIFEST.json")
RUNTIME_CONTENT_HASH=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['content_hash_sha256'])" "$RUNTIME_DIST/RUNTIME_BUILD_MANIFEST.json")
log "Runtime construit depuis $RUNTIME_COMMIT_SHA (content_hash_sha256=$RUNTIME_CONTENT_HASH)"

# ---------------------------------------------------------------------------
section "5/8 — ANTI-FUITE"
# ---------------------------------------------------------------------------
log "runtime_build.verify (Runtime : aucun module Research/DEV-only)..."
(cd "$REPO_ROOT" && "$PY" -m runtime_build.verify "$RUNTIME_DIST")

# ---------------------------------------------------------------------------
section "6/8 — REPRODUCTIBILITÉ (vérifiée empiriquement, jamais affirmée)"
# ---------------------------------------------------------------------------
log "Runtime : reconstruction depuis le même commit dans un second répertoire..."
RUNTIME_DIST_2="$OUT_ROOT/runtime-repro-check"
(cd "$REPO_ROOT" && "$PY" -m runtime_build.build --source commit --commit "$HEAD_SHA" --out "$RUNTIME_DIST_2")
RUNTIME_CONTENT_HASH_2=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['content_hash_sha256'])" "$RUNTIME_DIST_2/RUNTIME_BUILD_MANIFEST.json")
if [[ "$RUNTIME_CONTENT_HASH" == "$RUNTIME_CONTENT_HASH_2" ]]; then
  log "REPRODUCTIBLE (Runtime) : content_hash_sha256 identique sur 2 builds indépendants depuis $HEAD_SHA ($RUNTIME_CONTENT_HASH)"
  RUNTIME_REPRODUCIBLE=true
else
  log "ERREUR : Runtime NON reproductible depuis le même commit ($RUNTIME_CONTENT_HASH != $RUNTIME_CONTENT_HASH_2)"
  RUNTIME_REPRODUCIBLE=false
fi
rm -rf "$RUNTIME_DIST_2"

log "Setup (.NET) : reconstruction indépendante pour comparaison bit-à-bit du binaire CLI..."
SETUP_DIST_1="$OUT_ROOT/setup-repro-check-1"
SETUP_DIST_2="$OUT_ROOT/setup-repro-check-2"
"$SCRIPT_DIR/publish.sh" "$SETUP_DIST_1" >/dev/null
"$SCRIPT_DIR/publish.sh" "$SETUP_DIST_2" >/dev/null
CLI_EXE_1=$(find "$SETUP_DIST_1" -name apex-setup.exe | head -1)
CLI_EXE_2=$(find "$SETUP_DIST_2" -name apex-setup.exe | head -1)
CLI_SHA_1=$(sha256sum "$CLI_EXE_1" | cut -d' ' -f1)
CLI_SHA_2=$(sha256sum "$CLI_EXE_2" | cut -d' ' -f1)
if [[ "$CLI_SHA_1" == "$CLI_SHA_2" ]]; then
  log "REPRODUCTIBLE (Setup CLI) : SHA-256 identique sur 2 publications indépendantes ($CLI_SHA_1)"
  SETUP_REPRODUCIBLE=true
else
  log "CONSTAT (pas une erreur) : Setup CLI NON bit-reproductible entre 2 publications ($CLI_SHA_1 != $CLI_SHA_2)."
  log "  Cause connue et documentée : dotnet publish self-contained single-file embarque des métadonnées"
  log "  de build non déterministes (horodatage PE, identifiants internes du bundler) — jamais affirmé"
  log "  reproductible sans cette vérification empirique, voir docs/PACKAGING.md."
  SETUP_REPRODUCIBLE=false
fi
rm -rf "$SETUP_DIST_1" "$SETUP_DIST_2"

# ---------------------------------------------------------------------------
section "7/8 — BUILD SETUP + HASH + MANIFESTE"
# ---------------------------------------------------------------------------
DIST_ROOT="$OUT_ROOT/dist"
"$SCRIPT_DIR/publish.sh" "$DIST_ROOT"
VERSION=$(grep -oP '(?<=<SetupProductVersion>)[^<]+' "$SETUP_ROOT/Directory.Build.props")
DIST_DIR="$DIST_ROOT/apex-setup-${VERSION}-win-x64"

RELEASE_CLI_DLL="$SETUP_ROOT/src/ApexSetup.Release.Cli/bin/Debug/net8.0/apex-setup-release.dll"
if [[ ! -f "$RELEASE_CLI_DLL" ]]; then
  log "apex-setup-release.dll absent, build Debug de ApexSetup.Release.Cli..."
  dotnet build "$SETUP_ROOT/src/ApexSetup.Release.Cli/ApexSetup.Release.Cli.csproj" -c Debug --nologo -v minimal
fi

GEN_ARGS=(--product-name "APEX Setup" --setup-version "$VERSION" --architecture "win-x64" --runtime-version "$RUNTIME_COMMIT_SHA")
if [[ -n "$SETUP_COMMIT_SHA" ]]; then
  GEN_ARGS+=(--commit-sha "$SETUP_COMMIT_SHA")
fi

for component in cli gui launcher; do
  exe=$(find "$DIST_DIR/$component" -maxdepth 1 -name '*.exe' | head -1)
  manifest_path="$DIST_DIR/$component/manifest.json"
  log "manifeste pour $exe ..."
  dotnet "$RELEASE_CLI_DLL" generate --artifact "$exe" --out "$manifest_path" "${GEN_ARGS[@]}"
done

# ---------------------------------------------------------------------------
section "8/8 — VALIDATION FINALE"
# ---------------------------------------------------------------------------
for component in cli gui launcher; do
  manifest_path="$DIST_DIR/$component/manifest.json"
  log "validation du manifeste $component ..."
  dotnet "$RELEASE_CLI_DLL" validate --manifest "$manifest_path" --artifact-dir "$DIST_DIR/$component" \
    --architecture "win-x64" --setup-version "$VERSION"
done

log "sécurité (post-build, sur les binaires publiés)..."
"$SCRIPT_DIR/security_scan.sh" artifact "$DIST_DIR"

log "aucun fichier source Python Runtime dans le paquet Setup..."
if find "$DIST_DIR" -name '*.py' | grep -q .; then
  echo "[release.sh] ERREUR : fichier(s) Python trouvé(s) dans le paquet Setup :" >&2
  find "$DIST_DIR" -name '*.py' >&2
  exit 1
fi
log "vérifié : aucun .py dans $DIST_DIR"

log "aucun fichier de développement inattendu (.git, .vs, appsettings.Development.json)..."
if find "$DIST_DIR" \( -name '.git*' -o -iname 'appsettings.Development.json' -o -iname '*.user' \) | grep -q .; then
  echo "[release.sh] ERREUR : fichier(s) de développement trouvé(s) dans le paquet Setup :" >&2
  find "$DIST_DIR" \( -name '.git*' -o -iname 'appsettings.Development.json' -o -iname '*.user' \) >&2
  exit 1
fi
log "vérifié : aucun fichier de développement inattendu"

section "RÉCAPITULATIF"
log "Setup version         : $VERSION"
log "Setup build_commit_sha: ${SETUP_COMMIT_SHA:-<null — setup/ non committé ou dépôt sale, voir étape 1>}"
log "Runtime commit testé  : $RUNTIME_COMMIT_SHA"
log "Runtime reproductible : $RUNTIME_REPRODUCIBLE"
log "Setup CLI reproductible (bit-à-bit) : $SETUP_REPRODUCIBLE"
log "Distribution           : $DIST_DIR"
log "Pipeline terminée avec succès."
