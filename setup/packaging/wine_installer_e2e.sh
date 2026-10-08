#!/usr/bin/env bash
# WINE E2E du cycle de vie de l'installateur — binaire Windows RÉEL exécuté sous Wine, dans un préfixe
# neuf. Ce n'est PAS une preuve Windows : Wine n'a ni WSL, ni le vrai shell (raccourcis .lnk créés et
# lancés par Wine), ni SmartScreen, ni redémarrage. Ce qui est vérifié : l'installateur et le Panel
# eux-mêmes (fichiers, raccourcis, désinstallation, conservation des données, mise à jour,
# interruption, instance unique, plantage).
#
# Usage : wine_installer_e2e.sh <APEX-Setup-x64-vX.exe> <dossier de travail> [installateur de mise à jour]
# Sortie : une ligne « CHECK <id> PASS|FAIL <détail> » par vérification, puis <travail>/results.json.
# Code de sortie : 0 si toutes les vérifications passent, 1 sinon.
set -uo pipefail

SETUP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
WORK="$2"
UPGRADE="${3:-}"
[[ -n "$UPGRADE" ]] && UPGRADE="$(cd "$(dirname "$UPGRADE")" && pwd)/$(basename "$UPGRADE")"

rm -rf "$WORK"; mkdir -p "$WORK"
export WINEPREFIX="$WORK/prefix" WINEARCH=win64 WINEDEBUG=-all
# Rendu logiciel : sous Xvfb, le rendu ANGLE/Direct3D de Wine est noir (sans effet sur le verdict).
export APEX_PANEL_RENDERING=software
DISPLAY_NUM=$(( 90 + RANDOM % 9 ))
Xvfb ":$DISPLAY_NUM" -screen 0 1280x900x24 >/dev/null 2>&1 &
XVFB=$!
export DISPLAY=":$DISPLAY_NUM"
trap 'wineserver -k 2>/dev/null; kill $XVFB 2>/dev/null' EXIT
sleep 1
wine cmd /c ver >/dev/null 2>&1   # crée le préfixe (wineboot seul échoue ici, constaté)

RESULTS="$WORK/results.jsonl"; : > "$RESULTS"
FAILED=0
check() { # id, 0|1, détail
  local status=PASS; [[ "$2" == 0 ]] || { status=FAIL; FAILED=1; }
  echo "CHECK $1 $status $3"
  python3 -c 'import json,sys; print(json.dumps({"id":sys.argv[1],"status":sys.argv[2],"detail":sys.argv[3]}, ensure_ascii=False))' "$1" "$status" "$3" >> "$RESULTS"
}
U="$WINEPREFIX/drive_c/users/$(whoami)"
APP="$U/AppData/Local/Programs/APEX"
APPW='C:\users\'"$(whoami)"'\AppData\Local\Programs\APEX\APEX.exe'
DESKTOP="$U/Desktop/APEX.lnk"
STARTMENU="$U/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/APEX.lnk"
SETTINGS="$U/AppData/Roaming/APEX"
LOGS="$U/AppData/Local/APEX/logs"
UNINSTALL_KEY='6C6B2B0E-3E0C-4E2E-9D6A-3B1E7B7B6B10'

install() { # installateur, journal -> code de sortie
  wine "$1" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/LOG=$(winepath -w "$2")" >/dev/null 2>&1
}
panels() { pgrep -f 'Programs\\APEX\\APEX.exe' | wc -l; }
launch_shortcut() { (cd /tmp && wine start "$(winepath -w "$DESKTOP")" >/dev/null 2>&1); }
wait_panels() { local n; for _ in $(seq 1 40); do n=$(panels); [[ "$n" -ge "$1" ]] && return 0; sleep 0.5; done; return 1; }
reg_has_key() { wineserver -w; grep -q "$UNINSTALL_KEY" "$WINEPREFIX/user.reg"; }

# --- W1-W6 installation vierge ------------------------------------------------
t0=$(date +%s.%N); install "$SETUP" "$WORK/install1.log"; code=$?
dt=$(python3 -c "print(round($(date +%s.%N)-$t0,1))")
check W1_install_vierge "$code" "code $code en ${dt}s"
[[ -f "$APP/APEX.exe" ]]; check W2_fichiers_installes $? "$(ls "$APP" 2>/dev/null | wc -l) fichiers dans %LOCALAPPDATA%\\Programs\\APEX"
[[ -f "$DESKTOP" ]] && strings "$DESKTOP" | grep -qF 'AppData\Local\Programs\APEX\APEX.exe'
check W3_raccourci_bureau $? "Bureau\\APEX.lnk -> $(strings "$DESKTOP" 2>/dev/null | grep -F 'APEX.exe' | tail -1)"
[[ -f "$STARTMENU" ]]; check W4_menu_demarrer $? "Menu Démarrer\\APEX.lnk"
reg_has_key; check W5_entree_desinstallation $? "HKCU\\...\\Uninstall\\{$UNINSTALL_KEY}_is1"
grep -q "APEX: validation OK" "$WORK/install1.log"; check W6_validation_post_installation $? "APEX.exe --version exécuté par l'installateur"
python3 -c "print(round($dt,1))" > "$WORK/install_seconds"

# --- W7-W10 usage : raccourci, instance unique, verdict, installation pendant l'exécution -------------
t0=$(date +%s.%N); launch_shortcut; wait_panels 1; ok=$?
check W7_double_clic_raccourci_autre_repertoire "$ok" "Panel démarré depuis /tmp via le raccourci en $(python3 -c "print(round($(date +%s.%N)-$t0,1))")s"
sleep 6; launch_shortcut; sleep 6
[[ "$(panels)" -eq 1 ]]; check W8_second_double_clic_instance_unique $? "$(panels) Panel(s) après un second double-clic"
out=$(wine "$APPW" --status-json 2>/dev/null); code=$?
verdict=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["headline"], d["cause"], d.get("mode"))' "$out" 2>/dev/null)
# 0.3.0 : l'application APEX est installée à côté du Panel (app\) -> mode Windows natif, sans WSL ; tant que
# « Préparer APEX » n'a pas été fait, le seul verdict honnête est « runtime non préparé » (jamais « WSL absent »).
if [[ -f "$APP/app/apex/standalone/__init__.py" ]]; then expected="APEX NOT READY RuntimeNotPrepared native"
else expected="APEX NOT READY WslNotInstalled wsl"; fi
[[ "$code" -eq 2 && "$verdict" == "$expected" ]]
check W9_verdict_au_premier_lancement $? "code $code, « $verdict » (attendu : « $expected »)"
install "$SETUP" "$WORK/install_running.log"; code=$?
[[ "$code" -ne 0 && "$(panels)" -eq 1 ]]
check W10_installation_refusee_pendant_execution $? "code $code, Panel toujours en cours : $(panels)"

# --- W11-W12 plantage du Panel, relance, données utilisateur ---------------------------------------
pkill -9 -f 'Programs\\APEX\\APEX.exe'; sleep 2
launch_shortcut; wait_panels 1; ok=$?
check W11_relance_apres_plantage "$ok" "kill -9 du Panel puis double-clic : $(panels) Panel(s)"
[[ -d "$LOGS" ]] && ls "$LOGS"/panel-*.log >/dev/null 2>&1
check W12_journal_utilisateur $? "$(ls "$LOGS" 2>/dev/null | tr '\n' ' ')"
mkdir -p "$SETTINGS"; printf '{"WslDistro":"Ubuntu"}' > "$SETTINGS/dashboard-settings.json"
pkill -f 'Programs\\APEX\\APEX.exe'; sleep 2; pkill -9 -f 'Programs\\APEX\\APEX.exe'

# --- W13 réinstallation (même version) -----------------------------------------------------------
install "$SETUP" "$WORK/install2.log"; code=$?
[[ "$code" -eq 0 ]] && grep -q "APEX: validation OK" "$WORK/install2.log"
check W13_reinstallation "$?" "code $code"

# --- W14 mise à jour -----------------------------------------------------------------------------
if [[ -n "$UPGRADE" ]]; then
  before=$(wine "$APPW" --version 2>/dev/null | tr -d '\r')
  install "$UPGRADE" "$WORK/upgrade.log"; code=$?
  after=$(wine "$APPW" --version 2>/dev/null | tr -d '\r')
  [[ "$code" -eq 0 && "$after" != "$before" && -f "$DESKTOP" && -f "$SETTINGS/dashboard-settings.json" ]]
  check W14_mise_a_jour "$?" "$before -> $after, raccourci et paramètres conservés"
fi

# --- W15-W16 désinstallation et politique de conservation ----------------------------------------
wine "$(winepath -w "$APP/unins000.exe")" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >/dev/null 2>&1; code=$?
sleep 2
[[ "$code" -eq 0 && ! -f "$APP/APEX.exe" && ! -f "$DESKTOP" && ! -f "$STARTMENU" ]] && ! reg_has_key
check W15_desinstallation "$?" "code $code ; programme, raccourcis et entrée retirés"
[[ -f "$SETTINGS/dashboard-settings.json" && -d "$LOGS" ]]
check W16_donnees_utilisateur_conservees $? "paramètres et journaux conservés après désinstallation"

# --- W17 réinstallation après désinstallation ----------------------------------------------------
install "$SETUP" "$WORK/install3.log"; code=$?
[[ "$code" -eq 0 && -f "$DESKTOP" ]]; check W17_reinstallation_apres_desinstallation $? "code $code"

# --- W18 installation interrompue puis relancée --------------------------------------------------
wine "$(winepath -w "$APP/unins000.exe")" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >/dev/null 2>&1; sleep 2
wine "$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >/dev/null 2>&1 &
sleep 2.5; wineserver -k 2>/dev/null; sleep 1
partial=$(ls "$APP" 2>/dev/null | wc -l)
install "$SETUP" "$WORK/install_after_interrupt.log"; code=$?
[[ "$code" -eq 0 ]] && grep -q "APEX: validation OK" "$WORK/install_after_interrupt.log"
check W18_installation_interrompue_puis_relancee "$?" "interrompue avec $partial fichier(s) copiés ; relance : code $code"

python3 - "$RESULTS" "$WORK/results.json" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
json.dump({"checks": rows, "passed": sum(r["status"] == "PASS" for r in rows), "total": len(rows)},
          open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
echo "RESUME $(grep -c '"PASS"' "$RESULTS")/$(wc -l < "$RESULTS") vérifications"
exit "$FAILED"
