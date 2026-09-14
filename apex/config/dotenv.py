"""Chargement de `.env` dans `os.environ` — la CAUSE RACINE d'un décalage
réel diagnostiqué (mission « Correction du doctor : hardware config / Ollama
reference ») : `.env` contient bien `APEX_GPU_VRAM_GB=8` (vérifié
directement par l'utilisateur via `python-dotenv`), mais `apex doctor`
continuait de voir `24` — parce qu'**aucun** code d'APEX ne chargeait jamais
`.env` dans `os.environ`.

`docker compose` charge `.env` automatiquement pour les VARIABLES DE
CONTENEUR (c'est pourquoi Postgres/Redis/MinIO avaient déjà les bons mots de
passe) — mais `apex doctor`/`apex gate`/`apex serve` sont de simples
processus Python, qui ne voient que le `os.environ` RÉEL du shell qui les a
lancés. Tant que rien n'appelle explicitement un chargeur `.env`, poser des
valeurs dans le fichier `.env` n'a AUCUN effet sur `apex.config.loader.
substitute_env` (qui lit `os.environ` quand aucun `environ` explicite n'est
fourni) : `${APEX_GPU_VRAM_GB:-24}` retombe silencieusement sur son défaut.

**Stdlib pur, par construction** (voir `pyproject.toml` : « Phase 1 : PyYAML
uniquement. Le noyau [...] est volontairement stdlib pur — `apex doctor`
doit diagnostiquer un environnement où rien n'est installé, y compris
lui-même. ») — jamais une dépendance à `python-dotenv`, même si ce paquet
est ce que l'utilisateur a utilisé pour DIAGNOSTIQUER le problème (il n'est
pas garanti installé pour un utilisateur final).

**Priorité** : un `.env` ne DOIT JAMAIS écraser une variable déjà réellement
exportée par le shell — `os.environ.setdefault`, jamais une affectation
directe. Une valeur explicitement exportée (`export APEX_GPU_VRAM_GB=8`)
reste toujours prioritaire sur le fichier, même discipline que
`python-dotenv` lui-même et que `docker compose`."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import MutableMapping

#: `KEY=VALUE`, `export KEY=VALUE` toléré (habitude shell fréquente en tête
#: de `.env`), valeur optionnellement entre guillemets simples/doubles.
_LINE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)\s*$"
)


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse le CONTENU d'un fichier `.env` — fonction PURE, aucune E/S ici
    (voir `load_dotenv_file` pour la lecture disque). Lignes vides et
    commentaires (`#` en tête, espaces ignorés avant) sautés. Une valeur
    entre guillemets (simples ou doubles) est dé-quotée ; sans guillemets,
    les espaces de fin sont retirés mais le contenu n'est jamais interprété
    (aucune expansion `$VAR`, aucune substitution — un `.env` reste une
    liste `KEY=VALUE` littérale, jamais un script shell exécuté)."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        key = match.group("key")
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv_file(path: str | os.PathLike[str]) -> dict[str, str] | None:
    """Lit et parse `path` — `None` si le fichier n'existe pas (un `.env`
    reste FACULTATIF, jamais une exigence : voir `.env.example`, copié par
    l'utilisateur en `.env`, gitignored)."""
    file = Path(path)
    if not file.is_file():
        return None
    return parse_dotenv(file.read_text(encoding="utf-8"))


def apply_dotenv(
    path: str | os.PathLike[str] = ".env",
    *,
    environ: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Charge `path` et applique chaque variable à `environ` (`os.environ`
    réel par défaut ; un `dict` explicite en test, pour ne jamais muter le
    processus de test lui-même) via `setdefault` — retourne les CLÉS
    RÉELLEMENT appliquées (jamais celles déjà présentes, ignorées en
    silence : une variable déjà exportée par le shell n'est jamais
    remplacée). Fichier absent -> tuple vide, jamais une erreur."""
    target = os.environ if environ is None else environ
    values = load_dotenv_file(path)
    if values is None:
        return ()
    applied = []
    for key, value in values.items():
        if key not in target:
            target[key] = value
            applied.append(key)
    return tuple(applied)
