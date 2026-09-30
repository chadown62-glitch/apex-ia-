#!/usr/bin/env bash
# WINE E2E — séquence de robustesse de fin de parcours sur un installateur construit : installation ->
# redémarrage de la session Wine (wineserver -k + wineboot -r : ANALOGUE, ce n'est PAS un redémarrage de
# Windows) -> double-clic sur le raccourci -> Panel ouvert -> diagnostic redirigé dans un fichier (UTF-8,
# sans secret) -> fermeture -> réouverture -> désinstallation -> vérification -> réinstallation -> Panel.
# Ce n'est pas une preuve Windows (voir wine_installer_e2e.sh).
#
# Usage : wine_robustness_e2e.sh <APEX-Setup-x64-vX.exe> <dossier de travail>
# Sortie : une ligne « STEP <id> PASS|FAIL <détail> » par étape. Code de sortie : 0 si tout passe.
set -uo pipefail
SETUP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; WORK="$2"
rm -rf "$WORK"; mkdir -p "$WORK"
export WINEPREFIX="$WORK/prefix" WINEARCH=win64 WINEDEBUG=-all APEX_PANEL_RENDERING=software
N=$(( 110 + RANDOM % 9 )); Xvfb ":$N" -screen 0 1280x900x24 -nolisten tcp >/dev/null 2>&1 & XVFB=$!
export DISPLAY=":$N"; trap 'wineserver -k 2>/dev/null; kill $XVFB 2>/dev/null' EXIT; sleep 1
wine cmd /c ver >/dev/null 2>&1
U="$WINEPREFIX/drive_c/users/$(whoami)"; APP="$U/AppData/Local/Programs/APEX"; DESKTOP="$U/Desktop/APEX.lnk"
APPW='C:\users\'"$(whoami)"'\AppData\Local\Programs\APEX\APEX.exe'
panels() { pgrep -f 'Programs\\APEX\\APEX.exe' | wc -l; }
wait_n() { for _ in $(seq 1 40); do [[ "$(panels)" -ge "$1" ]] && return 0; sleep 0.5; done; return 1; }
FAILED=0
step() { local s=PASS; [[ "$2" == 0 ]] || { s=FAIL; FAILED=1; }; echo "STEP $1 $s $3"; }

wine "$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >/dev/null 2>&1; c=$?
step R1_install "$c" "code $c, $(ls "$APP" | wc -l) fichiers"
# « Redémarrage » : fin de la session Wine (tous les processus Wine arrêtés), puis redémarrage du préfixe.
wineserver -k; sleep 1; wine wineboot -r >/dev/null 2>&1; c=$?
step R2_redemarrage_session_wine "$c" "wineserver -k + wineboot -r (analogue, pas Windows)"
t0=$(date +%s.%N); (cd /tmp && wine start "$(winepath -w "$DESKTOP")" >/dev/null 2>&1); wait_n 1; c=$?
step R3_double_clic_apres_redemarrage "$c" "Panel lancé via Bureau\\APEX.lnk en $(python3 -c "print(round($(date +%s.%N)-$t0,1))") s"
sleep 4; [[ "$(panels)" -eq 1 ]]; step R4_panel_reste_ouvert $? "$(panels) Panel après 4 s"
wine "$APPW" --diagnostic > "$WORK/diagnostic.txt" 2>/dev/null; c=$?
diag=$(tr -d '\r' < "$WORK/diagnostic.txt")
iconv -f UTF-8 -t UTF-8 "$WORK/diagnostic.txt" >/dev/null 2>&1 && grep -q "Mesuré le" <<<"$diag" && grep -qi "wsl" <<<"$diag" \
  && ! grep -qiE "bearer [a-z0-9]{8,}|api-token\s*[:=]\s*\S{8,}" <<<"$diag"
step R5_diagnostic $? "code $c, $(wc -l <<<"$diag") lignes, UTF-8 valide, accents lisibles, aucun jeton"
pkill -f 'Programs\\APEX\\APEX.exe'; sleep 2; [[ "$(panels)" -eq 0 ]]; step R6_fermeture $? "0 Panel"
(cd "$HOME" && wine start "$(winepath -w "$DESKTOP")" >/dev/null 2>&1); wait_n 1; step R7_reouverture $? "$(panels) Panel"
pkill -f 'Programs\\APEX\\APEX.exe'; sleep 2
wine "$(winepath -w "$APP/unins000.exe")" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >/dev/null 2>&1; c=$?; sleep 2
[[ "$c" -eq 0 && ! -f "$APP/APEX.exe" && ! -f "$DESKTOP" && -n "$(ls "$U/AppData/Local/APEX/logs" 2>/dev/null)" ]]
step R8_desinstallation_verifiee $? "code $c ; programme et raccourci retirés ; journaux conservés"
wine "$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >/dev/null 2>&1; c=$?
[[ "$c" -eq 0 && -f "$DESKTOP" ]]; step R9_reinstallation $? "code $c"
(cd /tmp && wine start "$(winepath -w "$DESKTOP")" >/dev/null 2>&1); wait_n 1; step R10_panel_apres_reinstallation $? "$(panels) Panel"
pkill -f 'Programs\\APEX\\APEX.exe'
exit "$FAILED"
