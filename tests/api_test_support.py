"""Aide commune aux tests de l'API — jeton d'authentification ÉPHÉMÈRE.

`apex.api.auth.get_or_create_api_token()` lit `APEX_API_TOKEN` en priorité
sur le fichier persistant `.apex/api-token` du poste — cette aide exploite
exactement ce point d'entrée pour que la suite de tests ne touche JAMAIS le
jeton réel d'un développeur : un jeton frais est généré une fois par
processus de test, jamais committé, jamais réutilisé d'une exécution à
l'autre.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi.testclient import TestClient

from apex.api.app import create_app

#: Généré une fois par import — jamais un secret en dur, jamais le fichier
#: `.apex/api-token` réel. `test-` en préfixe : jamais confondu avec un
#: jeton de production si jamais loggé par erreur pendant le développement
#: de ces tests (bien qu'aucun code de `apex.api.auth` ne journalise le
#: jeton — voir sa docstring).
TEST_API_TOKEN = "test-" + secrets.token_urlsafe(16)

os.environ["APEX_API_TOKEN"] = TEST_API_TOKEN


def authed_client(**kwargs: Any) -> TestClient:
    """`TestClient` dont chaque requête porte déjà `Authorization: Bearer
    <jeton de test>` — un seul point d'ajustement si le mécanisme change,
    au lieu de modifier chaque appel individuel dans chaque fichier de
    test."""
    return TestClient(
        create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"}, **kwargs
    )
