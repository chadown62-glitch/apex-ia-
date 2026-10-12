"""Backend `apex_brain` : le Cerveau APEX (`ApexLM`, le réseau construit de zéro par APEX), servi dans le
processus APEX par son propre moteur d'inférence (`apex.brain.inference`).

Deux générations, reconnues à leurs fichiers (jamais supposées) :

- **v1** (1,9 M paramètres, contexte 128) : pré-entraîné sur des textes techniques, jamais instruit. Il
CONTINUE
  le texte reçu : il ne reçoit que le dernier message de l'utilisateur ; EXPÉRIMENTAL, dit comme tel.
- **v2** (tokenizer de conversation) : pré-entraîné puis instruit à converser ; il reçoit la conversation
  structurée (consignes système, tours précédents, message) dans son gabarit (`apex.brain.chat`), tronquée
  par le début pour tenir dans son contexte. Poids éventuellement en APEX-Q5.

Sa sortie est rendue telle quelle, jamais retouchée, jamais complétée par un autre modèle. Chaque réponse
porte ses mesures réelles (jetons, délai avant le premier jeton, débit, mémoire, périphérique).

Mémoire : un Cerveau inutilisé pendant `APEX_BRAIN_IDLE_UNLOAD_S` secondes (défaut 600) est déchargé (RAM et
VRAM libérées) ; il est rechargé, empreintes revérifiées, à la demande suivante. Réglages :
`APEX_BRAIN_DEVICE` (`auto` | `cpu` | `cuda`), `APEX_BRAIN_WEIGHTS` (`dequantized` | `q5_resident`),
`APEX_BRAIN_THREADS`, `APEX_BRAIN_REPETITION_PENALTY` (défaut 1.1)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from apex.config.schema import ModelEndpoint
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider

BACKEND = "apex_brain"
#: Plafond de jetons produits par le Cerveau v1 : au-delà, un modèle non instruit de cette taille ne fait
#: que dériver.
MAX_NEW_TOKENS = 160
#: Plafond par défaut d'une réponse du Cerveau v2 (borné en plus par son contexte).
V2_MAX_NEW_TOKENS = 512
_NEW_MESSAGE = "Nouveau message de l'utilisateur :\n"

_lock = threading.Lock()
_engines: dict[str, Any] = {}
_inspected: dict[tuple[str, int, int, int, int], Any] = {}
_reaper: threading.Thread | None = None


def brain_dir(endpoint: ModelEndpoint) -> Path | None:
    if (
        not endpoint.url.startswith("file://")
        or not endpoint.served_model
        or "/" in endpoint.served_model
        or "\\" in endpoint.served_model
        or endpoint.served_model in (".", "..")
    ):
        return None
    return Path(endpoint.url[len("file://") :]) / endpoint.served_model


def _libraries() -> str | None:
    import importlib.util

    for name in ("torch", "safetensors"):
        if importlib.util.find_spec(name) is None:
            return name
    return None


def last_user_message(prompt: str) -> str:
    """Le message de l'utilisateur, sans l'historique composé par l'assistant."""
    return prompt.rsplit(_NEW_MESSAGE, 1)[-1].strip() if _NEW_MESSAGE in prompt else prompt.strip()


def is_conversational(path: Path) -> bool:
    """Le Cerveau de ce répertoire sait-il converser (tokenizer de génération 2) ? Lu dans ses fichiers."""
    try:
        data = json.loads((path / "tokenizer.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return "<|assistant|>" in (data.get("special_tokens") or [])


def measured_quality(evaluation: Any) -> str:
    """La qualité MESURÉE du Cerveau (résumé d'évaluation écrit dans sa provenance), en clair ; jamais
    supposée : sans évaluation, c'est dit."""
    if not isinstance(evaluation, dict) or evaluation.get("tasks_rate") is None:
        return "qualité non évaluée"
    when = str(evaluation.get("measured_at") or "")[:10]
    rate = f"{evaluation['tasks_rate'] * 100:.0f} %"
    return f"évalué{f' le {when}' if when else ''} : {rate} d'exercices jamais vus réussis"


def _idle_limit() -> float:
    try:
        return float(os.environ.get("APEX_BRAIN_IDLE_UNLOAD_S", "600"))
    except ValueError:
        return 600.0


def _reap_forever() -> None:
    while True:
        time.sleep(30)
        unload_idle()


def unload_idle(now: float | None = None) -> list[str]:
    """Décharge les Cerveaux inutilisés depuis plus que la limite ; rend leurs chemins."""
    limit = _idle_limit()
    now = time.time() if now is None else now
    released = []
    with _lock:
        for key, engine in _engines.items():
            if engine.loaded and not engine.lock.locked() and now - engine.last_used > limit:
                engine.unload()
                released.append(key)
    return released


def unload_all() -> None:
    with _lock:
        for engine in _engines.values():
            engine.unload()
        _engines.clear()


def engine_for(path: Path) -> Any:
    """Le moteur partagé de ce Cerveau (un seul chargement par processus)."""
    global _reaper
    from apex.brain.inference import BrainEngine

    key = str(path)
    with _lock:
        engine = _engines.get(key)
        if engine is None:
            engine = BrainEngine(
                path,
                device=os.environ.get("APEX_BRAIN_DEVICE", "auto"),
                weights=os.environ.get("APEX_BRAIN_WEIGHTS", "dequantized"),
                threads=int(os.environ.get("APEX_BRAIN_THREADS", "0") or 0),
            )
            _engines[key] = engine
        if _reaper is None:
            _reaper = threading.Thread(target=_reap_forever, name="apex-brain-unload", daemon=True)
            _reaper.start()
    return engine


class ApexBrainProvider(ModelProvider):
    def __init__(self, endpoint: ModelEndpoint) -> None:
        self._endpoint = endpoint

    def _inspection(self, path: Path) -> Any:
        from apex.brain.artifact import inspect, weights_file

        try:
            provenance = json.loads((path / "provenance.json").read_text(encoding="utf-8"))
            stat = (path / weights_file(provenance)).stat()
            # La provenance compte aussi : une évaluation ajoutée après l'export est vue sans redémarrer.
            described = (path / "provenance.json").stat()
            key = (str(path), stat.st_size, stat.st_mtime_ns, described.st_size, described.st_mtime_ns)
        except (OSError, ValueError):
            return inspect(path)
        with _lock:
            if key not in _inspected:
                _inspected[key] = inspect(path)
            return _inspected[key]

    def detect(self) -> ModelCapabilityReport:
        from apex.obs.capability import CapabilityStatus

        missing = _libraries()
        if missing is not None:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV,
                backend=BACKEND,
                detail=f"bibliothèque absente ({missing}) : « Préparer APEX » l'installe",
            )
        path = brain_dir(self._endpoint)
        if path is None:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNSUPPORTED,
                backend=BACKEND,
                detail=f"endpoint du Cerveau APEX mal formé : {self._endpoint.url}",
            )
        found = self._inspection(path)
        if not found.ok:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV,
                backend=BACKEND,
                detail=found.detail,
                facts={"path": str(path)},
            )
        provenance = found.provenance or {}
        conversational = is_conversational(path)
        parameters = provenance.get("parameters", "?")
        if conversational:
            quantization = provenance.get("quantization") or "float32"
            detail = (
                f"Cerveau APEX « {provenance.get('name', path.name)} » — {parameters} paramètres, "
                f"construit et "
                f"entraîné de zéro par APEX, instruit à converser ; poids {quantization}"
                f" ; {measured_quality(provenance.get('evaluation'))}"
            )
        else:
            detail = (
                f"Cerveau APEX « {provenance.get('name', path.name)} » — {parameters} paramètres, "
                "EXPÉRIMENTAL : pré-entraîné sur des textes techniques, pas encore entraîné à converser"
            )
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE,
            backend=BACKEND,
            detail=detail,
            facts={
                "path": str(path),
                "experimental": not conversational,
                "conversational": conversational,
                "provenance": provenance.get("name"),
                "model_fingerprint": provenance.get("model_fingerprint"),
                "quantization": provenance.get("quantization"),
                "evaluation": provenance.get("evaluation"),
            },
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from apex.brain.chat import Message
        from apex.brain.inference import GenerationCancelled, GenerationTimeout, SamplingSettings
        from apex.errors import ModelErrorCategory, ModelProviderError, TimeoutExceeded
        from apex.obs.status import Measurement

        report = self.detect()
        if not report.is_available:
            raise ModelProviderError(
                "ApexBrainProvider.generate",
                endpoint_url=self._endpoint.url,
                detail=report.detail,
                category=ModelErrorCategory.OFFLINE,
            )
        if request.cancel_event is not None and request.cancel_event.is_set():
            raise ModelProviderError(
                "ApexBrainProvider.generate",
                endpoint_url=self._endpoint.url,
                detail="génération annulée avant son début",
            )
        started = time.perf_counter()
        deadline = started + request.timeout_s
        path = brain_dir(self._endpoint)
        assert path is not None
        engine = engine_for(path)
        expected = report.facts.get("model_fingerprint")
        if engine.loaded and expected and engine.fingerprint != expected:
            # Cerveau réinstallé pendant qu'APEX tourne (entraînement terminé sous WSL, par exemple) : les
            # poids en mémoire ne sont plus ceux, vérifiés, du disque. Rechargés, jamais servis périmés.
            with engine.lock:
                engine.unload()
        try:
            just_loaded = engine.load()
        except Exception as exc:
            raise ModelProviderError(
                "ApexBrainProvider.generate",
                endpoint_url=self._endpoint.url,
                detail=f"chargement impossible : {exc.__class__.__name__}: {str(exc)[:300]}",
                category=ModelErrorCategory.PROVIDER_ERROR,
            ) from exc
        conversational = bool(report.facts.get("conversational"))
        temperature = request.temperature if request.temperature is not None else 0.0
        try:
            penalty = float(os.environ.get("APEX_BRAIN_REPETITION_PENALTY", "1.1"))
        except ValueError:
            penalty = 1.1
        try:
            if conversational:
                limit = request.max_tokens or V2_MAX_NEW_TOKENS
                settings = SamplingSettings(
                    max_new_tokens=min(limit, engine.context_length // 2),
                    temperature=temperature,
                    top_k=40 if temperature > 0 else 0,
                    top_p=0.95 if temperature > 0 else 1.0,
                    repetition_penalty=penalty,
                    seed=request.seed,
                )
                messages = _conversation(request)
                result = engine.chat(
                    [Message(role, content) for role, content in messages],
                    settings,
                    cancel=request.cancel_event,
                    deadline=deadline,
                )
                prompt_used = (
                    f"conversation structurée : {len(messages)} message(s), "
                    f"{result.stats.truncated_messages} ancien(s) retiré(s) pour tenir dans le contexte "
                    f"de {engine.context_length} jetons"
                )
            else:
                tokenizer = engine.tokenizer
                context = engine.context_length
                text = last_user_message(request.prompt)
                prompt_ids = (
                    tokenizer.encode(text, add_bos=True)[-(context - 1) :] if text else [tokenizer.bos_id]
                )
                settings = SamplingSettings(
                    max_new_tokens=min(request.max_tokens or MAX_NEW_TOKENS, MAX_NEW_TOKENS),
                    temperature=temperature,
                    seed=request.seed,
                )
                result = engine.generate_ids(
                    prompt_ids,
                    settings,
                    stop=frozenset({tokenizer.eos_id}),
                    cancel=request.cancel_event,
                    deadline=deadline,
                )
                prompt_used = (
                    f"dernier message de l'utilisateur seulement (contexte de {context} jetons, modèle "
                    "non instruit)"
                )
        except GenerationCancelled as exc:
            raise ModelProviderError(
                "ApexBrainProvider.generate", endpoint_url=self._endpoint.url, detail="génération annulée"
            ) from exc
        except GenerationTimeout as exc:
            raise TimeoutExceeded("ApexBrainProvider.generate", request.timeout_s) from exc
        except ValueError as exc:
            raise ModelProviderError(
                "ApexBrainProvider.generate",
                endpoint_url=self._endpoint.url,
                detail=str(exc),
                category=ModelErrorCategory.CONTEXT_TOO_LONG,
            ) from exc
        stats = result.stats
        return GenerationResult(
            text=result.text,
            model_used=self._endpoint.served_model,
            tokens_prompt=Measurement.real(stats.prompt_tokens),
            tokens_completion=Measurement.real(stats.generated_tokens),
            finish_reason=stats.finish_reason,
            elapsed_s=time.perf_counter() - started,
            facts={
                "backend": BACKEND,
                "device": stats.device,
                # Moteur partagé peut-être déchargé entre-temps (Cerveau réinstallé) : pas de plantage.
                "dtype": str(next(engine.model.parameters()).dtype) if engine.model is not None else None,
                "experimental": not conversational,
                "conversational": conversational,
                "weights": stats.weights,
                "quantization": engine.provenance.get("quantization"),
                "loaded_now": just_loaded,
                "load_s": round(engine.load_s, 3) if just_loaded else None,
                "generation_s": stats.decode_s,
                "time_to_first_token_s": stats.time_to_first_token_s,
                "tokens_per_s": stats.tokens_per_s,
                "prefill_tokens_per_s": stats.prefill_tokens_per_s,
                "kv_cache_bytes": stats.kv_cache_bytes,
                "weights_bytes": engine.weight_bytes(),
                "seed": request.seed,
                "max_new_tokens": settings.max_new_tokens,
                "model_fingerprint": engine.fingerprint,
                # Calculs exacts demandés par le Cerveau (expression, résultat de la calculatrice).
                "tool_calls": list(stats.tool_calls),
                "prompt_used": prompt_used,
            },
        )


def _conversation(request: GenerationRequest) -> list[tuple[str, str]]:
    """Consignes système + tours. Sans conversation structurée (appelant historique), le prompt à plat
    devient le message de l'utilisateur."""
    messages: list[tuple[str, str]] = []
    if request.system:
        messages.append(("system", request.system))
    if request.messages:
        messages.extend(
            (role, content) for role, content in request.messages if role in ("user", "assistant")
        )
    else:
        messages.append(("user", request.prompt))
    return messages
