"""Tool Engine : contrat, permissions explicites, espace de travail fail-closed, exécution RÉELLE en sandbox.

Aucune doublure : vraie sandbox (backend sélectionné par APEX), vrai `AgentRuntime` (PolicyEngine), vraie base
SQLite. Inclut les tests adversariaux demandés : remontée de chemin, accès hors espace, liens symboliques,
fichiers spéciaux, commande interdite, délai, ressources, sortie énorme, processus persistant, réseau.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from apex.agents.runtime import AgentRuntime
from apex.policy.model import Subject
from apex.sandbox import get_default_provider
from apex.sandbox.model import ResourceLimits
from apex.tools.builtin import default_registry, parse_unittest
from apex.tools.engine import ToolContext, ToolEngine, ToolRegistry
from apex.tools.model import ErrorCode, Permission, ToolCall, ToolFailure, ToolSpec
from apex.tools.sandbox_runner import sandbox_runner
from apex.tools.workspace import Workspace, WorkspaceLimits
from tests.test_taskloop_execution import a_pool, sqlite_session_factory

ALL = frozenset(Permission)
LIMITS = ResourceLimits(cpu_cores=1.0, memory_mb=256, pids_limit=64, timeout_s=20.0, disk_mb=32, network="none")


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    return Workspace.create(tmp_path / "workspaces", "ws-test")


@pytest.fixture
def ctx(workspace):
    factory = sqlite_session_factory()
    events: list = []
    run = sandbox_runner(agent_runtime=AgentRuntime(factory), sandbox=get_default_provider(), pool=a_pool(),
                         base_limits=LIMITS, requested_by=Subject.CORE)
    return ToolContext(workspace=workspace, granted=ALL, run_in_sandbox=run,
                       emit=lambda kind, details: events.append((kind, details)), extra={"events": events})


ENGINE = ToolEngine(default_registry())


def call(ctx, tool, **arguments):
    return ENGINE.execute(ToolCall(tool=tool, arguments=arguments, call_id=str(uuid.uuid4()), requested_by="test"), ctx)


# -- contrat du moteur ---------------------------------------------------------------------------------


def test_unknown_tool_invalid_input_and_oversized_input_are_structured_errors(ctx) -> None:
    assert call(ctx, "shell.exec", command="ls").error.code is ErrorCode.UNKNOWN_TOOL
    assert call(ctx, "fs.read").error.code is ErrorCode.INVALID_INPUT  # « path » requis
    assert call(ctx, "fs.read", path="a", surprise=1).error.code is ErrorCode.INVALID_INPUT  # argument inconnu
    assert call(ctx, "fs.read", path=3).error.code is ErrorCode.INVALID_INPUT
    assert call(ctx, "tests.run", framework="bash").error.code is ErrorCode.INVALID_INPUT  # commande interdite
    assert call(ctx, "fs.write", path="a.txt", content="x" * 1_000_001).error.code is ErrorCode.INVALID_INPUT
    assert call(ctx, "fs.write", path="a.txt", content="x" * 2_000_000).error.code is ErrorCode.TOO_LARGE
    assert call(ctx, "fs.write", path="a.txt", content="x" * 900_000).ok  # sous la limite : accepté


def test_permissions_are_never_implicit(ctx) -> None:
    ctx.granted = frozenset({Permission.READ_WORKSPACE})
    result = call(ctx, "fs.write", path="a.txt", content="x")
    assert result.error.code is ErrorCode.PERMISSION_DENIED and "workspace.write" in result.error.message
    assert not (ctx.workspace.root / "a.txt").exists()
    assert call(ctx, "code.run", path="a.py").error.code is ErrorCode.PERMISSION_DENIED
    assert {s.name for s in ENGINE.registry.available_to(ctx.granted)} == {"fs.read", "fs.list", "project.inspect"}


def test_every_call_is_logged_with_its_provenance(ctx) -> None:
    call(ctx, "fs.write", path="a.txt", content="bonjour")
    kind, details = ctx.extra["events"][-1]
    assert kind == "tool.called" and details["tool"] == "fs.write" and details["ok"] is True
    assert details["requested_by"] == "test" and details["workspace_id"] == "ws-test"
    assert len(details["arguments_sha256"]) == 64 and "bonjour" not in str(details)  # empreinte, pas contenu


def test_an_unexpected_handler_exception_becomes_an_internal_error(ctx) -> None:
    registry = ToolRegistry()

    def boom(args, context, spec):
        raise RuntimeError("panne")

    registry.register(ToolSpec(name="t.boom", description="d", input_schema={"type": "object"},
                               output_schema={"type": "object"}, permissions=frozenset()), boom)
    result = ToolEngine(registry).execute(ToolCall("t.boom", {}, "c", "test"), ctx)
    assert result.ok is False and result.error.code is ErrorCode.INTERNAL_ERROR and "panne" in result.error.message


def test_a_schema_with_an_unsupported_keyword_is_refused_at_registration() -> None:
    with pytest.raises(ValueError):
        ToolRegistry().register(ToolSpec(name="t", description="d", input_schema={"type": "object", "oneOf": []},
                                         output_schema={}, permissions=frozenset()), lambda a, c, s: {})


# -- fichiers et inspection ----------------------------------------------------------------------------


def test_write_read_list_and_inspect(ctx) -> None:
    assert call(ctx, "fs.write", path="pkg/__init__.py", content="").output["created"] is True
    call(ctx, "fs.write", path="pkg/calc.py", content="def add(a, b):\n    return a + b\n")
    call(ctx, "fs.write", path="tests/test_calc.py", content="import unittest\n")
    assert call(ctx, "fs.read", path="pkg/calc.py").output["content"].startswith("def add")
    assert call(ctx, "fs.write", path="pkg/calc.py", content="x = 1\n").output["created"] is False
    entries = call(ctx, "fs.list", path="pkg").output["entries"]
    assert [e["name"] for e in entries] == ["__init__.py", "calc.py"]
    info = call(ctx, "project.inspect").output
    assert info["file_count"] == 3 and info["test_files"] == ["tests/test_calc.py"]
    assert info["python_packages"] == ["pkg"] and info["extensions"] == {"py": 3}
    missing = call(ctx, "fs.read", path="absent.py").error
    assert missing.code is ErrorCode.NOT_FOUND
    # L'observation nomme ce qui existe (mesuré : un vrai modèle relisait 10 fois un chemin absent).
    assert "pkg/calc.py" in missing.message and "tests/test_calc.py" in missing.message


@pytest.mark.parametrize(
    "path",
    ["../escape.txt", "a/../../escape.txt", "/etc/passwd", "~/.ssh/id_rsa", "a\\..\\b", "nul\x00.txt", "..", ""],
)
def test_path_traversal_and_out_of_workspace_paths_are_refused(ctx, path) -> None:
    for tool, extra in (("fs.read", {}), ("fs.write", {"content": "x"})):
        result = call(ctx, tool, path=path, **extra)
        assert result.ok is False and result.error.code in (ErrorCode.PATH_REFUSED, ErrorCode.INVALID_INPUT), path
    assert not (ctx.workspace.root.parent / "escape.txt").exists()


def test_symlinks_are_never_followed_nor_written_through(ctx, tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    os.symlink(secret, ctx.workspace.root / "link.txt")
    os.symlink(tmp_path, ctx.workspace.root / "linkdir")
    assert call(ctx, "fs.read", path="link.txt").error.code is ErrorCode.PATH_REFUSED
    assert call(ctx, "fs.read", path="linkdir/secret.txt").error.code is ErrorCode.PATH_REFUSED
    assert call(ctx, "fs.write", path="link.txt", content="pwned").error.code is ErrorCode.PATH_REFUSED
    assert call(ctx, "fs.write", path="linkdir/new.txt", content="pwned").error.code is ErrorCode.PATH_REFUSED
    assert secret.read_text() == "SECRET" and not (tmp_path / "new.txt").exists()
    assert all(path != "link.txt" for path, _ in ctx.workspace.snapshot())  # jamais copié vers la sandbox


def test_without_o_nofollow_like_windows_files_still_work_and_links_are_still_refused(ctx, tmp_path, monkeypatch) -> None:
    """Le Python officiel de Windows n'a ni `O_NOFOLLOW` ni `O_NONBLOCK` (constaté : AttributeError sur toute
    écriture d'outil et toute pièce jointe en mode natif). Sans ces drapeaux, lecture et écriture fonctionnent,
    et un lien substitué au chemin vérifié est refusé par la comparaison d'identité du fichier ouvert."""
    import apex.tools.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "_NOFOLLOW", 0)
    monkeypatch.setattr(workspace_module, "_NONBLOCK", 0)
    assert call(ctx, "fs.write", path="notes/a.txt", content="ligne 1\nligne 2\n").ok
    assert (ctx.workspace.root / "notes" / "a.txt").read_bytes() == b"ligne 1\nligne 2\n"  # aucun \r ajouté
    assert call(ctx, "fs.read", path="notes/a.txt").output["content"] == "ligne 1\nligne 2\n"
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    os.symlink(secret, ctx.workspace.root / "swapped.txt")
    # Lien posé APRÈS la vérification du chemin : l'ouverture le suivrait, l'identité le refuse.
    with pytest.raises(ToolFailure) as refused:
        workspace_module.Workspace._open(ctx.workspace.root / "swapped.txt", os.O_RDONLY, "swapped.txt")
    assert refused.value.code is ErrorCode.PATH_REFUSED
    assert secret.read_text() == "SECRET"


def test_special_files_and_size_limits_are_refused(tmp_path) -> None:
    ws = Workspace.create(tmp_path / "w", "small", limits=WorkspaceLimits(max_file_bytes=100, max_total_bytes=150,
                                                                             max_files=2))
    os.mkfifo(ws.root / "pipe")
    with pytest.raises(ToolFailure) as exc:
        ws.read_bytes("pipe")
    assert exc.value.code is ErrorCode.PATH_REFUSED
    with pytest.raises(ToolFailure) as exc:
        ws.write_bytes("big.txt", b"x" * 101)
    assert exc.value.code is ErrorCode.TOO_LARGE
    ws.write_bytes("a.txt", b"x" * 100)
    with pytest.raises(ToolFailure) as exc:
        ws.write_bytes("b.txt", b"x" * 60)  # total > 150
    assert exc.value.code is ErrorCode.TOO_LARGE


# -- exécution de code (vraie sandbox) -------------------------------------------------------------------


def test_code_runs_in_the_sandbox_with_structured_output_and_never_touches_the_workspace(ctx) -> None:
    call(ctx, "fs.write", path="main.py", content=(
        "import sys\nopen('created_in_sandbox.txt', 'w').write('x')\nprint('bonjour', sys.argv[1:])\n"
        "sys.exit(3)\n"))
    result = call(ctx, "code.run", path="main.py", args=["a; rm -rf /", "$(id)"])
    assert result.ok is True
    out = result.output
    assert out["exit_code"] == 3 and out["status"] == "failed"
    assert "bonjour ['a; rm -rf /', '$(id)']" in out["stdout"]  # arguments jamais interprétés par un shell
    assert not (ctx.workspace.root / "created_in_sandbox.txt").exists()  # copie à sens unique


def test_forbidden_commands_cannot_be_expressed(ctx) -> None:
    call(ctx, "fs.write", path="run.sh", content="echo pwned\n")
    assert call(ctx, "code.run", path="run.sh").error.code is ErrorCode.INVALID_INPUT
    assert call(ctx, "code.run", path="../../bin/sh.py").error.code is ErrorCode.PATH_REFUSED
    assert call(ctx, "tests.run", pattern="*.py; rm -rf /").error.code is ErrorCode.INVALID_INPUT


def test_timeout_is_enforced_and_reported(ctx) -> None:
    call(ctx, "fs.write", path="slow.py", content="import time\nprint('start', flush=True)\ntime.sleep(60)\n")
    result = call(ctx, "code.run", path="slow.py", timeout_s=2)
    assert result.error.code is ErrorCode.TIMEOUT and result.output["status"] == "timeout"
    assert result.duration_s < 30


def test_resource_exhaustion_is_reported(ctx) -> None:
    call(ctx, "fs.write", path="hog.py", content="blocks = []\nwhile True:\n    blocks.append(bytearray(10_000_000))\n")
    result = call(ctx, "code.run", path="hog.py")
    assert result.ok is False and result.error.code in (ErrorCode.RESOURCE_LIMIT, ErrorCode.TIMEOUT), result
    if result.error.code is ErrorCode.RESOURCE_LIMIT:
        assert result.output["status"] in ("oom_killed", "resource_limit_exceeded")
    else:  # certains backends tuent par délai avant de constater la mémoire
        assert result.output["status"] == "timeout"


def test_huge_output_is_truncated_and_flagged(ctx) -> None:
    call(ctx, "fs.write", path="loud.py", content="print('x' * 5_000_000)\nprint('FIN')\n")
    out = call(ctx, "code.run", path="loud.py").output
    assert out["stdout_truncated"] is True and len(out["stdout"]) <= 20_000 and out["stdout"].rstrip().endswith("FIN")


def test_no_process_survives_the_execution(ctx) -> None:
    marker = f"apex_persist_{uuid.uuid4().hex[:8]}"
    call(ctx, "fs.write", path="fork.py", content=(
        "import subprocess, sys\n"
        f"subprocess.Popen(['sleep', '300', '{marker}'] if False else ['python3', '-c', 'import time; time.sleep(300)', '{marker}'])\n"
        "print('parent exits')\n"))
    assert call(ctx, "code.run", path="fork.py").output["exit_code"] == 0
    survivors = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    assert survivors.stdout.strip() == "", survivors.stdout


def test_network_is_cut(ctx) -> None:
    call(ctx, "fs.write", path="net.py", content=(
        "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 80), timeout=3)\n    print('CONNECTED')\n"
        "except OSError as exc:\n    print('BLOCKED', type(exc).__name__)\n"))
    out = call(ctx, "code.run", path="net.py").output
    assert "BLOCKED" in out["stdout"] and "CONNECTED" not in out["stdout"]


def test_the_apex_repository_and_secrets_are_not_readable_from_the_sandbox(ctx) -> None:
    repo_config = Path(__file__).resolve().parents[1] / "config.yaml"
    call(ctx, "fs.write", path="peek.py", content=(
        f"try:\n    print(open({str(repo_config)!r}).read()[:20])\nexcept OSError as exc:\n"
        "    print('DENIED', type(exc).__name__)\nimport os\nprint('ENV', sorted(k for k in os.environ if 'PASS' in k))\n"))
    out = call(ctx, "code.run", path="peek.py").output
    assert "DENIED" in out["stdout"] and "ENV []" in out["stdout"]


# -- tests ----------------------------------------------------------------------------------------------


def _project(ctx, body: str) -> None:
    call(ctx, "fs.write", path="calc.py", content="def add(a, b):\n    return a - b\n")
    call(ctx, "fs.write", path="tests/test_calc.py", content=(
        "import unittest\nfrom calc import add\n\nclass TestAdd(unittest.TestCase):\n" + body))


def test_the_test_runner_reports_failures_with_ids_and_files(ctx) -> None:
    _project(ctx, "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n"
                  "    def test_zero(self):\n        self.assertEqual(add(0, 0), 0)\n")
    out = call(ctx, "tests.run", start_dir="tests").output
    assert out["status"] == "failed" and out["tests_run"] == 2 and out["failures"] == 1
    assert out["failing_tests"] == ["test_calc.TestAdd.test_add"]
    assert "tests/test_calc.py" in out["files"] and out["exit_code"] == 1


def test_the_test_runner_reports_success_after_a_fix(ctx) -> None:
    _project(ctx, "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n")
    call(ctx, "fs.write", path="calc.py", content="def add(a, b):\n    return a + b\n")
    out = call(ctx, "tests.run", start_dir="tests").output
    assert out["status"] == "passed" and out["tests_run"] == 1 and out["failing_tests"] == []


def test_pytest_absent_from_the_sandbox_is_an_explicit_unavailable_error(ctx) -> None:
    probe = subprocess.run(["python3", "-c", "import pytest"], capture_output=True)
    _project(ctx, "    def test_add(self):\n        pass\n")
    result = call(ctx, "tests.run", framework="pytest", start_dir="tests")
    if probe.returncode != 0:
        assert result.error.code is ErrorCode.UNAVAILABLE
    else:  # pragma: no cover — dépend de l'interpréteur de la sandbox
        assert result.ok


def test_unittest_output_parser() -> None:
    parsed = parse_unittest(
        "test_a (m.T.test_a) ... FAIL\n\n======\nFAIL: test_a (m.T.test_a)\n----\nRan 3 tests in 0.001s\n\n"
        "FAILED (failures=1, errors=1, skipped=1)\n")
    assert parsed == {"tests_run": 3, "failures": 1, "errors": 1, "skipped": 1, "failing_tests": ["m.T.test_a"],
                      "status": "failed"}
    assert parse_unittest("Ran 0 tests in 0.000s\n\nNO TESTS RAN\n")["status"] == "no_tests"


def test_an_endless_output_stream_is_bounded_on_the_host(ctx) -> None:
    """Flux infini jusqu'au délai : l'hôte ne garde que début + fin (avant le 06/10/2026, `communicate()`
    accumulait tout en mémoire)."""
    import resource

    call(ctx, "fs.write", path="flood.py", content="import sys\nwhile True:\n    sys.stdout.write('y' * 100_000)\n")
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = call(ctx, "code.run", path="flood.py", timeout_s=4)
    grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
    assert result.error.code is ErrorCode.TIMEOUT
    assert result.output["stdout_truncated"] is True and len(result.output["stdout"]) <= 20_000
    assert grown_mb < 200, f"mémoire de l'hôte : +{grown_mb:.0f} Mo"


def test_bounded_capture_keeps_head_and_tail() -> None:
    import subprocess as sp

    from apex.sandbox.capture import BoundedCapture

    proc = sp.Popen(["python3", "-c", "print('DEBUT'); print('m' * 500000); print('FIN')"],
                    stdout=sp.PIPE, stderr=sp.PIPE)
    capture = BoundedCapture(proc, limit=1000).start()
    assert capture.wait(30)
    out, err, cut, err_cut = capture.finish()
    assert cut is True and err_cut is False and out.startswith(b"DEBUT") and out.rstrip().endswith(b"FIN")
    assert b"octets omis" in out and len(out) < 2200
