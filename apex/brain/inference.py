"""Moteur d'inférence du Cerveau APEX v2 — génération RÉELLE, mesurée, interruptible.

- **Cache clé/valeur** : l'invite est calculée une fois (pré-remplissage), puis chaque jeton une seule fois.
- **Échantillonnage** : glouton (température 0) ou température, top-k, top-p, pénalité de répétition ;
  graine pour la reproductibilité.
- **Arrêt** : fin de réponse (`<|end|>`), fin de texte, début d'un autre tour, budget de jetons, délai,
  annulation (vérifiée entre deux jetons).
- **Flux** : `on_text` reçoit le texte au fur et à mesure (caractères UTF-8 complets seulement).
- **Contexte** : la conversation est tronquée par le DÉBUT (consignes système conservées, puis les tours les
  plus récents) pour laisser la place à la réponse ; jamais coupée au milieu d'un message.
- **Mesures** : jetons de l'invite, jetons produits, délai avant le premier jeton, débit, mémoire (poids et
  cache), périphérique — rendues avec chaque réponse, jamais estimées.
- **Mémoire** : `unload()` libère réellement les poids (et la mémoire GPU) ; `BrainEngine` peut garder les
  poids compressés en APEX-Q5 (`weights="q5_resident"`).
- **Calcul exact** : quand le modèle écrit `<|tool_call|> expression <|tool_result|>`, l'expression est
  évaluée par `apex.brain.calc` et le résultat est placé dans la réponse (et dans le contexte du modèle) ;
  le texte rendu montre le résultat, pas l'expression. Chaque appel figure dans les mesures (`tool_calls`).
- **Atelier 3D** : un appel `vehicle(…)`, `weapon(…)` ou `character(…)` est construit par
  `apex.brain.studio3d` ; le modèle lit le résumé du modèle 3D RÉELLEMENT construit (dimensions, pièces). Le
  moteur n'écrit aucun fichier : l'assistant le fait ensuite, avec ses permissions (outil `model3d.create`).
"""

from __future__ import annotations

import gc
import math
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch

from apex.brain import calc, studio3d
from apex.brain.chat import (
    TOOL_CALL,
    TOOL_RESULT,
    Message,
    encode_chat,
    stop_ids,
    supports_chat,
    supports_tools,
)
from apex.brain.model import ApexLM, KVCache

#: Appels de calcul au plus par réponse, et longueur maximale d'une expression (en jetons).
MAX_TOOL_CALLS = 16
MAX_CALL_TOKENS = 64


class GenerationCancelled(RuntimeError):
    pass


class GenerationTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class SamplingSettings:
    max_new_tokens: int = 256
    temperature: float = 0.0
    top_k: int = 0
    top_p: float = 1.0
    repetition_penalty: float = 1.0
    seed: int | None = None


@dataclass
class GenerationStats:
    prompt_tokens: int = 0
    generated_tokens: int = 0
    truncated_messages: int = 0
    time_to_first_token_s: float | None = None
    prefill_s: float = 0.0
    decode_s: float = 0.0
    total_s: float = 0.0
    finish_reason: str = ""
    device: str = "cpu"
    weights: str = ""
    kv_cache_bytes: int = 0
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    #: Confiance du modèle dans SA réponse : somme des log-probabilités (distribution brute, température 1)
    #: des jetons qu'il a choisis, leur nombre, et la plus faible probabilité rencontrée. Les jetons fournis
    #: par un outil (résultat de calcul, résumé 3D) n'y entrent pas : le modèle ne les a pas choisis.
    logprob_sum: float = 0.0
    scored_tokens: int = 0
    min_token_prob: float | None = None

    @property
    def confidence(self) -> float | None:
        """Moyenne géométrique des probabilités des jetons choisis (0 à 1) ; None si rien n'a été choisi."""
        return round(math.exp(self.logprob_sum / self.scored_tokens), 4) if self.scored_tokens else None

    @property
    def tokens_per_s(self) -> float | None:
        return (
            round(self.generated_tokens / self.decode_s, 2)
            if self.decode_s > 0 and self.generated_tokens
            else None
        )

    @property
    def prefill_tokens_per_s(self) -> float | None:
        return round(self.prompt_tokens / self.prefill_s, 1) if self.prefill_s > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "tokens_per_s": self.tokens_per_s,
            "prefill_tokens_per_s": self.prefill_tokens_per_s,
            "confidence": self.confidence,
        }


@dataclass
class GenerationResult:
    text: str
    token_ids: list[int]
    stats: GenerationStats = field(default_factory=GenerationStats)


class _Utf8Stream:
    """Assemble les octets des jetons et ne rend que des caractères complets (un caractère accentué peut
    être coupé entre deux jetons)."""

    def __init__(self) -> None:
        self._pending = b""

    def push(self, data: bytes) -> str:
        self._pending += data
        for cut in range(len(self._pending), max(-1, len(self._pending) - 4), -1):
            try:
                text = self._pending[:cut].decode("utf-8")
            except UnicodeDecodeError:
                continue
            self._pending = self._pending[cut:]
            return text
        return ""

    def flush(self) -> str:
        text = self._pending.decode("utf-8", errors="replace")
        self._pending = b""
        return text


def pick_device(requested: str = "auto") -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(requested)


class BrainEngine:
    """Un Cerveau APEX chargé (répertoire `apex.brain.artifact`). Une génération à la fois (verrou)."""

    def __init__(
        self, directory: Path, *, device: str = "auto", weights: str = "dequantized", threads: int = 0
    ) -> None:
        if weights not in ("dequantized", "q5_resident"):
            raise ValueError(f"mode de poids inconnu : {weights}")
        self.directory = directory
        #: Périphérique DEMANDÉ (`auto`, `cpu`, `cuda`) ; `device` est celui du prochain chargement, choisi
        #: par l'appelant (`place`, partage du GPU : `apex.models.gpu_handoff`).
        self.requested_device = device
        self.device = pick_device(device)
        self.weights_mode = weights
        self.threads = threads
        self.model: ApexLM | None = None
        self.tokenizer: Any = None
        self.provenance: dict[str, Any] = {}
        self.fingerprint = ""
        self.load_s = 0.0
        self.lock = threading.Lock()
        self.last_used = 0.0

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def place(self, device: str) -> None:
        """Périphérique du prochain chargement ; sans effet sur un Cerveau déjà chargé."""
        if self.model is None:
            self.device = torch.device(device)

    def load(self) -> bool:
        """Charge si nécessaire ; vrai si le chargement vient d'avoir lieu. Contrôles d'intégrité de
        `apex.brain.artifact.load` (SHA-256 du fichier, empreinte des poids) jamais contournés."""
        if self.model is not None:
            return False
        from apex.brain.artifact import load, weights_file

        started = time.perf_counter()
        if self.threads:
            torch.set_num_threads(self.threads)
        loaded = load(self.directory)  # CPU, float32, empreinte vérifiée
        model = loaded.model
        if self.weights_mode == "q5_resident":
            if not loaded.provenance.get("quantization"):
                raise ValueError(
                    "poids non quantifiés : le mode « Q5 résident » demande un fichier APEX-Q5"
                )
            from apex.brain.quant import make_q5_resident

            make_q5_resident(model, self.directory / weights_file(loaded.provenance))
        if self.device.type == "cuda":
            model = model.to(
                self.device, dtype=torch.float16 if not torch.cuda.is_bf16_supported() else torch.bfloat16
            )
        model.eval()
        self.model = model
        self.tokenizer = loaded.tokenizer
        self.provenance = loaded.provenance
        self.fingerprint = loaded.fingerprint
        self.load_s = time.perf_counter() - started
        return True

    def unload(self) -> None:
        """Libère les poids et le cache de calcul (RAM, et VRAM si GPU). Sous Linux, la mémoire libérée est
        aussi RENDUE au système (`malloc_trim`) : sans cela l'allocateur la garde pour lui."""
        self.model = None
        gc.collect()
        if self.device.type == "cuda":
            torch.cuda.empty_cache()
        if sys.platform.startswith("linux"):
            try:
                import ctypes

                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except (OSError, AttributeError):
                pass  # autre libc : la mémoire reste réutilisable par le processus

    def weight_bytes(self) -> int:
        if self.model is None:
            return 0
        tensors = {id(t): t for t in (*self.model.parameters(), *self.model.buffers())}
        return sum(t.numel() * t.element_size() for t in tensors.values())

    @property
    def context_length(self) -> int:
        assert self.model is not None
        return self.model.config.context_length

    # ------------------------------------------------------------------------------------------ invites

    def chat_prompt(self, messages: Sequence[Message], max_new_tokens: int) -> tuple[list[int], int]:
        """Identifiants de l'invite ; tronque les tours les plus anciens (jamais les consignes système,
        jamais le dernier message) pour laisser `max_new_tokens` jetons à la réponse. Rend (identifiants,
        tours retirés)."""
        assert self.model is not None
        if not supports_chat(self.tokenizer):
            raise ValueError(
                "ce Cerveau n'a pas de format de conversation (génération 1 : il continue le texte)"
            )
        budget = self.context_length - max_new_tokens
        if budget < 16:
            raise ValueError(
                f"réponse de {max_new_tokens} jetons : trop longue pour le contexte de "
                f"{self.context_length}"
            )
        system = [m for m in messages if m.role == "system"]
        turns = [m for m in messages if m.role != "system"]
        dropped = 0
        while True:
            ids, _ = encode_chat(self.tokenizer, [*system, *turns], add_generation_prompt=True)
            if len(ids) <= budget or len(turns) <= 1:
                break
            turns = turns[1:]
            dropped += 1
        if len(ids) > budget:
            # Un seul message trop long : on garde sa FIN (la question est en général à la fin).
            head = ids[:1]
            ids = head + ids[-(budget - 1) :]
        return ids, dropped

    # ------------------------------------------------------------------------------------------ génération

    @torch.inference_mode()
    def generate_ids(
        self,
        prompt: list[int],
        settings: SamplingSettings,
        *,
        stop: frozenset[int] | None = None,
        cancel: threading.Event | None = None,
        deadline: float | None = None,
        on_text: Callable[[str], None] | None = None,
        tools: bool = True,
    ) -> GenerationResult:
        self.load()
        assert self.model is not None
        model, tokenizer = self.model, self.tokenizer
        started = time.perf_counter()
        stats = GenerationStats(
            prompt_tokens=len(prompt), device=self.device.type, weights=self.weights_mode
        )
        room = self.context_length - len(prompt)
        if room <= 0:
            raise ValueError(f"invite de {len(prompt)} jetons ≥ contexte {self.context_length}")
        max_new = min(settings.max_new_tokens, room)
        stops = stop if stop is not None else stop_ids(tokenizer)
        generator = torch.Generator(device="cpu")
        if settings.seed is not None:
            generator.manual_seed(settings.seed)
        dtype = next(model.parameters()).dtype
        cache = KVCache(model.config, 1, len(prompt) + max_new, device=self.device, dtype=dtype)
        stats.kv_cache_bytes = cache.nbytes()
        stream = _Utf8Stream()
        produced: list[int] = []
        text_parts: list[str] = []
        stats.finish_reason = "length"
        use_tools = tools and supports_tools(tokenizer)
        call_id = tokenizer.special_id(TOOL_CALL) if use_tools else -1
        result_id = tokenizer.special_id(TOOL_RESULT) if use_tools else -1
        call_start: int | None = None  # position de l'expression en cours dans `produced`

        def emit(piece: str) -> None:
            if piece:
                text_parts.append(piece)
                if on_text is not None:
                    on_text(piece)

        def feed(ids: list[int]) -> torch.Tensor:
            out, _ = model(torch.tensor([ids], device=self.device), cache=cache, last_only=True)
            return out

        with self.lock:
            self.last_used = time.time()
            prefill_started = time.perf_counter()
            logits = feed(prompt)
            stats.prefill_s = time.perf_counter() - prefill_started
            decode_started = time.perf_counter()
            while len(produced) < max_new:
                if cancel is not None and cancel.is_set():
                    raise GenerationCancelled("génération annulée")
                if deadline is not None and time.perf_counter() > deadline:
                    raise GenerationTimeout("délai de génération dépassé")
                token = self._sample(logits[0, -1].float(), produced, settings, generator)
                if stats.time_to_first_token_s is None:
                    stats.time_to_first_token_s = round(time.perf_counter() - started, 4)
                if call_start is not None and len(produced) - call_start >= MAX_CALL_TOKENS:
                    token = result_id  # expression démesurée : refermée, la calculatrice dira l'erreur
                else:
                    logprob = float(torch.log_softmax(logits[0, -1].float(), dim=-1)[token])
                    stats.logprob_sum += logprob
                    stats.scored_tokens += 1
                    probability = math.exp(logprob)
                    if stats.min_token_prob is None or probability < stats.min_token_prob:
                        stats.min_token_prob = round(probability, 6)
                if use_tools and token == call_id and call_start is None:
                    if len(stats.tool_calls) >= MAX_TOOL_CALLS:
                        stats.finish_reason = "tool_limit"
                        break
                    emit(stream.flush())
                    produced.append(token)
                    call_start = len(produced)
                    logits = feed([token])
                    continue
                if use_tools and token == result_id and call_start is not None:
                    expression = tokenizer.decode(produced[call_start:])
                    tool = "3d" if studio3d.is_call(expression) else "calc"
                    answer = studio3d.run(expression) if tool == "3d" else calc.run(expression)
                    answer_ids = tokenizer.encode(answer)
                    if len(produced) + 1 + len(answer_ids) >= max_new:
                        break  # plus de place pour le résultat : arrêt sur la longueur
                    stats.tool_calls.append(
                        {"tool": tool, "expression": expression.strip(), "result": answer}
                    )
                    produced += [token, *answer_ids]
                    call_start = None
                    emit(answer)
                    logits = feed([token, *answer_ids])
                    continue
                if token in stops and call_start is None:
                    stats.finish_reason = "stop"
                    break
                produced.append(token)
                if call_start is None:
                    emit(stream.push(tokenizer.token_bytes(token)))
                if len(produced) < max_new:
                    logits = feed([token])
            stats.decode_s = time.perf_counter() - decode_started
            self.last_used = time.time()
        tail = stream.flush()
        if tail:
            text_parts.append(tail)
            if on_text is not None:
                on_text(tail)
        stats.generated_tokens = len(produced)
        stats.total_s = round(time.perf_counter() - started, 4)
        stats.prefill_s = round(stats.prefill_s, 4)
        stats.decode_s = round(stats.decode_s, 4)
        return GenerationResult("".join(text_parts), produced, stats)

    def chat(
        self, messages: Sequence[Message], settings: SamplingSettings, **kwargs: Any
    ) -> GenerationResult:
        self.load()
        prompt, dropped = self.chat_prompt(messages, settings.max_new_tokens)
        result = self.generate_ids(prompt, settings, **kwargs)
        result.stats.truncated_messages = dropped
        return result

    @staticmethod
    def _sample(
        logits: torch.Tensor, produced: list[int], settings: SamplingSettings, generator: torch.Generator
    ) -> int:
        # Copie : la pénalité ci-dessous modifie les valeurs, et l'appelant mesure la confiance sur la
        # distribution BRUTE du modèle (sans copie, un tenseur CPU float32 était modifié sur place).
        logits = logits.to("cpu", copy=True)
        if settings.repetition_penalty != 1.0 and produced:
            seen = torch.tensor(sorted(set(produced[-256:])), dtype=torch.long)
            values = logits[seen]
            logits[seen] = torch.where(
                values > 0, values / settings.repetition_penalty, values * settings.repetition_penalty
            )
        if settings.temperature <= 0:
            return int(logits.argmax())
        logits = logits / settings.temperature
        if settings.top_k > 0:
            kth = torch.topk(logits, min(settings.top_k, logits.numel())).values[-1]
            logits = torch.where(logits < kth, torch.full_like(logits, float("-inf")), logits)
        probabilities = torch.softmax(logits, dim=-1)
        if settings.top_p < 1.0:
            ordered, order = torch.sort(probabilities, descending=True)
            cumulative = torch.cumsum(ordered, dim=-1)
            keep = cumulative - ordered < settings.top_p
            filtered = torch.zeros_like(probabilities)
            filtered[order[keep]] = probabilities[order[keep]]
            probabilities = filtered / filtered.sum()
        return int(torch.multinomial(probabilities, 1, generator=generator))
