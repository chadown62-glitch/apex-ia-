"""Backend d'inférence NATIF d'APEX (`runtime: transformers`) — le modèle est chargé et exécuté DANS le
processus APEX, sans aucun serveur de modèles (ni Ollama, ni serveur HTTP).

APEX reste propriétaire de l'abstraction (`apex.models.provider.ModelProvider`) ; PyTorch et Hugging Face
`transformers` ne sont que la bibliothèque de calcul (chargement des poids, tokenisation, génération).

Où sont les poids : `endpoint.url` = `file://<répertoire des modèles>`, `endpoint.served_model` = nom du
sous-répertoire (format Hugging Face : `config.json`, poids `safetensors`, fichiers du tokenizer). Rien n'est
JAMAIS téléchargé ici : des poids absents rendent le backend indisponible (`detect`), avec la commande à lancer
(`apex models fetch`, action explicite de l'utilisateur, réseau requis).

Exécution : GPU CUDA s'il est réellement présent (bf16), sinon CPU. Sur CPU, la précision est choisie AVANT le
chargement d'après la mémoire réellement disponible (`choose_precision`) : float32 si elle suffit (plus rapide :
4,3 contre 3,0 tokens/s mesurés pour l'architecture 1,5B sur un Xeon 4 threads sans instructions bf16), sinon
bfloat16 (moitié de la mémoire), sinon refus EXPLICITE « mémoire insuffisante » avec les chiffres — jamais un
chargement qui ferait swapper ou planter le PC. Réglable (`configure` : précision, threads). Une génération à la
fois par modèle chargé (verrou) ; un délai (`timeout_s`) et une annulation (`cancel_event`) arrêtent la
génération entre deux tokens ; une graine (`seed`) rend l'échantillonnage reproductible.
"""

from __future__ import annotations

import json
import os
import struct
import sys
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
#: Précision demandée (`auto` | `float32` | `bfloat16`) et threads CPU (0 = défaut de PyTorch) — posés par les
#: paramètres de l'application (`apex.assistant.settings`) ou `APEX_MODEL_PRECISION` / `APEX_MODEL_THREADS`.
_PRECISIONS = ("auto", "float32", "bfloat16")
_SETTINGS: dict[str, Any] = {"precision": os.environ.get("APEX_MODEL_PRECISION", "auto"),
                             "threads": int(os.environ.get("APEX_MODEL_THREADS", "0") or 0)}
#: Marge au-delà des poids : cache d'attention, activations, tokenizer, Python (mesurée large, jamais serrée).
_OVERHEAD_FACTOR = 1.2
_OVERHEAD_BYTES = 600 * 1024 * 1024
_BYTES = {"float32": 4, "bfloat16": 2}
_DTYPE_SIZES = {"F64": 8, "F32": 4, "BF16": 2, "F16": 2, "I64": 8, "I32": 4, "I16": 2, "I8": 1, "U8": 1, "BOOL": 1}


def configure(*, precision: str | None = None, threads: int | None = None) -> bool:
    """Applique les réglages ; vrai s'ils ont changé (les modèles chargés sont alors libérés : le prochain
    tour les recharge avec la nouvelle précision)."""
    changed = False
    if precision is not None:
        if precision not in _PRECISIONS:
            raise ValueError(f"précision « {precision} » inconnue ({', '.join(_PRECISIONS)})")
        changed |= _SETTINGS["precision"] != precision
        _SETTINGS["precision"] = precision
    if threads is not None:
        if threads < 0:
            raise ValueError("threads ≥ 0")
        changed |= _SETTINGS["threads"] != threads
        _SETTINGS["threads"] = threads
    if changed:
        unload_all()
    return changed


def available_memory_bytes() -> int | None:
    """Mémoire physique DISPONIBLE maintenant (Windows : GlobalMemoryStatusEx ; Linux : MemAvailable) ;
    `None` si elle ne peut pas être mesurée (jamais supposée)."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return None
            return int(status.ullAvailPhys)
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, AttributeError):
        return None
    return None


def parameter_count(path: Path) -> int | None:
    """Nombre de paramètres lu dans les en-têtes safetensors (sans charger les poids) ; `None` sinon."""
    total = 0
    files = sorted(path.glob("*.safetensors"))
    if not files:
        return None
    try:
        for file in files:
            with open(file, "rb") as handle:
                size = struct.unpack("<Q", handle.read(8))[0]
                header = json.loads(handle.read(size))
            for name, info in header.items():
                if name == "__metadata__":
                    continue
                count = 1
                for dim in info["shape"]:
                    count *= int(dim)
                total += count
    except (OSError, ValueError, KeyError, struct.error):
        return None
    return total


def required_bytes(params: int, precision: str) -> int:
    return int(params * _BYTES[precision] * _OVERHEAD_FACTOR) + _OVERHEAD_BYTES


class InsufficientMemory(RuntimeError):
    pass


def choose_precision(params: int | None, available: int | None, requested: str = "auto",
                     cuda: bool = False) -> str:
    """Précision de chargement SUR CPU. `requested` explicite : respecté, mais refusé s'il ne tient pas en
    mémoire. `auto` : float32 s'il tient, sinon bfloat16, sinon refus. Mémoire ou taille non mesurables :
    la demande explicite, ou float32 (comportement antérieur) — jamais un refus sur une supposition."""
    if cuda:
        return "bfloat16"
    gib = 1024 ** 3
    if params is None or available is None:
        return "float32" if requested == "auto" else requested
    candidates = ["float32", "bfloat16"] if requested == "auto" else [requested]
    for precision in candidates:
        if required_bytes(params, precision) <= available:
            return precision
    need = {p: required_bytes(params, p) / gib for p in ("float32", "bfloat16")}
    hint = ("" if requested == "bfloat16" or (requested == "auto")
            else " ; la précision bfloat16 (Paramètres > Ressources) en demande moitié moins")
    raise InsufficientMemory(
        f"mémoire insuffisante pour charger le modèle : {available / gib:.1f} Go disponibles, "
        f"{need['bfloat16']:.1f} Go nécessaires en bfloat16 ({need['float32']:.1f} Go en float32){hint}. "
        "Fermez des applications puis relancez la génération.")
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


def _standalone() -> bool:
    from apex.standalone import is_standalone

    return is_standalone()


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
        precision = choose_precision(parameter_count(path), available_memory_bytes(), _SETTINGS["precision"], cuda)
        dtype = torch.bfloat16 if precision == "bfloat16" else torch.float32
        if _SETTINGS["threads"]:
            torch.set_num_threads(int(_SETTINGS["threads"]))
        tokenizer = AutoTokenizer.from_pretrained(key, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(key, local_files_only=True, dtype=dtype)
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
                detail=f"bibliothèques d'inférence absentes ({missing_lib}) — " + (
                    "« Préparer APEX » dans le Panel les installe" if _standalone() else "installer l'extra « native »"),
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
            remedy = ("« Installer le modèle » dans le Panel APEX (action explicite, réseau)" if _standalone()
                      else "`apex models fetch` (action explicite, réseau)")
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend=BACKEND,
                detail=(f"poids absents ou incomplets dans {path} (config : {not absent}, tokenizer : "
                        f"{has_tokenizer}, poids : {has_weights}) — {remedy}"),
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
        except InsufficientMemory as exc:
            raise ModelProviderError("NativeTransformersProvider.generate", endpoint_url=self._endpoint.url,
                                     detail=str(exc), category=ModelErrorCategory.PROVIDER_ERROR) from exc
        except Exception as exc:
            raise ModelProviderError("NativeTransformersProvider.generate", endpoint_url=self._endpoint.url,
                                     detail=f"chargement impossible : {exc.__class__.__name__}: {str(exc)[:300]}",
                                     category=ModelErrorCategory.PROVIDER_ERROR) from exc
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList

        tokenizer = loaded.tokenizer
        if getattr(tokenizer, "chat_template", None):
            messages = ([{"role": "system", "content": request.system}] if request.system else []) + [
                {"role": "user", "content": request.prompt}]
            input_ids = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt")
        else:
            text = f"{request.system}\n\n{request.prompt}" if request.system else request.prompt
            input_ids = tokenizer(text, return_tensors="pt").input_ids
        prompt_tokens = int(input_ids.shape[-1])
        # Contexte RÉEL : le plus petit du contexte déclaré et de celui du modèle (jamais au-delà de ce qu'il
        # a été construit pour traiter).
        model_context = getattr(loaded.model.config, "max_position_embeddings", None) or self._endpoint.max_context
        context = min(self._endpoint.max_context, int(model_context))
        budget = context - prompt_tokens
        if budget <= 0:
            raise ModelProviderError("NativeTransformersProvider.generate", endpoint_url=self._endpoint.url,
                                     detail=f"prompt de {prompt_tokens} tokens ≥ contexte {context}",
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
        # Une génération à la fois par modèle chargé ; l'ATTENTE du tour est bornée par le délai de la requête
        # (sinon un appel court — le Chat — pouvait attendre bien au-delà de son délai derrière une TaskRun).
        if not loaded.lock.acquire(timeout=max(0.0, deadline - time.perf_counter())):
            raise TimeoutExceeded("NativeTransformersProvider.generate (modèle occupé)", request.timeout_s)
        try:
            if request.seed is not None:
                torch.manual_seed(request.seed)
            gen_started = time.perf_counter()
            with torch.inference_mode():
                output = loaded.model.generate(
                    input_ids.to(loaded.device), attention_mask=torch.ones_like(input_ids).to(loaded.device),
                    **kwargs)
            gen_s = time.perf_counter() - gen_started
        finally:
            loaded.lock.release()
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
