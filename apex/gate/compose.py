"""Analyse statique d'un fichier docker-compose.

K2 : `docker-compose.yml` declarait 9,5 Go de limites memoire pour une machine
qui n'en expose que 6,7 a WSL2. Aucun controle ne confrontait les deux, et
`docker compose up` aurait echoue ou declenche l'OOM killer avec un diagnostic
obscur.

Ce module lit la structure reelle du fichier. Trois distinctions comptent :

**Limites contre reservations.** `deploy.resources.limits.memory` est un
plafond ; `reservations.memory` est une reservation d'ordonnancement qui ne
plafonne rien. Seules les limites peuvent provoquer une sur-allocation, donc
seules les limites entrent dans le ratio.

**Forme longue contre forme courte.** `deploy.resources.limits` n'est honore
que par Compose V2 hors Swarm ; `docker-compose` V1 (Python) l'ignore
**silencieusement**. `mem_limit` est honore par les deux. Un fichier qui ne
declare que la forme longue est donc sans protection sur V1 — le pire cas pour
une contrainte de securite, puisque rien ne le signale.

**Declare contre applique.** Savoir si une limite sera effectivement appliquee
demande de connaitre le runtime. Sans Docker installe, cette question reste
sans reponse, et le module le dit plutot que de supposer.

Le module n'execute rien : il lit un fichier. Aucune image n'est tiree, aucun
conteneur demarre.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apex.config.loader import substitute_env

#: Suffixes acceptes par Docker Compose. Les formes `Ki/Mi/Gi` sont admises par
#: certains outils ; on les traite comme leurs equivalents binaires.
_UNITS: dict[str, int] = {
    "b": 1,
    "k": 1024, "kb": 1024, "ki": 1024, "kib": 1024,
    "m": 1024**2, "mb": 1024**2, "mi": 1024**2, "mib": 1024**2,
    "g": 1024**3, "gb": 1024**3, "gi": 1024**3, "gib": 1024**3,
    "t": 1024**4, "tb": 1024**4, "ti": 1024**4, "tib": 1024**4,
}

_MEMORY = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$")


class ComposeParseError(Exception):
    """Fichier Compose illisible ou structurellement invalide."""


def parse_memory(value: Any) -> int | None:
    """Convertit une valeur memoire Compose en octets.

    Accepte un entier nu (octets), ou une chaine avec suffixe : `512m`, `1.5G`,
    `2Gi`, `1073741824`. Retourne None si la valeur est absente ou
    ininterpretable — jamais une valeur par defaut, qui masquerait une erreur
    de configuration.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None

    match = _MEMORY.match(str(value))
    if not match:
        return None
    amount, suffix = match.group(1), match.group(2).lower()
    if suffix and suffix not in _UNITS:
        return None
    try:
        return int(float(amount) * _UNITS.get(suffix, 1))
    except (ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class ServiceMemory:
    name: str
    limit_bytes: int | None
    reservation_bytes: int | None
    #: Ou la limite est declaree : « deploy », « short » (mem_limit), ou les deux.
    declared_in: tuple[str, ...] = ()
    raw_limit: Any = None
    parse_error: str | None = None

    @property
    def has_limit(self) -> bool:
        return self.limit_bytes is not None

    @property
    def enforced_on_compose_v1(self) -> bool:
        """`mem_limit` est la seule forme honoree par Compose V1 hors Swarm."""
        return "short" in self.declared_in

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.name,
            "limit_bytes": self.limit_bytes,
            "limit_gb": round(self.limit_bytes / 1024**3, 2) if self.limit_bytes else None,
            "reservation_gb": (
                round(self.reservation_bytes / 1024**3, 2) if self.reservation_bytes else None
            ),
            "declared_in": list(self.declared_in),
            "enforced_on_compose_v1": self.enforced_on_compose_v1,
            "parse_error": self.parse_error,
        }


@dataclass(frozen=True)
class ComposeAnalysis:
    path: str
    services: tuple[ServiceMemory, ...] = ()
    error: str | None = None

    @property
    def total_limit_bytes(self) -> int:
        return sum(s.limit_bytes or 0 for s in self.services)

    @property
    def total_limit_gb(self) -> float:
        return self.total_limit_bytes / 1024**3

    @property
    def total_reservation_gb(self) -> float:
        return sum(s.reservation_bytes or 0 for s in self.services) / 1024**3

    @property
    def services_without_limit(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.services if not s.has_limit)

    @property
    def services_ignored_on_v1(self) -> tuple[str, ...]:
        """Services dont la limite disparait sur un runtime Compose V1."""
        return tuple(s.name for s in self.services if s.has_limit and not s.enforced_on_compose_v1)

    @property
    def parse_errors(self) -> tuple[str, ...]:
        return tuple(f"{s.name}: {s.parse_error}" for s in self.services if s.parse_error)

    def top_consumers(self, count: int = 3) -> list[dict[str, Any]]:
        ranked = sorted(
            (s for s in self.services if s.has_limit),
            key=lambda s: s.limit_bytes or 0,
            reverse=True,
        )
        return [s.to_dict() for s in ranked[:count]]


def analyse_compose_file(
    path: str | Path, *, environ: dict[str, str] | None = None
) -> ComposeAnalysis:
    """Lit un fichier Compose et en extrait les contraintes memoire.

    La substitution `${VAR:-defaut}` reutilise celle de la configuration APEX :
    un fichier Compose parametre doit etre analyse tel qu'il sera interprete,
    pas tel qu'il est ecrit.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return ComposeAnalysis(path=str(file_path), error="fichier introuvable")

    try:
        import yaml
    except ImportError:
        return ComposeAnalysis(path=str(file_path), error="PyYAML absent")

    try:
        document = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return ComposeAnalysis(path=str(file_path), error=f"YAML invalide : {exc}")

    if not isinstance(document, dict):
        return ComposeAnalysis(path=str(file_path), error="document Compose invalide")

    try:
        document = substitute_env(document, environ if environ is not None else {})
    except Exception as exc:  # variable sans defaut : on analyse ce qu'on peut
        return ComposeAnalysis(path=str(file_path), error=f"substitution impossible : {exc}")

    services_raw = document.get("services")
    if not isinstance(services_raw, dict):
        return ComposeAnalysis(path=str(file_path), error="aucune section « services »")

    services: list[ServiceMemory] = []
    for name, raw in services_raw.items():
        if not isinstance(raw, dict):
            continue
        services.append(_read_service(str(name), raw))

    return ComposeAnalysis(path=str(file_path), services=tuple(services))


def _read_service(name: str, raw: dict[str, Any]) -> ServiceMemory:
    declared: list[str] = []
    errors: list[str] = []

    resources = ((raw.get("deploy") or {}).get("resources") or {}) if isinstance(raw.get("deploy"), dict) else {}
    long_raw = (resources.get("limits") or {}).get("memory") if isinstance(resources, dict) else None
    reservation_raw = (
        (resources.get("reservations") or {}).get("memory") if isinstance(resources, dict) else None
    )
    short_raw = raw.get("mem_limit")

    long_bytes = parse_memory(long_raw)
    short_bytes = parse_memory(short_raw)

    if long_raw is not None:
        if long_bytes is None:
            errors.append(f"deploy.resources.limits.memory illisible ({long_raw!r})")
        else:
            declared.append("deploy")
    if short_raw is not None:
        if short_bytes is None:
            errors.append(f"mem_limit illisible ({short_raw!r})")
        else:
            declared.append("short")

    # Si les deux formes sont presentes, le plafond effectif est la plus basse.
    candidates = [b for b in (long_bytes, short_bytes) if b is not None]
    limit = min(candidates) if candidates else None

    return ServiceMemory(
        name=name,
        limit_bytes=limit,
        reservation_bytes=parse_memory(reservation_raw),
        declared_in=tuple(declared),
        raw_limit=short_raw if short_raw is not None else long_raw,
        parse_error="; ".join(errors) or None,
    )
