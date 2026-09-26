"""Découverte et acquisition des sources.

Règles d'accès, jamais contournées : un paywall, une authentification, un
DRM ou un contrôle d'accès (HTTP 401/402/403/451) est une réponse, pas un
obstacle à franchir ; robots.txt est respecté ; aucun identifiant n'est
envoyé ; la taille téléchargée est bornée."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import urllib.parse
import urllib.robotparser
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from apex.knowledge.model import SourceKind, SourceRef

USER_AGENT = "APEX-KnowledgeAgent/0.1 (+respecte robots.txt)"
_REFUSED_STATUSES = {
    401: "authentification requise",
    402: "paiement requis",
    403: "accès refusé",
    451: "indisponible pour raisons légales",
}


@dataclass(frozen=True)
class AcquisitionResult:
    ref: SourceRef
    ok: bool
    data: bytes | None
    content_type: str | None
    detail: str

    @property
    def source_hash(self) -> str | None:
        return "sha256:" + hashlib.sha256(self.data).hexdigest() if self.data is not None else None


class Discovery(Protocol):
    def discover(self) -> tuple[SourceRef, ...]: ...


class Acquirer(Protocol):
    def supports(self, ref: SourceRef) -> bool: ...

    def acquire(self, ref: SourceRef) -> AcquisitionResult: ...


@dataclass(frozen=True)
class StaticDiscovery:
    refs: tuple[SourceRef, ...]

    def discover(self) -> tuple[SourceRef, ...]:
        return self.refs


@dataclass(frozen=True)
class LocalDirectoryDiscovery:
    root: Path
    patterns: tuple[str, ...]
    kind: SourceKind
    license_hint: str | None
    publisher: str | None = None

    def discover(self) -> tuple[SourceRef, ...]:
        files = sorted({p for pattern in self.patterns for p in self.root.glob(pattern) if p.is_file()})
        return tuple(
            SourceRef(
                locator=f"file://{p.resolve()}",
                kind=self.kind,
                title=p.stem,
                publisher=self.publisher,
                license_hint=self.license_hint,
                discovered_by="local-directory",
            )
            for p in files
        )


@dataclass(frozen=True)
class PythonDocsDiscovery:
    """Documentation de la bibliothèque standard Python installée (licence PSF)."""

    modules: tuple[str, ...]

    def discover(self) -> tuple[SourceRef, ...]:
        return tuple(
            SourceRef(
                locator=f"pydoc:{name}",
                kind=SourceKind.DOCUMENTATION,
                title=f"Python — module {name}",
                publisher="Python Software Foundation",
                license_hint="PSF-2.0",
                discovered_by="python-docs",
            )
            for name in self.modules
        )


@dataclass(frozen=True)
class ResearchDiscovery:
    """Réutilise le contrat existant `apex.design.research.ResearchProvider`.
    Sans backend réel (état actuel du dépôt), il ne découvre rien."""

    provider: object
    queries: tuple[str, ...]
    max_results: int = 5

    def discover(self) -> tuple[SourceRef, ...]:
        refs: list[SourceRef] = []
        for query in self.queries:
            for result in self.provider.search(query, max_results=self.max_results):  # type: ignore[attr-defined]
                if result.url:
                    refs.append(
                        SourceRef(
                            locator=result.url,
                            kind=SourceKind.WEB_PAGE,
                            title=result.title,
                            discovered_by=f"research:{result.provenance}",
                        )
                    )
        return tuple(refs)


def discover_all(sources: Iterable[Discovery]) -> tuple[SourceRef, ...]:
    return tuple(ref for source in sources for ref in source.discover())


class FileAcquirer:
    def __init__(self, *, max_bytes: int = 2_000_000) -> None:
        self.max_bytes = max_bytes

    def supports(self, ref: SourceRef) -> bool:
        return ref.locator.startswith("file://")

    def acquire(self, ref: SourceRef) -> AcquisitionResult:
        path = Path(urllib.parse.urlparse(ref.locator).path)
        if not path.is_file():
            return AcquisitionResult(ref, False, None, None, "fichier introuvable")
        if path.stat().st_size > self.max_bytes:
            return AcquisitionResult(ref, False, None, None, f"fichier > {self.max_bytes} octets")
        suffix = path.suffix.lower()
        content_type = {
            ".md": "text/markdown",
            ".html": "text/html",
            ".htm": "text/html",
            ".py": "text/x-python",
            ".json": "application/json",
            ".txt": "text/plain",
            ".rst": "text/plain",
            ".png": "image/png",
            ".gif": "image/gif",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(suffix, "application/octet-stream")
        return AcquisitionResult(ref, True, path.read_bytes(), content_type, "lecture locale")


class PythonDocsAcquirer:
    """Docstrings réelles du module et de ses membres publics documentés."""

    def supports(self, ref: SourceRef) -> bool:
        return ref.locator.startswith("pydoc:")

    def acquire(self, ref: SourceRef) -> AcquisitionResult:
        name = ref.locator.removeprefix("pydoc:")
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            return AcquisitionResult(ref, False, None, None, f"module introuvable : {exc}")
        parts = [f"# {name}\n\n{inspect.getdoc(module) or ''}"]
        for member_name, member in sorted(vars(module).items()):
            if member_name.startswith("_") or getattr(member, "__module__", None) != name:
                continue
            doc = inspect.getdoc(member)
            if doc and (inspect.isfunction(member) or inspect.isclass(member)):
                parts.append(f"## {name}.{member_name}\n\n{doc}")
        text = "\n\n".join(p for p in parts if p.strip())
        return AcquisitionResult(
            ref, True, text.encode("utf-8"), "text/markdown", "docstrings de la bibliothèque standard"
        )


class HttpAcquirer:
    def __init__(
        self, *, timeout_s: float = 15.0, max_bytes: int = 2_000_000, respect_robots: bool = True
    ) -> None:
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.respect_robots = respect_robots

    def supports(self, ref: SourceRef) -> bool:
        return ref.locator.startswith(("http://", "https://"))

    def _robots_allow(self, url: str) -> tuple[bool, str]:
        import httpx

        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            response = httpx.get(robots_url, timeout=self.timeout_s, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            return (
                False,
                f"robots.txt illisible ({exc.__class__.__name__}) — acquisition refusée par prudence",
            )
        if response.status_code >= 400:
            return True, f"pas de robots.txt (HTTP {response.status_code})"
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        allowed = parser.can_fetch(USER_AGENT, url)
        return allowed, "autorisé par robots.txt" if allowed else "interdit par robots.txt"

    def acquire(self, ref: SourceRef) -> AcquisitionResult:
        import httpx

        if self.respect_robots:
            allowed, detail = self._robots_allow(ref.locator)
            if not allowed:
                return AcquisitionResult(ref, False, None, None, detail)
        try:
            with httpx.stream(
                "GET",
                ref.locator,
                timeout=self.timeout_s,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as response:
                if response.status_code in _REFUSED_STATUSES:
                    reason = _REFUSED_STATUSES[response.status_code]
                    return AcquisitionResult(
                        ref, False, None, None, f"HTTP {response.status_code} : {reason} — jamais contourné"
                    )
                if response.status_code >= 400:
                    return AcquisitionResult(ref, False, None, None, f"HTTP {response.status_code}")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        return AcquisitionResult(
                            ref, False, None, None, f"contenu > {self.max_bytes} octets"
                        )
                    chunks.append(chunk)
                content_type = response.headers.get("content-type", "").split(";")[0].strip() or None
                return AcquisitionResult(
                    ref, True, b"".join(chunks), content_type, f"HTTP {response.status_code}"
                )
        except httpx.HTTPError as exc:
            return AcquisitionResult(ref, False, None, None, f"échec réseau : {exc.__class__.__name__}")


def acquire(ref: SourceRef, acquirers: Iterable[Acquirer]) -> AcquisitionResult:
    for acquirer in acquirers:
        if acquirer.supports(ref):
            return acquirer.acquire(ref)
    return AcquisitionResult(ref, False, None, None, "aucun acquéreur pour ce type de source")
