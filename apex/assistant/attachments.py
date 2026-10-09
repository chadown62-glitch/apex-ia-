"""Pièces jointes d'un projet — seulement ce qu'APEX lit RÉELLEMENT : des fichiers TEXTE (code, notes, données
CSV/JSON…), décodés en UTF-8, bornés en taille. Un format non pris en charge (image, PDF, bureautique, binaire)
est refusé avec la raison, jamais accepté puis ignoré. Les fichiers de secrets connus (.env, clés) sont refusés.

Le contenu est joint aux tours des conversations du projet dans un budget de caractères (les plus récents
d'abord, tronqués et signalés), et déposé dans l'espace de travail du projet (`pieces-jointes/`) pour que le mode
Code puisse le lire avec ses outils."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

MAX_BYTES = 512 * 1024
MAX_PER_PROJECT = 30
CONTEXT_BUDGET_CHARS = 16_000
WORKSPACE_DIR = "pieces-jointes"

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".xml", ".html", ".htm", ".css", ".scss", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".py", ".ipynb",
    ".sql", ".sh", ".ps1", ".bat", ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".kt", ".go", ".rs", ".rb",
    ".php", ".swift", ".r", ".lua", ".log",
}
#: Jamais acceptés, même texte : secrets probables.
REFUSED_NAMES = {".env", "id_rsa", "id_ed25519", ".npmrc", ".pypirc"}
REFUSED_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".kdbx"}


class AttachmentRefused(ValueError):
    pass


@dataclass(frozen=True)
class Attachment:
    id: str
    project_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "project_id": self.project_id, "filename": self.filename,
                "media_type": self.media_type, "size_bytes": self.size_bytes, "sha256": self.sha256,
                "created_at": self.created_at.isoformat()}


def validate(filename: str, raw: bytes) -> tuple[str, str]:
    """(nom sûr, texte) ou `AttachmentRefused` avec la raison."""
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."} or len(name) > 200 or any(ord(c) < 32 for c in name):
        raise AttachmentRefused("nom de fichier invalide")
    lower = name.lower()
    suffix = PurePosixPath(lower).suffix
    if lower in REFUSED_NAMES or suffix in REFUSED_EXTENSIONS or lower.startswith(".env"):
        raise AttachmentRefused(f"« {name} » ressemble à un fichier de secrets : refusé")
    if suffix not in TEXT_EXTENSIONS:
        raise AttachmentRefused(
            f"format « {suffix or 'sans extension'} » non pris en charge : APEX ne lit que des fichiers texte "
            "(code, notes, CSV, JSON…), pas les images, PDF ou documents bureautiques")
    if len(raw) > MAX_BYTES:
        raise AttachmentRefused(f"« {name} » dépasse {MAX_BYTES // 1024} Ko")
    if b"\x00" in raw:
        raise AttachmentRefused(f"« {name} » contient des données binaires")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentRefused(f"« {name} » n'est pas un texte UTF-8") from exc
    return name, text


class AttachmentService:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def add(self, project_id: str, filename: str, raw: bytes) -> Attachment:
        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectAttachmentRepository, ProjectRepository

        name, text = validate(filename, raw)
        with session_scope(self._session_factory) as session:
            if ProjectRepository(session).get(project_id) is None:
                raise LookupError(f"projet « {project_id} » introuvable")
            repo = ProjectAttachmentRepository(session)
            existing = repo.list_by_project(project_id)
            if len(existing) >= MAX_PER_PROJECT:
                raise AttachmentRefused(f"limite de {MAX_PER_PROJECT} pièces jointes par projet atteinte")
            if any(row.filename == name for row in existing):
                raise AttachmentRefused(f"« {name} » est déjà joint à ce projet (supprimez-le d'abord)")
            row = repo.create(id=str(uuid.uuid4()), project_id=project_id, filename=name,
                              media_type="text/plain; charset=utf-8", size_bytes=len(raw),
                              sha256=hashlib.sha256(raw).hexdigest(), content=text, created_at=datetime.now(UTC))
            attachment = _from_row(row)
        from apex.assistant.workspace import project_workspace

        project_workspace(project_id).write_bytes(f"{WORKSPACE_DIR}/{name}", text.encode("utf-8"))
        return attachment

    def list_all(self, project_id: str) -> list[Attachment]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectAttachmentRepository

        with session_scope(self._session_factory) as session:
            return [_from_row(r) for r in ProjectAttachmentRepository(session).list_by_project(project_id)]

    def delete(self, attachment_id: str) -> bool:
        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectAttachmentRepository

        with session_scope(self._session_factory) as session:
            repo = ProjectAttachmentRepository(session)
            row = repo.get(attachment_id)
            if row is None:
                return False
            project_id, name = row.project_id, row.filename
            repo.delete(attachment_id)
        from apex.assistant.workspace import project_workspace

        target = project_workspace(project_id).resolve(f"{WORKSPACE_DIR}/{name}")
        target.unlink(missing_ok=True)
        return True

    def render_context(self, project_id: str, budget: int = CONTEXT_BUDGET_CHARS) -> tuple[str, list[str]]:
        """Texte joint au prompt (les pièces les plus récentes d'abord, dans le budget) et noms réellement
        joints ; une pièce tronquée est signalée comme telle."""
        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectAttachmentRepository

        with session_scope(self._session_factory) as session:
            rows = [(r.filename, r.content) for r in ProjectAttachmentRepository(session).list_by_project(project_id)]
        parts: list[str] = []
        used: list[str] = []
        remaining = budget
        for filename, content in reversed(rows):
            if remaining < 400:
                break
            text = content if len(content) <= remaining - 200 else content[: remaining - 200] + "\n[… tronqué]"
            parts.append(f"--- pièce jointe : {filename} ---\n{text}")
            used.append(filename)
            remaining -= len(text) + 60
        if not parts:
            return "", []
        return "Pièces jointes du projet (contenu réel des fichiers) :\n" + "\n\n".join(reversed(parts)), used


def _from_row(row: Any) -> Attachment:
    return Attachment(id=row.id, project_id=row.project_id, filename=row.filename, media_type=row.media_type,
                      size_bytes=row.size_bytes, sha256=row.sha256, created_at=row.created_at)
