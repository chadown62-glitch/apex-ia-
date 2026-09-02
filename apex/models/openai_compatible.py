"""`OpenAICompatibleProvider` — un SEUL backend `ModelProvider` générique
pour tout service exposant l'API `/v1/chat/completions`/`/v1/models`
(OpenAI, OpenRouter, Together, Groq, Fireworks, DeepSeek, Mistral, xAI,
Qwen/DashScope en mode compatible, ...) — jamais une classe par
fournisseur : même contrat HTTP, un seul adapter, `ModelEndpoint.url`
distingue le service réel.

Même discipline que `OllamaProvider` (Phase 6) : HTTP réel via `urllib`
(stdlib, ADR-0002), `detect()` sonde réellement avant tout `generate()`,
`UNAVAILABLE_ON_DEV` jamais `UNSUPPORTED`, aucune fuite de prompt/réponse
ni de credential dans une erreur (§72 — étendu ici à l'en-tête
`Authorization`, jamais journalisé, jamais dans un message d'exception).

**Authentification via `SecretBroker`** (`apex.secrets`), jamais une clé en
dur : `ModelEndpoint.secret_key` porte une clé LOGIQUE
(`"openai_api_key"`), résolue à CHAQUE appel — jamais mise en cache par ce
module, pour qu'une rotation de clé soit vue au prochain appel sans
redémarrage. `ModelEndpoint.secret_key is None` signifie « aucune
authentification requise » (services locaux compatibles), jamais une
erreur.

**Aucune intégration réelle prétendue par ce module** : `detect()`/
`generate()` sont réels et testés contre un serveur HTTP de test qui imite
la forme exacte des réponses OpenAI-compatible — jamais contre un vrai
service. Le seul test contre un vrai endpoint externe (`tests/
test_models_openai_compatible.py::TestRealOpenAICompatibleIntegration`,
s'il existe) resterait explicitement sauté tant qu'aucune clé réelle et
aucun endpoint réel ne sont fournis — même discipline que
`tests/test_models_ollama.py`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from apex.config.schema import ModelEndpoint
from apex.errors import ModelProviderError, SecretNotFound, TimeoutExceeded
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.secrets.provider import EnvSecretBroker, SecretBroker

_ERROR_DETAIL_MAX_CHARS = 300


def _truncate(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _ERROR_DETAIL_MAX_CHARS else text[:_ERROR_DETAIL_MAX_CHARS] + "…"


class OpenAICompatibleProvider(ModelProvider):
    def __init__(
        self, endpoint: ModelEndpoint, *, secret_broker: SecretBroker | None = None,
        connect_timeout_s: float = 2.0,
    ) -> None:
        self._endpoint = endpoint
        self._base_url = endpoint.url.rstrip("/")
        self._secret_broker = secret_broker or EnvSecretBroker()
        self._connect_timeout_s = connect_timeout_s

    # -- authentification ---------------------------------------------------

    def _auth_headers(self) -> dict[str, str] | None:
        """`None` si un `secret_key` est requis mais introuvable — jamais
        une tentative sans authentification pour un endpoint qui en exige
        une. `{}` (dict vide, pas `None`) si aucune authentification n'est
        requise (`secret_key is None`) — les deux cas sont distincts,
        jamais confondus."""
        if self._endpoint.secret_key is None:
            return {}
        try:
            token = self._secret_broker.resolve(self._endpoint.secret_key)
        except SecretNotFound:
            return None
        return {"Authorization": f"Bearer {token}"}

    # -- détection ------------------------------------------------------------

    def _fetch_models(self) -> list[str] | None:
        headers = self._auth_headers()
        if headers is None:
            return None
        req = urllib.request.Request(f"{self._base_url}/models", method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._connect_timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, OSError, ValueError):
            return None
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return [str(m.get("id", "")) for m in data if isinstance(m, dict)]

    def detect(self) -> ModelCapabilityReport:
        if self._endpoint.secret_key is not None and self._auth_headers() is None:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="openai_compatible",
                detail=(
                    f"secret « {self._endpoint.secret_key} » introuvable — aucune tentative faite "
                    f"contre {self._base_url} sans authentification résolue"
                ),
                facts={"url": self._base_url, "reachable": False, "secret_resolved": False},
            )
        models = self._fetch_models()
        if models is None:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="openai_compatible",
                detail=f"{self._base_url} injoignable sur cette machine — backend architecturé, jamais UNSUPPORTED",
                facts={"url": self._base_url, "reachable": False},
            )
        if self._endpoint.served_model not in models:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="openai_compatible",
                detail=(
                    f"{self._base_url} joignable mais le modèle « {self._endpoint.served_model} » "
                    "n'y est pas listé"
                ),
                facts={"url": self._base_url, "reachable": True, "model_present": False, "available_models": models},
            )
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend="openai_compatible",
            detail=f"{self._base_url} joignable, modèle « {self._endpoint.served_model} » présent",
            facts={"url": self._base_url, "reachable": True, "model_present": True, "available_models": models},
        )

    # -- génération -----------------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        report = self.detect()
        if not report.is_available:
            raise ModelProviderError(
                "OpenAICompatibleProvider.generate", endpoint_url=self._base_url,
                detail=f"backend non disponible : {report.status.value}",
            )
        if request.cancel_event is not None and request.cancel_event.is_set():
            raise ModelProviderError(
                "OpenAICompatibleProvider.generate", endpoint_url=self._base_url, detail="annulé avant tout appel",
            )

        headers = self._auth_headers()
        assert headers is not None  # garanti par report.is_available ci-dessus
        headers = {**headers, "Content-Type": "application/json"}

        body: dict[str, Any] = {
            "model": self._endpoint.served_model,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature

        started = time.perf_counter()
        payload = self._post(f"{self._base_url}/chat/completions", body, headers=headers, timeout_s=request.timeout_s)
        elapsed_s = time.perf_counter() - started

        choices = payload.get("choices") or []
        first = choices[0] if choices else {}
        message = first.get("message") or {}
        text = str(message.get("content", ""))
        usage = payload.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")

        return GenerationResult(
            text=text,
            model_used=str(payload.get("model", self._endpoint.served_model)),
            tokens_prompt=Measurement.real(prompt_tokens) if prompt_tokens is not None else Measurement.unknown(),
            tokens_completion=(
                Measurement.real(completion_tokens) if completion_tokens is not None else Measurement.unknown()
            ),
            finish_reason=first.get("finish_reason"),
            elapsed_s=round(elapsed_s, 3),
            facts={k: payload[k] for k in ("id", "created", "system_fingerprint") if k in payload},
        )

    # -- internes -----------------------------------------------------------

    def _post(self, url: str, body: dict[str, Any], *, headers: dict[str, str], timeout_s: float) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise TimeoutExceeded("OpenAICompatibleProvider.generate", timeout_s) from exc
        except urllib.error.HTTPError as exc:
            # Jamais le corps de la réponse d'erreur en entier (pourrait
            # échoïter le prompt envoyé, ou des détails internes du
            # fournisseur) — seulement le code et un résumé borné, jamais
            # l'en-tête Authorization (jamais relu depuis `req`, jamais
            # inclus ici).
            raise ModelProviderError(
                "OpenAICompatibleProvider.generate", endpoint_url=self._base_url,
                detail=f"HTTP {exc.code} : {_truncate(exc.reason or '')}",
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(
                "OpenAICompatibleProvider.generate", endpoint_url=self._base_url, detail=_truncate(str(exc.reason)),
            ) from exc
        except (OSError, ValueError) as exc:
            raise ModelProviderError(
                "OpenAICompatibleProvider.generate", endpoint_url=self._base_url, detail=_truncate(str(exc)),
            ) from exc
