"""Chargement et validation de la configuration APEX.

Substitution `${VAR}` et `${VAR:-defaut}` depuis l'environnement, de sorte que
`config.yaml` soit versionnable dans Git sans jamais contenir de secret (§72).

Le chargeur ne tente aucune correction automatique : une configuration
invalide échoue avec un message localisé. Deviner l'intention de l'opérateur
sur un fichier de configuration est une source de dérive silencieuse.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from apex.config.schema import (
    ApexConfig,
    as_bool,
    ComputeBudget,
    ComputePool,
    ExternalPolicy,
    GatePolicy,
    LanguageToolchain,
    ModelEndpoint,
    ModelRole,
    Paths,
    RouterScoringPolicy,
    SandboxDefaults,
    SchedulerPolicy,
    ServiceEndpoint,
)
from apex.errors import ConfigError, DependencyMissing

DEFAULT_CONFIG_PATH = "config.yaml"

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def substitute_env(value: Any, environ: dict[str, str] | None = None) -> Any:
    """Remplace `${VAR}` / `${VAR:-defaut}` récursivement."""
    env = environ if environ is not None else dict(os.environ)

    if isinstance(value, dict):
        return {k: substitute_env(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_env(v, env) for v in value]
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            raise ConfigError(f"variable d'environnement « {name} » non définie et sans défaut")

        return _VAR.sub(replace, value)
    return value


def parse_config(raw: dict[str, Any]) -> ApexConfig:
    """Construit un ApexConfig depuis un dictionnaire déjà substitué."""
    if not isinstance(raw, dict):
        raise ConfigError("le document racine doit être un mapping")

    version = int(raw.get("version", 0))
    if version != 1:
        raise ConfigError(f"version de configuration non supportée : {version}", field="version")

    paths_raw = raw.get("paths") or {}
    for key in ("data_root", "workspaces", "artifacts", "logs"):
        if key not in paths_raw:
            raise ConfigError(f"chemin « {key} » manquant", field="paths")
    paths = Paths(
        data_root=str(paths_raw["data_root"]),
        workspaces=str(paths_raw["workspaces"]),
        artifacts=str(paths_raw["artifacts"]),
        logs=str(paths_raw["logs"]),
    )

    pools_raw = (raw.get("compute") or {}).get("pools") or {}
    pools = {pid: ComputePool.parse(pid, data or {}) for pid, data in pools_raw.items()}

    models_raw = raw.get("models") or {}
    roles = {
        name: ModelRole.parse(name, data or {}) for name, data in (models_raw.get("roles") or {}).items()
    }
    endpoints = tuple(
        ModelEndpoint.parse(e, where=f"models.endpoints[{i}]")
        for i, e in enumerate(models_raw.get("endpoints") or [])
    )

    languages = {
        name: LanguageToolchain.parse(name, data or {})
        for name, data in (raw.get("languages") or {}).items()
    }

    services_raw = raw.get("services") or {}
    services = {}
    for name, data in services_raw.items():
        data = data or {}
        if "host" not in data or "port" not in data:
            raise ConfigError("host et port requis", field=f"services.{name}")
        services[name] = ServiceEndpoint(
            host=str(data["host"]),
            port=int(data["port"]),
            required=as_bool(data.get("required", True), field=f"services.{name}.required"),
        )

    config = ApexConfig(
        version=version,
        phase=int(raw.get("phase", 1)),
        paths=paths,
        pools=pools,
        model_roles=roles,
        endpoints=endpoints,
        external=ExternalPolicy.parse(raw.get("external") or {}),
        budget=ComputeBudget.parse(raw.get("budget") or {}),
        languages=languages,
        services=services,
        sandbox=SandboxDefaults.parse(raw.get("sandbox") or {}),
        gate=GatePolicy.parse(raw.get("gate") or {}),
        scheduler=SchedulerPolicy.parse(raw.get("scheduler") or {}),
        routing=RouterScoringPolicy.parse(raw.get("routing") or {}),
    )
    config.validate()
    return config


def load_config(path: str | os.PathLike[str] | None = None, *, environ: dict[str, str] | None = None) -> ApexConfig:
    """Charge, substitue puis valide la configuration."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement
        raise DependencyMissing(
            "PyYAML", remediation="uv sync  (ou : pip install pyyaml)"
        ) from exc

    config_path = Path(path or os.environ.get("APEX_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.is_file():
        raise ConfigError("fichier de configuration introuvable", path=str(config_path))

    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide : {exc}", path=str(config_path)) from exc

    try:
        return parse_config(substitute_env(document, environ))
    except ConfigError as exc:
        exc.path = exc.path or str(config_path)
        raise
