"""Exécution RÉELLE de code candidat dans le sandbox, jugement hors sandbox.

Un appel = UN lancement du sandbox (`SandboxProvider`, isolation vérifiée,
réseau coupé) qui exécute une liste de sources. Dans le sandbox, un
« runner » lance CHAQUE source dans un processus Python ENFANT séparé, avec
son propre délai : une boucle infinie ou un plantage n'affecte que ce
candidat. L'enfant publie ses sorties (repr des valeurs retournées, ou
`EXC:<Type>`) sur un descripteur de pipe dédié — jamais sur stdout, que le
code candidat contrôle. Le runner imprime UNE seule ligne préfixée par un
nonce frais tiré par l'hôte.

Les sorties ATTENDUES ne quittent jamais l'hôte : la comparaison se fait ici,
après l'exécution. Aucune ligne, plusieurs lignes, nonce faux, nombre de
résultats faux -> exécution INVALIDE (jamais un succès)."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any

from apex.sandbox.model import ResourceLimits, SandboxSpec
from apex.sandbox.provider import SandboxProvider

_PREFIX = "APEX_REPAIR_RESULT:"
_MAX_REPR = 400
#: Taille cumulée maximale des sources d'un lancement (marge sous les 128 Kio d'un argument).
_MAX_BATCH_CHARS = 40_000

_CHILD = r"""
import ast, json, os, sys
fd = int(sys.argv[1])
data = json.loads(sys.stdin.read())
namespace = {}
try:
    exec(compile(data["source"], "<candidate>", "exec"), namespace)
    function = namespace[data["name"]]
except BaseException as exc:
    os.write(fd, json.dumps({"load_error": type(exc).__name__}).encode())
    raise SystemExit(0)
outputs = []
for raw in data["inputs"]:
    args = ast.literal_eval(raw)
    try:
        outputs.append(repr(function(*args))[:__LIMIT__])
    except Exception as exc:
        outputs.append("EXC:" + type(exc).__name__)
os.write(fd, json.dumps({"outputs": outputs}).encode())
""".replace("__LIMIT__", str(_MAX_REPR))

_RUNNER = r"""
import json, os, subprocess, sys
payload = json.loads(sys.argv[1])
child = payload["child"]
results = []
for source in payload["sources"]:
    read_end, write_end = os.pipe()
    status, body = "ok", b""
    try:
        subprocess.run(
            [sys.executable, "-s", "-B", "-c", child, str(write_end)],
            input=json.dumps({"source": source, "name": payload["name"], "inputs": payload["inputs"]}),
            capture_output=True, text=True, timeout=payload["timeout"], pass_fds=(write_end,),
            env={"PYTHONHASHSEED": "0", "PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        status = "timeout"
    os.close(write_end)
    with os.fdopen(read_end, "rb") as handle:
        body = handle.read()
    if status == "ok":
        try:
            data = json.loads(body.decode() or "{}")
        except ValueError:
            data = {}
        if "outputs" in data:
            results.append({"status": "ok", "outputs": data["outputs"]})
        elif "load_error" in data:
            results.append({"status": "load_error", "detail": data["load_error"]})
        else:
            results.append({"status": "crash"})
    else:
        results.append({"status": status})
print(payload["nonce"] + json.dumps(results))
"""


class ExecutionInvalid(RuntimeError):
    """Le sandbox n'a pas produit un résultat exploitable (jamais un succès)."""


@dataclass(frozen=True)
class SourceRun:
    status: str  # ok | timeout | crash | load_error
    outputs: tuple[str, ...] | None
    detail: str | None = None


def run_sources(
    provider: SandboxProvider,
    *,
    name: str,
    sources: list[str],
    inputs: list[str],
    child_timeout_s: float = 2.0,
) -> list[SourceRun]:
    """Exécute chaque source dans le sandbox et renvoie ses sorties. Les
    sources sont envoyées par lots bornés en taille : un argument de commande
    ne peut dépasser `MAX_ARG_STRLEN` (128 Kio) côté noyau."""
    batches: list[list[str]] = [[]]
    size = 0
    for source in sources:
        if batches[-1] and size + len(source) > _MAX_BATCH_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(source)
        size += len(source)
    runs: list[SourceRun] = []
    for batch in batches:
        if batch:
            runs.extend(
                _run_batch(
                    provider, name=name, sources=batch, inputs=inputs, child_timeout_s=child_timeout_s
                )
            )
    return runs


def _run_batch(
    provider: SandboxProvider,
    *,
    name: str,
    sources: list[str],
    inputs: list[str],
    child_timeout_s: float,
) -> list[SourceRun]:
    nonce = f"{_PREFIX}{secrets.token_hex(8)}:"
    payload = {
        "child": _CHILD,
        "sources": sources,
        "name": name,
        "inputs": inputs,
        "timeout": child_timeout_s,
        "nonce": nonce,
    }
    limits = ResourceLimits(
        cpu_cores=1.0,
        memory_mb=512,
        pids_limit=32,
        timeout_s=child_timeout_s * len(sources) + 20.0,
        disk_mb=16,
        network="none",
    )
    result = provider.execute(
        SandboxSpec(
            "code_repair", "capability", ("python3", "-s", "-c", _RUNNER, json.dumps(payload)), limits
        )
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith(_PREFIX)]
    if len(lines) != 1 or not lines[0].startswith(nonce):
        raise ExecutionInvalid(
            f"exécution sandbox invalide ({result.status.value}, {len(lines)} ligne(s) de résultat) : "
            f"{(result.error_detail or result.stderr)[-300:]}"
        )
    try:
        raw: list[dict[str, Any]] = json.loads(lines[0][len(nonce) :])
    except ValueError as exc:
        raise ExecutionInvalid(f"résultat illisible : {exc}") from exc
    if len(raw) != len(sources):
        raise ExecutionInvalid(f"{len(raw)} résultat(s) pour {len(sources)} source(s)")
    runs = []
    for item in raw:
        outputs = item.get("outputs")
        if item.get("status") == "ok" and (
            not isinstance(outputs, list)
            or len(outputs) != len(inputs)
            or not all(isinstance(o, str) for o in outputs)
        ):
            raise ExecutionInvalid("sorties de test mal formées")
        runs.append(
            SourceRun(
                str(item.get("status")),
                tuple(outputs) if isinstance(outputs, list) else None,
                item.get("detail"),
            )
        )
    return runs


@dataclass(frozen=True)
class TestOutcome:
    """Résultat d'UNE exécution candidate comparé aux sorties attendues."""

    executed: bool
    status: str
    tests_total: int
    tests_passed: int
    first_failure: str | None  # "wrong_output@<i>" | "exception:<Type>@<i>" | "timeout" | ...

    @property
    def passed(self) -> bool:
        return self.executed and self.status == "ok" and self.tests_passed == self.tests_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed": self.executed,
            "status": self.status,
            "tests_total": self.tests_total,
            "tests_passed": self.tests_passed,
            "first_failure": self.first_failure,
        }


def judge_run(run: SourceRun, expected: tuple[str, ...]) -> TestOutcome:
    total = len(expected)
    if run.status != "ok" or run.outputs is None:
        kind = run.status if run.status != "load_error" else f"load_error:{run.detail}"
        return TestOutcome(True, run.status, total, 0, kind)
    passed = 0
    first = None
    for index, (got, want) in enumerate(zip(run.outputs, expected, strict=True)):
        if got == want:
            passed += 1
        elif first is None:
            first = (
                f"exception:{got[4:]}@{index}"
                if got.startswith("EXC:") and not want.startswith("EXC:")
                else f"wrong_output@{index}"
            )
    return TestOutcome(True, "ok", total, passed, first)
