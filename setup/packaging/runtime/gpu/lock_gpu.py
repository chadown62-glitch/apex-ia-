"""Produit les listes figées de l'accélération GPU facultative (`torch-<variante>.lock`) et complète
`gpu-runtime.json` (empreinte, tailles) — à exécuter là où https://download.pytorch.org est joignable :

    python setup/packaging/runtime/gpu/lock_gpu.py            # toutes les variantes de gpu-runtime.json
    python setup/packaging/runtime/gpu/lock_gpu.py cu128      # une seule

Pour chaque variante :
1. l'index de PyTorch (`<index>/<variante>/torch/`) doit contenir EXACTEMENT
   `torch-<version>+<variante>-cp312-cp312-win_amd64.whl` (version = celle du runtime de base,
   `requirements-windows.lock`) ; sinon la variante est déclarée absente, jamais remplacée par une autre ;
2. mode léger (défaut) : la roue n'est PAS téléchargée ; sa taille, son répertoire central et son METADATA
   sont lus par requêtes HTTP Range (quelques Mo), et l'empreinte retenue est celle de l'index — pip la
   revérifie sur chaque octet à l'installation. `--full` : la roue entière est téléchargée (~2,5 Go) et son
   SHA-256 recalculé ; il doit égaler celui de l'index ;
3. ses dépendances (`Requires-Dist` de son METADATA), évaluées pour Windows x64 / CPython 3.12, doivent
   TOUTES être satisfaites par les versions figées du runtime de base : la surcouche est installée en
   `--no-deps`, elle n'apporte rien d'autre que torch ;
4. la liste figée `torch @ <adresse> --hash=sha256:<empreinte>` est écrite, avec la taille de la roue et
   de son contenu extrait (pour la vérification d'espace disque avant installation).

Rien n'est écrit pour une variante dont une vérification échoue. Sortie : un compte rendu JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from packaging.markers import Marker  # `pip install packaging` (déjà dans l'environnement de dév.)
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

HERE = Path(__file__).resolve().parent
BASE_LOCK = HERE.parent / "requirements-windows.lock"

#: Environnement cible (python.exe 3.12 officiel, Windows x64) pour évaluer les marqueurs.
TARGET = {"implementation_name": "cpython", "implementation_version": "3.12.10", "os_name": "nt",
          "platform_machine": "AMD64", "platform_release": "10", "platform_system": "Windows",
          "platform_version": "10.0", "python_full_version": "3.12.10",
          "platform_python_implementation": "CPython", "python_version": "3.12", "sys_platform": "win32",
          "extra": ""}


class _Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append((href, ""))

    def handle_data(self, data: str) -> None:
        if self.links and not self.links[-1][1]:
            self.links[-1] = (self.links[-1][0], data.strip())


def base_pins(lock: Path) -> dict[str, Version]:
    pins = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", line)
        if match:
            pins[str(canonicalize_name(match.group(1)))] = Version(match.group(2))
    return pins


def _fetch(url: str, timeout: float = 60.0) -> bytes:
    _check_url(url)
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 — https (ou 127.0.0.1)
        return bytes(response.read())


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname == "127.0.0.1"):
        return
    raise ValueError(f"adresse refusée (https seulement) : {url}")


def find_wheel(index: str, variant: str, torch: str) -> tuple[str, str | None] | None:
    """(adresse, sha256 de l'index ou None) de la roue Windows CPython 3.12 attendue, ou None."""
    page = f"{index.rstrip('/')}/{variant}/torch/"
    parser = _Links()
    parser.feed(_fetch(page).decode("utf-8", "replace"))
    wanted = f"torch-{torch}+{variant}-cp312-cp312-win_amd64.whl"
    for href, _text in parser.links:
        url = urllib.parse.urljoin(page, href)
        path, _, fragment = url.partition("#")
        if urllib.parse.unquote(path.rsplit("/", 1)[-1]) == wanted:
            sha = re.fullmatch(r"sha256=([0-9a-f]{64})", fragment)
            return path, sha.group(1) if sha else None
    return None


class RangeReader(io.RawIOBase):
    """Roue distante lue par morceaux (requêtes HTTP Range) : `zipfile` n'en lit que la fin (répertoire
    central) et METADATA, soit quelques Mo d'une roue de plusieurs Go."""

    CHUNK = 1 << 18

    def __init__(self, url: str, timeout: float = 60.0) -> None:
        super().__init__()
        _check_url(url)
        self.url, self.timeout, self.pos, self.fetched = url, timeout, 0, 0
        self._chunks: dict[int, bytes] = {}
        request = urllib.request.Request(url, method="HEAD")  # noqa: S310 — adresse vérifiée
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — adresse vérifiée
            length = response.headers.get("Content-Length")
        if length is None or not length.isdigit():
            raise ValueError("taille de la roue inconnue (pas de Content-Length) : relancez avec --full")
        self.size = int(length)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _chunk(self, number: int) -> bytes:
        if number not in self._chunks:
            start = number * self.CHUNK
            end = min(self.size, start + self.CHUNK) - 1
            request = urllib.request.Request(  # noqa: S310 — adresse vérifiée
                self.url, headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                if response.status != 206:
                    raise ValueError(f"le serveur ne sert pas les plages (HTTP {response.status}) : "
                                     "relancez avec --full")
                data = bytes(response.read())
            self.fetched += len(data)
            self._chunks[number] = data
        return self._chunks[number]

    def readinto(self, buffer: Any) -> int:
        wanted = min(len(buffer), self.size - self.pos)
        if wanted <= 0:
            return 0
        out = bytearray()
        while len(out) < wanted:
            number, offset = divmod(self.pos + len(out), self.CHUNK)
            out += self._chunk(number)[offset:offset + wanted - len(out)]
        buffer[:wanted] = out
        self.pos += wanted
        return wanted


def _download(url: str, destination: Path) -> tuple[str, int]:
    _check_url(url)
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as out:  # noqa: S310
        for block in iter(lambda: response.read(1 << 20), b""):
            digest.update(block)
            out.write(block)
            size += len(block)
    return digest.hexdigest(), size


def check_wheel(wheel: Path | IO[bytes], pins: dict[str, Version]) -> tuple[int, list[str], list[str]]:
    """(taille extraite, dépendances requises sous Windows, celles que le runtime de base ne satisfait
    pas)."""
    with zipfile.ZipFile(wheel) as archive:
        installed = sum(info.file_size for info in archive.infolist())
        metadata_name = next((n for n in archive.namelist()
                              if n.endswith(".dist-info/METADATA") and n.count("/") == 1), None)
        if metadata_name is None:
            raise ValueError("METADATA absent de la roue")
        metadata = archive.read(metadata_name).decode("utf-8", "replace")
    required, missing = [], []
    for line in metadata.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        requirement = Requirement(line.split(":", 1)[1].strip())
        if requirement.marker is not None and not Marker(str(requirement.marker)).evaluate(TARGET):
            continue
        required.append(str(requirement))
        pinned = pins.get(str(canonicalize_name(requirement.name)))
        if pinned is None or not requirement.specifier.contains(pinned, prereleases=True):
            missing.append(f"{requirement} (runtime de base : {pinned or 'absent'})")
    return installed, required, missing


def lock_variant(spec: dict[str, Any], variant: dict[str, Any], pins: dict[str, Version],
                 workdir: Path, index: str, *, full: bool = False) -> dict[str, Any]:
    """Mode léger (défaut) : métadonnées lues par plages, empreinte de l'index (pip la vérifie à
    l'installation, octet par octet). `full` : la roue entière téléchargée et son empreinte recalculée."""
    torch = spec["torch"]
    found = find_wheel(index, variant["id"], torch)
    if found is None:
        return {"id": variant["id"], "ok": False,
                "reason": f"torch-{torch}+{variant['id']}-cp312-cp312-win_amd64.whl absent de l'index"}
    url, index_sha = found
    if full:
        wheel = workdir / url.rsplit("/", 1)[-1]
        try:
            sha, size = _download(url, wheel)
            if index_sha is not None and sha != index_sha:
                return {"id": variant["id"], "ok": False, "url": url,
                        "reason": f"empreinte téléchargée {sha} ≠ empreinte de l'index {index_sha}"}
            installed, required, missing = check_wheel(wheel, pins)
        finally:
            wheel.unlink(missing_ok=True)
        source, fetched = "download", size
    else:
        if index_sha is None:
            return {"id": variant["id"], "ok": False, "url": url,
                    "reason": "empreinte absente de l'index : relancez avec --full "
                              "(téléchargement complet)"}
        reader = RangeReader(url)
        installed, required, missing = check_wheel(io.BufferedReader(reader), pins)
        sha, size, source, fetched = index_sha, reader.size, "index", reader.fetched
    if missing:
        return {"id": variant["id"], "ok": False, "url": url, "requires": required,
                "reason": "dépendances non satisfaites par le runtime de base : " + "; ".join(missing)}
    return {"id": variant["id"], "ok": True, "url": url, "sha256": sha, "sha256_source": source,
            "wheel_bytes": size, "installed_bytes": installed, "fetched_bytes": fetched,
            "requires": required, "index_sha256": index_sha}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("variants", nargs="*", help="variantes à figer (défaut : toutes)")
    parser.add_argument("--spec-dir", type=Path, default=HERE, help=argparse.SUPPRESS)
    parser.add_argument("--base-lock", type=Path, default=BASE_LOCK, help=argparse.SUPPRESS)
    parser.add_argument("--index", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--full", action="store_true",
                        help="télécharger chaque roue entière (~2,5 Go) et recalculer son empreinte")
    args = parser.parse_args(argv)
    spec_path = args.spec_dir / "gpu-runtime.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    pins = base_pins(args.base_lock)
    if pins.get("torch") != Version(spec["torch"]):
        print(f"torch {spec['torch']} (gpu-runtime.json) ≠ torch {pins.get('torch')} (runtime de base) : "
              "accordez les deux avant de figer la surcouche.", file=sys.stderr)
        return 2
    index = args.index or spec["index"]
    chosen = [v for v in spec["variants"] if not args.variants or v["id"] in args.variants]
    report = []
    workdir = Path(tempfile.mkdtemp(prefix="apex-gpu-lock-"))
    try:
        for variant in chosen:
            try:
                result = lock_variant(spec, variant, pins, workdir, index, full=args.full)
            except (OSError, ValueError) as exc:
                result = {"id": variant["id"], "ok": False,
                          "reason": f"{exc.__class__.__name__}: {exc}"[:400]}
            report.append(result)
            if not result["ok"]:
                continue
            (args.spec_dir / variant["lock"]).write_text(
                f"# Accélération GPU d'APEX : torch {spec['torch']}+{variant['id']} "
                "(Windows x64, CPython 3.12).\n"
                f"# Produit par lock_gpu.py le {datetime.now(UTC):%Y-%m-%d} ; dépendances vérifiées contre "
                f"requirements-windows.lock ; empreinte : {result['sha256_source']}.\n"
                f"torch @ {result['url']} \\\n    --hash=sha256:{result['sha256']}\n", encoding="utf-8")
            variant.update(wheel_bytes=result["wheel_bytes"], installed_bytes=result["installed_bytes"],
                           sha256=result["sha256"], sha256_source=result["sha256_source"],
                           url=result["url"],
                           locked_at=datetime.now(UTC).isoformat(timespec="seconds"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if any(r["ok"] for r in report):
        spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report and all(r["ok"] for r in report) else 1


if __name__ == "__main__":
    sys.exit(main())
