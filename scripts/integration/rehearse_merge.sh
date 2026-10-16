#!/usr/bin/env bash
# Répétition LOCALE de la réunion des branches Claude / Codex dans apex/integration.
#
# Ce script ne pousse rien, ne fusionne aucune PR, ne modifie aucune branche existante : il crée un worktree
# JETABLE (HEAD détachée) depuis la base choisie, y fusionne les branches dans l'ordre proposé
# (docs/REUNION_BRANCHES.md), une à une, et rapporte pour chacune : fusion propre, ou conflits et fichiers.
# Les commits de fusion n'existent que dans ce worktree, supprimé à la fin (sauf --keep).
#
#   scripts/integration/rehearse_merge.sh                       # ordre proposé, rapport sur la sortie
#   scripts/integration/rehearse_merge.sh --pytest "tests/test_assistant_game_studio.py -q"
#   scripts/integration/rehearse_merge.sh --deps /chemin/deps --keep
#   scripts/integration/rehearse_merge.sh --order "claude/apex-official-development-4kao8t agent/codex/25-game-studio-candidate"
#
# --pytest ARGS  exécute pytest dans l'arbre fusionné (PYTHONPATH = arbre [+ --deps]) si tout a fusionné ;
# --deps DIR     dossier de bibliothèques déjà installées (pip --target), ajouté au PYTHONPATH — rien n'est
#                téléchargé par ce script ;
# --base REF     base de la répétition (défaut : origin/apex/integration) ;
# --keep         garde le worktree pour inspection (son chemin est affiché).
set -euo pipefail

BASE="origin/apex/integration"
ORDER=(
  claude/apex-official-development-4kao8t   # Cerveau, assistant, profil autonome, Panel (PR #2)
  agent/codex/18-game-studio-integration    # PR #19 : missions Game Studio dans APEX
  agent/codex/20-game-studio-multi-agent    # PR #22 (sur #19)
  agent/codex/23-game-studio-planning       # PR #24 (sur #22)
  agent/codex/25-game-studio-candidate      # PR #26 (sur #24)
  agent/codex/4-inference-diagnostics       # PR #5
  agent/codex/8-project-orchestrator        # PR #11
  agent/codex/10-game-engineer              # PR #13
  agent/codex/6-vision-specialist           # PR #7 (après le classement d'apex/vision)
  agent/codex/9-game-graphics               # PR #12 (à trancher par Codex face à la pile)
  agent/codex/14-neural-3d                  # PR #17 (sur #12)
  agent/codex/15-graphics-ue                # PR #16 (sur #12)
)
PYTEST_ARGS=""
DEPS=""
KEEP=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pytest) PYTEST_ARGS="$2"; shift 2 ;;
    --deps) DEPS="$2"; shift 2 ;;
    --base) BASE="$2"; shift 2 ;;
    --order) read -r -a ORDER <<<"$2"; shift 2 ;;
    --keep) KEEP=true; shift ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done

REPO=$(git rev-parse --show-toplevel)
WT=$(mktemp -d "${TMPDIR:-/tmp}/apex-rehearsal-XXXXXX")
cleanup() {
  if [[ "$KEEP" == true ]]; then
    echo "worktree gardé : $WT"
  else
    git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || rm -rf "$WT"
    git -C "$REPO" worktree prune >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

resolve() {  # la branche locale si elle contient sa copie distante (travail pas encore poussé), sinon origin/
  local here remote
  here=$(git -C "$REPO" rev-parse -q --verify "refs/heads/$1^{commit}" 2>/dev/null || true)
  remote=$(git -C "$REPO" rev-parse -q --verify "refs/remotes/origin/$1^{commit}" 2>/dev/null || true)
  if [[ -n "$here" && ( -z "$remote" || "$(git -C "$REPO" merge-base "$here" "$remote")" == "$remote" ) ]]; then
    echo "$1"
  elif [[ -n "$remote" ]]; then echo "origin/$1"
  else echo ""; fi
}

git -C "$REPO" worktree add --detach -q "$WT" "$BASE"
G=(git -C "$WT" -c user.name="APEX rehearsal" -c user.email="rehearsal@localhost" -c commit.gpgsign=false)
echo "# Répétition de fusion (locale, rien n'est poussé)"
echo
echo "- base : \`$BASE\` = $(git -C "$WT" rev-parse --short HEAD)"
echo "- date : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
echo "| # | branche | tête | commits apportés | fichiers modifiés | résultat |"
echo "|---|---|---|---|---|---|"
step=0
all_clean=true
for branch in "${ORDER[@]}"; do
  step=$((step + 1))
  ref=$(resolve "$branch")
  if [[ -z "$ref" ]]; then
    echo "| $step | \`$branch\` | — | — | — | introuvable |"
    all_clean=false
    continue
  fi
  head=$(git -C "$WT" rev-parse --short "$ref")
  commits=$(git -C "$WT" rev-list --count "HEAD..$ref")
  files=$(git -C "$WT" diff --name-only "HEAD...$ref" | wc -l | tr -d ' ')
  if [[ "$commits" == 0 ]]; then
    echo "| $step | \`$branch\` | $head | 0 | 0 | déjà contenue |"
    continue
  fi
  if "${G[@]}" merge --no-ff --no-edit -q -m "rehearsal: merge $branch" "$ref" >/dev/null 2>&1; then
    echo "| $step | \`$branch\` | $head | $commits | $files | fusion propre |"
  else
    conflicted=$(git -C "$WT" diff --name-only --diff-filter=U | tr '\n' ' ')
    echo "| $step | \`$branch\` | $head | $commits | $files | **CONFLITS** : ${conflicted:-?} |"
    all_clean=false
    "${G[@]}" merge --abort >/dev/null 2>&1 || "${G[@]}" reset -q --hard HEAD
  fi
done
echo
if [[ "$all_clean" == true ]]; then
  echo "Toutes les branches fusionnent sans conflit dans cet ordre (fusion TEXTUELLE : ce n'est pas une preuve"
  echo "de bon fonctionnement — voir les tests)."
else
  echo "Au moins une branche ne fusionne pas proprement : à résoudre par son propriétaire avant la réunion."
fi
if [[ -n "$PYTEST_ARGS" ]]; then
  echo
  if [[ "$all_clean" != true ]]; then
    echo "Tests non lancés : l'arbre fusionné est incomplet."
    exit 1
  fi
  PY="$REPO/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
  echo "## pytest $PYTEST_ARGS"
  echo
  echo '```'
  set +e
  # shellcheck disable=SC2086
  (cd "$WT" && PYTHONPATH="$WT${DEPS:+:$DEPS}" "$PY" -m pytest -p no:cacheprovider -o addopts="" $PYTEST_ARGS 2>&1 | tail -25)
  code=$?  # pipefail : le code de pytest, pas celui de tail
  set -e
  echo '```'
  exit "$code"
fi
[[ "$all_clean" == true ]]
