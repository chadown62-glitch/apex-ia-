"""Corpus d'entraînement du Cerveau APEX v2 — textes RÉELS, chacun avec sa provenance.

    sources (fichiers locaux, paquets Ubuntu signés, archives PyPI) -> conversion en texte -> filtres
    -> déduplication -> documents JSONL (texte + source + licence + langue + empreinte) -> manifeste

Rien n'est inventé ni généré ici : chaque document vient d'un fichier dont l'origine est vérifiable.

- **Paquets Ubuntu** : chaîne de confiance complète — `InRelease` signé (vérifié par `gpgv` avec le
  trousseau officiel Ubuntu), puis SHA-256 de l'index `Packages.xz` (lu dans `InRelease`), puis SHA-256 du
  `.deb` (lu dans l'index). Un fichier qui ne correspond pas est refusé, jamais utilisé.
- **PyPI** : l'archive source (`sdist`) est vérifiée contre le SHA-256 publié par PyPI ; seules les licences
  permissives d'une liste blanche sont acceptées.
- **Fichiers locaux** : bibliothèque standard Python, documentation d'APEX (licence déclarée par
  l'appelant).

Les textes produits par d'autres modèles d'IA ne font PAS partie du corpus (le Cerveau APEX est construit de
zéro par APEX) ; les exercices générés par APEX lui-même (`apex.neural.tasks`) sont un jeu séparé.
"""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import html
import json
import lzma
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar

UBUNTU_MIRROR = "http://archive.ubuntu.com/ubuntu"
UBUNTU_KEYRING = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
#: Licences acceptées pour une archive PyPI (champ `license` ou classifieurs) — permissives seulement.
PERMISSIVE = (
    "mit",
    "bsd",
    "apache",
    "psf",
    "python software foundation",
    "isc",
    "zlib",
    "unlicense",
    "mozilla public license 2.0",
    "mpl-2.0",
    "mpl 2.0",
    "hpnd",
)
_USER_AGENT = "APEX-corpus/1 (+https://github.com/chadown62-glitch/apex-ia-)"


@dataclass(frozen=True)
class CorpusDocument:
    text: str
    source: str
    path: str
    license: str
    lang: str
    kind: str
    sha256: str = ""

    def with_hash(self) -> CorpusDocument:
        digest = hashlib.sha256(normalize_for_dedup(self.text).encode("utf-8")).hexdigest()
        return CorpusDocument(self.text, self.source, self.path, self.license, self.lang, self.kind, digest)


def normalize_for_dedup(text: str) -> str:
    return " ".join(text.split()).lower()


# --------------------------------------------------------------------------------------------- conversions


class _HTMLText(HTMLParser):
    """HTML -> texte lisible : titres, paragraphes, listes, blocs de code conservés ; navigation, menus,
    scripts et styles retirés. Si la page déclare un contenu principal (`role="main"`, Sphinx `div.body`),
    seul ce contenu est gardé."""

    _SKIP: ClassVar[frozenset[str]] = frozenset(
        {"script", "style", "nav", "header", "footer", "noscript", "svg", "head", "form", "button"}
    )
    _SKIP_CLASSES: ClassVar[tuple[str, ...]] = (
        "sphinxsidebar",
        "related",
        "footer",
        "headerlink",
        "navigation",
        "breadcrumb",
        "toctree",
        "nav",
        "menu",
        "sidebar",
        "copyright",
        "highlight-default notranslate cell-toggle",
    )
    _BLOCK: ClassVar[frozenset[str]] = frozenset(
        {
            "p",
            "div",
            "section",
            "article",
            "br",
            "li",
            "tr",
            "table",
            "dt",
            "dd",
            "blockquote",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "pre",
            "ul",
            "ol",
            "dl",
            "hr",
            "figure",
            "caption",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main_parts: list[str] | None = None
        self._stack: list[tuple[str, bool, bool]] = []  # (tag, skipped, main)
        self._pre = 0

    def _skipping(self) -> bool:
        return any(skip for _, skip, _ in self._stack)

    def _in_main(self) -> bool:
        return any(main for _, _, main in self._stack)

    def _emit(self, text: str) -> None:
        if self._skipping():
            return
        self.parts.append(text)
        if self.main_parts is not None and self._in_main():
            self.main_parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "hr", "img", "meta", "link", "input"}:
            if tag in {"br", "hr"}:
                self._emit("\n")
            return
        values = dict(attrs)
        classes = (values.get("class") or "").lower()
        skip = (
            tag in self._SKIP
            or values.get("role") == "navigation"
            or any(c in classes.split() for c in self._SKIP_CLASSES)
        )
        main = values.get("role") == "main" or (tag == "div" and "body" in classes.split()) or tag == "main"
        if main and self.main_parts is None:
            self.main_parts = []
        self._stack.append((tag, skip, main))
        if tag == "pre":
            self._pre += 1
            self._emit("\n\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self._emit("\n- ")
        elif tag in self._BLOCK and not (self.parts and self.parts[-1].endswith("- ")):
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break
        else:
            return
        if tag == "pre":
            self._pre = max(0, self._pre - 1)
            self.parts.append("\n\n")
        elif tag in self._BLOCK:
            self.parts.append("\n")
            if self.main_parts is not None:
                self.main_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._pre:
            self._emit(data)
        else:
            self._emit(re.sub(r"\s+", " ", data))


def html_to_text(markup: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: S110 - HTML invalide : on garde ce qui a pu être lu
        pass
    chosen = (
        parser.main_parts if parser.main_parts and len("".join(parser.main_parts)) > 200 else parser.parts
    )
    return tidy("".join(chosen))


def tidy(text: str) -> str:
    """Fins de ligne normalisées, espaces de fin retirés, au plus deux lignes vides consécutives, caractères
    de contrôle retirés (hors tabulation et saut de ligne)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return html.unescape(text).strip() + "\n"


def man_to_text(path: Path) -> str:
    """Page de manuel (roff) -> texte, rendue par groff (pas de surimpression, pas de couleurs)."""
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    if raw.startswith(b".so "):
        return ""  # simple renvoi vers une autre page
    env = {**os.environ, "LC_ALL": "C.UTF-8", "GROFF_NO_SGR": "1"}
    try:
        command = ["groff", "-man", "-Tutf8", "-P", "-cbou", "-rLL=100n", "-rHY=0", "-dAD=l", "-K", "utf8"]
        done = subprocess.run(command, input=raw, capture_output=True, timeout=60, env=env, check=False)  # noqa: S603 - commande fixe, arguments construits ici
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return tidy(done.stdout.decode("utf-8", errors="replace"))


def info_to_text(path: Path) -> str:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace")
    out = []
    for node in text.split("\x1f"):
        lines = node.strip("\n").split("\n")
        if lines and lines[0].startswith("File:"):
            lines = lines[1:]
        if lines and lines[0].startswith(("Tag Table:", "End Tag Table", "Indirect:", "Local Variables")):
            continue
        out.append("\n".join(lines))
    return tidy("\n\n".join(out))


def mo_pairs(path: Path) -> list[tuple[str, str]]:
    """Catalogue gettext compilé (.mo) -> paires (original anglais, traduction) faites par des
    traducteurs."""
    import gettext

    try:
        with path.open("rb") as handle:
            catalog = gettext.GNUTranslations(handle)._catalog  # type: ignore[attr-defined]
    except Exception:
        return []
    pairs = []
    for key, value in catalog.items():
        if isinstance(key, tuple) or not key or not value or key == value:
            continue
        if isinstance(value, str) and len(key) >= 12 and any(c.isalpha() for c in key):
            pairs.append((key, value))
    return pairs


# ------------------------------------------------------------------------------------------------- filtres


def split_large(document: CorpusDocument, limit: int = 500_000) -> Iterator[CorpusDocument]:
    """Un manuel d'un seul tenant (fichier info de plusieurs Mo) est découpé en parties aux limites de
    paragraphe, au lieu d'être rejeté."""
    if len(document.text) <= limit:
        yield document
        return
    part, size, number = [], 0, 0
    for paragraph in document.text.split("\n\n"):
        if size and size + len(paragraph) > limit:
            yield CorpusDocument(
                "\n\n".join(part) + "\n",
                document.source,
                f"{document.path}#{number}",
                document.license,
                document.lang,
                document.kind,
            )
            part, size, number = [], 0, number + 1
        part.append(paragraph)
        size += len(paragraph) + 2
    if part:
        yield CorpusDocument(
            "\n\n".join(part) + "\n",
            document.source,
            f"{document.path}#{number}",
            document.license,
            document.lang,
            document.kind,
        )


def acceptable(text: str, kind: str) -> str | None:
    """`None` si le texte est retenu, sinon la raison du rejet (comptée dans le manifeste)."""
    stripped = text.strip()
    if len(stripped) < 200:
        return "trop court"
    lines = stripped.split("\n")
    if sum(len(line) for line in lines) / len(lines) > 400:
        return "lignes trop longues (minifié ou généré)"
    letters = sum(c.isalpha() for c in stripped)
    if kind == "prose" and letters / len(stripped) < 0.45:
        return "trop peu de lettres pour de la prose"
    if kind == "code" and letters / len(stripped) < 0.25:
        return "trop peu de lettres (données plutôt que code)"
    if len(stripped) > 2_000_000:
        return "document trop volumineux"
    return None


# ------------------------------------------------------------------------------------------------- sources


@dataclass(frozen=True)
class SourceSpec:
    """Une source du corpus. `kind` : `local` (fichiers d'un répertoire), `ubuntu` (paquet Ubuntu signé),
    `pypi` (archive source PyPI), `ubuntu_mo` (traductions gettext d'un paquet Ubuntu), `hf_parquet` (jeu de
    données public du Hub Hugging Face, fichiers vérifiés par leur SHA-256 publié)."""

    name: str
    kind: str
    target: str
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = ()
    lang: str = "en"
    doc_kind: str = "prose"
    license: str = ""
    max_bytes: int | None = None


def _match(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _convert(path: Path, relative: str) -> str:
    name = path.name.lower()
    if re.search(r"\.\d[a-z]*(\.gz)?$", name) and "/man/" in "/" + relative:
        return man_to_text(path)
    if name.endswith((".info", ".info.gz")) or re.search(r"\.info-\d+(\.gz)?$", name):
        return info_to_text(path)
    raw = path.read_bytes()
    if name.endswith(".gz"):
        try:
            raw = gzip.decompress(raw)
        except OSError:
            return ""
        name = name[:-3]
    if b"\x00" in raw[:4096]:
        return ""  # binaire
    text = raw.decode("utf-8", errors="replace")
    if name.endswith((".html", ".htm", ".xhtml")):
        return html_to_text(text)
    return tidy(text)


def iter_tree(spec: SourceSpec, root: Path, origin: str) -> Iterator[CorpusDocument]:
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if not _match(relative, spec.include) or _match(relative, spec.exclude):
            continue
        try:
            text = _convert(path, relative)
        except (OSError, ValueError, UnicodeError):
            continue
        if not text.strip():
            continue
        total += len(text.encode("utf-8"))
        yield CorpusDocument(
            text, spec.name, f"{origin}:{relative}", spec.license, spec.lang, spec.doc_kind
        )
        if spec.max_bytes is not None and total >= spec.max_bytes:
            return


def _download(url: str, target: Path, expected_sha256: str | None = None, timeout: float = 120) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310 - URL https construite ici (miroir, PyPI)
    digest = hashlib.sha256()
    partial = target.with_suffix(target.suffix + ".partial")
    with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as handle:  # noqa: S310 - URL https construite ici (miroir, PyPI)
        for block in iter(lambda: response.read(1 << 20), b""):
            digest.update(block)
            handle.write(block)
    if expected_sha256 is not None and digest.hexdigest() != expected_sha256.lower():
        partial.unlink(missing_ok=True)
        raise ValueError(
            f"SHA-256 inattendu pour {url} : {digest.hexdigest()} au lieu de {expected_sha256}"
        )
    partial.replace(target)
    return target


class UbuntuArchive:
    """Index des paquets d'une version d'Ubuntu, VÉRIFIÉ : signature de `InRelease` (gpgv + trousseau
    officiel), puis SHA-256 de chaque `Packages.xz`. Les paquets eux-mêmes sont vérifiés contre l'index."""

    def __init__(
        self,
        cache: Path,
        release: str = "noble",
        components: tuple[str, ...] = ("main", "universe"),
        mirror: str = UBUNTU_MIRROR,
        keyring: Path = UBUNTU_KEYRING,
    ) -> None:
        self.cache = cache
        self.release = release
        self.mirror = mirror.rstrip("/")
        cache.mkdir(parents=True, exist_ok=True)
        inrelease = cache / f"{release}-InRelease"
        if not inrelease.is_file():
            _download(f"{self.mirror}/dists/{release}/InRelease", inrelease)
        if not keyring.is_file():
            raise RuntimeError(
                f"trousseau Ubuntu absent ({keyring}) : signature invérifiable, archive refusée"
            )
        check = subprocess.run(  # noqa: S603 - commande fixe, arguments construits ici
            ["gpgv", "--keyring", str(keyring), str(inrelease)],  # noqa: S607 - outil système (PATH)
            capture_output=True,
            check=False,
        )
        if check.returncode != 0:
            inrelease.unlink(missing_ok=True)
            raise RuntimeError(
                "signature de InRelease invalide : " + check.stderr.decode(errors="replace")[-300:]
            )
        signed = inrelease.read_text(encoding="utf-8", errors="replace")
        sha_section = signed.split("SHA256:", 1)[1]
        self.packages: dict[str, dict[str, str]] = {}
        for component in components:
            relative = f"{component}/binary-amd64/Packages.xz"
            match = re.search(r"^ ([0-9a-f]{64})\s+\d+ " + re.escape(relative) + "$", sha_section, re.M)
            if match is None:
                raise RuntimeError(f"{relative} absent de InRelease")
            index = cache / f"{release}-{component}-Packages.xz"
            if not index.is_file() or hashlib.sha256(index.read_bytes()).hexdigest() != match.group(1):
                _download(f"{self.mirror}/dists/{release}/{relative}", index, match.group(1))
            for block in (
                lzma.decompress(index.read_bytes()).decode("utf-8", errors="replace").split("\n\n")
            ):
                fields = dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", block, re.M))
                if "Package" in fields:
                    self.packages.setdefault(fields["Package"], fields)

    def fetch(self, package: str) -> tuple[Path, dict[str, str]]:
        fields = self.packages.get(package)
        if fields is None:
            raise KeyError(f"paquet Ubuntu inconnu : {package}")
        deb = self.cache / Path(fields["Filename"]).name
        if not deb.is_file() or hashlib.sha256(deb.read_bytes()).hexdigest() != fields["SHA256"]:
            _download(f"{self.mirror}/{fields['Filename']}", deb, fields["SHA256"], timeout=600)
        return deb, fields

    def extract(self, package: str) -> tuple[Path, dict[str, str]]:
        deb, fields = self.fetch(package)
        target = self.cache / "x" / package
        if not (target / ".done").is_file():
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True)
            subprocess.run(["dpkg-deb", "-x", str(deb), str(target)], check=True, capture_output=True)  # noqa: S603, S607 - commande fixe, arguments construits ici; outil système (groff, gpgv, dpkg-deb) cherché dans le PATH
            (target / ".done").write_text(fields["SHA256"], encoding="utf-8")
        return target, fields


def _copyright_license(root: Path, package: str) -> str:
    path = root / "usr/share/doc" / package / "copyright"
    if not path.is_file():
        return "voir /usr/share/doc/<paquet>/copyright (absent)"
    found = re.findall(r"^License: *(.+)$", path.read_text(encoding="utf-8", errors="replace"), re.M)
    names = sorted({name.strip() for name in found})
    return ", ".join(names[:6]) if names else "licence libre (fichier copyright sans champ License)"


def iter_ubuntu(spec: SourceSpec, archive: UbuntuArchive) -> Iterator[CorpusDocument]:
    root, fields = archive.extract(spec.target)
    origin = f"ubuntu:{archive.release}:{spec.target}={fields.get('Version', '?')}"
    license_ = spec.license or _copyright_license(root, spec.target)
    yield from iter_tree(SourceSpec(**{**asdict(spec), "license": license_}), root, origin)


def iter_ubuntu_translations(spec: SourceSpec, archive: UbuntuArchive) -> Iterator[CorpusDocument]:
    """Traductions humaines anglais -> français des logiciels (gettext), regroupées par catalogue."""
    root, fields = archive.extract(spec.target)
    origin = f"ubuntu:{archive.release}:{spec.target}={fields.get('Version', '?')}"
    total = 0
    for path in sorted(root.rglob("*.mo")):
        if spec.max_bytes is not None and total >= spec.max_bytes:
            return
        pairs = mo_pairs(path)
        if len(pairs) < 5:
            continue
        lines = [f"{english}\n{french}" for english, french in pairs]
        text = tidy("\n\n".join(lines))
        total += len(text.encode("utf-8"))
        yield CorpusDocument(
            text,
            spec.name,
            f"{origin}:{path.relative_to(root).as_posix()}",
            spec.license or "licences des logiciels traduits (libres)",
            spec.lang,
            "translation",
        )


def _pypi_license(info: dict[str, Any]) -> str | None:
    candidates = [str(info.get("license_expression") or ""), str(info.get("license") or "")[:200]]
    candidates += [c for c in info.get("classifiers") or [] if c.startswith("License ::")]
    for candidate in candidates:
        lowered = candidate.lower()
        if any(name in lowered for name in PERMISSIVE) and "gpl" not in lowered:
            return candidate.strip()
    return None


def iter_pypi(spec: SourceSpec, cache: Path) -> Iterator[CorpusDocument]:
    cache.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        f"https://pypi.org/pypi/{spec.target}/json", headers={"User-Agent": _USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - URL https construite ici (miroir, PyPI)
        meta = json.load(response)
    license_ = _pypi_license(meta["info"])
    if license_ is None:
        raise ValueError(f"PyPI {spec.target} : licence non permissive ou non déclarée — refusé")
    version = meta["info"]["version"]
    sdists = [u for u in meta.get("urls", []) if u.get("packagetype") == "sdist"]
    if not sdists:
        raise ValueError(f"PyPI {spec.target} {version} : aucune archive source")
    sdist = sdists[0]
    archive = cache / sdist["filename"]
    if (
        not archive.is_file()
        or hashlib.sha256(archive.read_bytes()).hexdigest() != sdist["digests"]["sha256"]
    ):
        _download(sdist["url"], archive, sdist["digests"]["sha256"], timeout=600)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root)  # noqa: S202 - extraction filtrée (filter="data")
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(root, filter="data")
        yield from iter_tree(
            SourceSpec(**{**asdict(spec), "license": license_}), root, f"pypi:{spec.target}=={version}"
        )


# --------------------------------------------------------------------------------------------- construction


@dataclass
class SourceReport:
    name: str
    documents: int = 0
    bytes: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    duplicates: int = 0
    error: str | None = None
    origin: str = ""
    license: str = ""


HF_ENDPOINT = "https://huggingface.co"


def _json_get(url: str, timeout: float = 60) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310 - URL https du Hub
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL https du Hub
        return json.load(response)


def hf_dataset_files(
    repo: str, revision: str, folder: str, *, endpoint: str = HF_ENDPOINT
) -> list[dict[str, Any]]:
    """Fichiers parquet d'un dossier d'un jeu de données du Hub, avec leur SHA-256 publié (objet LFS)."""
    url = f"{endpoint}/api/datasets/{repo}/tree/{revision}/{folder}?recursive=1"
    files = []
    for entry in _json_get(url):
        if entry.get("type") != "file" or not str(entry.get("path", "")).endswith(".parquet"):
            continue
        lfs = entry.get("lfs") or {}
        sha = lfs.get("oid") or lfs.get("sha256")
        if not sha:
            raise ValueError(
                f"{repo}/{entry['path']} : empreinte SHA-256 non publiée — vérification impossible"
            )
        files.append(
            {
                "path": entry["path"],
                "size": int(lfs.get("size") or entry.get("size") or 0),
                "sha256": str(sha).lower(),
            }
        )
    return sorted(files, key=lambda f: f["path"])


def iter_hf_parquet(
    spec: SourceSpec, cache: Path, *, endpoint: str = HF_ENDPOINT
) -> Iterator[CorpusDocument]:
    """Jeu de données public du Hub Hugging Face (fichiers parquet, colonne `text`). `target` :
    `dépôt@révision:dossier`. Chaque fichier est vérifié contre le SHA-256 publié par le Hub AVANT d'être
    lu ; téléchargé seulement jusqu'à `max_bytes` de texte."""
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("pyarrow absent : `pip install pyarrow` (lecture des fichiers parquet)") from exc
    repo_rev, folder = spec.target.split(":", 1)
    repo, _, revision = repo_rev.partition("@")
    revision = revision or "main"
    cache.mkdir(parents=True, exist_ok=True)
    total = 0
    for entry in hf_dataset_files(repo, revision, folder, endpoint=endpoint):
        if spec.max_bytes is not None and total >= spec.max_bytes:
            return
        if not _match(entry["path"], spec.include) or _match(entry["path"], spec.exclude):
            continue
        local = cache / repo.replace("/", "__") / entry["path"]
        local.parent.mkdir(parents=True, exist_ok=True)
        if not local.is_file() or _sha256_path(local) != entry["sha256"]:
            _download(
                f"{endpoint}/datasets/{repo}/resolve/{revision}/{entry['path']}",
                local,
                entry["sha256"],
                timeout=1800,
            )
        table = parquet.ParquetFile(str(local))
        for batch in table.iter_batches(batch_size=1000, columns=["text"]):
            for row, text in enumerate(batch.column(0).to_pylist()):
                if not text:
                    continue
                total += len(text.encode("utf-8"))
                yield CorpusDocument(
                    tidy(text),
                    spec.name,
                    f"hf:{repo}@{revision}:{entry['path']}#{row}",
                    spec.license,
                    spec.lang,
                    spec.doc_kind,
                )
                if spec.max_bytes is not None and total >= spec.max_bytes:
                    return


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_corpus(
    specs: Iterable[SourceSpec], out_dir: Path, *, cache: Path, log: Callable[[str], None] = print
) -> dict[str, Any]:
    """Écrit `documents-<source>.jsonl.gz` et `manifest.json` dans `out_dir`. Une source en échec est
    rapportée (raison exacte), jamais remplacée par autre chose."""
    specs = list(specs)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    reports: list[SourceReport] = []
    archive: UbuntuArchive | None = None
    for spec in specs:
        report = SourceReport(spec.name)
        reports.append(report)
        try:
            if spec.kind == "local":
                documents: Iterator[CorpusDocument] = iter_tree(
                    spec, Path(spec.target).expanduser(), f"local:{spec.target}"
                )
            elif spec.kind in ("ubuntu", "ubuntu_mo"):
                archive = archive or UbuntuArchive(cache / "ubuntu")
                documents = (iter_ubuntu if spec.kind == "ubuntu" else iter_ubuntu_translations)(
                    spec, archive
                )
            elif spec.kind == "hf_parquet":
                documents = iter_hf_parquet(spec, cache / "hf")
            elif spec.kind == "pypi":
                documents = iter_pypi(spec, cache / "pypi")
            else:
                raise ValueError(f"type de source inconnu : {spec.kind}")
            path = out_dir / f"documents-{spec.name}.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                for document in (part for whole in documents for part in split_large(whole)):
                    reason = acceptable(
                        document.text, document.kind if document.kind != "translation" else "prose"
                    )
                    if reason is not None:
                        report.rejected[reason] = report.rejected.get(reason, 0) + 1
                        continue
                    document = document.with_hash()
                    if document.sha256 in seen:
                        report.duplicates += 1
                        continue
                    seen.add(document.sha256)
                    report.documents += 1
                    report.bytes += len(document.text.encode("utf-8"))
                    report.origin = report.origin or document.path.rsplit(":", 1)[0]
                    report.license = document.license
                    handle.write(json.dumps(asdict(document), ensure_ascii=False) + "\n")
            log(
                f"corpus {spec.name}: {report.documents} documents, {report.bytes / 1e6:.1f} Mo, "
                f"{report.duplicates} doublons, rejets {report.rejected}"
            )
        except Exception as exc:  # une source en échec n'arrête pas les autres, elle est rapportée
            report.error = f"{exc.__class__.__name__}: {str(exc)[:300]}"
            log(f"corpus {spec.name}: ÉCHEC {report.error}")
    manifest = {
        "format": 1,
        "sources": [asdict(r) for r in reports],
        "specs": [asdict(s) for s in specs],
        "documents": sum(r.documents for r in reports),
        "bytes": sum(r.bytes for r in reports),
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out_dir.glob("documents-*.jsonl.gz"))
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def iter_documents(corpus_dir: Path, sources: Iterable[str] | None = None) -> Iterator[CorpusDocument]:
    wanted = set(sources) if sources is not None else None
    for path in sorted(corpus_dir.glob("documents-*.jsonl.gz")):
        name = path.name[len("documents-") : -len(".jsonl.gz")]
        if wanted is not None and name not in wanted:
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                yield CorpusDocument(**json.loads(line))


def read_text_sample(corpus_dir: Path, budget_per_group: dict[str, int]) -> list[str]:
    """Échantillon équilibré pour entraîner le tokenizer : au plus `budget` octets par groupe
    (`lang`/`kind`)."""
    used: dict[str, int] = {}
    texts: list[str] = []
    for document in iter_documents(corpus_dir):
        group = group_of(document)
        budget = budget_per_group.get(group, 0)
        if used.get(group, 0) >= budget:
            continue
        texts.append(document.text)
        used[group] = used.get(group, 0) + len(document.text.encode("utf-8"))
    return texts


def group_of(document: CorpusDocument) -> str:
    """Groupe de mélange : `code`, `fr`, `en`, `translation`."""
    if document.kind == "code":
        return "code"
    if document.kind == "translation":
        return "translation"
    return "fr" if document.lang == "fr" else "en"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gzip_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


__all__ = [
    "CorpusDocument",
    "SourceSpec",
    "UbuntuArchive",
    "acceptable",
    "build_corpus",
    "group_of",
    "html_to_text",
    "info_to_text",
    "iter_documents",
    "man_to_text",
    "mo_pairs",
    "read_text_sample",
    "tidy",
]
