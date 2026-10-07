"""Espace de travail d'une tâche : un répertoire hôte dédié, seule zone de fichiers accessible aux outils.

Garanties (fail-closed, chacune testée adversarialement) :
- un chemin d'outil est TOUJOURS relatif à la racine : absolu, `..`, caractère nul, antislash, `~` refusés ;
- aucun lien symbolique n'est suivi ni créé : un composant symbolique refuse l'accès, et les fichiers sont
  ouverts avec `O_NOFOLLOW` (pas de substitution entre la vérification et l'ouverture) ;
- seuls des fichiers réguliers et des répertoires sont manipulés (ni périphérique, ni tube) ;
- tailles bornées : par fichier, au total, en nombre de fichiers ;
- l'exécution de code ne touche JAMAIS ce répertoire : la sandbox reçoit une COPIE (`snapshot`).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from apex.tools.model import ErrorCode, ToolFailure

_SKIPPED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
#: Fichiers nommés au plus dans l'observation d'un chemin absent.
_ABSENT_HINT_FILES = 20


@dataclass(frozen=True)
class WorkspaceLimits:
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 20_000_000
    max_files: int = 2_000
    max_path_chars: int = 512


class Workspace:
    def __init__(self, root: Path | str, *, workspace_id: str, limits: WorkspaceLimits | None = None) -> None:
        real = Path(os.path.realpath(root))
        if not real.is_dir():
            raise ValueError(f"racine d'espace de travail introuvable : {root}")
        if real == Path("/") or len(real.parts) < 3:
            raise ValueError(f"racine d'espace de travail refusée (trop haute dans l'arborescence) : {real}")
        self.root = real
        self.id = workspace_id
        self.limits = limits or WorkspaceLimits()

    @classmethod
    def create(cls, base_dir: Path | str, workspace_id: str, *, limits: WorkspaceLimits | None = None) -> Workspace:
        if not workspace_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"identifiant d'espace de travail refusé : {workspace_id!r}")
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = base / workspace_id
        root.mkdir(mode=0o700, exist_ok=True)
        if root.is_symlink():
            raise ValueError("la racine d'espace de travail ne peut pas être un lien symbolique")
        return cls(root, workspace_id=workspace_id, limits=limits)

    # -- résolution sûre ---------------------------------------------------------------------------------

    def resolve(self, rel: str, *, allow_root: bool = False) -> Path:
        """Chemin hôte d'un chemin RELATIF de l'espace — ou `ToolFailure(PATH_REFUSED)`."""
        if not isinstance(rel, str):
            raise ToolFailure(ErrorCode.PATH_REFUSED, "chemin non textuel")
        if rel in ("", ".") and allow_root:
            return self.root
        if (not rel or "\x00" in rel or "\\" in rel or rel.startswith(("/", "~"))
                or len(rel) > self.limits.max_path_chars):
            raise ToolFailure(ErrorCode.PATH_REFUSED, f"chemin refusé : {rel!r} (relatif à l'espace de travail)")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or len(p) > 255 for p in parts):
            raise ToolFailure(ErrorCode.PATH_REFUSED, f"chemin refusé : {rel!r} (aucune remontée « .. »)")
        current = self.root
        for part in parts:
            current = current / part
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode):
                raise ToolFailure(ErrorCode.PATH_REFUSED, f"lien symbolique refusé : {rel!r}")
        real = Path(os.path.realpath(current))
        if real != self.root and self.root not in real.parents:
            raise ToolFailure(ErrorCode.PATH_REFUSED, f"chemin hors de l'espace de travail : {rel!r}")
        return current

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # -- opérations ----------------------------------------------------------------------------------

    def read_bytes(self, rel: str) -> bytes:
        path = self.resolve(rel)
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError as exc:
            raise ToolFailure(ErrorCode.NOT_FOUND, self._absent(rel)) from exc
        if not stat.S_ISREG(mode):  # tube nommé, périphérique, socket : jamais ouverts (un FIFO bloquerait)
            raise ToolFailure(ErrorCode.PATH_REFUSED, f"pas un fichier régulier : {rel}")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError as exc:
            raise ToolFailure(ErrorCode.NOT_FOUND, self._absent(rel)) from exc
        except OSError as exc:
            raise ToolFailure(ErrorCode.PATH_REFUSED, f"lecture refusée : {rel} ({exc.strerror})") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ToolFailure(ErrorCode.PATH_REFUSED, f"pas un fichier régulier : {rel}")
            if info.st_size > self.limits.max_file_bytes:
                raise ToolFailure(ErrorCode.TOO_LARGE, f"fichier trop gros : {rel} ({info.st_size} octets)")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                return handle.read(self.limits.max_file_bytes + 1)
        finally:
            os.close(fd)

    def _absent(self, rel: str) -> str:
        """Mesuré avec un vrai modèle (validation `real-3`, projet `fix_mean`) : 10 lectures d'un chemin
        inexistant d'affilée, faute de savoir ce qui existe. L'observation nomme donc les fichiers RÉELLEMENT
        présents (contenu de l'espace de travail seulement, borné), jamais rien d'extérieur."""
        present = [name for name, _size in self.files()]
        shown = ", ".join(present[:_ABSENT_HINT_FILES]) or "aucun"
        more = f" (+{len(present) - _ABSENT_HINT_FILES})" if len(present) > _ABSENT_HINT_FILES else ""
        return f"fichier absent : {rel} — fichiers présents : {shown}{more}"

    def write_bytes(self, rel: str, content: bytes) -> bool:
        """Écrit (crée ou remplace) un fichier. Retourne True s'il a été créé."""
        if len(content) > self.limits.max_file_bytes:
            raise ToolFailure(ErrorCode.TOO_LARGE, f"contenu trop gros ({len(content)} octets)")
        path = self.resolve(rel)
        existed = path.exists()
        if existed and not path.is_file():
            raise ToolFailure(ErrorCode.PATH_REFUSED, f"pas un fichier régulier : {rel}")
        files, total = self._usage()
        previous = path.stat().st_size if existed else 0
        if not existed and files + 1 > self.limits.max_files:
            raise ToolFailure(ErrorCode.TOO_LARGE, f"trop de fichiers (> {self.limits.max_files})")
        if total - previous + len(content) > self.limits.max_total_bytes:
            raise ToolFailure(ErrorCode.TOO_LARGE, "espace de travail plein")
        parent = path.parent
        if parent != self.root:
            self.resolve(self.relative(parent))
        parent.mkdir(parents=True, exist_ok=True)
        self.resolve(rel)  # re-vérifie après création des répertoires (aucun lien introduit entre-temps)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)
        return not existed

    def list_dir(self, rel: str = ".") -> list[dict[str, object]]:
        path = self.resolve(rel, allow_root=True)
        if not path.exists():
            raise ToolFailure(ErrorCode.NOT_FOUND, f"répertoire absent : {rel}")
        if path.is_symlink() or not path.is_dir():
            raise ToolFailure(ErrorCode.PATH_REFUSED, f"pas un répertoire : {rel}")
        entries = []
        with os.scandir(path) as scan:
            for entry in sorted(scan, key=lambda e: e.name):
                if entry.is_symlink():
                    kind, size = "symlink", None
                elif entry.is_dir(follow_symlinks=False):
                    kind, size = "dir", None
                elif entry.is_file(follow_symlinks=False):
                    kind, size = "file", entry.stat(follow_symlinks=False).st_size
                else:
                    kind, size = "other", None
                entries.append({"name": entry.name, "type": kind, "size": size})
        return entries

    def files(self) -> list[tuple[str, int]]:
        """Fichiers RÉGULIERS (chemin relatif, taille), sans suivre aucun lien, caches exclus."""
        out: list[tuple[str, int]] = []
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIPPED_DIRS
                                 and not os.path.islink(os.path.join(dirpath, d)))
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                info = os.lstat(full)
                if stat.S_ISREG(info.st_mode):
                    out.append((Path(full).relative_to(self.root).as_posix(), info.st_size))
        return out

    def snapshot(self) -> tuple[tuple[str, bytes], ...]:
        """Copie de l'espace pour la sandbox (fichiers réguliers seulement), bornée par les limites."""
        files = self.files()
        if len(files) > self.limits.max_files:
            raise ToolFailure(ErrorCode.TOO_LARGE, f"trop de fichiers pour la sandbox ({len(files)})")
        if sum(size for _, size in files) > self.limits.max_total_bytes:
            raise ToolFailure(ErrorCode.TOO_LARGE, "espace de travail trop volumineux pour la sandbox")
        return tuple((rel, self.read_bytes(rel)) for rel, _ in files)

    def _usage(self) -> tuple[int, int]:
        files = self.files()
        return len(files), sum(size for _, size in files)
