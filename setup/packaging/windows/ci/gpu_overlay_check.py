"""Accélération GPU facultative d'APEX, vérifiée sur un Windows réel SANS GPU (runner GitHub) : tout ce
qui peut l'être avant le PC de l'utilisateur, avec l'application et le Python QU'APEX A INSTALLÉS.

Lancé par le Python préparé par le Panel
(`python -P gpu_overlay_check.py --out <dossier> --work <jetable>`), donc avec le code d'APEX installé
(`apex.standalone.gpu`), jamais celui du dépôt :

1. la description figée et ses listes (`app/runtime/gpu`) : chaque variante préparée a sa liste et ses
   tailles, et le torch de base est le même que celui de la surcouche ;
2. chaque roue figée EXISTE : requête HEAD sur son adresse exacte, taille annoncée = taille figée ;
3. la variante qu'APEX choisirait pour un pilote qui accepte CUDA 13.3 (celui de l'utilisateur, pilote
   610.62), et pour 12.8 : jamais au-delà de ce que le pilote accepte ;
4. la roue de cette variante est téléchargée et installée par la commande EXACTE de l'installation
   (`gpu.pip_command` : empreinte SHA-256 exigée par pip), dans un dossier jetable hors d'APEX, puis la
   sonde d'APEX l'importe dans un processus séparé. Sans GPU, la sonde doit dire que CUDA est indisponible :
   c'est le résultat attendu ici, et la raison pour laquelle rien n'est installé chez APEX.

Ce script ne dit JAMAIS que CUDA fonctionne : seul le PC de l'utilisateur, avec son GPU, peut le montrer.
Verdicts : PASS (0) si tout ce qui est vérifiable ici l'est, FAIL (1) sinon. Preuves :
`<sortie>/gpu-overlay.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

PASS, FAIL = 0, 1
USER_DRIVER_CUDA = "13.3"


def _head(url: str) -> tuple[int | None, int | None, str]:
    # Adresses https figées par la liste (`torch @ https://…`), jamais un autre schéma.
    request = urllib.request.Request(url, method="HEAD",  # noqa: S310
                                     headers={"User-Agent": "apex-ci-gpu-check"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            length = response.headers.get("Content-Length")
            return response.status, int(length) if length and length.isdigit() else None, response.url
    except Exception as exc:  # une adresse morte est un résultat, jamais une exception de ce script
        return None, None, f"{exc.__class__.__name__}: {exc}"[:300]


def _locked_url(lock: Path) -> str | None:
    match = re.search(r"^torch @ (https://\S+)", lock.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="dossier jetable, hors d'APEX")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {"phase": "gpu-overlay", "checks": []}
    failed = False

    def check(name: str, ok: bool, detail: str) -> None:
        nonlocal failed
        failed |= not ok
        evidence["checks"].append({"check": name, "ok": ok, "detail": detail})
        print(f"CHECK {'PASS' if ok else 'FAIL'} {name} : {detail}")

    try:
        from apex.standalone import app_root, gpu

        evidence["apex_module"] = gpu.__file__
        print(f"NOTE code d'APEX : {gpu.__file__} ; Python {sys.version.split()[0]} ({sys.executable})")
        spec, folder, problem = gpu.load_spec()
        check("description figée", spec is not None and folder is not None,
              problem or f"{folder} (torch {spec['torch'] if spec else '?'}, racine {app_root()})")
        if spec is None or folder is None:
            return _finish(args.out, evidence, FAIL)
        mismatch = gpu.torch_mismatch(spec, folder)
        check("torch de base = torch de la surcouche", mismatch is None,
              mismatch or f"torch {spec['torch']}")
        prepared = [v for v in gpu.variants(spec, folder) if v["prepared"]]
        names = ", ".join(f"{v['id']} (CUDA {v['cuda']})" for v in prepared)
        check("variantes préparées", bool(prepared), names or "aucune")
        declared = {v["id"]: v for v in spec["variants"]}
        for variant in prepared:
            url = _locked_url(Path(variant["lock"]))
            if url is None or url != declared[variant["id"]].get("url"):
                check(f"roue {variant['id']} : adresse", False, f"liste {url} ≠ description "
                      f"{declared[variant['id']].get('url')}")
                continue
            status, length, final = _head(url)
            check(f"roue {variant['id']} : existe", status == 200 and length == variant["wheel_bytes"],
                  f"HTTP {status}, {length} octets annoncés, {variant['wheel_bytes']} figés ({final})")
        # Le pilote n'est jamais supposé par APEX ; ici, deux pilotes DÉCRITS servent à vérifier le choix.
        choices = {}
        for cuda in (USER_DRIVER_CUDA, "12.8"):
            described = {"found": True, "cuda_driver": cuda,
                         "gpus": [{"name": "pilote décrit (runner sans GPU)"}]}
            variant, why = gpu.choose(spec, folder, described)
            choices[cuda] = variant["id"] if variant else None
            ceiling = gpu._cuda(cuda) or (0, 0)
            ok = variant is not None and (gpu._cuda(variant["cuda"]) or (99, 0)) <= ceiling
            check(f"variante pour un pilote CUDA {cuda}", ok, why)
        evidence["driver_here"] = gpu.detect_driver()
        here = evidence["driver_here"]
        print(f"NOTE pilote de ce runner : {here.get('reason') or here}")
        chosen = next((v for v in prepared if v["id"] == choices[USER_DRIVER_CUDA]), None)
        if chosen is None:
            return _finish(args.out, evidence, FAIL)
        target = args.work / f"torch-{spec['torch']}-{chosen['id']}"
        shutil.rmtree(target, ignore_errors=True)
        command = gpu.pip_command(sys.executable, target, chosen["lock"])
        print(f"NOTE {' '.join(command)}")
        # Comme l'installation : le téléchargement de pip reste dans ce dossier jetable.
        scratch = args.work / ".tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch)}
        started = time.monotonic()
        done = subprocess.run(command, text=True, capture_output=True, env=env,  # noqa: S603 — commande d'APEX
                              timeout=gpu.INSTALL_TIMEOUT_S)
        evidence["pip"] = {"code": done.returncode, "seconds": round(time.monotonic() - started),
                           "tail": (done.stdout + done.stderr).strip().splitlines()[-8:]}
        check(f"roue {chosen['id']} : téléchargée, empreinte vérifiée, installée à part",
              done.returncode == 0 and (target / "torch" / "__init__.py").is_file(),
              f"pip code {done.returncode} en {evidence['pip']['seconds']} s ; "
              + " | ".join(evidence["pip"]["tail"][-3:]))
        if done.returncode != 0:
            return _finish(args.out, evidence, FAIL)
        measured = gpu.probe(target)
        evidence["probe"] = measured
        verdict = gpu.probe_verdict(measured)
        check("sonde : torch de la surcouche, construit pour CUDA",
              bool(measured.get("from_overlay")) and measured.get("torch_cuda") == chosen["cuda"],
              f"torch {measured.get('torch')} (CUDA {measured.get('torch_cuda')}), depuis la surcouche : "
              f"{measured.get('from_overlay')}")
        check("sonde : refuse sans GPU (runner)",
              verdict is not None and not measured.get("cuda_available"),
              f"verdict de la sonde : {verdict}")
        shutil.rmtree(target, ignore_errors=True)
    except Exception as exc:  # jamais un arrêt muet : une ligne FAIL explicite
        check("exécution", False, f"{exc.__class__.__name__}: {exc}"[:500])
    return _finish(args.out, evidence, FAIL if failed else PASS)


def _finish(out: Path, evidence: dict[str, Any], verdict: int) -> int:
    evidence["verdict"] = "PASS" if verdict == PASS else "FAIL"
    (out / "gpu-overlay.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print("VERDICT", evidence["verdict"],
          "(rien ici ne prouve que CUDA fonctionne : seul le PC avec GPU le peut)")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
