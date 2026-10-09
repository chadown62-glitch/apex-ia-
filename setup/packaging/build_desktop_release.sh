#!/usr/bin/env bash
# Build de la distribution Windows d'APEX : APEX-Setup-x64-v<version>.exe + SHA256 + manifeste + journal de
# build. Réutilise publish_dashboard.sh (Panel : publication + contrôles), runtime_build (application APEX :
# artefact Runtime, sans l'extension Recherche, vérifié) et ApexSetup.iss.
#
# Usage : setup/packaging/build_desktop_release.sh [dossier de sortie]   (défaut : setup/dist/desktop)
# Compilateur : $ISCC s'il est défini (ex. "C:/Program Files (x86)/Inno Setup 6/ISCC.exe" sous
# Windows) ; sinon Inno Setup 6.4.3 figé (fetch_innosetup.sh) exécuté par Wine (Linux).
# Aucune signature (aucun certificat) : le manifeste le dit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SETUP_ROOT/.." && pwd)"
OUT="${1:-$SETUP_ROOT/dist/desktop}"

VERSION=$(grep -oP '(?<=<SetupProductVersion>)[^<]+' "$SETUP_ROOT/Directory.Build.props")
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version illisible : $VERSION" >&2; exit 1; }
PROTOCOL=$(grep -oP '(?<=public const int SupportedProtocol = )[0-9]+' "$SETUP_ROOT/src/ApexSetup.Dashboard/Services/StatusEngine.cs")

rm -rf "$OUT"
mkdir -p "$OUT"
LOG="$OUT/build.log"
exec > >(tee -a "$LOG") 2>&1
log() { echo "[build_desktop_release] $(date -u +%H:%M:%S) $*"; }
started=$(date +%s)

log "APEX Desktop $VERSION (protocole Panel $PROTOCOL)"
COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
DIRTY=$([[ -n "$(git -C "$REPO_ROOT" status --porcelain -- setup apex scripts runtime_build migrations frontend brain config.standalone.yaml alembic.ini)" ]] && echo true || echo false)
log "commit $COMMIT (modifications non commitées dans setup/apex/scripts : $DIRTY)"

# 1. Publication + contrôles (PE, aucun .pdb, aucun chemin de développement)
bash "$SCRIPT_DIR/publish_dashboard.sh" "$OUT/publish"
PUBLISH_DIR="$OUT/publish/apex-dashboard-${VERSION}-win-x64"
# Reproductibilité : l'installateur enregistre la date de chaque fichier ; un build propre donne des
# dates neuves. Toutes les dates sont ramenées à celle du commit (convention SOURCE_DATE_EPOCH).
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$REPO_ROOT" log -1 --format=%ct)}"
find "$PUBLISH_DIR" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
log "dates des fichiers publiés ramenées à SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH"

# 1b. Application APEX (mode Windows natif du Panel) : l'artefact APEX Runtime de CE commit (git archive ; arbre
#     de travail seulement s'il a des modifications non commitées — le manifeste le dit), vérifié : aucune fuite
#     de l'extension Recherche, aucun module importé absent. Plus l'interface web reconstruite (frontend/dist
#     n'est pas versionné) et la description figée du runtime (Python par empreinte, bibliothèques par empreintes).
PY="$REPO_ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
APP_DIR="$OUT/app"
if [[ "$DIRTY" == "true" ]]; then RT_SOURCE=(--source worktree); else RT_SOURCE=(--source commit --commit "$COMMIT"); fi
(cd "$REPO_ROOT" && "$PY" -m runtime_build.build "${RT_SOURCE[@]}" --out "$APP_DIR")
log "interface web : npm ci + npm run build (frontend/)"
(cd "$REPO_ROOT/frontend" && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent) >/dev/null
rm -rf "$APP_DIR/frontend_dist"
cp -r "$REPO_ROOT/frontend/dist" "$APP_DIR/frontend_dist"
[[ -f "$APP_DIR/frontend_dist/index.html" ]] || { log "ERREUR : interface web non construite"; exit 1; }
mkdir -p "$APP_DIR/runtime"
cp "$SCRIPT_DIR/runtime/python-runtime.json" "$SCRIPT_DIR/runtime/requirements-windows.lock" "$APP_DIR/runtime/"
(cd "$REPO_ROOT" && "$PY" -m runtime_build.verify "$APP_DIR") || { log "ERREUR : artefact Runtime refusé par runtime_build.verify"; exit 1; }
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} +
find "$APP_DIR" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
log "application APEX : $(du -sh "$APP_DIR" | cut -f1) ($(find "$APP_DIR" -type f | wc -l) fichiers), artefact $(python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print('release ' + m['source_commit'][:12] if m.get('release') else 'VALIDATION (arbre de travail)')" "$APP_DIR/RUNTIME_BUILD_MANIFEST.json")"

# 2. Compilateur
if [[ -n "${ISCC:-}" ]]; then
  ISCC_CMD=("$ISCC"); to_win() { printf '%s' "$1"; }
  if command -v cygpath >/dev/null 2>&1; then
    # Windows (Git Bash) : chemins Windows pour ISCC.exe, et aucune conversion automatique MSYS des
    # arguments « /Qp », « /DPublishDir=… » (constaté sur le runner Windows : sans cela, les chemins
    # /d/a/… arrivaient tels quels à ISCC).
    to_win() { cygpath -w "$1"; }
    export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
  fi
  COMPILER="ISCC fourni ($ISCC)"
else
  TOOLS="$SETUP_ROOT/dist/toolchain"
  ISCC_EXE=$(bash "$SCRIPT_DIR/fetch_innosetup.sh" "$TOOLS" | tail -1)
  export WINEPREFIX="${WINEPREFIX:-$SETUP_ROOT/dist/wineprefix}" WINEDEBUG=-all
  [[ -f "$WINEPREFIX/system.reg" ]] || WINEARCH=win64 wineboot -i >/dev/null 2>&1
  ISCC_CMD=(wine "$ISCC_EXE"); to_win() { winepath -w "$1" 2>/dev/null; }
  COMPILER="Inno Setup 6.4.3 (NuGet Tools.InnoSetup, empreinte figée) sous $(wine --version)"
fi
log "compilateur : $COMPILER"

# 3. Installateur
"${ISCC_CMD[@]}" /Qp "/DApexVersion=$VERSION" "/DPublishDir=$(to_win "$PUBLISH_DIR")" "/DAppDir=$(to_win "$APP_DIR")" \
  "/DIconFile=$(to_win "$SETUP_ROOT/src/ApexSetup.Dashboard/Assets/apex.ico")" \
  "/O$(to_win "$OUT")" "$(to_win "$SCRIPT_DIR/ApexSetup.iss")"
INSTALLER="$OUT/APEX-Setup-x64-v${VERSION}.exe"
[[ -f "$INSTALLER" ]] || { log "ERREUR : $INSTALLER non produit"; exit 1; }
kind=$(file -b "$INSTALLER")
[[ "$kind" == *"PE32"* && "$kind" == *"MS Windows"* ]] || { log "ERREUR : pas un exécutable Windows ($kind)"; exit 1; }
log "installateur : $(basename "$INSTALLER") — $kind"

# 4. Empreintes + manifeste
(cd "$OUT" && sha256sum "$(basename "$INSTALLER")" > "$(basename "$INSTALLER").sha256")
python3 - "$OUT" "$INSTALLER" "$PUBLISH_DIR" "$VERSION" "$PROTOCOL" "$COMMIT" "$DIRTY" "$COMPILER" "$APP_DIR" <<'PY'
import hashlib, json, os, platform, subprocess, sys, datetime
out, installer, publish, version, protocol, commit, dirty, compiler, app = sys.argv[1:10]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
files = sorted(os.path.relpath(os.path.join(d, n), publish) for d, _, ns in os.walk(publish) for n in ns)
dotnet = subprocess.run(["dotnet", "--version"], capture_output=True, text=True).stdout.strip()
manifest = {
    "product": "APEX (APEX.exe : Panel + application APEX autonome)",
    "version": version,
    "panel_protocol": int(protocol),
    "architecture": "win-x64",
    "installer": {"file": os.path.basename(installer), "sha256": sha(installer), "bytes": os.path.getsize(installer)},
    "panel": {
        "executable": {"file": "APEX.exe", "sha256": sha(os.path.join(publish, "APEX.exe"))},
        "assembly": {"file": "APEX.dll", "sha256": sha(os.path.join(publish, "APEX.dll"))},
        "files": len(files),
        "bytes": sum(os.path.getsize(os.path.join(publish, f)) for f in files),
    },
    "application": {
        "runtime_artifact": {k: json.load(open(os.path.join(app, "RUNTIME_BUILD_MANIFEST.json"))).get(k)
                             for k in ("release", "source", "source_commit", "head_commit", "content_hash_sha256")},
        "python_runtime": json.load(open(os.path.join(app, "runtime", "python-runtime.json")))["python"],
        "requirements_lock_sha256": sha(os.path.join(app, "runtime", "requirements-windows.lock")),
        # Cerveau APEX servi (0.4.1) : version promue du registre, poids vérifiés à chaque chargement.
        "brain": {k: json.load(open(os.path.join(app, "brain", "apex-brain-v1", "provenance.json"))).get(k)
                  for k in ("name", "weights_sha256", "model_fingerprint", "parameters")}
                 if os.path.isfile(os.path.join(app, "brain", "apex-brain-v1", "provenance.json")) else None,
        "files": sum(len(ns) for _, _, ns in os.walk(app)),
    },
    "install": {"scope": "utilisateur courant, sans administrateur", "directory": "%LOCALAPPDATA%\\Programs\\APEX",
                "shortcuts": ["Bureau\\APEX.lnk", "Menu Démarrer\\APEX.lnk"]},
    "build": {
        "commit": None if dirty == "true" else commit,
        "commit_of_worktree": commit,
        "uncommitted_changes": dirty == "true",
        "dotnet_sdk": dotnet,
        "compiler": compiler,
        "host": platform.platform(),
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    },
    "signed": False,
}
with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
print(json.dumps({k: manifest[k] for k in ("version", "installer")}, ensure_ascii=False))
PY
log "terminé en $(( $(date +%s) - started )) s — $OUT"
