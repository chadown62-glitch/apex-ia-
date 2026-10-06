"""Compatibilité : ce module vit désormais dans `apex.tasks.solution_tasks` (noyau RUNTIME, 06/10/2026 — les
tâches « écrire une fonction » sont exécutées par le TaskLoop du produit). L'ancien chemin désigne le MÊME objet
module pour le code de recherche et les tests qui l'importent encore."""

import sys

from apex.tasks import solution_tasks as _module

sys.modules[__name__] = _module
