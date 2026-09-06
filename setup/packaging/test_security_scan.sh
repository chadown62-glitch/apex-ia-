#!/usr/bin/env bash
# Régression pour packaging/security_scan.sh — PHASE 8.
#
# Trouvé lors de la première exécution réelle de release.sh : le motif
# PRIVATE KEY (qui commence par "-----") faisait échouer `grep -E`
# silencieusement (option inconnue), désactivant la détection sans jamais
# faire échouer le scan — un faux vert. Ce test invoque le VRAI script sur
# de VRAIS fichiers fixtures contenant chaque type de secret, et vérifie
# que CHAQUE motif déclenche réellement une détection (exit 1), pas
# seulement que le scan se termine sans erreur de syntaxe.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() { echo "[test_security_scan.sh] ÉCHEC : $*" >&2; exit 1; }

# --- 1. Répertoire propre -> exit 0 -------------------------------------
mkdir -p "$WORK/clean"
cat > "$WORK/clean/Foo.cs" <<'EOF'
namespace Foo;
public class Bar { public string Name => "hello"; }
EOF
if ! "$SCRIPT_DIR/security_scan.sh" source "$WORK/clean" >/tmp/security_scan_clean.log 2>&1; then
  cat /tmp/security_scan_clean.log >&2
  fail "un répertoire propre n'aurait jamais dû être rejeté"
fi
echo "[test_security_scan.sh] OK : répertoire propre accepté"

# --- 2. Clé AWS -----------------------------------------------------------
mkdir -p "$WORK/aws"
echo 'var key = "AKIAABCDEFGHIJKLMNOP";' > "$WORK/aws/Secret.cs"
if "$SCRIPT_DIR/security_scan.sh" source "$WORK/aws" >/tmp/security_scan_aws.log 2>&1; then
  cat /tmp/security_scan_aws.log >&2
  fail "une clé AWS n'a PAS été détectée"
fi
echo "[test_security_scan.sh] OK : clé AWS détectée (exit != 0)"

# --- 3. Bloc de clé privée PEM (LA régression trouvée) --------------------
mkdir -p "$WORK/pem"
cat > "$WORK/pem/id_rsa.txt" <<'EOF'
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz
-----END RSA PRIVATE KEY-----
EOF
set +e
"$SCRIPT_DIR/security_scan.sh" source "$WORK/pem" >/tmp/security_scan_pem.log 2>&1
rc=$?
set -e
if grep -q "ERREUR INTERNE" /tmp/security_scan_pem.log; then
  cat /tmp/security_scan_pem.log >&2
  fail "le scan a échoué en interne (grep) au lieu de détecter la clé — RÉGRESSION"
fi
if [[ "$rc" -eq 0 ]]; then
  cat /tmp/security_scan_pem.log >&2
  fail "un bloc de clé privée PEM n'a PAS été détecté — RÉGRESSION"
fi
echo "[test_security_scan.sh] OK : clé privée PEM détectée (exit != 0, pas d'erreur interne)"

# --- 4. Jeton générique ----------------------------------------------------
mkdir -p "$WORK/token"
echo 'token: "abcdef0123456789ABCD"' > "$WORK/token/config.json"
if "$SCRIPT_DIR/security_scan.sh" source "$WORK/token" >/tmp/security_scan_token.log 2>&1; then
  cat /tmp/security_scan_token.log >&2
  fail "un jeton générique n'a PAS été détecté"
fi
echo "[test_security_scan.sh] OK : jeton générique détecté (exit != 0)"

# --- 5. Mode artifact (strings) sur un faux binaire embarquant un secret --
mkdir -p "$WORK/artifact"
printf 'binary-noise\x00\x00AKIAABCDEFGHIJKLMNOP\x00\x00more-noise\n' > "$WORK/artifact/fake.exe"
if "$SCRIPT_DIR/security_scan.sh" artifact "$WORK/artifact" >/tmp/security_scan_artifact.log 2>&1; then
  cat /tmp/security_scan_artifact.log >&2
  fail "mode artifact : une clé AWS embarquée n'a PAS été détectée"
fi
echo "[test_security_scan.sh] OK : mode artifact détecte une clé AWS embarquée"

echo "[test_security_scan.sh] TOUS LES TESTS PASSENT (5/5)"
