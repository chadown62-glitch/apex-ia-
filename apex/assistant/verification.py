"""Vérification croisée, dans l'assistant, du code que le Cerveau APEX vient d'écrire
(`apex.brain.crosscheck`).

Sa version Python et sa réponse s'exécutent dans la SANDBOX d'APEX (réseau coupé, privilèges abaissés, clone
tracé par `AgentRuntime` et autorisé par `PolicyEngine`), sur les mêmes entrées générées. Jamais hors de la
sandbox : un langage que la sandbox ne sait pas exécuter est « non vérifiable », et c'est dit.

Ce que chaque sandbox sait exécuter, constaté le 16/10/2026 :
- Linux (espaces de noms) : les outils installés sous les préfixes système (/usr, /opt…) — Python, Ruby,
  JavaScript, TypeScript, PHP, C++, Java, C#, Go (disque porté à 256 Mo pour son cache de compilation) ;
  pas Rust s'il est installé dans le dossier personnel (~/.cargo, invisible depuis la sandbox).
- Windows (AppContainer) : Python seul (l'interpréteur embarqué) : rien d'autre n'y est vérifiable.
"""

from __future__ import annotations

import os
import shlex
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from apex.brain import crosscheck
from apex.brain.crosscheck import Verdict

#: (langage, fichiers, commandes) -> (sortie standard ou None, erreur, outil indisponible).
Executor = Callable[[str, tuple[tuple[str, bytes], ...], list[list[str]]], tuple[str | None, str, bool]]
#: Délai d'une exécution (compilation comprise ; Go : ~9 s mesurées dans la sandbox, cache vide).
TIMEOUT_S = 60.0
#: Message du noyau quand un fichier dépasse la taille permise (RLIMIT_FSIZE de la sandbox).
_RESOURCE_LIMIT = "File size limit exceeded"
#: Dossier de travail de la sandbox Linux (`apex.sandbox.linux_namespace`), propre à chaque exécution.
_SANDBOX_HOME = "/tmp/work"  # noqa: S108 — chemin DANS la sandbox, jamais sur l'hôte


#: Sondes déjà faites : langage -> (prêt, détail, instant). Refaites après `PROBE_TTL_S`.
Probes = dict[str, tuple[bool, str, float]]
PROBE_TTL_S = 600.0


def check(text: str, twin: Callable[[str], str], execute: Executor, *,
          probes: Probes | None = None) -> Verdict | None:
    """Vérifie la réponse `text` du Cerveau. None : rien à vérifier (pas une fonction de la forme apprise,
    ou déjà en Python). `twin(prefix)` rend la suite donnée par le Cerveau au début imposé de la version
    Python. Chaque langage est d'abord SONDÉ (`crosscheck.PROBES`, mémorisé dans `probes`) : là où une
    fonction juste échoue, rien n'est vérifiable et rien n'est déclaré faux."""
    answer = crosscheck.parse_answer(text)
    if answer is None or answer.language == "python":
        return None
    for language in ("python", answer.language):
        ready, detail = _ready(execute, language, probes if probes is not None else {})
        if not ready:
            return Verdict("unverifiable", detail)
    prefix = crosscheck.twin_prefix(answer)
    try:
        continuation = twin(prefix)
    except Exception as exc:  # moteur indisponible, délai : jamais une réponse déclarée fausse pour autant
        return Verdict("unverifiable", f"version Python impossible : {exc.__class__.__name__}: {exc}"[:300])
    code = crosscheck.twin_code(prefix, continuation, answer.function)
    if code is None:
        return Verdict("unverifiable", "version Python inutilisable")
    inputs = crosscheck.inputs_for(answer.function)
    twin_out, twin_error, _ = _run(execute, "python", code, answer.function, inputs)
    if twin_out is None:
        return crosscheck.verdict(inputs, None, None, twin_error=twin_error)
    got, error, unavailable = _run(execute, answer.language, answer.code, answer.function, inputs)
    if unavailable:
        return Verdict("unverifiable", error)
    return crosscheck.verdict(inputs, got, twin_out, answer_error=error)


def _ready(execute: Executor, language: str, probes: Probes) -> tuple[bool, str]:
    known = probes.get(language)
    if known is not None and time.monotonic() - known[2] < PROBE_TTL_S:
        return known[0], known[1]
    outputs, error, _ = _run(execute, language, crosscheck.PROBES[language], crosscheck.PROBE_FUNCTION,
                             crosscheck.PROBE_INPUTS)
    ready = outputs == crosscheck.PROBE_OUTPUTS
    reason = error or "résultat inattendu de la sonde"
    detail = "" if ready else f"{language} non vérifiable ici : {reason}"[:300]
    probes[language] = (ready, detail, time.monotonic())
    return ready, detail


def _run(execute: Executor, language: str, code: str, function: str,
         inputs: list[list[int]]) -> tuple[list[Any] | None, str, bool]:
    file_name, source = crosscheck.program(language, code, function, inputs)
    files = ((file_name, source.encode()), *((n, c.encode()) for n, c in crosscheck.extra_files(language)))
    stdout, error, unavailable = execute(language, files, crosscheck.commands(language))
    if stdout is None:
        return None, error, unavailable
    outputs = crosscheck.parse_outputs(stdout, len(inputs))
    return outputs, ("" if outputs is not None else "sortie illisible"), False


def sandbox_executor(run: Callable[..., Any]) -> Executor:
    """Exécuteur dans la sandbox. `run(command, files, env=…, disk_mb=…, timeout_s=…)` lance une commande
    dans la sandbox (voir `TurnManager._sandbox_run`). Python : la commande directe (acceptée par toutes les
    sandboxes) ; les autres langages : un script `sh` aux outils résolus en chemins absolus."""
    from apex.errors import IsolationUnavailable

    def execute(language: str, files: tuple[tuple[str, bytes], ...],
                commands: list[list[str]]) -> tuple[str | None, str, bool]:
        resolved: list[list[str]] = []
        for command in commands:
            if command[0].startswith("./"):
                resolved.append(command)
                continue
            tool = shutil.which(command[0])
            if tool is None:
                return None, f"{command[0]} absent de ce PC : {language} non vérifiable", True
            resolved.append([tool, *command[1:]])
        if language == "python":
            argv: tuple[str, ...] = ("python3", *commands[0][1:])
            env: dict[str, str] = {}
        else:
            argv = ("/bin/sh", "-c", " && ".join(shlex.join(c) for c in resolved))
            folders = dict.fromkeys(os.path.dirname(c[0]) for c in resolved if not c[0].startswith("./"))
            # .NET dans les limites de la sandbox (mesuré le 16/10/2026) : sans double mappage W^X (ses
            # grands fichiers mappés dépassaient la taille de fichier permise) et tas plafonné à 256 Mo (sa
            # réservation d'adresses dépassait la limite de mémoire virtuelle : « GC heap initialization
            # failed »).
            env = {"PATH": ":".join([*folders, "/usr/bin", "/bin"]), "DOTNET_CLI_HOME": _SANDBOX_HOME,
                   "DOTNET_NOLOGO": "1", "DOTNET_EnableWriteXorExecute": "0",
                   "DOTNET_GCHeapHardLimit": "0x10000000"}
        try:
            result = run(argv, files, env=env, disk_mb=crosscheck.DISK_MB.get(language, 0),
                         timeout_s=TIMEOUT_S)
        except IsolationUnavailable as exc:
            return None, f"la sandbox de ce PC n'exécute pas {language} : {exc}"[:300], True
        status = getattr(result.status, "value", str(result.status))
        code = result.exit_code.value
        if status == "success" and code == 0:
            return result.stdout, "", False
        if code in (126, 127):  # outil introuvable ou non exécutable DANS la sandbox
            return None, f"{language} inexécutable dans la sandbox : {result.stderr.strip()[-200:]}", True
        if status != "failed" or _RESOURCE_LIMIT in (result.stderr or ""):
            # Délai, mémoire, processus, taille de fichier : une limite de la sandbox, pas une preuve que la
            # réponse est fausse (mesuré : le compilateur C# dépassait la taille de fichier à 64 Mo).
            return None, f"limite de la sandbox atteinte ({status}) : {language} non vérifiable", True
        return None, f"{status} : {(result.stderr or result.stdout).strip()[-300:]}", False

    return execute


def sandbox_run(agent_runtime: Any, sandbox: Any, pool: Any, base_limits: Any, *,
                label: str) -> Callable[..., Any]:
    """Lance une commande dans la sandbox par `AgentRuntime` (clone RESEARCH tracé, autorisation
    PolicyEngine), réseau TOUJOURS coupé. Comme `apex.tools.sandbox_runner`, avec en plus l'environnement et
    le disque (un compilateur a besoin de plus que l'exécution d'un script)."""
    from apex.policy.model import Subject
    from apex.sandbox.model import SandboxSpec

    def run(command: tuple[str, ...], files: tuple[tuple[str, bytes], ...], *, env: dict[str, str],
            disk_mb: int, timeout_s: float) -> Any:
        limits = replace(base_limits, timeout_s=min(base_limits.timeout_s, timeout_s), network="none",
                         disk_mb=max(base_limits.disk_mb, disk_mb))
        execution_id = str(uuid.uuid4())
        clone = agent_runtime.spawn(Subject.RESEARCH, pool=pool, genome_ref=f"{label}:{execution_id}",
                                    spawned_by=Subject.CORE, mission_id=None)
        spec = SandboxSpec(mission_id=execution_id, clone_id=clone.clone_id, command=tuple(command),
                           limits=limits, env=env, input_files=files)
        return agent_runtime.execute_in_sandbox(clone, sandbox, spec, requested_by=Subject.CORE)

    return run
