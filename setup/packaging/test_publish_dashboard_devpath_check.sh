#!/usr/bin/env bash
# Régression pour publish_dashboard.sh — RELEASE LOCK 2/2.
#
# BUG RÉEL trouvé par revue (`bash -x`, reproduit manuellement) : sous
# `set -o pipefail` (déjà présent dans publish_dashboard.sh), l'ancien
# contrôle `strings "$EXE" | grep -qE "..."` faisait quitter `grep` dès la
# première correspondance ; `strings`, dont la sortie n'était alors plus
# lue, recevait SIGPIPE et terminait avec un statut non nul. `pipefail`
# reporte alors ce statut (celui de `strings`, pas celui de `grep`), et
# `if pipeline; then` évaluait la condition comme FAUSSE — le contrôle
# « aucun chemin de développement embarqué » passait silencieusement MÊME
# EN PRÉSENCE d'une vraie correspondance. Ce test prouve, avec les VRAIES
# commandes (pas une réimplémentation), que :
#   1. le motif dangereux (pipe direct vers `grep -qE`) n'est plus présent
#      dans le script ;
#   2. la technique corrective (here-string `<<<`) détecte réellement une
#      correspondance sur un flux volumineux, là où l'ancienne échouait.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/publish_dashboard.sh"

fail() { echo "[test_publish_dashboard_devpath_check.sh] ÉCHEC : $*" >&2; exit 1; }

# --- 1. Le motif dangereux (pipe direct dans grep -qE) a disparu du CODE --
# (grep -v '^\s*#' exclut les lignes de commentaire, qui décrivent
# délibérément l'ancien motif dangereux à titre d'explication du correctif)
if grep -v '^\s*#' "$SCRIPT" | grep -qE 'strings "\$EXE" \| grep -qE'; then
  fail "le motif dangereux (strings | grep -qE, sujet à SIGPIPE sous pipefail) est toujours présent dans le code"
fi
echo "[test_publish_dashboard_devpath_check.sh] OK : plus de pipe direct strings|grep -qE dans le code du script"

# --- 2. La forme corrigée (here-string) est bien celle utilisée -----------
if ! grep -qE 'grep -qE .* <<< "\$strings_output"' "$SCRIPT"; then
  fail "la forme corrigée (grep -qE ... <<< \$strings_output) est absente du script"
fi
echo "[test_publish_dashboard_devpath_check.sh] OK : la vérification utilise bien un here-string"

# --- 3. Preuve comportementale : reproduit le bug puis prouve le correctif -
big_output=$(printf '/home/user/apex-ia-/setup/leak\n%s\n' "$(head -c 2000000 /dev/zero | tr '\0' 'x')")

set +e
printf '%s\n' "$big_output" | grep -qE "/home/user/apex-ia-/setup"
old_status=$?
set -e
if [[ "$old_status" -eq 0 ]]; then
  fail "le scénario de reproduction n'a pas déclenché le bug (pipe direct) — environnement inattendu, test non concluant"
fi
echo "[test_publish_dashboard_devpath_check.sh] OK : reproduit le faux négatif de l'ancien pipe direct (exit $old_status, jamais 0)"

if ! grep -qE "/home/user/apex-ia-/setup" <<< "$big_output"; then
  fail "la forme corrigée (here-string) n'a PAS détecté une vraie correspondance — RÉGRESSION"
fi
echo "[test_publish_dashboard_devpath_check.sh] OK : la forme corrigée détecte réellement la correspondance"

echo "[test_publish_dashboard_devpath_check.sh] TOUS LES TESTS PASSENT (3/3)"
