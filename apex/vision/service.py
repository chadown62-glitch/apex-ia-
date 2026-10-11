"""Image orchestration, honest partial results and reuse of APEX's ArtifactStore."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from apex.artifacts.provider import ArtifactStoreProvider
from apex.vision.backend import VisionBackend
from apex.vision.contract import VisionError, VisionRequest, VisionResult, VisionStatus
from apex.vision.image import DEFAULT_IMAGE_LIMITS, ImageLimits, prepare_image
from apex.vision.ocr import check_deadline, extract_text
from apex.vision.session import ModelSession, default_session_path

_BLOCKERS = {
    "main_runtime_active",
    "main_runtime_unverified",
    "model_missing",
    "artifact_unverified",
    "dependency_missing",
    "device_unsupported",
    "memory_insufficient",
}
_LOG = logging.getLogger("apex.vision")


class VisionService:
    def __init__(
        self,
        backend: VisionBackend | None,
        *,
        use_ocr: bool = True,
        limits: ImageLimits = DEFAULT_IMAGE_LIMITS,
        session_path: Path | None = None,
        artifact_store: ArtifactStoreProvider | None = None,
    ) -> None:
        self.backend = backend
        self.use_ocr = use_ocr
        self.limits = limits
        self.session_path = session_path or default_session_path()
        self.artifact_store = artifact_store

    def analyze(self, request: VisionRequest) -> VisionResult:
        started = time.monotonic()
        deadline = started + request.timeout_s
        prepared = None
        ocr = None
        limitations: list[str] = []
        backend_attempted = False
        try:
            check_deadline(request, deadline)
            prepared = prepare_image(request.image, self.limits)
            with ModelSession(self.session_path, request, deadline):
                if self.use_ocr:
                    ocr = extract_text(prepared, request, deadline)
                    if ocr.detail:
                        limitations.append(ocr.detail)
                    if any(word.confidence < 50 for word in ocr.words):
                        limitations.append("Mots OCR de faible confiance ; scores Tesseract non calibrés.")
                if self.backend is None:
                    if ocr is None or ocr.status != "done":
                        raise VisionError(
                            "dependency_missing", "Aucun VLM ni OCR disponible pour cette image."
                        )
                    result = VisionResult(
                        request.request_id,
                        VisionStatus.PARTIAL,
                        request.context,
                        prepared.metadata,
                        ocr,
                        limitations=(*limitations, "OCR seul : aucune compréhension ou résolution VLM."),
                    )
                else:
                    try:
                        backend_attempted = True
                        inference = self.backend.infer(prepared, request, ocr, deadline)
                        check_deadline(request, deadline)
                    finally:
                        # Release model memory BEFORE the cross-process lease is relinquished.
                        self.backend.close()
                    limitations.append(
                        "Réponse du modèle non vérifiée par un oracle ; relire les incertitudes."
                    )
                    if inference.facts.get("text_budget_hits"):
                        limitations.append(
                            "Certains champs atteignent leur limite de texte et peuvent être incomplets ; "
                            "réduire la tâche ou recadrer l'image."
                        )
                    result = VisionResult(
                        request.request_id,
                        VisionStatus.COMPLETE,
                        request.context,
                        prepared.metadata,
                        ocr,
                        inference.answer,
                        facts=inference.facts,
                        limitations=tuple(limitations),
                    )
        except VisionError as exc:
            result = VisionResult(
                request.request_id,
                VisionStatus.BLOCKED if exc.code in _BLOCKERS else VisionStatus.ERROR,
                request.context,
                prepared.metadata if prepared is not None else None,
                ocr,
                error={"code": exc.code, "detail": exc.detail},
                facts=dict(exc.facts),
                limitations=tuple(limitations),
            )
        except (OSError, ImportError) as exc:
            result = VisionResult(
                request.request_id,
                VisionStatus.ERROR,
                request.context,
                prepared.metadata if prepared is not None else None,
                ocr,
                error={
                    "code": "setup_failed",
                    "detail": f"Dépendance/session indisponible ({type(exc).__name__}).",
                },
                limitations=tuple(limitations),
            )
        finally:
            if prepared is not None:
                prepared.pixels.close()
        result.facts["elapsed_s"] = time.monotonic() - started
        result.facts["evidence_scope"] = (
            "local_inference"
            if result.output is not None
            else "local_inference_rejected"
            if result.facts.get("completion_tokens", 0) > 0
            else "backend_attempt_failed"
            if backend_attempted
            else "no_backend_attempt"
        )
        if self.artifact_store is not None:
            try:
                artifact = self.artifact_store.put(
                    json.dumps(result.to_dict(), ensure_ascii=False).encode(),
                    content_type="application/json",
                )
                result.facts["result_artifact"] = artifact.content_hash
            except Exception as exc:
                result.facts["persistence_error"] = type(exc).__name__
        _LOG.info(
            "vision request=%s status=%s code=%s",
            request.request_id,
            result.status.value,
            result.error["code"] if result.error else "none",
        )
        return result
