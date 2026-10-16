"""Accélération GPU FACULTATIVE d'APEX autonome : PyTorch CUDA en surcouche du runtime de base.

Le runtime de base (`requirements-windows.lock`) installe `torch` depuis PyPI ; sous Windows, c'est une
version PROCESSEUR seulement. L'accélération GPU est un composant à part, jamais dans l'installateur
principal, installé SEULEMENT sur un geste de l'utilisateur (Panel, ou `python -m apex.standalone gpu
install`) :

- **variante** choisie d'après le pilote NVIDIA MESURÉ (`nvidia-smi` : version CUDA maximale que le pilote
  accepte) : la plus récente des variantes figées dont la version CUDA ne la dépasse pas ;
- **roue `torch` CUDA figée** par adresse et empreinte SHA-256 (`runtime/gpu/torch-<variante>.lock`,
  produite par `setup/packaging/runtime/gpu/lock_gpu.py` là où download.pytorch.org est joignable),
  installée par pip en mode `--require-hashes --no-deps --only-binary=:all:` dans un dossier À PART
  (`APEX_HOME/python/gpu/torch-<version>-<variante>`), jamais dans le runtime de base : la retirer rend exactement
  l'état d'avant ;
- **espace disque** vérifié avant ; installation dans un dossier provisoire, gardé seulement après une
  **sonde réussie** dans un processus séparé : `torch` importé depuis la surcouche (jamais celui de base),
  `torch.cuda.is_available()`, nom, capacité et mémoire du GPU, multiplication de matrices sur le GPU
  comparée au même calcul sur le processeur ;
- **activation à chaque démarrage** (`activate`, appelé par `serve` avant tout import de torch), seulement
  si le composant est installé, activé, accordé à cette version d'APEX et à ce Python, le pilote suffisant
  et la sonde de nouveau réussie ; sinon APEX démarre sur le processeur, et la raison est écrite
  (`.apex/runtime/gpu-activation.json`, journal de démarrage). Une surcouche cassée ne peut jamais
  empêcher APEX de démarrer.

Rien ici n'affirme qu'un GPU fonctionne : chaque état « actif » repose sur une sonde exécutée sur CE PC.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apex.standalone import app_root, paths

SPEC_NAME = "gpu-runtime.json"
#: Où l'installateur dépose la description figée (à côté de `python-runtime.json`), puis, dans un dépôt.
SPEC_LOCATIONS = ("runtime/gpu", "setup/packaging/runtime/gpu")
OVERLAY_ENV = "APEX_GPU_OVERLAY"
INSTALL_TIMEOUT_S = 2 * 3600
PROBE_TIMEOUT_S = 120
SMI_TIMEOUT_S = 20
#: Marge d'espace disque au-delà de la roue téléchargée et de son contenu extrait.
DISK_MARGIN_BYTES = 1 << 30

#: Codes de sortie de `gpu install` (le Panel les interprète).
EXIT_OK = 0
EXIT_REFUSED = 2  # condition préalable absente : pilote, liste figée, disque, APEX en cours…
EXIT_INSTALL_FAILED = 3
EXIT_PROBE_FAILED = 4

#: Exécutée dans un Python SÉPARÉ (`python -I -c PROBE <surcouche>`) : un import qui plante ou une DLL
#: absente n'atteint jamais le processus qui la lance. Rend un objet JSON sur sa dernière ligne.
PROBE = r"""
import json, os, sys, time
overlay = os.path.abspath(sys.argv[1])
sys.path.insert(0, overlay)
out = {"overlay": overlay, "python": sys.version.split()[0]}
def done():
    print(json.dumps(out))
    raise SystemExit(0)
try:
    import torch
except BaseException as exc:
    out["error"] = ("import torch : %s: %s" % (exc.__class__.__name__, exc))[:600]
    done()
where = os.path.normcase(os.path.abspath(torch.__file__))
out["torch"] = torch.__version__
out["torch_file"] = torch.__file__
out["from_overlay"] = where.startswith(os.path.normcase(overlay) + os.sep)
out["torch_cuda"] = torch.version.cuda
try:
    out["cuda_available"] = bool(torch.cuda.is_available())
except BaseException as exc:
    out["cuda_available"] = False
    out["error"] = ("torch.cuda.is_available : %s: %s" % (exc.__class__.__name__, exc))[:600]
    done()
if not out["cuda_available"]:
    done()
try:
    out["arch_list"] = list(torch.cuda.get_arch_list())
    out["devices"] = []
    for i in range(torch.cuda.device_count()):
        p = torch.cuda.get_device_properties(i)
        out["devices"].append({"index": i, "name": p.name, "capability": "%d.%d" % (p.major, p.minor),
                               "total_memory_mib": int(p.total_memory // (1 << 20))})
    torch.backends.cuda.matmul.allow_tf32 = False
    g = torch.Generator().manual_seed(0)
    a = torch.randn(512, 512, generator=g)
    b = torch.randn(512, 512, generator=g)
    ref = a @ b
    t0 = time.perf_counter()
    got = (a.cuda() @ b.cuda()).cpu()
    torch.cuda.synchronize()
    err = float((got - ref).abs().max())
    out["matmul"] = {"ok": bool(torch.allclose(got, ref, rtol=1e-3, atol=1e-2)), "max_abs_err": err,
                     "ms": round((time.perf_counter() - t0) * 1000, 1)}
except BaseException as exc:
    out["matmul"] = {"ok": False, "error": ("%s: %s" % (exc.__class__.__name__, exc))[:600]}
done()
"""

Runner = Callable[..., subprocess.CompletedProcess[str]]


# -- description figée ------------------------------------------------------------------------------


def spec_dir(root: Path | None = None) -> Path | None:
    root = root or app_root()
    for relative in SPEC_LOCATIONS:
        candidate = root / relative
        if (candidate / SPEC_NAME).is_file():
            return candidate
    return None


def load_spec(root: Path | None = None) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    """(description, dossier, problème). Lue strictement : un champ absent rend le composant
    indisponible."""
    folder = spec_dir(root)
    if folder is None:
        return None, None, "description du composant GPU absente de cette installation"
    try:
        spec = json.loads((folder / SPEC_NAME).read_text(encoding="utf-8"))
        if not (isinstance(spec, dict) and isinstance(spec.get("variants"), list) and spec.get("torch")):
            raise ValueError("champs « torch » et « variants » attendus")
        for variant in spec["variants"]:
            if not (isinstance(variant, dict) and re.fullmatch(r"cu\d{3}", str(variant.get("id", "")))
                    and _cuda(variant.get("cuda")) and str(variant.get("lock", "")).endswith(".lock")
                    and "/" not in str(variant["lock"]) and "\\" not in str(variant["lock"])):
                raise ValueError(f"variante mal formée : {variant!r}"[:200])
    except (OSError, ValueError) as exc:
        return None, folder, f"description du composant GPU illisible ({folder / SPEC_NAME}) : {exc}"
    return spec, folder, None


def _cuda(text: Any) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)", str(text or "").strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def variants(spec: dict[str, Any], folder: Path) -> list[dict[str, Any]]:
    """Variantes de la description, avec ce qui est réellement prêt : une variante sans liste figée
    (générée par `lock_gpu.py`) n'est jamais proposée."""
    out = []
    for variant in spec["variants"]:
        lock = folder / variant["lock"]
        prepared = lock.is_file() and isinstance(variant.get("wheel_bytes"), int) \
            and isinstance(variant.get("installed_bytes"), int)
        out.append({"id": variant["id"], "cuda": variant["cuda"], "prepared": prepared,
                    "lock": str(lock), "lock_sha256": _sha256(lock) if lock.is_file() else None,
                    "wheel_bytes": variant.get("wheel_bytes"),
                    "installed_bytes": variant.get("installed_bytes")})
    return out


# -- pilote NVIDIA --------------------------------------------------------------------------------------


def nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32":
        system_root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
        program_files = os.environ.get("PROGRAMFILES") or r"C:\Program Files"
        for candidate in (Path(system_root) / "System32" / "nvidia-smi.exe",
                          Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


def detect_driver(*, smi: str | None = None, run: Runner = subprocess.run) -> dict[str, Any]:
    """Pilote et GPU NVIDIA tels que `nvidia-smi` les rapporte — jamais supposés. `cuda_driver` est la
    version CUDA MAXIMALE que ce pilote accepte (en-tête de `nvidia-smi`)."""
    path = smi or nvidia_smi()
    if path is None:
        return {"found": False, "reason": "aucun pilote NVIDIA détecté (nvidia-smi introuvable)"}
    try:
        header = run([path], capture_output=True, text=True, timeout=SMI_TIMEOUT_S)
        fields = "name,driver_version,memory.total,compute_cap"
        query = run([path, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=SMI_TIMEOUT_S)
        if query.returncode != 0:  # pilote ancien sans `compute_cap`
            query = run([path, "--query-gpu=name,driver_version,memory.total",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=SMI_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"found": False, "path": path,
                "reason": f"nvidia-smi n'a pas répondu ({exc.__class__.__name__})"}
    if header.returncode != 0:
        detail = (header.stderr or header.stdout or "").strip().splitlines()[-1:] or ["sans message"]
        return {"found": False, "path": path, "reason": f"nvidia-smi a échoué : {detail[0][:200]}"}
    match = re.search(r"CUDA Version\s*:\s*(\d+\.\d+)", header.stdout or "")
    gpus = []
    for line in (query.stdout or "").splitlines() if query.returncode == 0 else []:
        cells = [c.strip() for c in line.split(",")]
        if len(cells) >= 3 and cells[0]:
            memory = int(cells[2]) if cells[2].isdigit() else None
            capability = cells[3] if len(cells) > 3 and _cuda(cells[3]) else None
            gpus.append({"name": cells[0], "driver_version": cells[1], "memory_total_mib": memory,
                         "compute_capability": capability})
    driver = {"found": True, "path": path, "cuda_driver": match.group(1) if match else None,
              "driver_version": gpus[0]["driver_version"] if gpus else None, "gpus": gpus}
    if not gpus:
        driver["reason"] = "nvidia-smi ne rapporte aucun GPU"
    elif match is None:
        driver["reason"] = "version CUDA du pilote illisible dans la sortie de nvidia-smi"
    return driver


def choose(spec: dict[str, Any], folder: Path, driver: dict[str, Any],
           requested: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """La variante à installer pour CE pilote, ou None et pourquoi."""
    if not driver.get("found") or driver.get("reason"):
        return None, driver.get("reason") or "aucun pilote NVIDIA détecté"
    ceiling = _cuda(driver.get("cuda_driver"))
    if ceiling is None:
        return None, "version CUDA du pilote illisible"
    known = variants(spec, folder)
    if requested is not None:
        known = [v for v in known if v["id"] == requested]
        if not known:
            return None, f"variante inconnue : {requested}"
    prepared = [v for v in known if v["prepared"]]
    if not prepared:
        return None, ("composant GPU non préparé pour cette version d'APEX : aucune liste figée de PyTorch "
                      "CUDA (à produire avec setup/packaging/runtime/gpu/lock_gpu.py)")
    fitting = [v for v in prepared if (_cuda(v["cuda"]) or (99, 0)) <= ceiling]
    if not fitting:
        lowest = min(prepared, key=lambda v: _cuda(v["cuda"]) or (99, 0))
        return None, (f"pilote NVIDIA trop ancien : il accepte CUDA {driver['cuda_driver']}, la variante "
                      f"la plus ancienne demande CUDA {lowest['cuda']} — mettez à jour le pilote NVIDIA")
    best = max(fitting, key=lambda v: _cuda(v["cuda"]) or (0, 0))
    return best, f"CUDA {best['cuda']} ≤ CUDA {driver['cuda_driver']} accepté par le pilote"


# -- état sur le disque --------------------------------------------------------------------------------


def gpu_home(home: Path | None = None) -> Path:
    """Sous `python/`, avec le runtime préparé : re-téléchargeable, retiré par la désinstallation comme lui
    (jamais avec les données, le modèle ou les journaux)."""
    return (home or paths().home) / "python" / "gpu"


def marker_path(home: Path | None = None) -> Path:
    return gpu_home(home) / "installed.json"


def settings_path(home: Path | None = None) -> Path:
    return gpu_home(home) / "settings.json"


def activation_path(home: Path | None = None) -> Path:
    return (home or paths().home) / ".apex" / "runtime" / "gpu-activation.json"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, path)


def enabled(home: Path | None = None) -> bool:
    return (_read(settings_path(home)) or {}).get("enabled", True) is not False


def set_enabled(value: bool, home: Path | None = None) -> None:
    _write(settings_path(home), {"enabled": bool(value), "changed_at": _now()})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _python() -> str:
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def _apex_running() -> bool:
    from apex.discovery import discovery_path
    from apex.standalone.process import alive

    record = _read(discovery_path()) or {}
    pid = record.get("pid")
    return record.get("phase") not in (None, "refused") and isinstance(pid, int) and alive(pid)


# -- sonde ----------------------------------------------------------------------------------------------


def probe(overlay: Path, *, python: str | None = None, run: Runner = subprocess.run,
          timeout_s: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    """Ce que la surcouche donne VRAIMENT sur ce PC, mesuré dans un Python séparé."""
    try:
        done = run([python or sys.executable, "-I", "-c", PROBE, str(overlay)], capture_output=True,
                   text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"error": f"sonde sans réponse en {timeout_s:.0f} s"}
    except OSError as exc:
        return {"error": f"sonde impossible à lancer : {exc}"[:300]}
    lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    if not isinstance(result, dict):
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-2:]
        return {"error": f"sonde illisible (code {done.returncode}) : {' | '.join(tail)}"[:600]}
    return result


def probe_verdict(result: dict[str, Any]) -> str | None:
    """None si la sonde prouve un GPU utilisable par la surcouche ; sinon la raison, en clair."""
    if result.get("error"):
        return str(result["error"])
    if not result.get("from_overlay"):
        return f"torch chargé hors de la surcouche ({result.get('torch_file')})"
    if not result.get("torch_cuda"):
        return f"la surcouche contient un torch sans CUDA ({result.get('torch')})"
    if not result.get("cuda_available"):
        return "torch.cuda.is_available() = False : pilote, GPU ou bibliothèques CUDA inutilisables"
    if not result.get("devices"):
        return "aucun GPU CUDA visible par torch"
    matmul = result.get("matmul") or {}
    if not matmul.get("ok"):
        return f"calcul de contrôle sur le GPU en échec : {matmul.get('error') or matmul}"[:600]
    return None


# -- activation au démarrage ----------------------------------------------------------------------------


def activate(home: Path | None = None, *, root: Path | None = None, run: Runner = subprocess.run,
             smi: str | None = None, sys_path: list[str] | None = None) -> dict[str, Any]:
    """Au démarrage de `serve`, AVANT tout import de torch : met la surcouche GPU en tête de `sys.path`
    si — et seulement si — tout est vérifié sur ce PC. Ne lève jamais : APEX démarre toujours, au pire sur
    le processeur, et la raison est écrite."""
    try:
        result = _activation(home, root=root, run=run, smi=smi,
                             sys_path=sys.path if sys_path is None else sys_path)
    except Exception as exc:  # une surcouche cassée n'empêche jamais APEX de démarrer
        result = {"state": "failed", "reason": f"erreur interne : {exc.__class__.__name__}: {exc}"[:400]}
    result["at"] = _now()
    with contextlib.suppress(OSError):
        _write(activation_path(home), result)
    return result


def _activation(home: Path | None, *, root: Path | None, run: Runner, smi: str | None,
                sys_path: list[str]) -> dict[str, Any]:
    marker = _read(marker_path(home))
    if marker is None:
        return {"state": "absent", "reason": "accélération GPU non installée : calcul sur le processeur"}
    base = {"variant": marker.get("variant"), "torch": marker.get("torch")}
    if not enabled(home):
        return {**base, "state": "disabled", "reason": "accélération GPU désactivée par l'utilisateur"}
    overlay = Path(str(marker.get("overlay") or ""))
    if not (overlay / "torch" / "__init__.py").is_file():
        return {**base, "state": "failed",
                "reason": f"surcouche GPU incomplète ou effacée ({overlay}) : réinstallez-la"}
    if marker.get("python") != _python():
        return {**base, "state": "inactive",
                "reason": f"surcouche installée pour Python {marker.get('python')}, APEX tourne sous "
                          f"{_python()} : réinstallez-la"}
    spec, folder, problem = load_spec(root)
    if spec is None or folder is None:
        return {**base, "state": "inactive", "reason": problem or "description du composant GPU absente"}
    current = next((v for v in variants(spec, folder) if v["id"] == marker.get("variant")), None)
    if current is None or current["lock_sha256"] != marker.get("lock_sha256"):
        return {**base, "state": "inactive",
                "reason": "APEX a été mis à jour depuis l'installation de l'accélération GPU : "
                          "réinstallez-la"}
    if "torch" in sys.modules:
        return {**base, "state": "failed",
                "reason": "torch déjà chargé avant l'activation : surcouche non appliquée"}
    driver = detect_driver(smi=smi, run=run)
    ceiling = _cuda(driver.get("cuda_driver"))
    needed = _cuda(current["cuda"])
    if not driver.get("found") or driver.get("reason") or ceiling is None or needed is None:
        return {**base, "state": "inactive", "driver": driver,
                "reason": "pilote NVIDIA indisponible : "
                          f"{driver.get('reason') or 'version CUDA illisible'}"}
    if needed > ceiling:
        return {**base, "state": "inactive", "driver": driver,
                "reason": f"pilote NVIDIA insuffisant : CUDA {driver['cuda_driver']} accepté, CUDA "
                          f"{current['cuda']} requis par la surcouche (pilote rétrogradé ?)"}
    measured = probe(overlay, run=run)
    verdict = probe_verdict(measured)
    if verdict is not None:
        return {**base, "state": "inactive", "driver": driver, "probe": measured,
                "reason": f"sonde GPU en échec : {verdict}"}
    sys_path.insert(0, str(overlay))
    os.environ[OVERLAY_ENV] = str(overlay)
    return {**base, "state": "active", "driver": driver, "probe": measured, "overlay": str(overlay),
            "reason": f"surcouche GPU active : {measured['devices'][0]['name']} "
                      f"(torch {measured['torch']})"}


def last_activation(home: Path | None = None) -> dict[str, Any] | None:
    return _read(activation_path(home))


def describe(activation: dict[str, Any]) -> str:
    return f"{activation.get('state')} — {activation.get('reason')}"


# -- installation, retrait ------------------------------------------------------------------------------


def _log(message: str) -> None:
    print(f"[apex-gpu] {time.strftime('%Y-%m-%d %H:%M:%S')} {message}", file=sys.stderr, flush=True)


def _disk_free(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free


def install(home: Path | None = None, *, root: Path | None = None, requested: str | None = None,
            run: Runner = subprocess.run, smi: str | None = None, python: str | None = None,
            disk_free: Callable[[Path], int] = _disk_free,
            running: Callable[[], bool] = _apex_running) -> tuple[int, dict[str, Any]]:
    """Installe la surcouche GPU (geste explicite de l'utilisateur). Rend (code de sortie, compte rendu)."""
    home = home or paths().home
    report: dict[str, Any] = {"at": _now()}

    def refuse(code: int, reason: str) -> tuple[int, dict[str, Any]]:
        _log(f"refusé : {reason}")
        report.update(ok=False, reason=reason)
        with contextlib.suppress(OSError):
            _write(gpu_home(home) / "last-install.json", report)
        return code, report

    if running():
        return refuse(EXIT_REFUSED, "APEX est en cours d'exécution : arrêtez-le avant d'installer "
                                    "l'accélération GPU")
    spec, folder, problem = load_spec(root)
    if spec is None or folder is None:
        return refuse(EXIT_REFUSED, problem or "description du composant GPU absente")
    driver = detect_driver(smi=smi, run=run)
    report["driver"] = driver
    names = ", ".join(g["name"] for g in driver.get("gpus", []))
    _log(f"pilote NVIDIA : {driver.get('driver_version') or '—'} ; CUDA accepté : "
         f"{driver.get('cuda_driver') or '—'} ; GPU : {names or '—'}")
    variant, why = choose(spec, folder, driver, requested)
    if variant is None:
        return refuse(EXIT_REFUSED, why)
    report["variant"] = variant["id"]
    _log(f"variante {variant['id']} ({why})")
    needed = int(variant["wheel_bytes"]) + int(variant["installed_bytes"]) + DISK_MARGIN_BYTES
    free = disk_free(gpu_home(home))
    report["disk"] = {"free_bytes": free, "needed_bytes": needed}
    if free < needed:
        return refuse(EXIT_REFUSED, f"espace disque insuffisant sur {gpu_home(home)} : {free / 1e9:.1f} Go "
                                    f"libres, {needed / 1e9:.1f} Go nécessaires")
    target = gpu_home(home) / f"torch-{spec['torch']}-{variant['id']}"
    staging = target.with_name(target.name + ".partial")
    scratch = gpu_home(home) / ".tmp"
    for leftover in (staging, scratch):
        shutil.rmtree(leftover, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch)}
    command = [python or sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
               "--no-input", "--no-warn-script-location", "--no-cache-dir", "--require-hashes",
               "--only-binary=:all:", "--no-deps", "--target", str(staging), "-r", variant["lock"]]
    _log(f"téléchargement et installation de torch {spec['torch']}+{variant['id']} "
         f"({int(variant['wheel_bytes']) / 1e9:.1f} Go, empreinte vérifiée) vers {staging}…")
    try:
        done = run(command, env=env, timeout=INSTALL_TIMEOUT_S, text=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return refuse(EXIT_INSTALL_FAILED, f"pip n'a pas abouti ({exc.__class__.__name__})")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if done.returncode != 0 or not (staging / "torch" / "__init__.py").is_file():
        shutil.rmtree(staging, ignore_errors=True)
        return refuse(EXIT_INSTALL_FAILED, f"pip a échoué (code {done.returncode}) : voir le journal")
    _log("sonde du GPU avec la surcouche (processus séparé)…")
    measured = probe(staging, python=python, run=run)
    report["probe"] = measured
    verdict = probe_verdict(measured)
    if verdict is not None:
        shutil.rmtree(staging, ignore_errors=True)
        return refuse(EXIT_PROBE_FAILED, f"sonde GPU en échec, surcouche retirée : {verdict}")
    previous = target.with_name(target.name + ".old")
    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        os.replace(target, previous)
    os.replace(staging, target)
    shutil.rmtree(previous, ignore_errors=True)
    marker = {"schema": 1, "variant": variant["id"], "cuda": variant["cuda"],
              "torch": measured.get("torch"), "overlay": str(target),
              "lock_sha256": variant["lock_sha256"], "python": _python(),
              "app_root": str(root or app_root()), "installed_at": _now(), "driver": driver,
              "probe": measured}
    _write(marker_path(home), marker)
    set_enabled(True, home)
    report.update(ok=True, overlay=str(target), reason=(
        f"surcouche GPU installée et vérifiée sur ce PC : {measured['devices'][0]['name']}"))
    _write(gpu_home(home) / "last-install.json", report)
    _log(report["reason"] + " — active au prochain démarrage d'APEX.")
    return EXIT_OK, report


def verify(home: Path | None = None, *, run: Runner = subprocess.run) -> tuple[int, dict[str, Any]]:
    """Refait la sonde de la surcouche installée (sans l'activer)."""
    marker = _read(marker_path(home))
    if marker is None:
        return EXIT_REFUSED, {"ok": False, "reason": "accélération GPU non installée"}
    measured = probe(Path(str(marker.get("overlay"))), run=run)
    verdict = probe_verdict(measured)
    return (EXIT_OK if verdict is None else EXIT_PROBE_FAILED), {"ok": verdict is None, "probe": measured,
                                                                 "reason": verdict or "sonde GPU réussie"}


def remove(home: Path | None = None, *,
           running: Callable[[], bool] = _apex_running) -> tuple[int, dict[str, Any]]:
    """Retire la surcouche (dossiers et marqueur) : le runtime de base, jamais modifié, reste tel quel."""
    if running():
        return EXIT_REFUSED, {"ok": False, "reason": (
            "APEX est en cours d'exécution : arrêtez-le avant de retirer l'accélération GPU (ses fichiers "
            "sont en cours d'usage)")}
    folder = gpu_home(home)
    removed = []
    if folder.is_dir():
        for child in folder.iterdir():
            if child.is_dir() and (child.name.startswith("torch-") or child.name == ".tmp"):
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
    for path in (marker_path(home), gpu_home(home) / "last-install.json"):
        with contextlib.suppress(OSError):
            path.unlink()
    return EXIT_OK, {"ok": True, "removed": removed,
                     "reason": "accélération GPU retirée : calcul sur le processeur"}


def status(home: Path | None = None, *, root: Path | None = None, run: Runner = subprocess.run,
           smi: str | None = None) -> dict[str, Any]:
    """Tout ce qui décide de l'accélération GPU sur ce PC, mesuré maintenant (pilote compris)."""
    spec, folder, problem = load_spec(root)
    driver = detect_driver(smi=smi, run=run)
    out: dict[str, Any] = {"installed": _read(marker_path(home)), "enabled": enabled(home),
                           "driver": driver, "activation": last_activation(home),
                           "last_install": _read(gpu_home(home) / "last-install.json"),
                           "spec_problem": problem, "variants": [], "choice": None,
                           "claims": "aucune compatibilité GPU n'est affirmée sans sonde réussie sur ce PC"}
    if spec is not None and folder is not None:
        out["torch"] = spec["torch"]
        out["variants"] = variants(spec, folder)
        variant, why = choose(spec, folder, driver)
        out["choice"] = {"variant": variant["id"] if variant else None, "reason": why}
        if variant is not None:
            out["disk"] = {"free_bytes": _disk_free(gpu_home(home)),
                           "needed_bytes": int(variant["wheel_bytes"]) + int(variant["installed_bytes"])
                           + DISK_MARGIN_BYTES}
    return out
