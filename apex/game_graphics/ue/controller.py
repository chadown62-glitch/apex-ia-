"""Neural artistic plan using the existing local ModelProvider, never executable model text."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from apex.config.schema import ModelEndpoint
from apex.game_graphics.ue.contract import ScenePlan
from apex.models.model import GenerationRequest
from apex.models.provider import get_default_provider


def propose_scene(
    endpoint: ModelEndpoint, prompt: str, *, level: str, record_path: Path | None = None
) -> tuple[ScenePlan, dict[str, Any]]:
    if urlparse(endpoint.url).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("local_graphics_inference_required")
    if len(prompt) > 12000:
        raise ValueError("scene_prompt_budget_exceeded")
    example = ScenePlan.demo(level=level).to_dict()
    request = GenerationRequest(
        endpoint=endpoint,
        temperature=0,
        seed=42,
        max_tokens=2048,
        timeout_s=600,
        system="APEX artistic specialist: JSON graphic operations only. No code, gameplay or commands.",
        prompt="Make a restricted UE5 scene: one road, one building, a few props and lighting. "
        "Use exactly this JSON schema/keys, allowed mesh shapes cube/sphere/cylinder, owner graphics. "
        "kind mesh/directional_light/point_light only. Keep scene_id urban_demo, revision 1, parent null, "
        "protected_engineer_ids empty. Locations/dimensions in cm; rotation [pitch,yaw,roll] degrees. "
        "Use at most 8 actors and 3 materials. Preserve level "
        + level
        + ". Output only JSON. Example: "
        + json.dumps(example)
        + " User request: "
        + prompt,
    )
    generation = get_default_provider(endpoint).generate(request)
    record = {
        "request": asdict(request),
        "generation": asdict(generation),
        "engine_execution": "unverified",
        "model_generated_code_executed": False,
    }
    if record_path:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, indent=2, default=str))
    text = generation.text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    plan = ScenePlan.parse(json.loads(text))
    if plan.level != level or plan.revision != 1 or plan.scene_id != "urban_demo":
        raise ValueError("model_changed_requested_scene_contract")
    return plan, {**record, "plan": plan.to_dict()}
