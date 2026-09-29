#!/usr/bin/env bash
# Build de la distribution Windows du Panel APEX : APEX-Setup-x64-v<version>.exe + SHA256 + manifeste
# + journal de build. Réutilise publish_dashboard.sh (publication + contrôles) et ApexSetup.iss.
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
DIRTY=$([[ -n "$(git -C "$REPO_ROOT" status --porcelain -- setup apex scripts)" ]] && echo true || echo false)
log "commit $COMMIT (modifications non commitées dans setup/apex/scripts : $DIRTY)"

# 1. Publication + contrôles (PE, aucun .pdb, aucun chemin de développement)
bash "$SCRIPT_DIR/publish_dashboard.sh" "$OUT/publish"
PUBLISH_DIR="$OUT/publish/apex-dashboard-${VERSION}-win-x64"

# 2. Compilateur
if [[ -n "${ISCC:-}" ]]; then
  ISCC_CMD=("$ISCC"); to_win() { printf '%s' "$1"; }
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
"${ISCC_CMD[@]}" /Qp "/DApexVersion=$VERSION" "/DPublishDir=$(to_win "$PUBLISH_DIR")" \
  "/DIconFile=$(to_win "$SETUP_ROOT/src/ApexSetup.Dashboard/Assets/apex.ico")" \
  "/O$(to_win "$OUT")" "$(to_win "$SCRIPT_DIR/ApexSetup.iss")"
INSTALLER="$OUT/APEX-Setup-x64-v${VERSION}.exe"
[[ -f "$INSTALLER" ]] || { log "ERREUR : $INSTALLER non produit"; exit 1; }
kind=$(file -b "$INSTALLER")
[[ "$kind" == *"PE32"* && "$kind" == *"MS Windows"* ]] || { log "ERREUR : pas un exécutable Windows ($kind)"; exit 1; }
log "installateur : $(basename "$INSTALLER") — $kind"

# 4. Empreintes + manifeste
(cd "$OUT" && sha256sum "$(basename "$INSTALLER")" > "$(basename "$INSTALLER").sha256")
python3 - "$OUT" "$INSTALLER" "$PUBLISH_DIR" "$VERSION" "$PROTOCOL" "$COMMIT" "$DIRTY" "$COMPILER" <<'PY'
import hashlib, json, os, platform, subprocess, sys, datetime
out, installer, publish, version, protocol, commit, dirty, compiler = sys.argv[1:9]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
files = sorted(os.path.relpath(os.path.join(d, n), publish) for d, _, ns in os.walk(publish) for n in ns)
dotnet = subprocess.run(["dotnet", "--version"], capture_output=True, text=True).stdout.strip()
manifest = {
    "product": "APEX Desktop (Panel APEX.exe)",
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
