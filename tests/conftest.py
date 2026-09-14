"""Isolation de `APEX_DATA_ROOT` pour la suite de tests — N+26.

**Le problème réel** (rapporté après un `pytest` sur un dépôt fraîchement
cloné) : `config.yaml` résout `paths.data_root` via
`${APEX_DATA_ROOT:-/home/apex/apex-data}` (`apex.config.substitute_env`).
`apply_dotenv` (`apex.config.dotenv`) — qui charge `.env` dans
`os.environ` — n'est appelée QUE par `apex.cli.main.main()` : `pytest`
n'invoque jamais cette fonction, donc TOUT test qui appelle `load_config()`
directement (des dizaines de fichiers) résout ce chemin contre
l'environnement shell réel, sans repli sur `.env`. Sur une machine où
`APEX_DATA_ROOT` n'est pas exporté et `/home/apex` n'existe pas, ce
chemin par défaut reste une chaîne littérale qui ne pointe nulle part.

**Correctif architectural retenu** : PAS une validation d'existence ajoutée
à `apex.config.loader` (qui doit rester sans effet de bord filesystem, par
conception — voir K1_K2_CORRECTIONS.md, un `data_root` non encore créé est
un état légitime, créé paresseusement). PAS un `.env` de test committé (un
chemin utilisateur arbitraire resterait deviné). La correction est ici :
un `pytest_configure` (s'exécute avant toute collecte de test, donc avant
le premier `load_config()`) qui fixe `APEX_DATA_ROOT` à un répertoire
temporaire RÉEL, créé pour la durée de la session, JAMAIS s'il est déjà
défini — un développeur qui exporte `APEX_DATA_ROOT` (ou le charge via
`.env`, son propre `direnv`, etc.) avant `pytest` garde la main, ce hook ne
l'écrase jamais.

Symétrique de `apex/doctor/checks/environment.py::check_data_root` (WARN
sur un `data_root` manquant en dehors des tests) et de son safe-fix
(`apex/doctor/repair.py`, `mkdir -p`) : ce fichier résout le MÊME problème
pour l'environnement de test, par un mécanisme différent et approprié
(aucune raison qu'un test crée un répertoire sous le HOME réel de
l'utilisateur qui lance la suite).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

_created_data_root: str | None = None


def pytest_configure(config: Any) -> None:
    global _created_data_root
    if os.environ.get("APEX_DATA_ROOT"):
        return  # déjà fixé (shell, .env, CI) — jamais écrasé aveuglément
    tmp_dir = tempfile.mkdtemp(prefix="apex-pytest-data-")
    os.environ["APEX_DATA_ROOT"] = tmp_dir
    _created_data_root = tmp_dir


def pytest_unconfigure(config: Any) -> None:
    global _created_data_root
    if _created_data_root is None:
        return
    shutil.rmtree(_created_data_root, ignore_errors=True)
    os.environ.pop("APEX_DATA_ROOT", None)
    _created_data_root = None
