#!/usr/bin/env bash
# PHASE 8 — contrôle anti-secret sur le CODE SOURCE de Setup (avant build)
# et, séparément, sur un ARTEFACT construit (après publication).
#
# Deux modes d'appel :
#   security_scan.sh source   <racine setup/>   -> scanne .cs/.csproj/.sh/.md (jamais bin/obj)
#   security_scan.sh artifact <répertoire dist>  -> scanne les binaires publiés (strings)
#
# Motifs recherchés : clés AWS, blocs de clé privée PEM, jetons génériques
# "api_key=/token=/secret=/password=" suivis d'une valeur non triviale.
#
# RÉGRESSION (trouvée lors de la première exécution réelle de la pipeline
# Phase 8) : le motif PRIVATE KEY commence par "-----", que `grep -E`
# interprétait comme une liste d'options inconnues au lieu d'un motif —
# CHAQUE invocation échouait silencieusement (grep code 2, avalé par
# `|| true`), désactivant cette vérification sans qu'aucun échec ne soit
# visible : exactement le genre de "faux vert" que ce projet interdit.
# Double correctif, jamais un seul :
#   1. `grep -e "$pattern"` partout (marque explicitement l'argument comme
#      motif, quel que soit son contenu) ;
#   2. code de sortie de `grep` distingué explicitement : 1 = aucune
#      correspondance (normal), >1 = échec réel de grep — ne doit JAMAIS
#      être avalé silencieusement (fail-closed : traité comme une erreur
#      du scan lui-même, jamais comme "aucun secret trouvé").
# Couvert par packaging/test_security_scan.sh.
set -euo pipefail

MODE="${1:?usage: security_scan.sh <source|artifact> <chemin>}"
TARGET="${2:?usage: security_scan.sh <source|artifact> <chemin>}"

# shellcheck disable=SC2016
PATTERNS=(
  'AKIA[0-9A-Z]{16}'                                  # clé d'accès AWS
  '-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'
  '(api[_-]?key|secret|password|token)["'"'"']?\s*[:=]\s*["'"'"'][A-Za-z0-9/+_.-]{12,}["'"'"']'
)

# Exécute `grep -E -e "$pattern" ...` en distinguant "aucune correspondance"
# (code 1, normal) d'un échec réel de grep (tout autre code non nul, qui ne
# doit JAMAIS être traité comme "rien trouvé").
run_grep() {
  local rc=0
  grep -E -e "$1" "${@:2}" || rc=$?
  if [[ "$rc" -gt 1 ]]; then
    echo "[security_scan.sh] ERREUR INTERNE : grep a échoué (code $rc) sur le motif « $1 » — scan considéré en échec, jamais interprété comme « aucun secret »" >&2
    exit 2
  fi
  return 0
}

found=0
for pattern in "${PATTERNS[@]}"; do
  case "$MODE" in
    source)
      # Balaye TOUT fichier sous $TARGET (pas seulement .cs/.sh/.json/...) —
      # un fichier de clé committé par erreur (id_rsa, *.pem, *.key, *.pfx,
      # un simple .txt) doit être détecté quelle que soit son extension.
      # -I ignore les fichiers binaires (bruit, pas des secrets textuels) ;
      # seuls bin/obj/dist (artefacts de build volumineux, non pertinents)
      # sont exclus — plus test_security_scan.sh lui-même, dont les
      # FIXTURES contiennent délibérément de FAUX secrets (clé AWS/PEM/jeton
      # factices) pour prouver que ce scanner les détecte réellement ; ce
      # n'est pas un affaiblissement du contrôle sur du code réel, seule
      # cette exception nommée existe.
      matches=$(run_grep "$pattern" -RIn \
        --exclude-dir=bin --exclude-dir=obj --exclude-dir=dist \
        --exclude=test_security_scan.sh \
        "$TARGET")
      ;;
    artifact)
      matches=""
      while IFS= read -r -d '' file; do
        if command -v strings >/dev/null 2>&1; then
          hit=$(strings "$file" | run_grep "$pattern" -n)
          if [[ -n "$hit" ]]; then
            matches+="$file: $hit"$'\n'
          fi
        fi
      done < <(find "$TARGET" -type f -print0)
      ;;
    *)
      echo "[security_scan.sh] mode inconnu : $MODE (attendu source|artifact)" >&2
      exit 2
      ;;
  esac

  if [[ -n "$matches" ]]; then
    echo "[security_scan.sh] ERREUR : motif suspect « $pattern » détecté :" >&2
    echo "$matches" >&2
    found=1
  fi
done

if [[ "$found" -ne 0 ]]; then
  exit 1
fi

echo "[security_scan.sh] ($MODE) aucun secret détecté dans : $TARGET"
