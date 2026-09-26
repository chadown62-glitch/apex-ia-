"""Multimodal : interfaces extensibles, implémentées selon ce que
l'environnement permet réellement.

    IMAGE : métadonnées -> OCR -> description -> diagrammes -> texte contextuel -> provenance
    VIDÉO : métadonnées -> transcription -> chapitres -> segments -> images -> OCR -> analyse

Réellement implémenté ici : métadonnées d'image PNG/JPEG/GIF (bibliothèque
standard). OCR, description visuelle et transcription exigent des modèles ou
des outils absents de cet environnement : ils répondent BLOCKED, jamais un
résultat inventé. Un outil externe (OCR, modèle de vision) utilisé plus tard
serait marqué EXTERNAL_MODEL_DEPENDENCY."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

EXTERNAL_MODEL_DEPENDENCY = "EXTERNAL_MODEL_DEPENDENCY"


class StepStatus(StrEnum):
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class MediaStep:
    name: str
    status: StepStatus
    detail: str
    data: dict[str, object] = field(default_factory=dict)


class ImageStep(Protocol):
    name: str

    def run(self, data: bytes) -> MediaStep: ...


class VideoStep(Protocol):
    name: str

    def run(self, locator: str) -> MediaStep: ...


def image_metadata(data: bytes) -> MediaStep:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return MediaStep(
            "metadata", StepStatus.DONE, "PNG", {"format": "png", "width": width, "height": height}
        )
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return MediaStep(
            "metadata", StepStatus.DONE, "GIF", {"format": "gif", "width": width, "height": height}
        )
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                break
            marker = data[index + 1]
            length = struct.unpack(">H", data[index + 2 : index + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                return MediaStep(
                    "metadata",
                    StepStatus.DONE,
                    "JPEG",
                    {"format": "jpeg", "width": width, "height": height},
                )
            index += 2 + length
        return MediaStep("metadata", StepStatus.FAILED, "JPEG sans segment SOF lisible")
    return MediaStep("metadata", StepStatus.FAILED, "format d'image non reconnu")


@dataclass(frozen=True)
class BlockedStep:
    """Étape dont la dépendance n'existe pas dans cet environnement."""

    name: str
    missing: str

    def run(self, _: object) -> MediaStep:
        return MediaStep(self.name, StepStatus.BLOCKED, f"dépendance absente : {self.missing}")


IMAGE_PIPELINE: tuple[str, ...] = ("metadata", "ocr", "description", "diagram", "context", "provenance")
VIDEO_PIPELINE: tuple[str, ...] = (
    "metadata",
    "transcript",
    "chapters",
    "segments",
    "frames",
    "ocr",
    "visual",
    "links",
)


def analyse_image(data: bytes) -> tuple[MediaStep, ...]:
    return (
        image_metadata(data),
        BlockedStep("ocr", "moteur OCR (ex. tesseract)").run(data),
        BlockedStep("description", f"modèle de vision — {EXTERNAL_MODEL_DEPENDENCY} si externe").run(data),
        BlockedStep("diagram", "reconnaissance de diagrammes").run(data),
    )


def analyse_video(locator: str) -> tuple[MediaStep, ...]:
    return tuple(BlockedStep(name, "outils vidéo/transcription").run(locator) for name in VIDEO_PIPELINE)
