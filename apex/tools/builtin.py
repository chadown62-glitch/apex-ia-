"""Outils réels pour une IA de code, tous bornés à UN espace de travail.

- `fs.read`, `fs.list`, `fs.write` : fichiers de l'espace (jamais au-delà, voir `apex.tools.workspace`) ;
- `fs.search` : chercher un texte dans les fichiers de l'espace (chemin, ligne, extrait) — naviguer dans un gros
  projet sans tout lire ;
- `fs.edit` : remplacer un passage EXACT d'un fichier (une seule occurrence, sauf demande explicite) — modifier un
  gros fichier sans le réécrire entier ;
- `project.inspect` : structure du projet (fichiers, tailles, lignes, extensions, tests, paquets Python) ;
- `code.run` : exécute un fichier Python de l'espace dans la SANDBOX (copie de l'espace, réseau coupé, limites
  CPU/mémoire/temps/processus du backend), sortie structurée ;
- `tests.run` : lance les tests de l'espace dans la sandbox (`unittest` de la bibliothèque standard ; `pytest`
  seulement s'il est réellement présent dans la sandbox, sinon erreur `unavailable`), résultats analysés.

Aucun outil n'accepte une commande shell : les seules commandes exécutées sont construites ici, en argv.
"""

from __future__ import annotations

import re
from typing import Any

from apex.tools.engine import ToolContext, ToolRegistry
from apex.tools.model import ErrorCode, Permission, ToolFailure, ToolLimits, ToolSpec

_PATH = {"type": "string", "minLength": 1, "maxLength": 512}
_OPT_PATH = {"type": "string", "maxLength": 512}
_SANDBOX_WORKDIR = "/tmp/work"  # noqa: S108 — répertoire de travail DANS la sandbox, jamais sur l'hôte
_TRACE_FILE = re.compile(r'File "/tmp/work/([^"]+)", line (\d+)')
_PATTERN = re.compile(r"^[A-Za-z0-9_*?.\-]{1,64}$")


def _clip(text: str, limit: int) -> tuple[str, bool]:
    """Garde la FIN (la plus utile : erreur, résumé de tests) et le signale."""
    if len(text) <= limit:
        return text, False
    return text[-limit:], True


# -- fichiers -------------------------------------------------------------------------------------------


def _read(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    raw = ctx.workspace.read_bytes(args["path"])
    limit = args.get("max_chars", 50_000)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": args["path"], "content": "", "size_bytes": len(raw), "binary": True, "truncated": False}
    truncated = len(text) > limit
    return {"path": args["path"], "content": text[:limit], "size_bytes": len(raw), "binary": False,
            "truncated": truncated}


def _list(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    rel = args.get("path") or "."
    return {"path": rel, "entries": ctx.workspace.list_dir(rel)}


def _write(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    content = args["content"].encode("utf-8")
    created = ctx.workspace.write_bytes(args["path"], content)
    return {"path": args["path"], "size_bytes": len(content), "created": created}


_SEARCH_MAX_FILE_BYTES = 1_000_000
_SEARCH_MAX_TOTAL_BYTES = 20_000_000
_SEARCH_LINE_CHARS = 240


def _search(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    """Texte LITTÉRAL (jamais une expression régulière : aucune recherche ne peut s'emballer), bornée en taille
    lue et en résultats ; les fichiers binaires ou trop gros sont comptés, pas lus."""
    import fnmatch

    query = args["query"]
    ignore_case = bool(args.get("ignore_case", False))
    needle = query.lower() if ignore_case else query
    prefix = (args.get("path") or "").strip("/")
    pattern = args.get("glob")
    limit = int(args.get("max_results", 50))
    matches: list[dict[str, Any]] = []
    scanned = skipped = read_bytes = 0
    truncated = False
    for rel, size in ctx.workspace.files():
        if prefix and not (rel == prefix or rel.startswith(prefix + "/")):
            continue
        if pattern and not fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pattern):
            continue
        if size > _SEARCH_MAX_FILE_BYTES or read_bytes + size > _SEARCH_MAX_TOTAL_BYTES:
            skipped += 1
            continue
        raw = ctx.workspace.read_bytes(rel)
        read_bytes += len(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        scanned += 1
        for number, line in enumerate(text.splitlines(), start=1):
            if needle in (line.lower() if ignore_case else line):
                if len(matches) >= limit:
                    truncated = True
                    break
                matches.append({"path": rel, "line": number, "text": line.strip()[:_SEARCH_LINE_CHARS]})
        if truncated:
            break
    return {"query": query, "matches": matches, "match_count": len(matches), "truncated": truncated,
            "files_scanned": scanned, "files_skipped": skipped}


def _edit(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    """Remplace `old` par `new` dans un fichier texte existant. `old` doit y figurer EXACTEMENT (espaces et
    retours à la ligne compris) et une seule fois, sauf `replace_all` : jamais une modification devinée."""
    path, old, new = args["path"], args["old"], args["new"]
    if not old:
        raise ToolFailure(ErrorCode.INVALID_INPUT, "« old » est vide : rien à remplacer")
    raw = ctx.workspace.read_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolFailure(ErrorCode.INVALID_INPUT, f"fichier non textuel : {path}") from exc
    count = text.count(old)
    if count == 0:
        raise ToolFailure(ErrorCode.NOT_FOUND, f"passage introuvable dans {path} : relire le fichier (fs.read) et "
                                               "recopier le passage exact")
    if count > 1 and not args.get("replace_all", False):
        raise ToolFailure(ErrorCode.INVALID_INPUT, f"passage présent {count} fois dans {path} : l'allonger pour "
                                                   "qu'il soit unique, ou demander replace_all")
    updated = text.replace(old, new) if args.get("replace_all", False) else text.replace(old, new, 1)
    content = updated.encode("utf-8")
    ctx.workspace.write_bytes(path, content)
    return {"path": path, "replacements": count if args.get("replace_all", False) else 1,
            "size_bytes": len(content), "lines": updated.count("\n") + (0 if updated.endswith("\n") else 1)}


def _inspect(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    files = ctx.workspace.files()
    extensions: dict[str, int] = {}
    listed, tests, packages, entry_points = [], [], set(), []
    for rel, size in files:
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel.rsplit("/", 1)[-1] else ""
        extensions[ext] = extensions.get(ext, 0) + 1
        name = rel.rsplit("/", 1)[-1]
        if name == "__init__.py":
            packages.add(rel.rsplit("/", 1)[0] if "/" in rel else ".")
        lines = None
        if ext == "py" and size <= 500_000:
            text = ctx.workspace.read_bytes(rel).decode("utf-8", errors="replace")
            lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
            if name.startswith("test") or name.endswith("_test.py"):
                tests.append(rel)
            if "__name__ == \"__main__\"" in text or "__name__ == '__main__'" in text:
                entry_points.append(rel)
        if len(listed) < 500:
            listed.append({"path": rel, "size_bytes": size, "lines": lines})
    return {
        "file_count": len(files), "total_bytes": sum(s for _, s in files), "files": listed,
        "files_truncated": len(files) > len(listed), "extensions": extensions, "test_files": tests,
        "python_packages": sorted(packages), "entry_points": entry_points,
    }


# -- exécution ------------------------------------------------------------------------------------------


def _sandbox(ctx: ToolContext, command: tuple[str, ...], timeout_s: float) -> Any:
    if ctx.run_in_sandbox is None:
        raise ToolFailure(ErrorCode.SANDBOX_UNAVAILABLE, "aucune sandbox fournie à ce contexte")
    from apex.errors import IsolationUnavailable

    try:
        return ctx.run_in_sandbox(command, ctx.workspace.snapshot(), timeout_s)
    except IsolationUnavailable as exc:
        raise ToolFailure(ErrorCode.SANDBOX_UNAVAILABLE, str(exc)[:500]) from exc


def _execution_view(result: Any, limit: int) -> dict[str, Any]:
    stdout, out_cut = _clip(result.stdout, limit)
    stderr, err_cut = _clip(result.stderr, limit)
    return {
        "status": result.status.value, "exit_code": result.exit_code.value,
        "stdout": stdout, "stderr": stderr,
        "stdout_truncated": out_cut or result.stdout_truncated,
        "stderr_truncated": err_cut or result.stderr_truncated,
        "elapsed_s": result.elapsed_s.value, "isolation": result.isolation_level.value,
    }


def _fail_on_limits(result: Any, view: dict[str, Any]) -> None:
    from apex.sandbox.model import ExecutionStatus

    if result.status is ExecutionStatus.TIMEOUT:
        raise ToolFailure(ErrorCode.TIMEOUT, "délai dépassé dans la sandbox", output=view)
    if result.status in (ExecutionStatus.OOM_KILLED, ExecutionStatus.RESOURCE_LIMIT_EXCEEDED,
                         ExecutionStatus.PROCESS_LIMIT_EXCEEDED, ExecutionStatus.DISK_LIMIT_EXCEEDED):
        raise ToolFailure(ErrorCode.RESOURCE_LIMIT, f"limite de ressources atteinte ({result.status.value})",
                          output=view)
    if result.status in (ExecutionStatus.PERMISSION_DENIED, ExecutionStatus.NETWORK_DENIED):
        raise ToolFailure(ErrorCode.PATH_REFUSED, f"refusé par la sandbox ({result.status.value})", output=view)


def _timeout(args: dict[str, Any], spec_limits: ToolLimits) -> float:
    return float(min(args.get("timeout_s", spec_limits.timeout_s), spec_limits.timeout_s))


def _run_code(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    path = args["path"]
    if not path.endswith(".py"):
        raise ToolFailure(ErrorCode.INVALID_INPUT, "seuls les fichiers Python (.py) sont exécutables")
    host = ctx.workspace.resolve(path)
    if not host.is_file():
        raise ToolFailure(ErrorCode.NOT_FOUND, f"fichier absent : {path}")
    limits = spec.limits
    result = _sandbox(ctx, ("python3", path, *args.get("args", [])), _timeout(args, limits))
    view = _execution_view(result, limits.max_output_chars)
    _fail_on_limits(result, view)
    return view


_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in", re.M)
_UNITTEST_FAILED = re.compile(r"^FAILED \(([^)]*)\)", re.M)
_UNITTEST_SKIPPED = re.compile(r"skipped=(\d+)")
_UNITTEST_CASE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.M)
_PYTEST_SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")
_PYTEST_CASE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)


def parse_unittest(output: str) -> dict[str, Any]:
    ran = _UNITTEST_RAN.search(output)
    tests_run = int(ran.group(1)) if ran else 0
    failures = errors = skipped = 0
    failed = _UNITTEST_FAILED.search(output)
    if failed:
        for part in failed.group(1).split(","):
            key, _, value = part.strip().partition("=")
            if key == "failures":
                failures = int(value)
            elif key == "errors":
                errors = int(value)
    if match := _UNITTEST_SKIPPED.search(output):
        skipped = int(match.group(1))
    failing = [f"{m.group(3)}.{m.group(2)}" if "." not in m.group(3) else m.group(3)
               for m in _UNITTEST_CASE.finditer(output)]
    if tests_run == 0:
        status = "no_tests" if "NO TESTS RAN" in output or ran else "error"
    else:
        status = "passed" if not (failures or errors) and re.search(r"^OK\b", output, re.M) else "failed"
    return {"tests_run": tests_run, "failures": failures, "errors": errors, "skipped": skipped,
            "failing_tests": failing, "status": status}


def parse_pytest(output: str) -> dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for number, kind in _PYTEST_SUMMARY.findall(output):
        counts["errors" if kind in ("error", "errors") else kind] += int(number)
    failing = _PYTEST_CASE.findall(output)
    total = counts["passed"] + counts["failed"] + counts["errors"]
    status = "no_tests" if total == 0 else ("passed" if not (counts["failed"] or counts["errors"]) else "failed")
    return {"tests_run": total, "failures": counts["failed"], "errors": counts["errors"],
            "skipped": counts["skipped"], "failing_tests": failing, "status": status}


def _run_tests(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    framework = args.get("framework", "unittest")
    start = args.get("start_dir") or "."
    pattern = args.get("pattern", "test*.py")
    if not _PATTERN.match(pattern):
        raise ToolFailure(ErrorCode.INVALID_INPUT, f"motif de fichiers refusé : {pattern!r}")
    host = ctx.workspace.resolve(start, allow_root=True)
    if not host.is_dir():
        raise ToolFailure(ErrorCode.NOT_FOUND, f"répertoire de tests absent : {start}")
    limits = spec.limits
    timeout = _timeout(args, limits)
    if framework == "pytest":
        probe = _sandbox(ctx, ("python3", "-c", "import pytest"), min(timeout, 15.0))
        if probe.exit_code.value != 0:
            raise ToolFailure(ErrorCode.UNAVAILABLE, "pytest n'est pas installé dans la sandbox ; utiliser unittest")
        command = ("python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", start)
    else:
        command = ("python3", "-m", "unittest", "discover", "-v", "-s", start, "-p", pattern)
    result = _sandbox(ctx, command, timeout)
    combined = f"{result.stdout}\n{result.stderr}"
    parsed = parse_pytest(combined) if framework == "pytest" else parse_unittest(combined)
    files = sorted({m.group(1) for m in _TRACE_FILE.finditer(combined)})
    stdout, out_cut = _clip(result.stdout, limits.max_output_chars)
    stderr, err_cut = _clip(result.stderr, limits.max_output_chars)
    view = {
        "framework": framework, **parsed, "files": files, "exit_code": result.exit_code.value,
        "sandbox_status": result.status.value, "stdout": stdout, "stderr": stderr,
        "output_truncated": out_cut or err_cut or result.stdout_truncated or result.stderr_truncated,
        "elapsed_s": result.elapsed_s.value, "isolation": result.isolation_level.value,
    }
    _fail_on_limits(result, view)
    return view


# -- registre par défaut --------------------------------------------------------------------------------

_EXEC_OUT = {
    "type": "object", "additionalProperties": True,
    "properties": {"status": {"type": "string"}, "exit_code": {}, "stdout": {"type": "string"},
                   "stderr": {"type": "string"}},
}

DEFAULT_LIMITS = {
    "code.run": ToolLimits(timeout_s=30.0, max_output_chars=20_000),
    "tests.run": ToolLimits(timeout_s=120.0, max_output_chars=20_000),
}


def _check_solution(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    """Vérification BON MARCHÉ de solution.py (08/10/2026) : s'importe-t-il, et que donnent les exemples PUBLICS
    de l'énoncé (`apex.tasks.public_examples`) ? Aucun argument à écrire, aucun test à rédiger : mesuré, un modèle
    1.5B n'appelait jamais `code.run`/`tests.run`, trop coûteux à préparer. Jamais un cas caché : l'énoncé
    (fourni par l'appelant dans `ctx.extra["statement"]`) est la seule source des exemples."""
    import uuid

    from apex.tasks.public_examples import examples_script, extract_examples, read_results

    if not ctx.workspace.resolve("solution.py").is_file():
        raise ToolFailure(ErrorCode.NOT_FOUND, "solution.py n'existe pas encore : écris-la d'abord avec fs.write")
    code = ctx.workspace.read_bytes("solution.py").decode("utf-8", errors="replace")
    examples = extract_examples(str(ctx.extra.get("statement") or ""))
    nonce = uuid.uuid4().hex[:12]
    result = _sandbox(ctx, ("python3", "-I", "-c", examples_script(code, examples, nonce)), spec.limits.timeout_s)
    view = _execution_view(result, spec.limits.max_output_chars)
    _fail_on_limits(result, view)
    results = read_results(result.stdout, examples, nonce)
    if results is None:
        return {"imported": False, "error": (view["stderr"] or view["stdout"])[-600:],
                "message": "solution.py ne s'exécute pas : corrige l'erreur ci-dessus"}
    failed = [r for r in results if not r["ok"]]
    return {"imported": True, "examples": results, "examples_failed": len(failed),
            "message": ("aucun exemple public dans l'énoncé : solution.py s'importe sans erreur" if not results
                        else "tous les exemples publics passent" if not failed
                        else f"{len(failed)} exemple(s) public(s) en échec : corrige solution.py")}


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="fs.read", description="Lire un fichier texte de l'espace de travail (chemin relatif).",
        input_schema={"type": "object", "required": ["path"], "properties": {
            "path": _PATH, "max_chars": {"type": "integer", "minimum": 1, "maximum": 200_000}}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.READ_WORKSPACE}),
    ), _read)
    registry.register(ToolSpec(
        name="fs.list", description="Lister un répertoire de l'espace de travail (défaut : la racine).",
        input_schema={"type": "object", "properties": {"path": _OPT_PATH}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.READ_WORKSPACE}),
    ), _list)
    registry.register(ToolSpec(
        name="fs.write", description="Créer ou remplacer un fichier texte de l'espace de travail.",
        input_schema={"type": "object", "required": ["path", "content"], "properties": {
            "path": _PATH, "content": {"type": "string", "maxLength": 1_000_000}}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.WRITE_WORKSPACE}),
        limits=ToolLimits(max_input_bytes=1_100_000),  # un fichier d'1 Mo doit pouvoir être écrit
    ), _write)
    registry.register(ToolSpec(
        name="fs.search",
        description=("Chercher un texte (littéral) dans les fichiers de l'espace : chemin, ligne, extrait. Filtres "
                     "facultatifs : dossier, motif de nom (*.cs), casse ignorée."),
        input_schema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200}, "path": _OPT_PATH,
            "glob": {"type": "string", "maxLength": 64}, "ignore_case": {"type": "boolean"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 200}}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.READ_WORKSPACE}),
    ), _search)
    registry.register(ToolSpec(
        name="fs.edit",
        description=("Modifier un fichier sans le réécrire : remplacer un passage EXACT (unique, sauf replace_all) "
                     "par un nouveau texte."),
        input_schema={"type": "object", "required": ["path", "old", "new"], "properties": {
            "path": _PATH, "old": {"type": "string", "maxLength": 200_000},
            "new": {"type": "string", "maxLength": 1_000_000}, "replace_all": {"type": "boolean"}}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.WRITE_WORKSPACE}),
        limits=ToolLimits(max_input_bytes=1_300_000),
    ), _edit)
    registry.register(ToolSpec(
        name="project.inspect",
        description="Structure du projet : fichiers, tailles, lignes, extensions, fichiers de tests, paquets.",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.READ_WORKSPACE}),
    ), _inspect)
    registry.register(ToolSpec(
        name="code.run",
        description="Exécuter un fichier Python de l'espace dans la sandbox (réseau coupé, limites).",
        input_schema={"type": "object", "required": ["path"], "properties": {
            "path": _PATH,
            "args": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 200}},
            "timeout_s": {"type": "number", "minimum": 1, "maximum": 300}}},
        output_schema=_EXEC_OUT, permissions=frozenset({Permission.EXECUTE_SANDBOX}),
        limits=DEFAULT_LIMITS["code.run"],
    ), _run_code)
    registry.register(ToolSpec(
        name="tests.run",
        description="Lancer les tests de l'espace dans la sandbox (unittest ; pytest s'il est disponible).",
        input_schema={"type": "object", "properties": {
            "framework": {"type": "string", "enum": ["unittest", "pytest"]},
            "start_dir": _OPT_PATH, "pattern": {"type": "string", "maxLength": 64},
            "timeout_s": {"type": "number", "minimum": 1, "maximum": 600}}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.EXECUTE_SANDBOX}),
        limits=DEFAULT_LIMITS["tests.run"],
    ), _run_tests)
    registry.register(ToolSpec(
        name="solution.check",
        description=("Vérifier solution.py dans la sandbox : import, puis exemples publics de l'énoncé "
                     "(attendu / obtenu). Sans argument."),
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.EXECUTE_SANDBOX}),
        limits=DEFAULT_LIMITS["code.run"],
    ), _check_solution)
    return registry
