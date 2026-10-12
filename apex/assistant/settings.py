"""Paramètres de l'application — sept sections, chacune validée (bornes comprises) et persistée en base
(`app_settings`, une ligne par section). Une section absente ou devenue invalide reprend ses valeurs par défaut
(et le problème est rapporté), jamais une valeur hors bornes. Les réglages du runtime du modèle (précision,
threads) sont appliqués au runtime natif dès l'enregistrement."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Appearance(_Section):
    theme: Literal["night", "dune"] = "night"
    animations: bool = True
    particles: bool = True
    #: `system` : suit le réglage « réduire les animations » du système ; `always` : toujours réduites.
    reduce_motion: Literal["system", "always", "never"] = "system"


class ModelSettings(_Section):
    #: Moteur de réponse (`apex.assistant.engines`) : `auto` (Ollama puis moteur intégré, jamais le
    #: Cerveau), `ollama`, `integrated` (Qwen 1.5B exécuté par APEX) ou `brain` (Cerveau APEX,
    #: expérimental).
    engine: Literal["auto", "ollama", "integrated", "brain"] = "auto"
    #: `auto` : le routage d'APEX choisit parmi les modèles du moteur ; sinon l'identifiant d'un modèle.
    preferred_model: str = Field(default="auto", max_length=200)
    temperature: float = Field(default=0.4, ge=0.0, le=1.5)
    max_tokens: int = Field(default=768, ge=64, le=4096)


class RuntimeSettings(_Section):
    #: Délai d'une génération. Sur processeur, le modèle 1,5B produit quelques tokens par seconde (mesuré :
    #: 3 à 4,3 tokens/s sur un Xeon 4 threads) : 60 s ne suffisent pas pour une réponse complète.
    generation_timeout_s: int = Field(default=900, ge=30, le=3600)


class Behavior(_Section):
    default_mode: Literal["normal", "code", "design", "finance", "3d"] = "normal"
    use_memory: bool = True
    history_chars: int = Field(default=6000, ge=1000, le=24000)


class ToolSettings(_Section):
    workspace_write: bool = True
    code_execution: bool = True
    max_tool_steps: int = Field(default=6, ge=1, le=12)


class Privacy(_Section):
    #: Indexer les échanges dans la mémoire d'APEX (rappelés dans d'autres conversations).
    index_conversations: bool = True
    attachments_enabled: bool = True


class Resources(_Section):
    model_precision: Literal["auto", "float32", "bfloat16"] = "auto"
    cpu_threads: int = Field(default=0, ge=0, le=64)


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appearance: Appearance = Field(default_factory=lambda: Appearance())
    models: ModelSettings = Field(default_factory=lambda: ModelSettings())
    runtime: RuntimeSettings = Field(default_factory=lambda: RuntimeSettings())
    behavior: Behavior = Field(default_factory=lambda: Behavior())
    tools: ToolSettings = Field(default_factory=lambda: ToolSettings())
    privacy: Privacy = Field(default_factory=lambda: Privacy())
    resources: Resources = Field(default_factory=lambda: Resources())


SECTIONS: dict[str, type[_Section]] = {name: field.annotation for name, field in AppSettings.model_fields.items()}  # type: ignore[misc]


class SettingsService:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def load(self) -> tuple[AppSettings, list[str]]:
        from apex.db.engine import session_scope
        from apex.db.repositories import AppSettingRepository

        with session_scope(self._session_factory) as session:
            stored = AppSettingRepository(session).all()
        values: dict[str, Any] = {}
        problems: list[str] = []
        for name, section in SECTIONS.items():
            try:
                values[name] = section.model_validate(stored.get(name, {}))
            except ValidationError as exc:
                values[name] = section()
                problems.append(f"{name} : valeurs enregistrées invalides, valeurs par défaut reprises "
                                f"({exc.error_count()} erreur(s))")
        return AppSettings(**values), problems

    def update(self, changes: dict[str, Any]) -> AppSettings:
        """Applique des changements PARTIELS (`{"appearance": {"theme": "dune"}}`) ; tout ou rien : une valeur
        invalide lève `ValueError` et rien n'est enregistré."""
        from apex.db.engine import session_scope
        from apex.db.repositories import AppSettingRepository

        unknown = sorted(set(changes) - set(SECTIONS))
        if unknown:
            raise ValueError(f"section(s) inconnue(s) : {', '.join(unknown)}")
        current, _ = self.load()
        merged = current.model_dump()
        for name, partial in changes.items():
            if not isinstance(partial, dict):
                raise ValueError(f"{name} : un objet est attendu")
            merged[name] = {**merged[name], **partial}
        try:
            updated = AppSettings.model_validate(merged)
        except ValidationError as exc:
            raise ValueError("; ".join(f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}" for e in exc.errors())) from exc
        with session_scope(self._session_factory) as session:
            repo = AppSettingRepository(session)
            now = datetime.now(UTC)
            for name in changes:
                repo.put(name, getattr(updated, name).model_dump(), updated_at=now)
        apply_runtime(updated)
        return updated


def apply_runtime(settings: AppSettings) -> bool:
    """Précision et threads du runtime natif (sans effet si le backend natif n'est pas installé)."""
    try:
        from apex.models import native
    except ImportError:  # pragma: no cover - runtime natif absent de l'installation
        return False
    return native.configure(precision=settings.resources.model_precision, threads=settings.resources.cpu_threads)
