"""Contrat `ArtifactStoreProvider` — le seul point que connaîtront les
futurs consommateurs (`FileScanner`, `MemoryProvider`, une future ingestion
Web). Ajouter un backend (MinIO, S3, ...) ne demande de modifier AUCUN
consommateur : seulement d'implémenter cette interface et de la brancher
dans `get_default_provider()` — même patron que `apex.sandbox.provider`
(étape 7) et `apex.scheduler.service` (étape 8).

Aucun couplage avec `apex.scheduler`, `apex.sandbox`, `apex.agents`,
`apex.missions`, `apex.memory` dans cette étape — volontairement (consigne
étape 10, périmètre strict).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from apex.artifacts.model import ArtifactMetadata, ArtifactStoreCapabilityReport


class ArtifactStoreProvider(ABC):
    @abstractmethod
    def detect(self) -> ArtifactStoreCapabilityReport:
        """Sonde (empiriquement, jamais supposée) la disponibilité réelle de
        ce backend. Ne lève jamais."""

    @abstractmethod
    def put(
        self, data: bytes, *, content_type: str | None = None, memory_record_id: str | None = None,
    ) -> ArtifactMetadata:
        """Stocke `data`, identifié par son SHA-256. Contenu identique ->
        même identifiant (déduplication naturelle de l'adressage par
        contenu). N'exécute ni n'interprète jamais `data` — un stockage
        pur, jamais un chemin d'exécution."""

    @abstractmethod
    def get(self, content_hash: str) -> bytes:
        """Lève `ValueError` si `content_hash` est mal formé, `LookupError`
        si absent, `apex.errors.ArtifactIntegrityError` si le contenu relu
        ne correspond plus au hash demandé — la vérification d'intégrité
        est systématique à la lecture, jamais optionnelle."""

    @abstractmethod
    def get_metadata(self, content_hash: str) -> ArtifactMetadata | None:
        """`None` si l'artefact n'existe pas — jamais une exception pour une
        simple absence (distinct de `get`, qui lève : demander le contenu
        d'un artefact absent est une erreur de l'appelant, demander ses
        métadonnées pour vérifier son existence ne l'est pas)."""

    @abstractmethod
    def exists(self, content_hash: str) -> bool:
        """Lève `ValueError` si `content_hash` est mal formé — un
        identifiant invalide n'est jamais silencieusement traité comme
        « n'existe pas »."""


def get_default_provider() -> ArtifactStoreProvider:
    """Seul point de construction utilisé par de futurs consommateurs —
    c'est ICI, et seulement ici, qu'un backend MinIO/S3 serait choisi une
    fois réellement disponible et implémenté. Aujourd'hui : le backend local
    (`LocalFilesystemArtifactStore`), le seul réellement fonctionnel."""
    from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore

    return LocalFilesystemArtifactStore()
