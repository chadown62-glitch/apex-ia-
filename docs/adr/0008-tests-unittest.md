# ADR-0008 — Suite de tests en unittest, exécutable aussi par pytest

**Statut :** Acceptée — phase 1, à réévaluer en phase 5

## Décision

Les tests sont écrits en `unittest.TestCase`. Ils s'exécutent par
`python -m unittest discover` (aucune dépendance) comme par `pytest`.

## Conséquence

La suite tourne sur un environnement où rien n'est installé, y compris pour
valider une installation en échec. Cohérent avec ADR-0002.

## Limite acceptée

On renonce aux fixtures paramétrées et au paramétrage de pytest. À partir de la
phase 5, les tests d'évaluation statistique auront besoin de `hypothesis` et de
la paramétrisation : ils seront écrits en pytest, tandis que le noyau restera
exécutable en stdlib. La coexistence est explicitement prévue.
