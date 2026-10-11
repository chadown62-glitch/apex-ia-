"""Moteurs de réponse de l'assistant — le choix de l'utilisateur, jamais deviné.

- `auto`       : APEX choisit parmi les modèles conversationnels déclarés (Ollama, puis moteur intégré),
                 avec repli ; JAMAIS le Cerveau APEX (expérimental, il ne sait pas converser).
- `ollama`     : seulement les modèles servis par Ollama sur ce poste.
- `integrated` : seulement le moteur intégré (`transformers`, Qwen2.5-Coder 1.5B exécuté par APEX
                 lui-même, sans Ollama).
- `brain`      : le Cerveau APEX (`apex_brain`, ApexLM, le modèle construit et entraîné de zéro par
                 APEX) : la génération la plus récente installée (v2, instruite à converser, sinon v1,
                 expérimentale).

Le Cerveau n'est pas un candidat de la configuration (`config*.yaml`) : il n'est donc jamais choisi par le
routage automatique, ni par les tâches, missions ou `apex solve`. Il n'existe que comme choix explicite
ici."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

ENGINES = ("auto", "ollama", "integrated", "brain")
#: Runtime d'un endpoint -> moteur. Les autres runtimes (API compatibles OpenAI…) : seulement en `auto`.
RUNTIME_ENGINE = {"ollama": "ollama", "transformers": "integrated", "apex_brain": "brain"}
#: Rôles avec lesquels l'assistant génère (mêmes que la ligne « Modèles » du statut).
GENERATION_ROLES = ("coder_main", "fast")


@dataclass(frozen=True)
class EngineInfo:
    id: str
    label: str
    description: str
    experimental: bool = False


INFO = {
    "auto": EngineInfo("auto", "Automatique",
                       "APEX choisit parmi les modèles disponibles (Ollama, puis le moteur intégré), avec "
                       "repli. Jamais le Cerveau APEX."),
    "ollama": EngineInfo("ollama", "Ollama",
                         "Les modèles servis par Ollama sur ce poste (serveur Ollama démarré)."),
    "integrated": EngineInfo("integrated", "APEX intégré",
                             "Qwen2.5-Coder 1.5B exécuté directement par APEX, sans Ollama ni serveur."),
    "brain": EngineInfo("brain", "Cerveau APEX",
                        "ApexLM, le modèle de langage construit et entraîné de zéro par APEX, exécuté par "
                        "APEX lui-même. Petit modèle : ses réponses doivent être vérifiées.",
                        experimental=True),
}


#: Générations du Cerveau, de la plus récente à la plus ancienne : la première installée est servie.
BRAIN_GENERATIONS = ("apex-brain-v2", "apex-brain-v1")


def brain_candidate() -> Any:
    """Le Cerveau APEX servi : la génération la plus récente présente dans les dossiers des Cerveaux
    (`apex.brain.artifact.brain_roots` : celui de l'utilisateur d'abord, puis celui de l'application),
    décrite d'après SES fichiers (contexte, quantification)."""
    import json

    from apex.brain.artifact import DEFAULT_NAME, brain_roots, default_root
    from apex.config.schema import ModelEndpoint, ModelRef
    from apex.models.registry import ModelCandidate

    found = next(((root, name) for name in BRAIN_GENERATIONS for root in brain_roots()
                  if (root / name / "provenance.json").is_file()), None)
    root, name = found if found is not None else (default_root(), DEFAULT_NAME)
    version = name.rsplit("-", 1)[-1]
    try:
        config = json.loads((root / name / "config.json").read_text(encoding="utf-8"))
        provenance = json.loads((root / name / "provenance.json").read_text(encoding="utf-8"))
        context, quantization = int(config["context_length"]), provenance.get("quantization")
    except (OSError, ValueError, KeyError, TypeError):
        context, quantization = 128, None
    ref = ModelRef(family="apex-brain", version=version, quantization="apex-q5" if quantization else "fp32",
                   capability_class="experimental" if version == "v1" else "generalist")
    endpoint = ModelEndpoint(pool_id="local", runtime="apex_brain", url=f"file://{root}", served_model=name,
                             max_context=context)
    return ModelCandidate(role="assistant", ref=ref, resident=False, endpoint=endpoint)


def generation_candidates(config: Any) -> list[Any]:
    from apex.models.registry import resolve_candidates

    return [c for c in resolve_candidates(config)
            if c.role in GENERATION_ROLES and c.endpoint is not None and c.endpoint.enabled]


def candidates_for(engine: str, config: Any) -> list[Any]:
    if engine == "brain":
        return [brain_candidate()]
    candidates = generation_candidates(config)
    if engine == "auto":
        return candidates
    return [c for c in candidates if RUNTIME_ENGINE.get(c.endpoint.runtime) == engine]


def describe(config: Any, *, detect: Any = None) -> list[dict[str, Any]]:
    """Chaque moteur, ses modèles et leur disponibilité MESURÉE (détections réelles, en parallèle ; rien
    n'est chargé ni téléchargé)."""
    from apex.models.provider import get_default_provider

    detect = detect or (lambda endpoint: get_default_provider(endpoint).detect())
    per_engine = {engine: candidates_for(engine, config) for engine in ENGINES if engine != "auto"}
    endpoints = {(c.endpoint.runtime, c.endpoint.url, c.endpoint.served_model): c.endpoint
                 for candidates in per_engine.values() for c in candidates}
    reports: dict[tuple[str, str, str], Any] = {}
    if endpoints:
        with ThreadPoolExecutor(max_workers=len(endpoints), thread_name_prefix="apex-engines") as pool:
            futures = {key: pool.submit(detect, endpoint) for key, endpoint in endpoints.items()}
            reports = {key: future.result() for key, future in futures.items()}
    out: list[dict[str, Any]] = []
    for engine in ENGINES:
        info = INFO[engine]
        if engine == "auto":
            models = [m for e in ("ollama", "integrated") for m in _models(per_engine[e], reports)]
        else:
            models = _models(per_engine[engine], reports)
        available = [m for m in models if m["available"]]
        detail = (f"{len(available)} modèle(s) disponible(s)" if available
                  else (models[0]["detail"] if models else "aucun modèle déclaré pour ce moteur"))
        experimental = info.experimental
        if engine == "brain" and available:
            experimental = not all(m.get("conversational") for m in available)
        out.append({"id": engine, "label": info.label, "description": info.description,
                    "experimental": experimental, "available": bool(available), "detail": detail,
                    "models": models})
    return out


def _models(candidates: list[Any], reports: dict[tuple[str, str, str], Any]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        endpoint = candidate.endpoint
        report = reports[(endpoint.runtime, endpoint.url, endpoint.served_model)]
        rows.setdefault(candidate.ref.id, {
            "id": candidate.ref.id, "served_model": endpoint.served_model, "runtime": endpoint.runtime,
            "available": report.is_available, "detail": report.detail,
            "conversational": bool((report.facts or {}).get("conversational"))})
    return list(rows.values())
