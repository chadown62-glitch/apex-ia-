"""Contract/error tests use explicit test doubles; no semantic model proof is claimed."""

from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.vision import MediaContext, VisionRequest, VisionService, VisionStatus
from apex.vision.backend import MANIFEST, BackendOptions, sha256_file, verify_artifact
from apex.vision.contract import InferenceOutput, VisionError
from apex.vision.image import ImageLimits, prepare_image
from apex.vision.output import ANSWER_SCHEMA, parse_answer
from apex.vision.session import ModelSession


def encoded_image(mode="RGB", size=(200, 120), color="white", **save_options):
    image = Image.new(mode, size, color)
    out = io.BytesIO()
    image.save(out, format="PNG", **save_options)
    return out.getvalue()


def answer_json(**changes):
    output = {
        "extracted_text": "2x + 3 = 11",
        "visual_elements": [],
        "analysis": "Test double only.",
        "uncertainties": [],
        "answer": "x = 4",
        "help_steps": ["Soustraire 3."],
    }
    output.update(changes)
    return json.dumps(output)


class TestDoubleBackend:
    __test__ = False

    def __init__(self, error=None):
        self.closed = 0
        self.calls = 0
        self.error = error

    def infer(self, image, request, ocr, deadline):
        self.calls += 1
        if self.error:
            raise self.error
        return InferenceOutput(parse_answer(answer_json()), {"backend": "test_double"})

    def close(self):
        self.closed += 1


@pytest.mark.parametrize(
    "change",
    [
        {"timeout_s": 0},
        {"timeout_s": float("nan")},
        {"max_new_tokens": 0},
        {"max_new_tokens": True},
        {"task": "video"},
        {"question": ""},
    ],
)
def test_invalid_requests_are_rejected(change):
    with pytest.raises(ValueError):
        VisionRequest(encoded_image(), **change)


def test_frames_require_explicit_temporal_provenance():
    with pytest.raises(ValueError):
        MediaContext(kind="video_frame")
    context = MediaContext(kind="video_frame", source_id="sample", timestamp_s=2.5)
    assert context.timestamp_s == 2.5


def test_decode_orient_resize_and_provenance():
    exif = Image.Exif()
    exif[274] = 6
    prepared = prepare_image(encoded_image(size=(200, 120), exif=exif), ImageLimits(max_side=100))
    assert prepared.pixels.size == (60, 100)
    assert prepared.metadata["oriented_width"] == 120 and prepared.metadata["resized"]
    assert len(prepared.metadata["sha256"]) == 64


def test_transparent_page_is_composited_on_white():
    prepared = prepare_image(encoded_image("RGBA", color=(0, 0, 0, 0)))
    assert prepared.pixels.getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize(
    "data,limits,code",
    [
        (b"corrupt", ImageLimits(), "invalid_image"),
        (b"", ImageLimits(), "image_size"),
        (encoded_image(), ImageLimits(max_bytes=10), "image_size"),
        (encoded_image(), ImageLimits(max_pixels=100), "image_pixels"),
    ],
)
def test_invalid_images_cannot_reach_inference(data, limits, code, tmp_path):
    backend = TestDoubleBackend()
    result = VisionService(backend, use_ocr=False, limits=limits, session_path=tmp_path / "lock").analyze(
        VisionRequest(data)
    )
    assert result.status is VisionStatus.ERROR and result.error["code"] == code
    assert backend.calls == 0


@pytest.mark.parametrize(
    "text",
    [
        "I think x = 4",
        answer_json(answer=4),
        answer_json(uncertainties="none"),
        answer_json(visual_elements=[{"kind": "plot"}]),
        answer_json(analysis=""),
        answer_json(extra="invented"),
    ],
)
def test_invalid_model_output_is_not_silently_repaired(text):
    with pytest.raises(VisionError, match="JSON"):
        parse_answer(text)


def test_valid_fenced_json_keeps_uncertainties():
    output = parse_answer(
        "```json\n" + answer_json(answer=None, uncertainties=["Symbole illisible."]) + "\n```"
    )
    assert output.answer is None and output.uncertainties == ("Symbole illisible.",)


def test_json_decoder_rejects_wrong_element_key_and_enforces_steps_before_answer():
    from lmformatenforcer import CharacterLevelParserConfig, JsonSchemaParser

    data = json.loads(answer_json())
    data["visual_elements"] = [{"kind": "text", "label": "Equation", "description": "2x + 3 = 11"}]
    ordered = {key: data[key] for key in ANSWER_SCHEMA["required"]}

    def accepted(text):
        parser = JsonSchemaParser(
            ANSWER_SCHEMA, config=CharacterLevelParserConfig(force_json_field_order=True)
        )
        for character in text:
            if character not in parser.get_allowed_characters():
                return False
            parser = parser.add_character(character)
        return parser.can_end()

    valid = json.dumps(ordered)
    assert accepted(valid)
    assert not accepted(valid.replace('"description"', '"description_textual"'))
    assert not accepted(answer_json())  # final answer comes before the calculation steps


def test_rejected_generation_retains_execution_facts_without_returning_an_answer(tmp_path):
    backend = TestDoubleBackend(
        VisionError("invalid_output", "Safe detail.", facts={"completion_tokens": 42})
    )
    result = VisionService(backend, use_ocr=False, session_path=tmp_path / "lock").analyze(
        VisionRequest(encoded_image())
    )
    assert result.status is VisionStatus.ERROR and result.output is None
    assert result.facts["evidence_scope"] == "local_inference_rejected"
    assert result.facts["completion_tokens"] == 42 and backend.closed == 1


@pytest.mark.parametrize(
    "code,status",
    [
        ("model_missing", VisionStatus.BLOCKED),
        ("artifact_integrity", VisionStatus.ERROR),
        ("timeout", VisionStatus.ERROR),
        ("invalid_output", VisionStatus.ERROR),
    ],
)
def test_backend_errors_release_model_and_retain_honest_status(code, status, tmp_path):
    backend = TestDoubleBackend(VisionError(code, "Safe detail."))
    service = VisionService(backend, use_ocr=False, session_path=tmp_path / "lock")
    result = service.analyze(VisionRequest(encoded_image()))
    assert result.status is status and result.output is None and result.error["code"] == code
    assert backend.closed == 1


def test_precancellation_never_loads_a_model(tmp_path):
    cancel = threading.Event()
    cancel.set()
    backend = TestDoubleBackend()
    result = VisionService(backend, session_path=tmp_path / "lock").analyze(
        VisionRequest(encoded_image(), cancel=cancel)
    )
    assert result.error["code"] == "cancelled" and backend.calls == 0


def test_exclusive_session_wait_is_bounded_and_released(tmp_path):
    path = tmp_path / "lock"
    request = VisionRequest(encoded_image())
    with ModelSession(path, request, time.monotonic() + 2):
        with pytest.raises(VisionError) as exc, ModelSession(path, request, time.monotonic() + 0.1):
            pytest.fail("second session acquired a held lock")
        assert exc.value.code == "timeout"
    with ModelSession(path, request, time.monotonic() + 1):
        pass


def test_success_serializes_and_reuses_existing_apex_artifact_store(tmp_path):
    backend = TestDoubleBackend()
    store = LocalFilesystemArtifactStore(root=str(tmp_path / "artifacts"))
    result = VisionService(
        backend, use_ocr=False, session_path=tmp_path / "lock", artifact_store=store
    ).analyze(VisionRequest(encoded_image()))
    assert result.status is VisionStatus.COMPLETE and backend.closed == 1
    saved = json.loads(store.get(result.facts["result_artifact"]))
    assert saved["output"]["answer"] == "x = 4" and saved["facts"]["backend"] == "test_double"


def make_artifact(path: Path):
    path.mkdir()
    (path / "config.json").write_text('{"model_type":"qwen2_5_vl"}')
    (path / "model.safetensors").write_bytes(b"test artifact only; never loaded")
    manifest = {
        "schema_version": 1,
        "repo": "Qwen/Qwen2.5-VL-3B-Instruct",
        "commit": "a" * 40,
        "sha256": {p.name: sha256_file(p) for p in path.iterdir()},
    }
    (path / MANIFEST).write_text(json.dumps(manifest))
    return path


def test_changed_artifacts_are_refused_without_deletion(tmp_path):
    path = make_artifact(tmp_path / "model")
    assert verify_artifact(path)["commit"] == "a" * 40
    (path / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(VisionError) as exc:
        verify_artifact(path)
    assert exc.value.code == "artifact_integrity" and (path / "model.safetensors").exists()


def test_manifest_cannot_name_paths_outside_model(tmp_path):
    path = make_artifact(tmp_path / "model")
    manifest = json.loads((path / MANIFEST).read_text())
    manifest["sha256"]["../secret"] = "0" * 64
    (path / MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(VisionError) as exc:
        verify_artifact(path)
    assert exc.value.code == "artifact_unverified"


def test_model_budgets_are_bounded():
    with pytest.raises(ValueError):
        BackendOptions(Path("model"), max_visual_tokens=100_000)


def test_registered_main_runtime_blocks_specialist_loading(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "backend.json").write_text(json.dumps({"phase": "serving", "pid": os.getpid()}))
    backend = TestDoubleBackend()
    result = VisionService(backend, use_ocr=False, session_path=tmp_path / "model-session.lock").analyze(
        VisionRequest(encoded_image())
    )
    assert result.status is VisionStatus.BLOCKED and result.error["code"] == "main_runtime_active"
    assert backend.calls == 0


def test_no_available_extraction_is_blocked_not_a_partial_success(tmp_path):
    result = VisionService(None, use_ocr=False, session_path=tmp_path / "lock").analyze(
        VisionRequest(encoded_image())
    )
    assert result.status is VisionStatus.BLOCKED and result.output is None


@pytest.mark.parametrize("record", ["[]", "{", "{}"])
def test_unverified_core_discovery_blocks_loading_instead_of_crashing(record, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "backend.json").write_text(record)
    backend = TestDoubleBackend()
    result = VisionService(backend, use_ocr=False, session_path=tmp_path / "lock").analyze(
        VisionRequest(encoded_image())
    )
    assert result.status is VisionStatus.BLOCKED
    assert result.error["code"] == "main_runtime_unverified" and backend.calls == 0


def test_custom_lease_cannot_bypass_public_core_discovery(tmp_path, monkeypatch):
    runtime = tmp_path / "global-runtime"
    runtime.mkdir()
    monkeypatch.setenv("APEX_DISCOVERY_DIR", str(runtime))
    (runtime / "backend.json").write_text(json.dumps({"phase": "serving", "pid": os.getpid()}))
    backend = TestDoubleBackend()
    result = VisionService(backend, use_ocr=False, session_path=tmp_path / "other" / "lock").analyze(
        VisionRequest(encoded_image())
    )
    assert result.status is VisionStatus.BLOCKED and result.error["code"] == "main_runtime_active"
    assert backend.calls == 0


def test_custom_home_cannot_bypass_the_default_standalone_core(tmp_path, monkeypatch):
    from apex import standalone

    default = tmp_path / "default-home"
    monkeypatch.setattr(standalone, "default_home", lambda: default)
    monkeypatch.setenv("APEX_HOME", str(tmp_path / "specialist-home"))
    runtime = default / ".apex" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "backend.json").write_text(json.dumps({"phase": "serving", "pid": os.getpid()}))
    backend = TestDoubleBackend()
    result = VisionService(backend, use_ocr=False, session_path=tmp_path / "lock").analyze(
        VisionRequest(encoded_image())
    )
    assert result.status is VisionStatus.BLOCKED and result.error["code"] == "main_runtime_active"
    assert backend.calls == 0
