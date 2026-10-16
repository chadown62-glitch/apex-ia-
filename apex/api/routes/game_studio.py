"""Authenticated Game Studio routes in the existing APEX API."""

from __future__ import annotations

import base64
import binascii
import io
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from apex.api.schemas import DataEnvelope

router = APIRouter(prefix="/game-studio", tags=["game-studio"])


class ReferenceIn(BaseModel):
    content_type: Literal["image/png", "image/jpeg", "text/plain"]
    data_base64: str = Field(min_length=4, max_length=6_000_000)
    provenance: str = Field(min_length=3, max_length=500)


class StudioRequestIn(BaseModel):
    creation_type: Literal["vehicule", "batiment", "objet_3d", "environnement", "monde_ouvert", "autre"]
    creation_other: str | None = Field(default=None, max_length=200)
    style: Literal["photorealiste", "realiste", "stylise", "cartoon_anime", "low_poly", "autre"]
    style_other: str | None = Field(default=None, max_length=200)
    quality: Literal["prototype", "jouable", "soigne", "photorealiste"]
    tool: Literal["auto", "blender", "unreal", "hybride"]
    compute_backend: Literal["auto", "cuda", "cpu"] = "auto"
    controller_model: Literal["auto", "pinned_7b", "apex_native"] = "auto"
    description: str = Field(min_length=20, max_length=4000)
    judges_all_tasks: bool = False
    automatic_corrections: bool = False
    max_time_s: int = Field(default=1800, ge=120, le=3600)
    max_rss_mib: int = Field(default=12000, ge=512, le=16384)
    max_attempts: int = Field(default=3, ge=1, le=3)
    criteria: dict[str, Any] = Field(default_factory=dict)
    references: list[ReferenceIn] = Field(default_factory=list, max_length=4)
    plan_only: bool = False

    @field_validator("criteria")
    @classmethod
    def criteria_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        integer_fields = {
            "max_triangles",
            "max_distinct_defect_attempts",
            "minimum_views",
            "required_lods",
            "required_texture_size",
        }
        float_fields = {"dimension_tolerance_m", "pivot_tolerance_m"}
        bool_fields = {"require_calibrated_visual_reference"}
        if set(value) - integer_fields - float_fields - bool_fields:
            raise ValueError("unknown_quality_criterion")
        for name, item in value.items():
            if (
                (name in integer_fields and type(item) is not int)
                or (name in float_fields and type(item) not in (int, float))
                or (name in bool_fields and type(item) is not bool)
            ):
                raise ValueError("invalid_quality_criterion_type")
        return value


def _service():
    from apex.api.dependencies import get_game_studio_service

    return get_game_studio_service()


def _projects():
    from apex.api.dependencies import get_game_studio_project_service

    return get_game_studio_project_service()


def _references(refs: list[ReferenceIn]) -> list[tuple[bytes, str, str]]:
    result = []
    for ref in refs:
        try:
            data = base64.b64decode(ref.data_base64, validate=True)
        except binascii.Error as exc:
            raise HTTPException(422, "reference_base64_invalid") from exc
        if not data or len(data) > 4_000_000:
            raise HTTPException(422, "reference_size_out_of_range")
        if ref.content_type == "text/plain":
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(422, "reference_text_not_utf8") from exc
            if len(data) > 256_000:
                raise HTTPException(422, "reference_text_too_large")
        else:
            try:
                from PIL import Image, UnidentifiedImageError

                with Image.open(io.BytesIO(data)) as image:
                    if image.format != ("PNG" if ref.content_type == "image/png" else "JPEG"):
                        raise HTTPException(422, "reference_type_mismatch")
                    if image.width * image.height > 16_000_000:
                        raise HTTPException(422, "reference_pixels_exceeded")
                    image.verify()
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                raise HTTPException(422, "reference_image_invalid") from exc
        result.append((data, ref.content_type, ref.provenance))
    return result


@router.get("/capabilities", response_model=DataEnvelope[dict[str, Any]])
def capabilities() -> DataEnvelope[dict[str, Any]]:
    from apex.game_studio.capabilities import probe

    return DataEnvelope(status="real", data=probe())


@router.get("/missions", response_model=DataEnvelope[list[dict[str, Any]]])
def missions() -> DataEnvelope[list[dict[str, Any]]]:
    return DataEnvelope(status="real", data=_service().list())


@router.post("/missions", response_model=DataEnvelope[dict[str, Any]])
def create(payload: StudioRequestIn) -> DataEnvelope[dict[str, Any]]:
    refs = _references(payload.references)
    if (
        payload.creation_type not in ("vehicule", "batiment", "objet_3d")
        or payload.style != "realiste"
        or payload.tool not in ("auto", "blender")
    ):
        raise HTTPException(422, "requested_creation_style_or_tool_not_implemented")
    try:
        request = payload.model_dump(exclude={"references"})
        return DataEnvelope(status="real", data=_service().create(request, refs))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/projects", response_model=DataEnvelope[dict[str, Any]])
def create_project(payload: StudioRequestIn) -> DataEnvelope[dict[str, Any]]:
    refs = _references(payload.references)
    try:
        return DataEnvelope(
            status="real", data=_projects().create_dynamic(payload.model_dump(exclude={"references"}), refs)
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/projects", response_model=DataEnvelope[list[dict[str, Any]]])
def projects() -> DataEnvelope[list[dict[str, Any]]]:
    return DataEnvelope(status="real", data=_projects().list())


@router.get("/projects/{project_id}", response_model=DataEnvelope[dict[str, Any]])
def get_project(project_id: str) -> DataEnvelope[dict[str, Any]]:
    try:
        return DataEnvelope(status="real", data=_projects().get(project_id))
    except (LookupError, ValueError) as exc:
        raise HTTPException(404, "game_studio_project_missing") from exc


@router.post("/projects/{project_id}/cancel", response_model=DataEnvelope[dict[str, Any]])
def cancel_project(project_id: str) -> DataEnvelope[dict[str, Any]]:
    try:
        return DataEnvelope(status="real", data=_projects().cancel(project_id))
    except (LookupError, ValueError) as exc:
        raise HTTPException(404, "game_studio_project_missing") from exc


@router.post("/projects/{project_id}/resume", response_model=DataEnvelope[dict[str, Any]])
def resume_project(project_id: str) -> DataEnvelope[dict[str, Any]]:
    try:
        return DataEnvelope(status="real", data=_projects().resume(project_id))
    except LookupError as exc:
        raise HTTPException(404, "game_studio_project_missing") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/projects/{project_id}/improve-framing", response_model=DataEnvelope[dict[str, Any]])
def improve_project_framing(project_id: str) -> DataEnvelope[dict[str, Any]]:
    try:
        return DataEnvelope(status="real", data=_projects().improve_framing(project_id))
    except LookupError as exc:
        raise HTTPException(404, "game_studio_project_missing") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/projects/{project_id}/artifacts/{digest}")
def project_artifact(project_id: str, digest: str) -> Response:
    try:
        UUID(project_id)
        content, mime = _projects().artifact(project_id, digest)
    except (LookupError, ValueError) as exc:
        raise HTTPException(404, "artifact_not_owned_or_missing") from exc
    return Response(
        content=content,
        media_type=mime,
        headers={"Cache-Control": "private, immutable", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/missions/{job_id}", response_model=DataEnvelope[dict[str, Any]])
def get(job_id: str) -> DataEnvelope[dict[str, Any]]:
    try:
        return DataEnvelope(status="real", data=_service().get(job_id))
    except (LookupError, ValueError) as exc:
        raise HTTPException(404, "game_studio_mission_missing") from exc


@router.post("/missions/{job_id}/cancel", response_model=DataEnvelope[dict[str, Any]])
def cancel(job_id: str) -> DataEnvelope[dict[str, Any]]:
    try:
        return DataEnvelope(status="real", data=_service().cancel(job_id))
    except (LookupError, ValueError) as exc:
        raise HTTPException(404, "game_studio_mission_missing") from exc


@router.post("/missions/{job_id}/resume", response_model=DataEnvelope[dict[str, Any]])
def resume(job_id: str) -> DataEnvelope[dict[str, Any]]:
    try:
        return DataEnvelope(status="real", data=_service().resume(job_id))
    except LookupError as exc:
        raise HTTPException(404, "game_studio_mission_missing") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/missions/{job_id}/artifacts/{digest}")
def artifact(job_id: str, digest: str) -> Response:
    try:
        UUID(job_id)
        content, mime = _service().artifact(job_id, digest)
    except (LookupError, ValueError) as exc:
        raise HTTPException(404, "artifact_not_owned_or_missing") from exc
    return Response(
        content=content,
        media_type=mime,
        headers={"Cache-Control": "private, immutable", "X-Content-Type-Options": "nosniff"},
    )
