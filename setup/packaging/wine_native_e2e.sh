#!/usr/bin/env bash
# Usage : wine_native_e2e.sh <APEX-Setup-x64-vX.exe> <dossier de travail> <dossier python\3.12.10 préparé>
# Scénario NATIF sous Wine (pas Windows) : installateur 0.3.0 réel -> Panel installé en mode natif -> backend
# Windows (python.exe) démarré/arrêté par le Panel -> désinstallation. Aménagement de test DÉCLARÉ : Wine 9
# n'exécute pas torch (ucrtbase.crealf, GetNumaNodeProcessorMaskEx non implémentés) ; le Python préparé (sans
# torch) est donc copié depuis un premier préfixe où « Préparer APEX » a réellement tourné, et son marqueur
# « prêt » est réécrit pour le dossier installé.
set -uo pipefail
SETUP="$1"; WORK="$2"; PREPARED="$3"
rm -rf "$WORK"; mkdir -p "$WORK"
export WINEPREFIX="$WORK/prefix" WINEARCH=win64 WINEDEBUG=-all APEX_PANEL_START_TIMEOUT_S=500
Xvfb :93 -screen 0 1280x900x24 >/dev/null 2>&1 & XVFB=$!
export DISPLAY=:93
trap 'wineserver -k 2>/dev/null; kill $XVFB 2>/dev/null' EXIT
sleep 1
wine cmd /c ver >/dev/null 2>&1
U="$WINEPREFIX/drive_c/users/$(whoami)"; APP="$U/AppData/Local/Programs/APEX"; H="$U/AppData/Local/APEX"
APPW='C:\users\'"$(whoami)"'\AppData\Local\Programs\APEX\APEX.exe'
FAILED=0
ok() { [[ $2 == 0 ]] || FAILED=1; echo "CHECK $1 $([[ $2 == 0 ]] && echo PASS || echo FAIL) $3"; }
verdict() { python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["headline"], d["cause"], d.get("mode"))' "$1" 2>/dev/null; }
wine "$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="C:\\install.log" >/dev/null 2>&1; c=$?
[[ $c == 0 && -f "$APP/app/apex/standalone/__init__.py" && -f "$APP/app/runtime/requirements-windows.lock" ]]
ok N1_installation_avec_application $? "code $c, $(find "$APP/app" -type f | wc -l) fichiers dans app\\"
out=$(wine "$APPW" --status-json 2>/dev/null); v=$(verdict "$out")
[[ "$v" == "APEX NOT READY RuntimeNotPrepared native" ]]; ok N2_premier_lancement_runtime_a_preparer $? "« $v »"
out=$(wine "$APPW" --start 2>/dev/null); v=$(verdict "$out")
[[ "$v" == *RuntimeNotPrepared* ]] && ! ls "$H/.apex/runtime/backend.json" >/dev/null 2>&1
ok N3_demarrage_impossible_sans_runtime $? "« $v », aucun backend lancé"
mkdir -p "$H/python"; cp -r "$PREPARED" "$H/python/3.12.10"
printf '%s\n' "C:\\users\\$(whoami)\\AppData\\Local\\Programs\\APEX\\app" > "$H/python/3.12.10/Lib/site-packages/apex-app.pth"
sha=$(sha256sum "$APP/app/runtime/requirements-windows.lock" | cut -d' ' -f1)
printf '{"python_version":"3.12.10","requirements_sha256":"%s","app_root":"C:\\\\users\\\\%s\\\\AppData\\\\Local\\\\Programs\\\\APEX\\\\app","prepared_at":"2026-10-08T23:00:00+00:00","verified":"WINE-TEST"}' "$sha" "$(whoami)" > "$H/python/3.12.10/apex-runtime.json"
out=$(wine "$APPW" --status-json 2>/dev/null); v=$(verdict "$out")
[[ "$v" == "APEX NOT READY BackendNotStarted native" ]]; ok N4_runtime_pret $? "« $v »"
t0=$(date +%s); out=$(wine "$APPW" --start 2>/dev/null); v=$(verdict "$out")
rows=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print("; ".join(c["key"]+"="+c["status"] for c in d["components"]))' "$out" 2>/dev/null)
[[ "$v" == "APEX DEGRADED PartialServices native" ]] && [[ "$rows" == *"Backend=Ready"* && "$rows" == *"Api=Ready"* && "$rows" == *"Database=Ready"* && "$rows" == *"Ui=Ready"* && "$rows" != *Redis* && "$rows" != *Wsl* ]]
ok N5_demarrage_par_le_panel $? "« $v » en $(( $(date +%s) - t0 )) s : $rows"
url=$(wine "$APPW" --ui-url 2>/dev/null | tr -d '\r'); code=$(curl -s -o /dev/null -w "%{http_code}" "$url")
[[ "$url" == http://127.0.0.1:8000/ui/login?code=* && "$code" == 303 ]]; ok N6_ouverture_interface $? "lien à usage unique -> HTTP $code"
pid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pid"])' "$H/.apex/runtime/backend.json")
out=$(wine "$APPW" --stop 2>/dev/null); v=$(verdict "$out")
[[ "$v" == "APEX NOT READY BackendNotStarted native" && ! -f "$H/.apex/runtime/backend.json" ]] && grep -q "APEX arrêté proprement" "$H/.apex/runtime/launch.log"
ok N7_arret_propre $? "« $v », enregistrement retiré, journal : $(tail -1 "$H/.apex/runtime/launch.log" | cut -c1-80)"
wine "$APPW" --start >/dev/null 2>&1; out=$(wine "$APPW" --status-json 2>/dev/null); v=$(verdict "$out")
[[ "$v" == "APEX DEGRADED PartialServices native" ]]; ok N8_relance $? "« $v »"
wine "C:\\users\\$(whoami)\\AppData\\Local\\Programs\\APEX\\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >/dev/null 2>&1; c=$?
sleep 3
[[ $c == 0 && ! -d "$H/python" && -f "$H/data/apex.db" && ! -f "$H/.apex/runtime/backend.json" ]]
ok N9_desinstallation $? "code $c ; APEX arrêté avant suppression ; runtime supprimé ; base conservée ($(du -h "$H/data/apex.db" 2>/dev/null | cut -f1))"
wineserver -k 2>/dev/null
# Code de sortie = verdict des vérifications (et non celui de `wineserver -k`, non nul s'il n'y a rien à arrêter).
exit $FAILED
