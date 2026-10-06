"""Backend d'inférence NATIF d'APEX (`runtime: transformers`) — le modèle est chargé et exécuté DANS le
processus APEX, sans aucun serveur de modèles (ni Ollama, ni serveur HTTP).

APEX reste propriétaire de l'abstraction (`apex.models.provider.ModelProvider`) ; PyTorch et Hugging Face
`transformers` ne sont que la bibliothèque de calcul (chargement des poids, tokenisation, génération).

Où sont les poids : `endpoint.url` = `file://<répertoire des modèles>`, `endpoint.served_model` = nom du
sous-répertoire (format Hugging Face : `config.json`, poids `safetensors`, fichiers du tokenizer). Rien n'est
JAMAIS téléchargé ici : des poids absents rendent le backend indisponible (`detect`), avec la commande à lancer
(`apex models fetch`, action explicite de l'utilisateur, réseau requis).

Exécution : GPU CUDA s'il est réellement présent (bf16), sinon CPU (float32). Une génération à la fois par
modèle chargé (verrou) ; un délai (`timeout_s`) et une annulation (`cancel_event`) arrêtent la génération entre
deux tokens ; une graine (`seed`) rend l'échantillonnage reproductible.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from apex.config.schema import ModelEndpoint
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider

BACKEND = "native_transformers"
#: Plafond de tokens générés quand l'appelant n'en fixe pas (un code complet tient largement dedans).
DEFAULT_MAX_NEW_TOKENS = 1024
_REQUIRED_FILES = ("config.json",)
_TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "vocab.json", "tokenizer.model")


def weights_dir(endpoint: ModelEndpoint) -> Path | None:
    """Répertoire des poids, ou `None` si `endpoint.url` n'est pas une URL `file://`."""
    parsed = urlparse(endpoint.url)
    if parsed.scheme != "file":
        return None
    base = Path(unquote(parsed.netloc + parsed.path)).expanduser()
    name = endpoint.served_model
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None  # un nom de modèle, jamais un chemin (aucune sortie du répertoire des modèles)
    return base / name


def _libraries() -> str | None:
    """`None` si torch et transformers sont importables, sinon la raison."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception as exc:  # ImportError, mais aussi une roue torch cassée
        return f"{exc.__class__.__name__}: {exc}"
    return None


@dataclass
class _Loaded:
    model: Any
    tokenizer: Any
    device: str
    dtype: str
    load_s: float
    lock: threading.Lock


_CACHE: dict[str, _Loaded] = {}
_CACHE_LOCK = threading.Lock()


def unload_all() -> None:
    """Libère les modèles chargés (tests, changement de configuration)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _load(path: Path) -> tuple[_Loaded, bool]:
    key = str(path.resolve())
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key], False
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        started = time.perf_counter()
        cuda = torch.cuda.is_available()
        dtype = torch.bfloat16 if cuda else torch.float32
        tokenizer = AutoTokenizer.from_pretrained(key, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(key, local_files_only=True, torch_dtype=dtype)
        device = "cuda" if cuda else "cpu"
        model.to(device)
        model.eval()
        loaded = _Loaded(model=model, tokenizer=tokenizer, device=device, dtype=str(dtype).replace("torch.", ""),
                         load_s=time.perf_counter() - started, lock=threading.Lock())
        _CACHE[key] = loaded
        return loaded, True


class NativeTransformersProvider(ModelProvider):
    def __init__(self, endpoint: ModelEndpoint) -> None:
        self._endpoint = endpoint

    def detect(self) -> ModelCapabilityReport:
        from apex.obs.capability import CapabilityStatus

        missing_lib = _libraries()
        if missing_lib is not None:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend=BACKEND,
                detail=f"bibliothèques d'inférence absentes ({missing_lib}) — installer l'extra « native »",
                facts={"libraries": False},
            )
        path = weights_dir(self._endpoint)
        if path is None:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNSUPPORTED, backend=BACKEND,
                detail=(f"endpoint natif mal formé : url « {self._endpoint.url} » (file:// attendu), "
                        f"served_model « {self._endpoint.served_model} » (un nom de répertoire attendu)"),
            )
        absent = [f for f in _REQUIRED_FILES if not (path / f).is_file()]
        has_tokenizer = any((path / f).is_file() for f in _TOKENIZER_FILES)
        has_weights = any(path.glob("*.safetensors")) or any(path.glob("*.bin"))
        if absent or not has_tokenizer or not has_weights:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend=BACKEND,
                detail=(f"poids absents ou incomplets dans {path} (config : {not absent}, tokenizer : "
                        f"{has_tokenizer}, poids : {has_weights}) — `apex models fetch` (action explicite, réseau)"),
                facts={"path": str(path), "present": False},
            )
        import torch

        size = sum(f.stat().st_size for f in path.iterdir() if f.is_file())
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend=BACKEND,
            detail=f"poids présents ({size / 1e9:.2f} Go) dans {path}",
            facts={"path": str(path), "present": True, "size_bytes": size,
                   "device": "cuda" if torch.cuda.is_available() else "cpu"},
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from apex.errors import ModelErrorCategory, ModelProviderError, TimeoutExceeded
        from apex.obs.status import Measurement

        report = self.detect()
        if not report.is_available:
            raise ModelProviderError("NativeTransformersProvider.generate", endpoint_url=self._endpoint.url,
                                     detail=report.detail, category=ModelErrorCategory.OFFLINE)
        if request.cancel_event is not None and request.cancel_event.is_set():
            raise ModelProviderError("NativeTransformersProvider.generate", endpoint_url=self._endpoint.url,
                                     detail="génération annulée avant son début")
        started = time.perf_counter()
        deadline = started + request.timeout_s
        path = weights_dir(self._endpoint)
        assert path is not None
        try:
            loaded, just_loaded = _load(path)
        except Exception as exc:
            raise ModelProviderError("NativeTransformersProvider.generate", endpoint_url=self._endpoint.url,
                                     detail=f"chargement impossible : {exc.__class__.__name__}: {str(exc)[:300]}",
                                     category=ModelErrorCategory.PROVIDER_ERROR) from exc
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList

        tokenizer = loaded.tokenizer
        if getattr(tokenizer, "chat_template", None):
            input_ids = tokenizer.apply_chat_template(
                [{"role": "user", "content": request.prompt}], add_generation_prompt=True, return_tensors="pt")
        else:
            input_ids = tokenizer(request.prompt, return_tensors="pt").input_ids
        prompt_tokens = int(input_ids.shape[-1])
        budget = self._endpoint.max_context - prompt_tokens
        if budget <= 0:
            raise ModelProviderError("NativeTransformersProvider.generate", endpoint_url=self._endpoint.url,
                                     detail=f"prompt de {prompt_tokens} tokens ≥ contexte {self._endpoint.max_context}",
                                     category=ModelErrorCategory.CONTEXT_TOO_LONG)
        max_new = min(request.max_tokens or DEFAULT_MAX_NEW_TOKENS, budget)
        stop_state: dict[str, str] = {}

        class _Stop(StoppingCriteria):
            def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
                if request.cancel_event is not None and request.cancel_event.is_set():
                    stop_state["reason"] = "cancelled"
                    return True
                if time.perf_counter() > deadline:
                    stop_state["reason"] = "timeout"
                    return True
                return False

        kwargs: dict[str, Any] = {"max_new_tokens": max_new, "stopping_criteria": StoppingCriteriaList([_Stop()])}
        if request.temperature is not None:
            if request.temperature <= 0:
                kwargs.update(do_sample=False, temperature=None, top_p=None, top_k=None)
            else:
                kwargs.update(do_sample=True, temperature=request.temperature)
        if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
            kwargs["pad_token_id"] = tokenizer.eos_token_id
        with loaded.lock:
            if request.seed is not None:
                torch.manual_seed(request.seed)
            gen_started = time.perf_counter()
            with torch.inference_mode():
                output = loaded.model.generate(
                    input_ids.to(loaded.device), attention_mask=torch.ones_like(input_ids).to(loaded.device),
                    **kwargs)
            gen_s = time.perf_counter() - gen_started
        new_tokens = output[0][prompt_tokens:]
        if stop_state.get("reason") == "timeout":
            raise TimeoutExceeded("NativeTransformersProvider.generate", request.timeout_s)
        if stop_state.get("reason") == "cancelled":
            raise ModelProviderError("NativeTransformersProvider.generate", endpoint_url=self._endpoint.url,
                                     detail="génération annulée")
        completion = int(new_tokens.shape[-1])
        eos = tokenizer.eos_token_id
        finish = "stop" if completion < max_new or (eos is not None and completion and int(new_tokens[-1]) == eos) \
            else "length"
        return GenerationResult(
            text=tokenizer.decode(new_tokens, skip_special_tokens=True),
            model_used=self._endpoint.served_model,
            tokens_prompt=Measurement.real(prompt_tokens), tokens_completion=Measurement.real(completion),
            finish_reason=finish, elapsed_s=time.perf_counter() - started,
            facts={"backend": BACKEND, "device": loaded.device, "dtype": loaded.dtype,
                   "loaded_now": just_loaded, "load_s": round(loaded.load_s, 3) if just_loaded else None,
                   "generation_s": round(gen_s, 3),
                   "tokens_per_s": round(completion / gen_s, 2) if gen_s > 0 else None,
                   "seed": request.seed, "max_new_tokens": max_new},
        )
