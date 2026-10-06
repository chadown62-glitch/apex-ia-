"""Noyau RUNTIME de l'expérience d'exécution (06/10/2026).

Ce que le produit (TaskLoop, API) utilise réellement pour enregistrer, classer et réutiliser une tentative :
`store` (Experience, dépôt, `build_experience`), `failure_taxonomy` (catégorie d'échec), `failure_facts` (faits
d'échec autorisés, liste fermée), `fitness` (score d'une tentative), `strategy` (règle de stratégie fondée sur
l'expérience). Extrait de `apex.metalearner` (recherche), qui garde des alias de compatibilité aux anciens
chemins. Ce paquet n'importe AUCUN paquet de recherche — vérifié par `runtime_build.verify`.

Volontairement vide d'imports : importer un sous-module ne doit jamais charger les autres.
"""
