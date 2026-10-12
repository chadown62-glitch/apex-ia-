"""Real local UE code baseline through the existing provider; generated text cannot certify API validity."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse

from apex.config.schema import ModelEndpoint
from apex.models.model import GenerationRequest
from apex.models.provider import get_default_provider


def propose_unreal_code(endpoint: ModelEndpoint) -> dict[str, Any]:
    if urlparse(endpoint.url).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("local_inference_required")
    request = GenerationRequest(
        endpoint=endpoint,
        temperature=0,
        seed=42,
        max_tokens=1200,
        timeout_s=300,
        system="You are a UE5 C++ candidate. Do not claim compilation or runtime success.",
        prompt="Write a minimal UE5 C++ header and cpp for a class derived from AWheeledVehiclePawn. "
        "Add a spring arm and camera attached to the mesh. Expose throttle and steering functions calling "
        "UChaosWheeledVehicleMovementComponent. Name required Build.cs modules. Mention the skeletal mesh, "
        "wheel bone and PhysicsAsset prerequisites. No fabricated API stubs. Keep within 1000 tokens.",
    )
    result = get_default_provider(endpoint).generate(request)
    return {
        "generation": asdict(result),
        "request": asdict(request),
        "ue_api_qualified": False,
        "compile_status": "blocked_without_actual_engine",
        "driving_status": "blocked",
        "policy": "candidate text never executed on host or accepted as compile evidence",
    }
