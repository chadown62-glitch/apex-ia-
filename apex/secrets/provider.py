"""`SecretBroker` — résolution de secrets hors contexte modèle (registre :
« injection de secrets hors contexte modèle », phase 2-7, `PLANNED` jusqu'ici).

Même patron que `apex.sandbox.provider`/`apex.artifacts.provider`/
`apex.models.provider` : un contrat abstrait minimal, un seul point de choix
de backend. Prérequis dur avant tout `ModelProvider` payant — sans lui,
chaque futur provider cloud inventerait sa propre gestion de clé, exactement
la dette que ce module existe pour éviter.

**Aucune clé API réelle nulle part dans ce dépôt** (§72, et l'instruction
explicite de cette passe) : `EnvSecretBroker` lit `os.environ`, ne stocke ni
ne journalise jamais une valeur résolue. `apex.obs.logging.redact()` reste la
seule défense en profondeur si un appelant venait à journaliser un secret par
erreur — ce module ne le fait jamais lui-même.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from apex.errors import SecretNotFound
from apex.secrets.model import SecretRef


class SecretBroker(ABC):
    @abstractmethod
    def resolve(self, key: str) -> str:
        """Retourne la valeur réelle du secret référencé par `key`. Lève
        `SecretNotFound` si absent — ne retourne JAMAIS une chaîne vide ou
        `None` comme substitut silencieux (même discipline que
        `ModelProvider.generate()` : jamais un résultat inventé faute de
        backend disponible)."""

    def resolve_ref(self, ref: SecretRef) -> str:
        """Confort — délègue à `resolve()`, aucune logique propre."""
        return self.resolve(ref.key)


class EnvSecretBroker(SecretBroker):
    """Seul backend réel aujourd'hui : variables d'environnement.

    Convention de nommage : `key` (`"openai_api_key"`) est mise en
    majuscules et préfixée `APEX_SECRET_` (`APEX_SECRET_OPENAI_API_KEY`) —
    un espace de noms dédié, jamais partagé avec les variables déjà
    utilisées ailleurs dans APEX (`APEX_DB_*_PASSWORD`,
    `POSTGRES_PASSWORD`, ...), pour qu'un secret de credential de provider
    ne soit jamais confondu avec un secret d'infrastructure.
    """

    _PREFIX = "APEX_SECRET_"

    def __init__(self, *, env: dict[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ

    def _env_var(self, key: str) -> str:
        return f"{self._PREFIX}{key.upper()}"

    def resolve(self, key: str) -> str:
        var = self._env_var(key)
        value = self._env.get(var)
        if not value:
            raise SecretNotFound(key, source=f"variable d'environnement « {var} »")
        return value

    def has(self, key: str) -> bool:
        """Sonde la présence SANS lever ni retourner la valeur — pour un
        appelant qui doit décider entre plusieurs providers avant de
        s'engager (ex. le futur routeur), sans provoquer d'erreur pour un
        provider simplement non configuré."""
        return bool(self._env.get(self._env_var(key)))
