"""Modèle du Hardware & Environment Gate.

Décision centrale de ce module : **le statut SIMULATED n'est pas déclaratif.**
Il est dérivé de la provenance de la sonde. Une vérification exécutée avec
autre chose qu'une `SystemProbe` réelle ne peut pas produire PASS — quel que
soit son résultat. Il devient donc impossible de considérer une vérification
comme valide parce qu'elle passe dans l'environnement de développement : c'est
mécanique, pas une discipline.

Symétriquement, NOT_TESTED est réservé aux vérifications qui exigent une action
que le Gate ne s'autorise pas par défaut (démarrer un conteneur, tirer une
image). Le Gate reste en lecture seule sauf consentement explicite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"
    SIMULATED = "SIMULATED"

    @property
    def is_blocking(self) -> bool:
        return self is GateStatus.FAIL

    @property
    def is_conclusive(self) -> bool:
        """Un résultat non concluant ne peut pas servir de base à une phase."""
        return self in (GateStatus.PASS, GateStatus.FAIL, GateStatus.DEGRADED)


@dataclass(frozen=True)
class Evidence:
    """Trace de ce qui a réellement été observé.

    Un rapport d'environnement sans preuve est invérifiable. On conserve la
    commande et un extrait borné de sa sortie : assez pour rejouer et
    contredire, pas assez pour transformer le rapport en journal.
    """

    source: str                 # commande, fichier ou sonde
    raw: str = ""
    truncated: bool = False

    @classmethod
    def from_output(cls, source: str, output: str, limit: int = 400) -> Evidence:
        text = (output or "").strip()
        return cls(source=source, raw=text[:limit], truncated=len(text) > limit)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "raw": self.raw, "truncated": self.truncated}


@dataclass(frozen=True)
class GateCheckResult:
    check_id: str
    title: str
    status: GateStatus
    message: str
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    remediation: str = ""
    blocks_phases: tuple[int, ...] = ()
    to_validate_on_target: bool = False
    unknown_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "status": self.status.value,
            "message": self.message,
            "facts": self.facts,
            "evidence": [e.to_dict() for e in self.evidence],
            "remediation": self.remediation,
            "blocks_phases": list(self.blocks_phases),
            "to_validate_on_target": self.to_validate_on_target,
            "unknown_reason": self.unknown_reason,
        }


# ---------------------------------------------------------------------------
# Catalogue de dépendances
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dependency:
    """Une dépendance documentée selon les quatre axes exigés.

    Pourquoi elle est nécessaire, comment l'installer, quelle phase en dépend,
    quel repli existe. Une dépendance sans repli déclaré est un point de
    rupture ; le champ vaut alors explicitement « aucun ».
    """

    name: str
    why: str
    install: str
    required_from_phase: int
    fallback: str
    check_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "why": self.why,
            "install": self.install,
            "required_from_phase": self.required_from_phase,
            "fallback": self.fallback,
            "check_id": self.check_id,
        }


DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency(
        "python3.12",
        "Runtime du Core. 3.12 pour les génériques natifs et les améliorations de typage.",
        "sudo apt install python3.12 python3.12-venv  (dans la distribution WSL2, pas sur Windows)",
        1,
        "aucun — dépendance dure",
        "GATE-05",
    ),
    Dependency(
        "docker-engine",
        "Sandbox d'exécution du code généré et services d'infrastructure. "
        "Engine natif et non Docker Desktop : Desktop ne permet pas de déclarer un runtime OCI alternatif.",
        "curl -fsSL https://get.docker.com | sh ; sudo usermod -aG docker $USER",
        1,
        "aucun pour la sandbox. Les services peuvent tourner nativement, au prix de la reproductibilité.",
        "GATE-06",
    ),
    Dependency(
        "docker-compose-plugin",
        "Orchestration des services locaux (PostgreSQL, Redis, MinIO).",
        "sudo apt install docker-compose-plugin",
        1,
        "docker run manuel — non reproductible, à éviter",
        "GATE-06",
    ),
    Dependency(
        "nvidia-driver (hôte Windows)",
        "Expose le GPU à WSL2. S'installe côté Windows, jamais dans la distribution.",
        "Télécharger le pilote NVIDIA Game Ready ou Studio sur nvidia.com (hôte Windows).",
        6,
        "CPU seul : le mode interactif reste utilisable, le mode autonome n'est pas viable.",
        "GATE-07",
    ),
    Dependency(
        "nvidia-container-toolkit",
        "Permet d'exposer le GPU à l'intérieur d'un conteneur. Nécessaire si l'inférence "
        "ou l'évaluation sont conteneurisées.",
        "sudo apt install nvidia-container-toolkit && sudo nvidia-ctk runtime configure --runtime=docker",
        6,
        "Ollama en dehors du conteneur, sur l'hôte WSL2. Fonctionne, mais rend l'environnement "
        "d'inférence moins reproductible.",
        "GATE-18",
    ),
    Dependency(
        "ollama",
        "Runtime de modèles locaux. Sert tous les rôles de la boucle autonome.",
        "curl -fsSL https://ollama.com/install.sh | sh",
        6,
        "llama.cpp derrière la même interface ModelProvider (ADR-0003).",
        "GATE-16",
    ),
    Dependency(
        "runsc (gVisor)",
        "Isolation renforcée en espace utilisateur. Condition d'activation du Red Team.",
        "Voir gvisor.dev/docs/user_guide/install ; exige un Docker Engine natif.",
        21,
        "Docker durci : non-root, cap-drop ALL, seccomp, rootfs en lecture seule, réseau coupé. "
        "Suffisant pour du code généré ordinaire ; le Red Team reste désactivé.",
        "GATE-20",
    ),
    Dependency(
        "git",
        "Versionnement du Core et des workspaces de tâches.",
        "sudo apt install git",
        1,
        "aucun — dépendance dure",
        "GATE-16",
    ),
    Dependency(
        "pyyaml",
        "Lecture de config.yaml. Seule dépendance Python de la phase 1.",
        "pip install pyyaml  (ou uv sync)",
        1,
        "aucun — le chargement de configuration échoue proprement avec DependencyMissing.",
        "GATE-16",
    ),
)


def dependency_for(check_id: str) -> tuple[Dependency, ...]:
    return tuple(d for d in DEPENDENCIES if d.check_id == check_id)
