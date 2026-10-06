"""Compatibilité : ce module vit désormais dans `apex.experience.failure_facts` (noyau RUNTIME, 06/10/2026 — voir
`apex/experience/__init__.py`). L'ancien chemin désigne le MÊME objet module (aucune copie, aucune divergence
possible) pour le code de recherche qui l'importe encore."""

import sys

from apex.experience import failure_facts as _module

sys.modules[__name__] = _module
