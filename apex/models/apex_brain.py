"""Backend `apex_brain` : le Cerveau APEX (`ApexLM`, le réseau propre à APEX) servi dans le processus APEX.

EXPÉRIMENTAL, et dit comme tel partout : ce modèle (1,9 M paramètres) a été pré-entraîné à prédire la suite
de textes techniques (docstrings de la bibliothèque standard Python, documentation d'APEX) ; il n'a jamais
été entraîné à suivre des consignes ni à converser (aucun jeu instruction/réponse, voir
docs/NATIVE_INTELLIGENCE.md §10). Il CONTINUE le texte reçu : sa sortie est rendue telle quelle, jamais
retouchée, jamais complétée par un autre modèle.

Ce qu'il reçoit : le dernier message de l'utilisateur seulement (son contexte est de 128 jetons ; les
consignes du mode et l'historique ne tiennent pas et n'auraient aucun effet sur un modèle non instruit)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from apex.config.schema import ModelEndpoint
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider

BACKEND = "apex_brain"
#: Plafond de jetons produits : au-delà, un modèle non instruit de cette taille ne fait que dériver.
MAX_NEW_TOKENS = 160
_NEW_MESSAGE = "Nouveau message de l'utilisateur :\n"

_lock = threading.Lock()
_loaded: dict[str, Any] = {}
_inspected: dict[tuple[str, int, float], Any] = {}


def brain_dir(endpoint: ModelEndpoint) -> Path | None:
    if not endpoint.url.startswith("file://") or not endpoint.served_model or "/" in endpoint.served_model \
            or "\\" in endpoint.served_model or endpoint.served_model in (".", ".."):
        return None
    return Path(endpoint.url[len("file://"):]) / endpoint.served_model


def _libraries() -> str | None:
    import importlib.util

    for name in ("torch", "safetensors"):
        if importlib.util.find_spec(name) is None:
            return name
    return None


def last_user_message(prompt: str) -> str:
    """Le message de l'utilisateur, sans l'historique composé par l'assistant."""
    return prompt.rsplit(_NEW_MESSAGE, 1)[-1].strip() if _NEW_MESSAGE in prompt else prompt.strip()


class ApexBrainProvider(ModelProvider):
    def __init__(self, endpoint: ModelEndpoint) -> None:
        self._endpoint = endpoint

    def _inspection(self, path: Path) -> Any:
        from apex.brain.artifact import WEIGHTS, inspect

        weights = path / WEIGHTS
        try:
            stat = weights.stat()
            key = (str(path), stat.st_size, stat.st_mtime)
        except OSError:
            return inspect(path)
        with _lock:
            if key not in _inspected:
                _inspected[key] = inspect(path)
            return _inspected[key]

    def detect(self) -> ModelCapabilityReport:
        from apex.obs.capability import CapabilityStatus

        missing = _libraries()
        if missing is not None:
            return ModelCapabilityReport(status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend=BACKEND,
                                         detail=f"bibliothèque absente ({missing}) : « Préparer APEX » l'installe")
        path = brain_dir(self._endpoint)
        if path is None:
            return ModelCapabilityReport(status=CapabilityStatus.UNSUPPORTED, backend=BACKEND,
                                         detail=f"endpoint du Cerveau APEX mal formé : {self._endpoint.url}")
        found = self._inspection(path)
        if not found.ok:
            return ModelCapabilityReport(status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend=BACKEND,
                                         detail=found.detail, facts={"path": str(path)})
        provenance = found.provenance or {}
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend=BACKEND,
            detail=(f"Cerveau APEX « {provenance.get('name', path.name)} » — "
                    f"{provenance.get('parameters', '?')} paramètres, EXPÉRIMENTAL : pré-entraîné sur des textes "
                    "techniques, pas encore entraîné à converser"),
            facts={"path": str(path), "experimental": True, "provenance": provenance.get("name"),
                   "model_fingerprint": provenance.get("model_fingerprint")},
        )

    def _load(self, path: Path) -> tuple[Any, bool]:
        from apex.brain.artifact import load

        with _lock:
            current = _loaded.get(str(path))
            if current is not None:
                return current, False
            loaded = load(path)
            _loaded[str(path)] = loaded
            return loaded, True

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from apex.errors import ModelErrorCategory, ModelProviderError, TimeoutExceeded
        from apex.obs.status import Measurement

        report = self.detect()
        if not report.is_available:
            raise ModelProviderError("ApexBrainProvider.generate", endpoint_url=self._endpoint.url,
                                     detail=report.detail, category=ModelErrorCategory.OFFLINE)
        if request.cancel_event is not None and request.cancel_event.is_set():
            raise ModelProviderError("ApexBrainProvider.generate", endpoint_url=self._endpoint.url,
                                     detail="génération annulée avant son début")
        started = time.perf_counter()
        deadline = started + request.timeout_s
        path = brain_dir(self._endpoint)
        assert path is not None
        try:
            brain, just_loaded = self._load(path)
        except Exception as exc:
            raise ModelProviderError("ApexBrainProvider.generate", endpoint_url=self._endpoint.url,
                                     detail=f"chargement impossible : {exc.__class__.__name__}: {str(exc)[:300]}",
                                     category=ModelErrorCategory.PROVIDER_ERROR) from exc
        import torch

        tokenizer, model = brain.tokenizer, brain.model
        context = model.config.context_length
        text = last_user_message(request.prompt)
        prompt_ids = tokenizer.encode(text, add_bos=True)[-(context - 1):] if text else [tokenizer.bos_id]
        max_new = min(request.max_tokens or MAX_NEW_TOKENS, MAX_NEW_TOKENS)
        generator = torch.Generator().manual_seed(request.seed) if request.seed is not None else None
        temperature = request.temperature if request.temperature is not None else 0.0
        ids = torch.tensor([prompt_ids], dtype=torch.long)
        produced: list[int] = []
        finish = "length"
        gen_started = time.perf_counter()
        with torch.inference_mode():
            for _ in range(max_new):
                if request.cancel_event is not None and request.cancel_event.is_set():
                    raise ModelProviderError("ApexBrainProvider.generate", endpoint_url=self._endpoint.url,
                                             detail="génération annulée")
                if time.perf_counter() > deadline:
                    raise TimeoutExceeded("ApexBrainProvider.generate", request.timeout_s)
                logits, _ = model(ids[:, -context:])
                last = logits[0, -1].float()
                if temperature <= 0:
                    token = int(last.argmax())
                else:
                    probabilities = torch.softmax(last / temperature, dim=-1)
                    token = int(torch.multinomial(probabilities, 1, generator=generator))
                if token == tokenizer.eos_id:
                    finish = "stop"
                    break
                produced.append(token)
                ids = torch.cat([ids, torch.tensor([[token]], dtype=torch.long)], dim=1)
        gen_s = time.perf_counter() - gen_started
        return GenerationResult(
            text=tokenizer.decode(produced),
            model_used=self._endpoint.served_model,
            tokens_prompt=Measurement.real(len(prompt_ids)), tokens_completion=Measurement.real(len(produced)),
            finish_reason=finish, elapsed_s=time.perf_counter() - started,
            facts={"backend": BACKEND, "device": "cpu", "dtype": model.config.dtype, "experimental": True,
                   "loaded_now": just_loaded, "load_s": round(brain.load_s, 3) if just_loaded else None,
                   "generation_s": round(gen_s, 3),
                   "tokens_per_s": round(len(produced) / gen_s, 2) if gen_s > 0 else None,
                   "seed": request.seed, "max_new_tokens": max_new, "model_fingerprint": brain.fingerprint,
                   "prompt_used": "dernier message de l'utilisateur seulement (contexte de "
                                  f"{context} jetons, modèle non instruit)"},
        )
