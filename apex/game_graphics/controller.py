"""A real local language controller emits validated parameters, not arbitrary host code."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse

from apex.config.schema import ModelEndpoint
from apex.game_graphics.contract import VehicleSpec
from apex.models.model import GenerationRequest
from apex.models.provider import get_default_provider


def propose_vehicle(endpoint: ModelEndpoint, prompt: str) -> tuple[VehicleSpec, dict[str, Any]]:
    if urlparse(endpoint.url).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("local_inference_required")
    schema_example = VehicleSpec.example().to_dict()
    request = GenerationRequest(
        endpoint=endpoint,
        temperature=0,
        seed=42,
        max_tokens=512,
        timeout_s=300,
        system="Tu pilotes Blender. Fournis uniquement des paramètres JSON, jamais un script exécutable.",
        prompt="Produis les paramètres Blender d'un coupé sportif. Retourne UNIQUEMENT un objet "
        "JSON avec exactement ces clés et types : "
        + json.dumps(schema_example)
        + ". Respecte la demande. Dimensions en mètres, masse en kg, RGB de 0 à 1. "
        "wheelbase_m + 2*wheel_radius_m < length_m. Aucun commentaire ou Markdown. Demande : " + prompt,
    )
    result = get_default_provider(endpoint).generate(request)
    text = result.text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("vehicle_json_object_required")
    spec = VehicleSpec.from_dict(data)
    record = asdict(result)
    record["prompt"] = prompt
    record["spec"] = spec.to_dict()
    return spec, record
