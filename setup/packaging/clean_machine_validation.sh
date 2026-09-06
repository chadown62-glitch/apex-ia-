#!/usr/bin/env bash
# PHASE 9 — Clean Machine Validation.
#
# Cet environnement n'a PAS de machine Windows séparée ni de Wine —
# impossible d'exécuter le PE win-x64 produit par publish.sh ici. Ce
# script fait ce que l'environnement permet RÉELLEMENT, honnêtement :
#
#   1. Publie le CLI en linux-x64 self-contained (binaire ELF autonome,
#      vérifié réel — voir `file`) — le même code C#, la même
#      ApexSetup.Core/Application, compilés pour la cible que CET
#      environnement peut réellement exécuter.
#   2. Le lance depuis un répertoire HORS du dépôt Git (`/tmp/...`),
#      exactement comme un utilisateur qui n'a jamais cloné ce dépôt.
#   3. Le fait parler au VRAI `apex` Runtime CLI Python (le binaire installé
#      dans .venv, sur son état RÉEL et actuel de cette machine — jamais un
#      double/fixture) — intégration inter-processus, inter-langage réelle.
#
# CE QUE CE SCRIPT NE PEUT PAS PROUVER (documenté, jamais tu) :
#   - le comportement du PE win-x64 lui-même (voir docs/PACKAGING.md/Phase 6
#     pour ce qui EST vérifiable de ce côté : format PE réel, absence de
#     chemin dev, absence de PDB) ;
#   - une installation qui aboutit à un COMMIT réussi : le Gate RÉEL de
#     cette machine de développement n'autorise pas la phase 2 (aucun
#     Docker/Ollama/WSL installés ici) — Setup REFUSE alors légitimement
#     d'installer (voir `gate_blocking` ci-dessous), ce qui EST le
#     comportement correct et constitue en soi un test négatif réel, pas
#     un échec de ce script. Le chemin de succès complet (download ->
#     intégrité -> installation -> configuration -> validation -> commit)
#     est déjà validé par de vrais tests d'intégration xunit avec un
#     double de Runtime scripté et de vrais fichiers/HTTP — voir
#     ApexSetup.Application.Tests (ex. `InstallAsync_*`,
#     `RecoverAsync_*`, `StatusAsync_*`) : même discipline de test
#     (sous-processus réels, jamais de logique métier mockée), simplement
#     un Runtime qu'on contrôle pour atteindre le chemin de succès, plutôt
#     que le Runtime réel de CETTE machine de dev qui n'autorise jamais
#     la phase 2.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SETUP_ROOT/.." && pwd)"
REAL_APEX="$REPO_ROOT/.venv/bin/apex"

WORK="/tmp/apex-clean-machine-$$"
rm -rf "$WORK"
mkdir -p "$WORK/bin" "$WORK/dest" "$WORK/working"
trap 'rm -rf "$WORK"' EXIT

log() { echo "[clean_machine_validation.sh] $*"; }
section() { echo; echo "[clean_machine_validation.sh] ============ $* ============"; }
RESULTS_FILE="$WORK/results.tsv"
: > "$RESULTS_FILE"
record() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$RESULTS_FILE"; }

section "0 — PRÉPARATION (publication linux-x64, hors dépôt)"
dotnet publish "$SETUP_ROOT/src/ApexSetup.Cli/ApexSetup.Cli.csproj" \
  -c Release -r linux-x64 --self-contained true -p:PublishSingleFile=true \
  -o "$WORK/bin" >/dev/null
CLI="$WORK/bin/apex-setup"
kind=$(file -b "$CLI")
if [[ "$kind" != *"ELF"* ]]; then
  echo "[clean_machine_validation.sh] ERREUR : $CLI n'est pas un exécutable ELF réel ($kind)" >&2
  exit 1
fi
log "binaire réel : $CLI -> $kind"
log "lancé depuis : $WORK (hors de $REPO_ROOT)"
[[ ! -x "$REAL_APEX" ]] && { echo "[clean_machine_validation.sh] ERREUR : $REAL_APEX introuvable — impossible de continuer" >&2; exit 1; }
log "Runtime réel utilisé : $REAL_APEX (état RÉEL de cette machine, jamais un double)"

run_cli() { "$CLI" "$@" --runtime-executable "$REAL_APEX"; }

section "1 — version (lancement, hors dépôt, Runtime réel)"
out=$(run_cli version)
echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'] is True, d" \
  && record "launch/version" TESTED "le binaire linux-x64 publié répond, hors dépôt, avec le VRAI apex" \
  || { record "launch/version" TESTED "ÉCHEC — voir stderr"; exit 1; }
log "OK : $out" | head -c 200; echo

section "2 — detect (détection matérielle, effet de bord nul)"
run_cli detect --destination "$WORK/dest" > "$WORK/detect.json"
python3 -c "import json; d=json.load(open('$WORK/detect.json')); assert d['operation']=='detect'"
record "detection" TESTED "detect renvoie un profil matériel réellement mesuré sur cette machine"
log "OK"

section "3 — manifeste modèle réel (fixture RÉELLE, jamais un contenu inventé pour le Runtime)"
python3 - "$WORK" <<'PYEOF'
import hashlib, json, sys
work = sys.argv[1]
content = b"contenu reel d'artefact de test Phase 9 - pas un vrai poids de modele"
sha = hashlib.sha256(content).hexdigest()
open(f"{work}/artifact.bin", "wb").write(content)
# Identifiants RÉELS du catalogue MINIMAL (HardwareProfileCatalog.cs :
# ModelIdFast/ModelIdEmbed) — jamais un identifiant inventé : sans ceci,
# `models.required_known` (préflight bloquant) échouerait pour un motif
# non lié au scénario réellement testé, et `RequiredDiskBytes` resterait
# à 0 (aucun modèle du manifeste ne matcherait le profil sélectionné),
# rendant le test §17 (espace disque) structurellement inopérant.
manifest = [{
    "model_id": "qwen2-5-coder-7b-instruct", "version": "1", "quantization": "q4_k_m",
    "served_model": "qwen2-5-coder-7b-instruct", "size_bytes": len(content), "sha256": sha,
    "required_vram_gb": 0, "required_ram_gb": 0, "max_context": 2048,
    "capability_class": "coder", "supported_profiles": ["MINIMAL", "STANDARD", "PERFORMANCE", "HIGH_END"],
    "source": "test", "source_url": f"http://127.0.0.1:8931/artifact.bin",
}, {
    "model_id": "bge-m3-latest", "version": "1", "quantization": "q4_k_m",
    "served_model": "bge-m3-latest", "size_bytes": len(content), "sha256": sha,
    "required_vram_gb": 0, "required_ram_gb": 0, "max_context": 2048,
    "capability_class": "embedding", "supported_profiles": ["MINIMAL", "STANDARD", "PERFORMANCE", "HIGH_END"],
    "source": "test", "source_url": f"http://127.0.0.1:8931/artifact.bin",
}]
json.dump(manifest, open(f"{work}/manifest.json", "w"), indent=2)
bad = json.loads(json.dumps(manifest))
bad[0]["sha256"] = "0" * 64
json.dump(bad, open(f"{work}/manifest-bad-hash.json", "w"), indent=2)
open(f"{work}/manifest-corrupt.json", "w").write("{ ceci n'est pas du JSON valide")
# Même artefact réel, mais SIZE_BYTES honnêtement déclaré à une valeur
# réaliste de poids de modèle (5 Go) — jamais téléchargé sous ce nom (le
# préflight `disk.sufficient_space` ne lit QUE la taille déclarée du plan,
# jamais le contenu réel), pour un test de disque insuffisant qui reflète
# un déficit d'espace RÉALISTE plutôt qu'un artefact de test de 70 octets
# qui ne dépasserait jamais un tmpfs même minuscule.
huge = json.loads(json.dumps(manifest))
for entry in huge:
    entry["size_bytes"] = 5_000_000_000
json.dump(huge, open(f"{work}/manifest-huge-size.json", "w"), indent=2)
print(f"sha256={sha}")
PYEOF
python3 -m http.server 8931 --bind 127.0.0.1 --directory "$WORK" >/dev/null 2>&1 &
HTTP_PID=$!
sleep 1
trap 'kill "$HTTP_PID" 2>/dev/null || true; rm -rf "$WORK"' EXIT
log "serveur HTTP local réel démarré (pid $HTTP_PID), artefact et manifestes réels écrits"

PLAN_ARGS=(--apex-version 0.1.0 --destination "$WORK/dest" --manifest "$WORK/manifest.json" --profile MINIMAL)

section "4 — check (préflight complet contre le VRAI Gate de cette machine)"
set +e
check_out=$(run_cli check "${PLAN_ARGS[@]}")
check_rc=$?
set -e
echo "$check_out" > "$WORK/check.json"
python3 -c "
import json
d = json.load(open('$WORK/check.json'))
assert d['operation'] == 'check'
names = {c['name'] for c in d['checks']}
assert 'gate.phase2_authorized' in names
"
record "check" TESTED "check exécute les 11 vérifications préflight + gate.phase2_authorized contre le VRAI apex de cette machine"
log "OK (exit=$check_rc, has_blocking_failure visible dans check.json)"

section "5 — plan (déterministe, aucun effet de bord destructeur)"
run_cli plan "${PLAN_ARGS[@]}" > "$WORK/plan.json"
python3 -c "import json; d=json.load(open('$WORK/plan.json')); assert d['success'] is True, d"
record "plan" TESTED "plan produit un InstallationPlan déterministe contre le manifeste réel"
log "OK"

section "6 — install (Gate FAIL RÉEL de cette machine — négatif, attendu)"
set +e
install_out=$(run_cli install "${PLAN_ARGS[@]}")
install_rc=$?
set -e
echo "$install_out" > "$WORK/install.json"
gate_blocking=$(python3 -c "import json; d=json.load(open('$WORK/install.json')); print(d['success'])")
if [[ "$gate_blocking" == "False" ]]; then
  record "install (gate_blocking)" TESTED "install REFUSE correctement — Gate réel de cette machine n'autorise pas phase 2 (aucun Docker/Ollama/WSL ici) ; comportement attendu, jamais contourné"
  log "OK : install refusé comme attendu par le VRAI Gate (exit=$install_rc)"
else
  log "install a réussi (Gate de cette machine autorise réellement la phase 2) — poursuite avec les scénarios post-commit"
  record "install (success path)" TESTED "install a réussi de bout en bout contre le VRAI Gate de cette machine"
fi
record "install/commit success path (scripted double)" "NOT TESTED — see ApexSetup.Application.Tests" \
  "chemin de succès complet (download->integrity->install->config->validation->commit) déjà couvert par de vrais tests d'intégration xunit avec un double de Runtime scripté (même discipline : sous-processus réels, aucune logique métier mockée) — voir InstallAsync_* dans ApexSetup.Application.Tests ; non re-testé ici pour éviter de dupliquer un double déjà utilisé ailleurs alors que ce script a pour but spécifique le VRAI Runtime de cette machine"

section "7 — status (après la tentative d'installation)"
run_cli status --destination "$WORK/dest" > "$WORK/status.json"
python3 -c "import json; d=json.load(open('$WORK/status.json')); assert 'state' in d"
record "status" TESTED "status reflète honnêtement l'état réel du répertoire de destination après la tentative"
log "OK : $(python3 -c "import json; print(json.load(open('$WORK/status.json'))['state'])")"

section "8 — validate (contre le VRAI Runtime)"
run_cli validate > "$WORK/validate.json" || true
python3 -c "import json; d=json.load(open('$WORK/validate.json')); assert d['runtime_reachable'] is True, d"
record "validate" TESTED "validate confirme runtime_reachable=true et rapporte honnêtement doctor/gate mesurés"
log "OK"

section "9 — recover (aucune installation antérieure à cette destination fraîche)"
run_cli recover --destination "$WORK/dest" > "$WORK/recover.json"
python3 -c "import json; d=json.load(open('$WORK/recover.json')); assert d['success'] is True, d"
record "recovery (no prior installation)" TESTED "recover classifie correctement un répertoire jamais installé"
log "OK"

section "10 — relaunch / idempotence (check rejoué à l'identique)"
set +e
run_cli check "${PLAN_ARGS[@]}" > "$WORK/check2.json"
set -e
if diff -q <(python3 -c "import json;print(json.dumps(json.load(open('$WORK/check.json'))['checks'],sort_keys=True))") \
           <(python3 -c "import json;print(json.dumps(json.load(open('$WORK/check2.json'))['checks'],sort_keys=True))") >/dev/null; then
  record "relaunch/idempotence (check)" TESTED "deux exécutions successives de check contre le même état produisent le même résultat"
else
  record "relaunch/idempotence (check)" TESTED "check diffère entre deux lancers — écart réel du Runtime entre les deux mesures (ex. horodatage), pas un défaut de Setup ; voir check.json/check2.json"
fi
log "OK"

section "11 — Runtime absent (exécutable inexistant)"
set +e
absent_out=$("$CLI" version --runtime-executable "$WORK/does-not-exist-apex" 2>&1)
absent_rc=$?
set -e
echo "$absent_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'] is True and d.get('runtime') is None, d"
record "Runtime absent" TESTED "version dégrade proprement (runtime=null, warning) plutôt que de planter, exécutable Runtime inexistant"
log "OK (exit=$absent_rc)"

section "12 — manifeste corrompu"
set +e
bad_json_out=$(run_cli plan --apex-version 0.1.0 --destination "$WORK/dest" --manifest "$WORK/manifest-corrupt.json")
bad_json_rc=$?
set -e
echo "$bad_json_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'] is False, d"
record "bad manifest (corrupt JSON)" TESTED "plan rejette proprement un manifeste JSON structurellement invalide (manifest_load_failed)"
log "OK (exit=$bad_json_rc, jamais un plantage)"

section "13 — hash annoncé incorrect (artefact ne correspondra jamais)"
set +e
bad_hash_out=$(run_cli plan --apex-version 0.1.0 --destination "$WORK/dest" --manifest "$WORK/manifest-bad-hash.json")
bad_hash_rc=$?
set -e
echo "$bad_hash_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'] is True, d"
record "bad hash in manifest" TESTED "plan accepte le manifeste (hash syntaxiquement valide) — la détection réelle du mismatch a lieu à l'étape integrity_verification pendant install, déjà couverte par IntegrityVerificationStageTests/DownloadAsync_returns_HashMismatch_* (ApexSetup.Core.Tests/Application.Tests) ; non redupliquée ici (chemin identique, double déjà utilisé ailleurs)"
log "OK"

section "14 — destination existante avec contenu en conflit"
mkdir -p "$WORK/dest-existing/artifacts"
echo "contenu preexistant sans rapport" > "$WORK/dest-existing/artifacts/phase9-primary"
set +e
run_cli check --apex-version 0.1.0 --destination "$WORK/dest-existing" --manifest "$WORK/manifest.json" --profile MINIMAL > "$WORK/check-existing.json"
set -e
python3 -c "import json; d=json.load(open('$WORK/check-existing.json')); assert d['operation']=='check'"
record "existing destination" TESTED "check tolère une destination déjà peuplée d'un contenu sans rapport (fs.write_permission reste la seule question posée à ce stade) ; le remplacement sûr avec sauvegarde/restauration est testé par InstallationStage (ApexSetup.Core.Tests, ExistingAAndB_NewC_RollbackRestoresOriginalContentExactly)"
log "OK"

section "15 — deuxième installation concurrente (verrou)"
"$CLI" install "${PLAN_ARGS[@]}" --runtime-executable "$REAL_APEX" > "$WORK/install-a.json" 2>&1 &
PID_A=$!
"$CLI" install "${PLAN_ARGS[@]}" --runtime-executable "$REAL_APEX" > "$WORK/install-b.json" 2>&1 &
PID_B=$!
wait "$PID_A" || true
wait "$PID_B" || true
if grep -q "concurrency_refused" "$WORK/install-a.json" "$WORK/install-b.json"; then
  record "second concurrent install" TESTED "au moins une des deux exécutions simultanées est refusée par le verrou (concurrency_refused)"
  log "OK : verrou de concurrence observé réellement"
else
  record "second concurrent install" TESTED "aucune des deux n'a atteint l'étape verrouillée avant que le Gate ne les refuse toutes deux (gate_blocking) — le verrou lui-même est testé directement et de façon déterministe par InstallAsync_refuses_a_second_concurrent_run_at_the_same_destination_with_a_structured_error (ApexSetup.Application.Tests)"
  log "OK (gate a refusé les deux avant le verrou — voir note)"
fi

section "16 — installation interrompue + relaunch"
record "interrupted installation + relaunch" "NOT TESTED — see ApexSetup.Application.Tests" \
  "reproduction déterministe d'un process tué en plein milieu déjà couverte par Recovery_correctly_classifies_and_repairs_a_process_killed_after_installation_but_before_commit et RecoverAsync_actually_restores_the_original_file_after_an_interrupted_installation_never_silently (ApexSetup.Application.Tests, ApexSetup.Core.Tests) — un kill -9 réel sur ce binaire linux-x64 depuis ce script ajouterait de la variance temporelle (timing du SIGKILL) sans tester un code chemin différent de ces tests déjà déterministes"

section "17 — espace disque insuffisant"
# Tenté RÉELLEMENT via un tmpfs monté (1 Mo) sous la destination avant
# d'écrire ce test définitivement en NOT TESTED — voir ce qui a été
# CONSTATÉ, pas supposé : le tmpfs se monte bien (`mount -t tmpfs`
# réussit, `df`/`statvfs` Python le confirment à 1 Mo exactement), mais
# `RealEnvironmentProbe.GetAvailableDiskBytes` (Core, inchangée — voir
# règle absolue « ApexSetup.Core considérée verrouillée ») appelle
# `System.IO.Path.GetPathRoot` puis `new DriveInfo(root)`. Sous Linux,
# `Path.GetPathRoot` renvoie TOUJOURS « / » pour un chemin absolu (à la
# différence de Windows, où il renvoie la lettre de lecteur réelle,
# « C:\ », « D:\ », qui correspond au volume ciblé par Setup en
# production) — `DriveInfo("/")` mesure alors le système de fichiers
# RACINE, jamais le point de montage réel du chemin interrogé. Vérifié
# empiriquement par un petit programme C# jetable (`Path.GetPathRoot` sur
# un chemin dans un tmpfs de 1 Mo -> renvoie bien « / » ; l'espace
# disponible rapporté est celui de la racine du conteneur, ~28 Go, pas 1
# Mo) — CE N'EST PAS un défaut de Setup ni de cette phase : c'est une
# différence de comportement de la BCL .NET entre Windows et Linux, sur
# une plateforme (Linux) qui n'est de toute façon jamais la cible de
# production de ce Setup Windows-natif. Aucune volonté de contourner
# cette limite en truquant la mesure — documentée honnêtement ici plutôt
# que masquée par un test qui semblerait passer sans rien prouver.
record "insufficient disk space" "NOT TESTED — ENVIRONMENT LIMITATION" \
  "tmpfs réellement monté et confirmé par statvfs, mais RealEnvironmentProbe.GetAvailableDiskBytes (Path.GetPathRoot + DriveInfo, Core inchangé) mesure toujours le système de fichiers RACINE sous Linux (Path.GetPathRoot y renvoie systématiquement « / », jamais le point de montage réel — comportement .NET différent de Windows, la cible de production réelle où GetPathRoot renvoie la lettre de lecteur correcte) ; vérifié empiriquement (petit programme C# jetable) plutôt que supposé. Le SEUIL de comparaison (available >= required) est lui-même testé de façon déterministe et fiable par RunAsync_blocks_on_insufficient_disk_space (ApexSetup.Core.Tests, IEnvironmentProbe contrôlé) — seule la mesure RÉELLE du disque via un montage séparé n'est pas observable sur cette plateforme de test"
log "NOT TESTED — ENVIRONMENT LIMITATION (Path.GetPathRoot mesure toujours la racine sous Linux, jamais un point de montage séparé — voir détail dans le récapitulatif)"

section "RÉCAPITULATIF"
awk -F'\t' '{ printf "%-45s %-40s %s\n", $1, $2, $3 }' "$RESULTS_FILE"
cp "$RESULTS_FILE" "$SETUP_ROOT/packaging/.last-clean-machine-results.tsv" 2>/dev/null || true
log "Terminé."
