"""GET /models, GET /providers, POST /models/discover — projection HTTP du
Model/Provider Catalog (`apex.models.catalog`), déjà réel. Aucune donnée
recalculée ici : ces routes ne font que sérialiser `build_model_catalog()`/
`build_provider_catalog()`/`discover_live()`, la même discipline que
`/registry` (aucune duplication de logique).

`GET /models`/`GET /providers` sont des vues STATIQUES — jamais d'appel
réseau implicite (même règle que `apex.models.catalog.build_model_catalog`) :
`availability` y reste `"unknown"` pour tout candidat non désactivé. Seul
`POST /models/discover` touche réellement le réseau (`ModelProvider.
detect()` par candidat), sur demande explicite du client — jamais déclenché
par un simple GET.
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import CatalogModelOut, DataEnvelope, ProviderInfoOut

router = APIRouter(tags=["models"])


def _to_catalog_model_out(model: object) -> CatalogModelOut:
    candidate = model.candidate  # type: ignore[attr-defined]
    endpoint = candidate.endpoint
    return CatalogModelOut(
        model_id=model.model_id,  # type: ignore[attr-defined]
        display_name=model.display_name,  # type: ignore[attr-defined]
        role=candidate.role,
        family=candidate.ref.family,
        version=candidate.ref.version,
        quantization=candidate.ref.quantization,
        capability_class=candidate.ref.capability_class,
        is_local=candidate.is_local,
        is_declared_available=candidate.is_available,
        capabilities=sorted(c.value for c in model.capabilities),  # type: ignore[attr-defined]
        availability=model.availability.value,  # type: ignore[attr-defined]
        discovery=model.discovery.value,  # type: ignore[attr-defined]
        provider_id=model.provider_id,  # type: ignore[attr-defined]
        runtime=endpoint.runtime if endpoint is not None else None,
        served_model=endpoint.served_model if endpoint is not None else None,
        max_context=endpoint.max_context if endpoint is not None else None,
        vram_gb=endpoint.vram_gb if endpoint is not None else None,
    )


def _to_provider_info_out(provider: object) -> ProviderInfoOut:
    return ProviderInfoOut(
        id=provider.id,  # type: ignore[attr-defined]
        display_name=provider.display_name,  # type: ignore[attr-defined]
        runtime=provider.runtime,  # type: ignore[attr-defined]
        base_url=provider.base_url,  # type: ignore[attr-defined]
        secret_key=provider.secret_key,  # type: ignore[attr-defined]
        local=provider.local,  # type: ignore[attr-defined]
        enabled=provider.enabled,  # type: ignore[attr-defined]
        model_ids=list(provider.model_ids),  # type: ignore[attr-defined]
    )


@router.get("/models", response_model=DataEnvelope[list[CatalogModelOut]])
def list_models() -> DataEnvelope[list[CatalogModelOut]]:
    try:
        from apex.config.loader import load_config
        from apex.models.catalog import build_model_catalog

        catalog = build_model_catalog(load_config())
        views = [_to_catalog_model_out(m) for m in catalog]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.get("/providers", response_model=DataEnvelope[list[ProviderInfoOut]])
def list_providers() -> DataEnvelope[list[ProviderInfoOut]]:
    try:
        from apex.config.loader import load_config
        from apex.models.catalog import build_provider_catalog

        providers = build_provider_catalog(load_config())
        views = [_to_provider_info_out(p) for p in providers]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.post("/models/discover", response_model=DataEnvelope[list[CatalogModelOut]])
def discover_models() -> DataEnvelope[list[CatalogModelOut]]:
    """SEUL point de ce module qui touche le réseau — un `ModelProvider.
    detect()` réel par candidat déclaré (`apex.models.catalog.
    discover_live`). Jamais appelé automatiquement par le frontend au
    chargement d'une page : c'est une action explicite (bouton « Vérifier
    la connexion »)."""
    try:
        from apex.config.loader import load_config
        from apex.models.catalog import build_model_catalog, discover_live

        catalog = build_model_catalog(load_config())
        resolved = discover_live(catalog)
        views = [_to_catalog_model_out(m) for m in resolved]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)
