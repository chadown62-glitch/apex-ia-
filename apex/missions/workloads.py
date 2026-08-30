"""Charges de travail exécutables en sandbox — délibérément réduites à UN
seul cas, RESEARCH, pour cette étape (§6 de la consigne étape 7) : "ne
commence pas encore avec génération massive de code, accès Internet,
modification du système, auto-évolution, agents autonomes complexes".

Ce module n'est PAS un registre extensible de charges par spécialité — en
créer un maintenant pour une seule entrée serait une abstraction prématurée
(la même réserve que `apex.specialties.registry` applique déjà : ne pas
inventer 15 implémentations). Quand une deuxième spécialité aura un vrai
besoin d'exécution, ce module grandira en conséquence, pas avant.
"""

from __future__ import annotations

#: Script Python autonome, sans dépendance, strictement déterministe : ne
#: fait qu'observer son propre environnement (version, isolation réseau,
#: présence de secrets dans l'environnement) et le rapporte en JSON. Aucun
#: appel modèle, aucun téléchargement, aucune écriture hors de son
#: répertoire de travail éphémère.
RESEARCH_DIAGNOSTIC_SCRIPT = """
import json, os, socket, sys

report = {
    "python_version": sys.version.split()[0],
    "network_reachable": None,
    "postgres_env_present": any(k.startswith(("POSTGRES_", "APEX_DB_")) for k in os.environ),
    "cwd_writable": os.access(os.getcwd(), os.W_OK),
}
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    s.connect(("1.1.1.1", 443))
    report["network_reachable"] = True
except OSError:
    report["network_reachable"] = False

print(json.dumps(report))
"""


def research_diagnostic_command() -> tuple[str, ...]:
    """Commande argv pour la première mission réelle (RESEARCH uniquement).

    Le résultat inclut sa PROPRE observation de l'isolation (network_reachable
    doit valoir False, postgres_env_present doit valoir False) — une preuve
    vérifiable depuis l'intérieur du bac à sable, pas seulement depuis
    l'extérieur (`apex.sandbox.detect`)."""
    return ("python3", "-c", RESEARCH_DIAGNOSTIC_SCRIPT)
