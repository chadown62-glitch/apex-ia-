"""Real Tesseract/image checks; pretrained VLM tests explicitly skip when its artifact is absent."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from apex.vision import VisionRequest, VisionService, VisionStatus
from apex.vision.backend import MANIFEST, BackendOptions, LocalQwenVLBackend
from scripts.vision.build_fixtures import build_fixtures


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    path = tmp_path_factory.mktemp("vision-fixtures")
    build_fixtures(path)
    return path


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="BLOCKED: Tesseract not installed")
@pytest.mark.parametrize(
    "name,expected", [("exercise.jpg", "2x"), ("document.png", "Masse"), ("chart.png", "GRAPHIQUE")]
)
def test_real_ocr_on_synthetic_documents(images, name, expected):
    result = VisionService(None, session_path=images / "lock").analyze(
        VisionRequest((images / name).read_bytes())
    )
    assert result.status is VisionStatus.PARTIAL and result.output is None
    assert result.ocr.status == "done" and expected in result.ocr.text
    assert result.ocr.words and all(w.box[2] > 0 for w in result.ocr.words)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="BLOCKED: Tesseract not installed")
def test_blank_ocr_does_not_invent_an_exercise(images):
    result = VisionService(None, session_path=images / "lock").analyze(
        VisionRequest((images / "blank.png").read_bytes())
    )
    assert result.ocr.status == "done" and result.ocr.text == "" and result.output is None


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="BLOCKED: Tesseract not installed")
def test_cli_distinguishes_partial_ocr_from_full_analysis(images):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "apex.vision",
            "analyze",
            str(images / "exercise.jpg"),
            "--ocr-only",
            "--session-path",
            str(images / "lock"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3
    output = json.loads(result.stdout)
    assert output["status"] == "partial" and output["output"] is None and "2x" in output["ocr"]["text"]


@pytest.mark.parametrize(
    "name,expected", [("exercise.jpg", "4"), ("document.png", "3"), ("chart.png", "3"), ("blank.png", None)]
)
def test_pretrained_local_vlm_semantics(images, name, expected, monkeypatch):
    configured = os.environ.get("APEX_VISION_MODEL_DIR")
    if not configured or not (Path(configured) / MANIFEST).is_file():
        pytest.skip("BLOCKED: verified pretrained vision artifact absent; no inference/quality proof")
    cases = json.loads((images / "cases.json").read_text())
    question = next(c["question"] for c in cases if c["name"] == name)
    if name == "blank.png":
        # Exercise the actual CLI as well as the Python delegation interface.
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "apex.vision",
                "analyze",
                str(images / name),
                "--model-dir",
                configured,
                "--question",
                question,
                "--timeout",
                "1800",
                "--session-path",
                str(images / "lock"),
                "--artifact-dir",
                str(images / "artifacts"),
            ],
            capture_output=True,
            text=True,
            timeout=1950,
        )
        payload = json.loads(process.stdout)
        assert process.returncode == 0, payload.get("error")
        assert payload["facts"]["result_artifact"]
    else:
        backend = LocalQwenVLBackend(BackendOptions(Path(configured)))
        from apex.vision import backend as backend_module

        original_parser = backend_module.parse_answer

        def record_and_parse(text):
            # Observe actual generated text; neither generation nor validation is replaced.
            (images / (name + ".model-output.txt")).write_text(text, encoding="utf-8")
            return original_parser(text)

        monkeypatch.setattr(backend_module, "parse_answer", record_and_parse)
        result = VisionService(backend, session_path=images / "lock").analyze(
            VisionRequest(
                (images / name).read_bytes(), question=question, timeout_s=1800, max_new_tokens=1024
            )
        )
        payload = result.to_dict()
    (images / (name + ".result.json")).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    assert payload["status"] == "complete", payload["error"]
    assert payload["facts"]["completion_tokens"] > 0
    assert payload["facts"]["backend"] == "local_qwen2_5_vl"
    elements = payload["output"]["visual_elements"]
    assert all(e["label"].lower() not in ("nom", "type", "label") for e in elements)
    if name == "document.png":
        assert "table" in {e["kind"] for e in elements}
    if name == "chart.png":
        assert "chart" in {e["kind"] for e in elements}
    if expected is None:
        assert payload["output"]["answer"] is None and payload["output"]["uncertainties"]
        assert payload["output"]["extracted_text"].strip() == "" and not elements
    else:
        # Minimal semantic check; extraction, units and visual grounding still need examination.
        assert payload["output"]["answer"] is not None
        assert re.search(rf"\b{expected}\b", payload["output"]["answer"])
