"""Modèle de domaine d'ArtifactStore — dataclasses pures + identité de
contenu (étape 10).

`ArtifactStore` = STOCKAGE, rien de plus. Ce module ne contient donc
volontairement AUCUNE logique d'exécution, d'analyse ou de confiance :
- ArtifactStore ≠ sandbox (aucune exécution — `apex.sandbox`).
- ArtifactStore ≠ scanner (aucune analyse de sécurité — futur `FileScanner`,
  voir `docs/UNTRUSTED_DATA_SECURITY.md`).
- ArtifactStore ≠ système de provenance (`apex.memory.model.MemoryRecord`
  porte déjà source/confiance/statut de vérification — `ArtifactMetadata`
  ne duplique RIEN de cela, elle porte seulement `memory_record_id`, le
  point d'association futur, nullable, jamais une copie de la provenance).

Identité par contenu : `sha256:<64 caractères hexadécimaux>`, format
canonique et strict. Le préfixe d'algorithme permet une évolution future
(`sha512:`, ...) sans ambiguïté — jamais un hash nu, qui ne dirait pas quel
algorithme l'a produit.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from apex.obs.capability import CapabilityStatus

_CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def compute_content_hash(data: bytes) -> str:
    """SHA-256 RÉEL du contenu — jamais un placeholder, jamais dérivé d'un
    nom de fichier ou d'une métadonnée fournie par l'appelant. C'est cette
    valeur, et uniquement elle, qui identifie le contenu (principe 5)."""
    digest = hashlib.sha256(data).hexdigest()
    return f"sha256:{digest}"


def is_valid_content_hash(value: str) -> bool:
    """Format canonique STRICT `sha256:<64 hex>`. Toute valeur qui ne
    correspond pas exactement est invalide — pas de tolérance de casse, de
    longueur ou de préfixe alternatif. C'est cette rigueur qui empêche un
    identifiant de contenu de devenir un chemin filesystem arbitraire (voir
    `apex.artifacts.local_filesystem` : le chemin est TOUJOURS dérivé des
    seuls caractères hexadécimaux d'un hash déjà validé ici, jamais d'une
    chaîne externe non vérifiée)."""
    return bool(_CONTENT_HASH_RE.match(value))


def content_hash_hex(content_hash: str) -> str:
    """Partie hexadécimale d'un hash déjà validé. Lève `ValueError` si le
    format n'est pas exactement `sha256:<64 hex>` — jamais une acceptation
    partielle ou une correction silencieuse d'un identifiant malformé."""
    if not is_valid_content_hash(content_hash):
        raise ValueError(f"identifiant de contenu invalide : {content_hash!r}")
    return content_hash.split(":", 1)[1]


@dataclass(frozen=True)
class ArtifactMetadata:
    """Faits INTRINSÈQUES d'un artefact stocké — jamais sa provenance
    épistémique (source, confiance, statut de vérification), qui reste
    entièrement la responsabilité de `apex.memory.model.MemoryRecord`.
    `memory_record_id` est le SEUL point d'association prévu ici : une
    référence nullable, jamais validée ni résolue par ce module (aucun
    couplage avec `apex.memory` dans cette étape), simplement le futur point
    d'accroche pour qu'un consommateur relie un artefact à sa provenance
    sans qu'ArtifactStore n'ait besoin de la connaître."""

    content_hash: str
    size_bytes: int
    created_at: datetime
    content_type: str | None = None
    memory_record_id: str | None = None


@dataclass(frozen=True)
class ArtifactStoreCapabilityReport:
    """Résultat d'une détection EMPIRIQUE (jamais supposée) — même
    discipline que `apex.sandbox.model.SandboxCapabilityReport`. `status`
    utilise `CapabilityStatus` (`apex.obs.capability`), jamais un autre
    vocabulaire fermé du dépôt."""

    status: CapabilityStatus
    backend: str
    detail: str
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE
