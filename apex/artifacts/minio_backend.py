"""`MinioArtifactStore` — backend PLANNED, architecturé mais jamais prétendu
opérationnel tant que MinIO n'est pas réellement joignable (consigne étape
10 : « ne modifie pas l'architecture pour contourner l'absence de MinIO »,
« ne prétends jamais avoir validé MinIO si le service n'est pas
disponible »).

`detect()` sonde une VRAIE connexion TCP — même mécanisme empirique que
`apex.doctor.checks.services.check_minio` — le seul fait vérifiable sans
bibliothèque cliente S3. Les opérations de stockage elles-mêmes lèvent
`NotImplementedYet` : les écrire maintenant, sans jamais avoir pu les tester
contre un vrai serveur MinIO, serait exactement l'erreur qu'interdit
ADR-0007 — une capacité qui a l'air d'exister sans l'être.

Sur DEV_MACHINE (aucun démon MinIO joignable ici, vérifié empiriquement
lors de l'audit de l'étape 10) : `UNAVAILABLE_ON_DEV`, jamais `UNSUPPORTED`.
"""

from __future__ import annotations

import socket

from apex.artifacts.model import ArtifactMetadata, ArtifactStoreCapabilityReport
from apex.artifacts.provider import ArtifactStoreProvider
from apex.errors import NotImplementedYet
from apex.obs.capability import CapabilityStatus


class MinioArtifactStore(ArtifactStoreProvider):
    def __init__(self, *, host: str = "127.0.0.1", port: int = 9000, connect_timeout_s: float = 1.5) -> None:
        self._host = host
        self._port = port
        self._connect_timeout_s = connect_timeout_s

    def _tcp_reachable(self) -> bool:
        try:
            with socket.create_connection((self._host, self._port), timeout=self._connect_timeout_s):
                return True
        except OSError:
            return False

    def detect(self) -> ArtifactStoreCapabilityReport:
        reachable = self._tcp_reachable()
        facts = {"host": self._host, "port": self._port, "reachable": reachable}
        if reachable:
            return ArtifactStoreCapabilityReport(
                status=CapabilityStatus.AVAILABLE, backend="minio",
                detail=(
                    f"MinIO joignable sur {self._host}:{self._port} (sonde TCP réelle) — "
                    "les opérations de stockage restent PLANNED (NotImplementedYet) dans "
                    "cette étape : aucune bibliothèque cliente S3 n'a été intégrée ni testée"
                ),
                facts=facts,
            )
        return ArtifactStoreCapabilityReport(
            status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="minio",
            detail=(
                f"MinIO injoignable sur {self._host}:{self._port} sur cette machine — "
                "backend architecturé pour TARGET_MACHINE, jamais interprété comme UNSUPPORTED"
            ),
            facts=facts,
        )

    def put(
        self, data: bytes, *, content_type: str | None = None, memory_record_id: str | None = None,
    ) -> ArtifactMetadata:
        raise NotImplementedYet(
            "MinioArtifactStore.put", phase=10, fallback="LocalFilesystemArtifactStore",
        )

    def get(self, content_hash: str) -> bytes:
        raise NotImplementedYet(
            "MinioArtifactStore.get", phase=10, fallback="LocalFilesystemArtifactStore",
        )

    def get_metadata(self, content_hash: str) -> ArtifactMetadata | None:
        raise NotImplementedYet(
            "MinioArtifactStore.get_metadata", phase=10, fallback="LocalFilesystemArtifactStore",
        )

    def exists(self, content_hash: str) -> bool:
        raise NotImplementedYet(
            "MinioArtifactStore.exists", phase=10, fallback="LocalFilesystemArtifactStore",
        )
