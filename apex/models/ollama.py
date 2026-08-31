"""`OllamaProvider` — le seul backend `ModelProvider` réellement implémenté
(Phase 6, partiel). Même discipline que `MinioArtifactStore` (étape 10) :
`detect()` sonde RÉELLEMENT (HTTP réel, jamais un simple port ouvert),
`UNAVAILABLE_ON_DEV` jamais `UNSUPPORTED`, aucune opération n'est jamais
simulée comme ayant réussi.

Aucune dépendance externe : l'API Ollama est du JSON sur HTTP simple —
`urllib.request` (stdlib) suffit, y compris pour le streaming (lecture par
blocs). Cohérent avec le noyau stdlib pur (ADR-0002), plus strict encore que
les extras `db`/`api`.

Ne journalise et n'expose JAMAIS le contenu d'un prompt ou d'une réponse
dans un message d'erreur (§72) — seuls l'URL de l'endpoint et un résumé
borné de la cause y figurent.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from apex.config.schema import ModelEndpoint
from apex.errors import ModelProviderError, TimeoutExceeded
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement

_ERROR_DETAIL_MAX_CHARS = 300


def _truncate(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _ERROR_DETAIL_MAX_CHARS else text[:_ERROR_DETAIL_MAX_CHARS] + "…"


class OllamaProvider(ModelProvider):
    def __init__(self, endpoint: ModelEndpoint, *, connect_timeout_s: float = 2.0) -> None:
        self._endpoint = endpoint
        self._base_url = endpoint.url.rstrip("/")
        self._connect_timeout_s = connect_timeout_s

    # -- détection --------------------------------------------------------

    def _fetch_tags(self) -> list[str] | None:
        """`None` si l'endpoint est injoignable — jamais une liste vide
        confondue avec « aucun modèle » (distinction false/null, même
        principe que `apex.gate.checks`)."""
        req = urllib.request.Request(f"{self._base_url}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._connect_timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, OSError, ValueError):
            return None
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return [str(m.get("name", "")) for m in models if isinstance(m, dict)]

    def _model_present(self, tags: list[str]) -> bool:
        served = self._endpoint.served_model
        return any(tag == served or tag.startswith(f"{served}:") for tag in tags)

    def detect(self) -> ModelCapabilityReport:
        tags = self._fetch_tags()
        if tags is None:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="ollama",
                detail=(
                    f"Ollama injoignable sur {self._base_url} sur cette machine — "
                    "backend architecturé pour TARGET_MACHINE, jamais interprété comme UNSUPPORTED"
                ),
                facts={"url": self._base_url, "reachable": False},
            )
        if not self._model_present(tags):
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="ollama",
                detail=(
                    f"Ollama joignable sur {self._base_url} mais le modèle "
                    f"« {self._endpoint.served_model} » n'y est pas chargé — "
                    "capacité de génération pour CE modèle indisponible ici"
                ),
                facts={"url": self._base_url, "reachable": True, "model_present": False, "available_models": tags},
            )
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend="ollama",
            detail=f"Ollama joignable sur {self._base_url}, modèle « {self._endpoint.served_model} » présent",
            facts={"url": self._base_url, "reachable": True, "model_present": True, "available_models": tags},
        )

    # -- génération ---------------------------------------------------------

    def _payload(self, request: GenerationRequest, *, stream: bool) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.temperature is not None:
            options["temperature"] = request.temperature
        body: dict[str, Any] = {
            "model": self._endpoint.served_model, "prompt": request.prompt, "stream": stream,
        }
        if options:
            body["options"] = options
        return body

    def generate(self, request: GenerationRequest) -> GenerationResult:
        report = self.detect()
        if not report.is_available:
            raise ModelProviderError(
                "OllamaProvider.generate", endpoint_url=self._base_url,
                detail=f"backend non disponible : {report.status.value}",
            )
        if request.cancel_event is not None and request.cancel_event.is_set():
            raise ModelProviderError(
                "OllamaProvider.generate", endpoint_url=self._base_url, detail="annulé avant tout appel",
            )

        body = self._payload(request, stream=False)
        started = time.perf_counter()
        payload = self._post(f"{self._base_url}/api/generate", body, timeout_s=request.timeout_s)
        elapsed_s = time.perf_counter() - started

        prompt_tokens = payload.get("prompt_eval_count")
        completion_tokens = payload.get("eval_count")
        return GenerationResult(
            text=str(payload.get("response", "")),
            model_used=str(payload.get("model", self._endpoint.served_model)),
            tokens_prompt=Measurement.real(prompt_tokens) if prompt_tokens is not None else Measurement.unknown(),
            tokens_completion=(
                Measurement.real(completion_tokens) if completion_tokens is not None else Measurement.unknown()
            ),
            finish_reason=payload.get("done_reason"),
            elapsed_s=round(elapsed_s, 3),
            facts={
                k: payload.get(k)
                for k in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration", "done")
                if k in payload
            },
        )

    def generate_stream(self, request: GenerationRequest) -> Iterator[str]:
        """Méthode CONCRÈTE, hors du contrat abstrait `ModelProvider`
        (décision explicite, Phase 6). Cède des fragments de texte au fur et
        à mesure ; s'arrête silencieusement dès que `request.cancel_event`
        est levé — l'appelant, qui détient déjà cet événement, sait pourquoi.
        Ne retourne aucune statistique agrégée : utiliser `generate()` pour
        les `Measurement` de tokens."""
        report = self.detect()
        if not report.is_available:
            raise ModelProviderError(
                "OllamaProvider.generate_stream", endpoint_url=self._base_url,
                detail=f"backend non disponible : {report.status.value}",
            )
        if request.cancel_event is not None and request.cancel_event.is_set():
            return

        body = self._payload(request, stream=True)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/generate", data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=request.timeout_s) as resp:
                for raw_line in resp:
                    if request.cancel_event is not None and request.cancel_event.is_set():
                        return
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    fragment = chunk.get("response")
                    if fragment:
                        yield fragment
                    if chunk.get("done"):
                        return
        except TimeoutError as exc:
            raise TimeoutExceeded("OllamaProvider.generate_stream", request.timeout_s) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ModelProviderError(
                "OllamaProvider.generate_stream", endpoint_url=self._base_url, detail=_truncate(str(exc)),
            ) from exc

    # -- internes -----------------------------------------------------------

    def _post(self, url: str, body: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise TimeoutExceeded("OllamaProvider.generate", timeout_s) from exc
        except urllib.error.HTTPError as exc:
            raise ModelProviderError(
                "OllamaProvider.generate", endpoint_url=self._base_url,
                detail=f"HTTP {exc.code} : {_truncate(exc.reason or '')}",
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(
                "OllamaProvider.generate", endpoint_url=self._base_url, detail=_truncate(str(exc.reason)),
            ) from exc
        except (OSError, ValueError) as exc:
            raise ModelProviderError(
                "OllamaProvider.generate", endpoint_url=self._base_url, detail=_truncate(str(exc)),
            ) from exc
