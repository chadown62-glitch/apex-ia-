"""Optional Tesseract subprocess. Its model exits before VLM loading begins."""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import time

from apex.vision.contract import OCRResult, OCRWord, VisionError, VisionRequest
from apex.vision.image import PreparedImage


def check_deadline(request: VisionRequest, deadline: float) -> None:
    if request.cancel is not None and request.cancel.is_set():
        raise VisionError("cancelled", "Analyse annulée.")
    if time.monotonic() >= deadline:
        raise VisionError("timeout", "Délai de l'analyse dépassé.")


def extract_text(image: PreparedImage, request: VisionRequest, deadline: float) -> OCRResult:
    executable = shutil.which("tesseract")
    if executable is None:
        return OCRResult("unavailable", detail="Tesseract absent ; le VLM peut lire l'image directement.")
    check_deadline(request, deadline)
    try:
        available = (
            subprocess.run(  # noqa: S603 - resolved local binary, fixed arguments; no shell
                [executable, "--list-langs"],
                capture_output=True,
                check=True,
                timeout=min(5.0, max(0.001, deadline - time.monotonic())),
            )
            .stdout.decode("utf-8", errors="replace")
            .splitlines()
        )
        languages = "+".join(lang for lang in ("fra", "eng") if lang in available)
        if not languages:
            return OCRResult("unavailable", detail="Données OCR fra/eng absentes.")
        encoded = io.BytesIO()
        image.pixels.save(encoded, format="PNG")
        process = subprocess.run(  # noqa: S603 - image is stdin bytes, never executable arguments
            [executable, "stdin", "stdout", "-l", languages, "--psm", "3", "tsv"],
            input=encoded.getvalue(),
            capture_output=True,
            check=True,
            timeout=min(30.0, max(0.001, deadline - time.monotonic())),
        )
        check_deadline(request, deadline)
    except subprocess.TimeoutExpired:
        check_deadline(request, deadline)
        return OCRResult("failed", detail="Tesseract a dépassé son délai de 30 secondes.")
    except (subprocess.CalledProcessError, OSError):
        return OCRResult("failed", detail="Échec du processus OCR ; aucun texte OCR inventé.")
    words: list[OCRWord] = []
    lines: dict[tuple[str, ...], list[str]] = {}
    try:
        for row in csv.DictReader(io.StringIO(process.stdout.decode("utf-8")), delimiter="\t"):
            text = (row.get("text") or "").strip()
            confidence = float(row["conf"])
            if not text or confidence < 0:
                continue
            box = tuple(int(row[k]) for k in ("left", "top", "width", "height"))
            words.append(OCRWord(text, confidence, (box[0], box[1], box[2], box[3])))
            key = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
            lines.setdefault(key, []).append(text)
    except (KeyError, ValueError, UnicodeError):
        return OCRResult("failed", detail="Sortie OCR mal formée.")
    detail = None if "fra" in languages else "OCR anglais seulement ; données françaises absentes."
    return OCRResult(
        "done", "\n".join(" ".join(line) for line in lines.values()), tuple(words), languages, detail
    )
