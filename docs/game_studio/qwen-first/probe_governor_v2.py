"""Reproduce the real protocol-2 CPU/OS-lock probe; no CUDA simulation.

Pass a local copy of Claude PR27 gpu_handoff.py at the pinned commit.
The source hash is verified before import. No protected repository file is edited.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

from apex.game_graphics.resources import _locked
from apex.game_studio.gpu import resource_lease

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
source = args.source
assert (
    hashlib.sha256(source.read_bytes()).hexdigest()
    == "78add5edbaffdd6a0075f56e504ff7b8b0a539a9d00118228558043e741971e1"
)
assert not args.output.exists(), "preserve prior evidence"
name = "apex.models.gpu_handoff"
spec = importlib.util.spec_from_file_location(name, source)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix="apex-30-v2-") as folder:
    os.environ["APEX_HOME"] = folder
    governor = module.governor()
    assert governor.status()["cuda_available"] is False
    initial = governor.status()
    with resource_lease(Path(folder) / "cpu/task.lock", compute_backend="cpu"):
        assert governor.status() == initial
    ack = module.request_gpu(1, requester="probe:other")
    assert ack["released"] is True and ack["protocol_version"] == 2
    before = governor.status()
    unknown = module.return_gpu(requester="probe:unknown")
    assert unknown["returned"] is False and governor.status() == before
    with (
        _locked(Path(folder) / ".apex/model-session.lock"),
        resource_lease(Path(folder) / "cpu/task.lock", compute_backend="cpu"),
    ):
        assert governor.status() == before
    # Exercise the CUDA adapter's ownership boundary without a CUDA model/task.
    # This is an actual CPU governor, not a fabricated CUDA device.
    with resource_lease(Path(folder) / "ownership/task.lock", compute_backend="cuda"):
        during = governor.status()
        assert len(during["lenders"]) == 2
    after = governor.status()
    assert after["lenders"] == {"probe:other": 1} and after["admissions_open"] is False
    report = json.loads(next((Path(folder) / "ownership").glob("gpu-handoff-*.json")).read_text())
    assert report["return"]["returned"] is True and report["return"]["resumed"] is False
    final_return = module.return_gpu(requester="probe:other")
    assert final_return["returned"] is True and final_return["resumed"] is True
    result = {
        "claude_commit": "cf76ff1af6216d1cf615621bebf00a2bc3f4495c",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "scope": (
            "Actual Claude protocol2 source, Game Studio adapter and OS locks on Linux CPU; "
            "no CUDA model, inference or unloading"
        ),
        "cpu_does_not_borrow": True,
        "cpu_preserves_foreign_loan_and_lock": True,
        "unknown_return": unknown,
        "during": during,
        "after": after,
        "adapter_receipt": report,
        "final_return": final_return,
        "passed": True,
        "cuda_qualified": False,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("passed", "scope", "cuda_qualified")}))
