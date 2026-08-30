"""`LocalFilesystemArtifactStore` — le seul backend `ArtifactStoreProvider`
réellement implémenté aujourd'hui (étape 10), fonctionnel sur DEV_MACHINE
sans dépendance externe (ni MinIO, ni Docker).

**Sécurité (consigne étape 10)** — ArtifactStore = stockage, jamais un
mécanisme d'exécution :
- ce module n'exécute JAMAIS ce qu'il stocke, ne l'interprète jamais comme
  du code ;
- il ne fait JAMAIS confiance à un nom de fichier ou un identifiant fourni
  par un appelant pour construire un chemin filesystem — le chemin est
  TOUJOURS dérivé des seuls caractères hexadécimaux d'un hash déjà validé
  par un motif strict (`apex.artifacts.model.is_valid_content_hash`), jamais
  d'une chaîne arbitraire. C'est ce qui rend un path traversal
  STRUCTURELLEMENT impossible, pas seulement filtré au cas par cas ;
- l'intégrité du contenu est reconstatée (SHA-256 recalculé) à CHAQUE
  lecture, jamais supposée depuis le seul emplacement du fichier ;
- chaque écriture est atomique (fichier temporaire + `os.replace`, atomique
  sur POSIX) : aucun fichier partiellement écrit ne devient jamais visible
  sous son nom final.

**Répertoire créé PARESSEUSEMENT** : `__init__` ne touche jamais le
filesystem. `detect()` sonde sans créer. Seul `put()` crée l'arborescence,
et uniquement au moment d'un stockage réel.

**Limitation assumée, documentée explicitement (consigne étape 10)** :
aucun quota n'est appliqué ni ici ni ailleurs dans ce module — ni sur la
taille d'un artefact, ni sur l'espace total utilisé. Ce n'est pas un oubli :
c'est une CAPACITÉ FUTURE, volontairement non construite maintenant (un
faux quota calé sur l'espace disque de DEV_MACHINE serait exactement
l'erreur que ce projet interdit — confondre une limite de la machine de
développement avec une limite architecturale).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from apex.artifacts.model import (
    ArtifactMetadata,
    ArtifactStoreCapabilityReport,
    compute_content_hash,
    content_hash_hex,
)
from apex.artifacts.provider import ArtifactStoreProvider
from apex.errors import ArtifactIntegrityError
from apex.obs.capability import CapabilityStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalFilesystemArtifactStore(ArtifactStoreProvider):
    def __init__(self, *, root: str | None = None) -> None:
        # AUCUN accès filesystem ici — voir la docstring du module.
        self._root = root

    def _resolve_root(self) -> str:
        if self._root is not None:
            return self._root
        from apex.config.loader import load_config

        self._root = load_config().paths.artifacts
        return self._root

    def _object_dir(self, hex_digest: str) -> str:
        # hex_digest provient TOUJOURS de content_hash_hex() (déjà validé) —
        # jamais d'une chaîne externe non vérifiée.
        return os.path.join(self._resolve_root(), "objects", "sha256", hex_digest[:2])

    def _content_path(self, hex_digest: str) -> str:
        return os.path.join(self._object_dir(hex_digest), hex_digest)

    def _metadata_path(self, hex_digest: str) -> str:
        return os.path.join(self._object_dir(hex_digest), f"{hex_digest}.meta.json")

    # -- détection ------------------------------------------------------

    def detect(self) -> ArtifactStoreCapabilityReport:
        root = self._resolve_root()
        if os.path.isdir(root):
            writable = os.access(root, os.W_OK)
            if writable:
                return ArtifactStoreCapabilityReport(
                    status=CapabilityStatus.AVAILABLE, backend="local_filesystem",
                    detail=f"répertoire « {root} » existe et est inscriptible",
                    facts={"root": root, "existed": True, "writable": True},
                )
            return ArtifactStoreCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="local_filesystem",
                detail=(
                    f"répertoire « {root} » existe mais n'est pas inscriptible sur cette "
                    "machine — jamais interprété comme une limite architecturale"
                ),
                facts={"root": root, "existed": True, "writable": False},
            )
        parent = os.path.dirname(root.rstrip("/")) or "/"
        if os.path.isdir(parent) and os.access(parent, os.W_OK):
            return ArtifactStoreCapabilityReport(
                status=CapabilityStatus.AVAILABLE, backend="local_filesystem",
                detail=f"répertoire « {root} » pas encore créé — sera créé au premier stockage réel",
                facts={"root": root, "existed": False, "parent_writable": True},
            )
        return ArtifactStoreCapabilityReport(
            status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="local_filesystem",
            detail=(
                f"ni « {root} » ni son répertoire parent ne sont accessibles en écriture "
                "sur cette machine — UNAVAILABLE_ON_DEV, jamais UNSUPPORTED"
            ),
            facts={"root": root, "existed": False, "parent_writable": False},
        )

    # -- écriture ---------------------------------------------------------

    def put(
        self, data: bytes, *, content_type: str | None = None, memory_record_id: str | None = None,
    ) -> ArtifactMetadata:
        content_hash = compute_content_hash(data)
        hex_digest = content_hash_hex(content_hash)
        object_dir = self._object_dir(hex_digest)
        os.makedirs(object_dir, exist_ok=True)  # création paresseuse — jamais avant ce point

        content_path = self._content_path(hex_digest)
        if not os.path.isfile(content_path):
            self._atomic_write(content_path, data)
        # Sinon : contenu déjà présent (déduplication par adressage de
        # contenu) — pas de réécriture inutile, le hash garantit déjà
        # l'identité.

        metadata = ArtifactMetadata(
            content_hash=content_hash, size_bytes=len(data), created_at=_utcnow(),
            content_type=content_type, memory_record_id=memory_record_id,
        )
        self._write_metadata(hex_digest, metadata)
        return metadata

    def _atomic_write(self, path: str, data: bytes) -> None:
        directory = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)  # atomique sur POSIX
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def _write_metadata(self, hex_digest: str, metadata: ArtifactMetadata) -> None:
        payload = {
            "content_hash": metadata.content_hash,
            "size_bytes": metadata.size_bytes,
            "created_at": metadata.created_at.isoformat(),
            "content_type": metadata.content_type,
            "memory_record_id": metadata.memory_record_id,
        }
        self._atomic_write(self._metadata_path(hex_digest), json.dumps(payload).encode("utf-8"))

    # -- lecture ------------------------------------------------------------

    def get(self, content_hash: str) -> bytes:
        hex_digest = content_hash_hex(content_hash)  # lève ValueError si mal formé
        path = self._content_path(hex_digest)
        if not os.path.isfile(path):
            raise LookupError(f"artefact « {content_hash} » introuvable")
        with open(path, "rb") as fh:
            data = fh.read()
        # Vérification d'intégrité SYSTÉMATIQUE — jamais une confiance dans
        # le seul emplacement du fichier pour garantir son contenu.
        actual = compute_content_hash(data)
        if actual != content_hash:
            raise ArtifactIntegrityError(content_hash, actual=actual)
        return data

    def get_metadata(self, content_hash: str) -> ArtifactMetadata | None:
        hex_digest = content_hash_hex(content_hash)
        path = self._metadata_path(hex_digest)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        return ArtifactMetadata(
            content_hash=payload["content_hash"],
            size_bytes=payload["size_bytes"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            content_type=payload.get("content_type"),
            memory_record_id=payload.get("memory_record_id"),
        )

    def exists(self, content_hash: str) -> bool:
        hex_digest = content_hash_hex(content_hash)  # lève ValueError si mal formé
        return os.path.isfile(self._content_path(hex_digest))
