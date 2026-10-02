#!/usr/bin/env bash
# CI UNIQUEMENT (runner GitHub Windows Server + WSL2 jetable) : preuve, sur la VRAIE machine et par le VRAI
# launcher, du contrat de démarrage de 5736ec9 (`apex gate --startup`, docs/PHASE_1_GATE.md §10). Jamais de
# dérogation : la présence d'un gate.exceptions.yaml est un échec. Les cas d'échec sont produits par une
# condition RÉELLE introduite dans une COPIE jetable du dépôt (jamais dans le dépôt préparé), mesurée par le
# vrai Gate et traversant le vrai launch.sh : ce sont des injections de fautes, étiquetées comme telles.
#
# Usage (utilisateur par défaut de la distribution) : startup_contract_evidence.sh <dépôt APEX préparé>
# Code de sortie : 0 si chaque assertion est vérifiée, 1 sinon. Aucune valeur de .env n'est affichée
# (launch.sh, doctor et gate n'affichent que des noms).
set -uo pipefail
REPO="${1:?dépôt APEX préparé}"
WORK="$(mktemp -d /tmp/apex-startup-evidence.XXXXXX)"
fail=0

say() { echo "[startup-evidence] $*"; }
expect() {  # libellé, code attendu, code obtenu
    if [[ "$3" == "$2" ]]; then say "PASS  $1 : code $3"; else say "FAIL  $1 : attendu $2, obtenu $3"; fail=1; fi
}
expect_line() {  # libellé, fichier, motif (grep -E)
    if grep -qE "$3" "$2"; then say "PASS  $1"; else say "FAIL  $1 (motif absent : $3)"; fail=1; fi
}
excerpt() {
    grep -E "Phase 2 autorisée|Démarrage d'APEX|FAIL mesurés|Préconditions non vérifiées|Non vérifiés|\[ FAIL \]|\[apex-launch\] (ERREUR|préflight)|RuntimeError" "$1" \
        | sed 's/^/        /' | cut -c1-220
}
copy_repo() {  # copie jetable : même .env, même .venv (lien), même projet Compose (`name: apex`)
    rsync -a --exclude .venv --exclude .apex "$REPO/" "$1/" && ln -s "$REPO/.venv" "$1/.venv"
}
run_launcher() {  # préflight réel (compose + doctor + gate --startup), sans `serve`
    (cd "$1" && bash scripts/apex-launch.sh) > "$2" 2>&1
    echo $?
}
run_gate() {  # apex gate comme launch.sh l'appelle : racine du projet, .env par le chargeur de launch.sh
    local dir="$1" out="$2"; shift 2
    (cd "$dir" && source scripts/apex-launch.sh >/dev/null 2>&1 && load_env_file .env >/dev/null 2>&1
     set +euo pipefail
     .venv/bin/python -m apex.cli.main gate --output "$WORK/environment.json" "$@") > "$out" 2>&1
    echo $?
}

for _ in $(seq 1 90); do docker info >/dev/null 2>&1 && break; sleep 1; done
docker info >/dev/null 2>&1 || { say "BLOQUÉ : démon Docker injoignable dans la distribution"; exit 1; }

say "0. aucune dérogation"
if [[ -e "$REPO/gate.exceptions.yaml" ]]; then say "FAIL  gate.exceptions.yaml présent dans $REPO"; fail=1
else say "PASS  aucun gate.exceptions.yaml dans $REPO"; fi

say "1. démarrage réel autorisé, phase 2 rapportée à part (vrai launch.sh, dépôt préparé, sans modification)"
code=$(run_launcher "$REPO" "$WORK/1-launcher.log"); excerpt "$WORK/1-launcher.log"
expect "launch.sh (préflight réel) : démarrage autorisé" 0 "$code"
expect_line "décision de démarrage affichée : AUTORISÉ" "$WORK/1-launcher.log" "Démarrage d'APEX : AUTORISÉ"
expect_line "phase 2 rapportée séparément : NON" "$WORK/1-launcher.log" "Phase 2 autorisée : NON"
expect_line "sondes à consentement listées « non vérifiés », pas en échec" "$WORK/1-launcher.log" \
    "Non vérifiés \(n'empêchent pas le démarrage\) : .*GATE-17 \(not_authorized\)"

say "2. même machine, même instant : décision de démarrage contre autorisation de phase 2"
expect "apex gate --startup" 0 "$(run_gate "$REPO" "$WORK/2-startup.log" --startup)"
expect "apex gate (phase 2, inchangé)" 1 "$(run_gate "$REPO" "$WORK/2-phase2.log")"

say "3. FAIL mesuré (injection : défaut mémoire de minio porté à 999g dans une copie ; minio n'est pas démarré)"
copy_repo "$WORK/fail"
sed -i 's/\${APEX_MINIO_MEM:-1g}/${APEX_MINIO_MEM:-999g}/g' "$WORK/fail/docker-compose.yml"
code=$(run_launcher "$WORK/fail" "$WORK/3-launcher.log"); excerpt "$WORK/3-launcher.log"
expect "launch.sh : FAIL mesuré" 5 "$code"
expect_line "GATE-21 listé en FAIL mesuré" "$WORK/3-launcher.log" "FAIL mesurés \(bloquants\) : .*GATE-21"
(cd "$WORK/fail" && source scripts/apex-launch.sh >/dev/null 2>&1 && load_env_file .env >/dev/null 2>&1
 set +euo pipefail; HOME="$WORK/home" timeout 300 .venv/bin/python -m apex.cli.main serve --port 18765) \
    > "$WORK/3-serve.log" 2>&1
code=$?; excerpt "$WORK/3-serve.log"
expect "apex serve direct (sans launcher) : refus" 1 "$code"
if (exec 3<>/dev/tcp/127.0.0.1/18765) 2>/dev/null; then say "FAIL  un serveur écoute sur 18765 malgré le refus"; fail=1
else say "PASS  rien n'écoute sur 18765 après le refus"; fi

say "4. précondition non vérifiée (injection : docker-compose.yml absent d'une copie -> GATE-21 UNKNOWN)"
copy_repo "$WORK/unknown"; rm -f "$WORK/unknown/docker-compose.yml"
code=$(run_gate "$WORK/unknown" "$WORK/4-startup.log" --startup); excerpt "$WORK/4-startup.log"
expect "apex gate --startup : précondition non vérifiée" 4 "$code"
expect_line "GATE-21 listé en précondition non vérifiée, pas en FAIL" "$WORK/4-startup.log" \
    "Préconditions non vérifiées \(bloquantes\) : GATE-21"
say "note  launch.sh code 6 non exerçable ici : docker compose exige ce même fichier (code 2 avant le Gate) ;"
say "      la correspondance 4 -> 6 est couverte par tests/test_launcher.py"

say "5. erreur interne du Gate (injection : exception levée à l'import d'apex/gate/runner.py dans une copie)"
copy_repo "$WORK/crash"
printf '\nraise RuntimeError("FAULT INJECTION: startup_contract_evidence")\n' >> "$WORK/crash/apex/gate/runner.py"
code=$(run_launcher "$WORK/crash" "$WORK/5-launcher.log"); excerpt "$WORK/5-launcher.log"
expect "launch.sh : erreur interne du Gate" 7 "$code"
expect_line "message « erreur interne », jamais « FAIL »" "$WORK/5-launcher.log" "erreur interne \(code 1\)"

say "6. sandbox à l'exécution : refus par défaut sans isolation prouvée (invariant, sur cette machine)"
(cd "$REPO" && .venv/bin/python - <<'PY'
from apex.errors import IsolationUnavailable
from apex.sandbox.model import ResourceLimits, SandboxSpec
from apex.sandbox.provider import select_sandbox_provider

selection = select_sandbox_provider()
report = selection.provider.detect()
print(f"        fournisseur retenu : {selection.provider_name} ; niveau : {report.level.value} ; "
      f"exécution permise : {report.is_available}")
if report.is_available:
    print("        isolation réelle prouvée sur cette machine : exécution non tentée (rien à refuser)")
    raise SystemExit(0)
spec = SandboxSpec(mission_id="evidence", clone_id="evidence", command=("true",),
                   limits=ResourceLimits(cpu_cores=0.5, memory_mb=64, pids_limit=16, timeout_s=10.0,
                                         disk_mb=16, network="none"))
try:
    selection.provider.execute(spec)
except IsolationUnavailable as exc:
    print(f"        refus attendu : IsolationUnavailable ({exc})")
    raise SystemExit(0)
print("        EXÉCUTION SANS ISOLATION PROUVÉE")
raise SystemExit(1)
PY
) > "$WORK/6-sandbox.log" 2>&1
code=$?; cat "$WORK/6-sandbox.log" | cut -c1-220
expect "sandbox : jamais d'exécution sans isolation prouvée" 0 "$code"

rm -rf "$WORK"
if [[ "$fail" -eq 0 ]]; then say "RÉSULTAT : contrat de démarrage vérifié"; else say "RÉSULTAT : ÉCART(S) — voir FAIL ci-dessus"; fi
exit "$fail"
