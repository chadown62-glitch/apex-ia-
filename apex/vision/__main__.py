"""Explicit CLI entrypoint; runtime never downloads models implicitly."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from apex.vision.backend import MANIFEST, MODEL_REPO, BackendOptions, LocalQwenVLBackend, sha256_file
from apex.vision.contract import VisionRequest, VisionStatus
from apex.vision.service import VisionService


def fetch(model_dir: Path, revision: str | None) -> Path:
    from huggingface_hub import HfApi
    from huggingface_hub.utils.tqdm import disable_progress_bars

    from apex.models.fetch import fetch_model

    # Avoid emitting presigned artifact URLs through retry/progress logs.
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    disable_progress_bars()
    # Resolve first, then fetch an immutable revision through the existing verified APEX fetcher.
    info = HfApi().model_info(MODEL_REPO, revision=revision, files_metadata=True)
    target = fetch_model(MODEL_REPO, models_dir=model_dir.parent, name=model_dir.name, revision=info.sha)
    files = [
        p
        for p in target.iterdir()
        if p.is_file()
        and p.suffix in (".json", ".txt", ".safetensors", ".model")
        and not p.name.startswith(".")
    ]
    manifest = {
        "schema_version": 1,
        "repo": MODEL_REPO,
        "commit": info.sha,
        "sha256": {p.name: sha256_file(p) for p in files},
    }
    destination = target / MANIFEST
    temporary = target / (MANIFEST + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="APEX local image specialist (separate from the main brain)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("fetch", help="explicitly download and verify Qwen2.5-VL-3B weights")
    download.add_argument("--model-dir", required=True, type=Path)
    download.add_argument("--revision")
    analyze = commands.add_parser("analyze")
    analyze.add_argument("image", type=Path)
    analyze.add_argument("--model-dir", type=Path)
    analyze.add_argument(
        "--ocr-only", action="store_true", help="partial extraction; no invented VLM answer"
    )
    analyze.add_argument("--no-ocr", action="store_true")
    analyze.add_argument("--question", default="Lis l'image et aide à comprendre ou résoudre le document.")
    analyze.add_argument(
        "--task", choices=("exercise", "document", "diagram", "transcribe"), default="exercise"
    )
    analyze.add_argument("--timeout", type=float, default=1200)
    analyze.add_argument("--max-tokens", type=int, default=768)
    analyze.add_argument("--precision", choices=("auto", "float32", "bfloat16"), default="auto")
    analyze.add_argument("--quantization", choices=("auto", "none", "nf4"), default="auto")
    analyze.add_argument("--memory-gib", type=float, default=12)
    analyze.add_argument("--visual-tokens", type=int, default=1024)
    analyze.add_argument("--session-path", type=Path)
    analyze.add_argument("--artifact-dir", type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        try:
            print(json.dumps({"installed": str(fetch(args.model_dir.resolve(), args.revision))}))
            return 0
        except Exception as exc:
            print(
                json.dumps(
                    {"status": "blocked", "error": "model_fetch_failed", "cause": type(exc).__name__}
                )
            )
            return 2
    if not args.ocr_only and args.model_dir is None:
        parser.error("--model-dir is required unless --ocr-only is selected")
    try:
        # Bound file reads, including files whose size changes between stat and read.
        with args.image.open("rb") as handle:
            data = handle.read(20 * 1024 * 1024 + 1)
        request = VisionRequest(
            data,
            question=args.question,
            task=args.task,
            timeout_s=args.timeout,
            max_new_tokens=args.max_tokens,
        )
        backend = (
            None
            if args.ocr_only
            else LocalQwenVLBackend(
                BackendOptions(
                    args.model_dir,
                    precision=args.precision,
                    quantization=args.quantization,
                    memory_budget_gib=args.memory_gib,
                    max_visual_tokens=args.visual_tokens,
                )
            )
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": "invalid_request", "cause": type(exc).__name__}))
        return 2
    store = None
    if args.artifact_dir:
        from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore

        store = LocalFilesystemArtifactStore(root=str(args.artifact_dir))
    result = VisionService(
        backend, use_ocr=not args.no_ocr, session_path=args.session_path, artifact_store=store
    ).analyze(request)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return (
        0 if result.status is VisionStatus.COMPLETE else 3 if result.status is VisionStatus.PARTIAL else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
