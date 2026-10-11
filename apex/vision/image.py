"""Bounded decoding, EXIF orientation and deterministic RGB preparation."""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import Any

from apex.vision.contract import VisionError


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int = 20 * 1024 * 1024
    max_pixels: int = 25_000_000
    max_side: int = 2048

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_pixels, self.max_side) < 1:
            raise ValueError("image limits must be positive")


@dataclass
class PreparedImage:
    pixels: Any
    metadata: dict[str, Any]


DEFAULT_IMAGE_LIMITS = ImageLimits()


def prepare_image(data: bytes, limits: ImageLimits = DEFAULT_IMAGE_LIMITS) -> PreparedImage:
    if not data or len(data) > limits.max_bytes:
        raise VisionError("image_size", "Image vide ou dépassant la taille autorisée.")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise VisionError("dependency_missing", "Pillow est nécessaire pour décoder les images.") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in ("PNG", "JPEG", "WEBP"):
                    raise VisionError("image_format", "Formats pris en charge : PNG, JPEG et WebP fixes.")
                if getattr(original, "n_frames", 1) != 1:
                    raise VisionError("animated_image", "Soumettre une image fixe ou des frames séparées.")
                width, height = original.size
                if width * height > limits.max_pixels:
                    raise VisionError("image_pixels", "Image dépassant le nombre de pixels autorisé.")
                orientation = original.getexif().get(274, 1)
                oriented = ImageOps.exif_transpose(original)
                # Transparent documents need a white page, not a black alpha background.
                if "A" in oriented.getbands() or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, "white")
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    rgb = oriented.convert("RGB")
                oriented_size = rgb.size
                rgb.thumbnail((limits.max_side, limits.max_side), Image.Resampling.LANCZOS)
                rgb.load()
                return PreparedImage(
                    rgb,
                    {
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "format": original.format,
                        "original_width": width,
                        "original_height": height,
                        "oriented_width": oriented_size[0],
                        "oriented_height": oriented_size[1],
                        "width": rgb.width,
                        "height": rgb.height,
                        "bytes": len(data),
                        "exif_orientation": orientation,
                        "resized": oriented_size != rgb.size,
                        "coordinates": "prepared_image_pixels",
                    },
                )
    except VisionError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise VisionError("image_pixels", "Image dépassant le plafond de décodage sûr.") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise VisionError("invalid_image", "Image illisible ou fichier corrompu.") from exc
