"""Construction de l'artefact APEX Runtime — sélection, jamais destruction.

Ce paquet ne fait partie ni de APEX DEV (recherche/auto-amélioration) ni de
l'artefact Runtime lui-même : c'est l'outillage qui produit ce second à
partir du premier, par copie filtrée d'un commit Git précis. Rien ici ne
modifie `apex/` ni aucun autre fichier du dépôt DEV.

Voir `runtime_build/README.md` pour les décisions d'architecture (chaîne
Alembic unique, statut de `artifacts`/`memory`) et `runtime_build/selection.py`
pour la liste exacte des sous-paquets sélectionnés.
"""
