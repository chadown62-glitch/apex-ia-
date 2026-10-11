"""Versioned, image-first delegation contract; video frames carry explicit provenance."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from threading import Event
from typing import Any
from uuid import uuid4


class VisionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    ERROR = "error"


class VisionError(Exception):
    def __init__(self, code: str, detail: str, *, facts: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.facts = facts or {}


@dataclass(frozen=True)
class MediaContext:
    kind: str = "image"
    source_id: str | None = None
    timestamp_s: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("image", "video_frame"):
            raise ValueError("kind must be image or video_frame")
        if self.kind == "video_frame" and (
            not self.source_id
            or self.timestamp_s is None
            or not math.isfinite(self.timestamp_s)
            or self.timestamp_s < 0
        ):
            raise ValueError("video_frame requires source_id and finite nonnegative timestamp_s")
        if self.kind == "image" and self.timestamp_s is not None:
            raise ValueError("timestamps are reserved for video_frame")


@dataclass(frozen=True)
class VisionRequest:
    image: bytes
    question: str = "Lis ce document et aide à résoudre l'exercice s'il y en a un."
    task: str = "exercise"
    context: MediaContext = field(default_factory=MediaContext)
    timeout_s: float = 1800.0
    max_new_tokens: int = 1024
    cancel: Event | None = field(default=None, repr=False, compare=False)
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not isinstance(self.image, bytes):
            raise ValueError("image must contain encoded image bytes")
        if self.task not in ("exercise", "document", "diagram", "transcribe"):
            raise ValueError("unknown visual task")
        if not isinstance(self.question, str) or not self.question.strip() or len(self.question) > 10_000:
            raise ValueError("question must contain 1-10000 characters")
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        if type(self.max_new_tokens) is not int or not 1 <= self.max_new_tokens <= 2048:
            raise ValueError("max_new_tokens must be between 1 and 2048")


@dataclass(frozen=True)
class VisualElement:
    kind: str
    label: str
    description: str


@dataclass(frozen=True)
class VisualAnswer:
    extracted_text: str
    visual_elements: tuple[VisualElement, ...]
    analysis: str
    uncertainties: tuple[str, ...]
    answer: str | None
    help_steps: tuple[str, ...]


@dataclass(frozen=True)
class OCRWord:
    text: str
    confidence: float
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class OCRResult:
    status: str
    text: str = ""
    words: tuple[OCRWord, ...] = ()
    languages: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class InferenceOutput:
    answer: VisualAnswer
    facts: dict[str, Any]


@dataclass(frozen=True)
class VisionResult:
    request_id: str
    status: VisionStatus
    context: MediaContext
    image: dict[str, Any] | None = None
    ocr: OCRResult | None = None
    output: VisualAnswer | None = None
    error: dict[str, str] | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
