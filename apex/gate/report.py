"""Rapport d'environnement machine-lisible et gestion des exceptions.

Deux mécanismes méritent d'être explicités.

**Provenance de la sonde.** Le rapport enregistre si les mesures viennent d'une
sonde système réelle. Si ce n'est pas le cas, tous les statuts concluants sont
dégradés en SIMULATED avant écriture. Il est donc impossible de produire un
rapport qui prétend refléter une machine réelle depuis un environnement de
test — la garantie est structurelle et non procédurale.

**Exceptions périssables.** Une exception acceptée porte une date d'expiration
obligatoire. Passé ce délai, elle cesse de s'appliquer et le contrôle
redevient bloquant. Une dérogation permanente n'est pas une dérogation, c'est
un changement de spécification silencieux.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from apex.errors import ConfigError
from apex.gate.model import DEPENDENCIES, GateCheckResult, GateStatus
from apex.version import __version__

SCHEMA_VERSION = 1
DEFAULT_REPORT_PATH = ".apex/environment.json"
ENVIRONMENTS_DIR = ".apex/environments"

#: Faits qui, s'ils changent, invalident la comparabilité des expériences.
FINGERPRINT_FACTS = (
    "distro_id", "distro_version", "kernel", "python_version", "docker",
    "cpu_model", "cpu_threads", "ram_total_gb", "gpus", "driver_version",
    "isolation_level",
)


@dataclass(frozen=True)
class GateException:
    """Dérogation explicite et datée à un contrôle bloquant."""

    check_id: str
    reason: str
    approved_by: str
    expires_on: date

    def is_active(self, today: date | None = None) -> bool:
        return (today or date.today()) <= self.expires_on

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "reason": self.reason,
            "approved_by": self.approved_by,
            "expires_on": self.expires_on.isoformat(),
            "active": self.is_active(),
        }

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> GateException:
        for key in ("check_id", "reason", "approved_by", "expires_on"):
            if key not in raw:
                raise ConfigError(f"exception incomplète : « {key} » manquant", field="exceptions")
        try:
            expiry = date.fromisoformat(str(raw["expires_on"]))
        except ValueError as exc:
            raise ConfigError(f"date d'expiration invalide : {raw['expires_on']}",
                              field="exceptions") from exc
        return cls(str(raw["check_id"]), str(raw["reason"]), str(raw["approved_by"]), expiry)


def load_exceptions(path: str | Path) -> tuple[GateException, ...]:
    file_path = Path(path)
    if not file_path.is_file():
        return ()
    try:
        import yaml
    except ImportError:
        return ()
    document = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    return tuple(GateException.parse(item) for item in document.get("exceptions", []))


class StartupRole(str, Enum):
    """Rôle d'un contrôle dans la décision de DÉMARRAGE d'APEX (`launch.sh serve`, `apex serve`,
    `apex start`) — distincte de l'autorisation de la phase 2 de la feuille de route, inchangée.

    Un FAIL mesuré bloque le démarrage quel que soit le rôle. Le rôle ne décide que du sort d'un
    UNKNOWN : seule une PRECONDITION non vérifiée bloque le démarrage ; les autres rôles sont
    « non vérifiés » affichés comme tels, jamais comme des échecs."""

    #: A — précondition : FAIL possible, et un UNKNOWN pourrait cacher ce FAIL -> bloque.
    PRECONDITION = "precondition"
    #: B — informatif : aucun chemin FAIL (pire résultat mesurable : DEGRADED, non bloquant).
    INFORMATIVE = "informative"
    #: C — capacité facultative (GPU, runsc) : nécessaire à des phases ultérieures, pas au démarrage.
    OPTIONAL_CAPABILITY = "optional_capability"
    #: D — non vérifiable par construction (mesure empêchée par un durcissement délibéré).
    UNVERIFIABLE = "unverifiable"
    #: E — sonde à consentement explicite, que les lanceurs n'accordent jamais d'eux-mêmes.
    CONSENT_REQUIRED = "consent_required"


#: Classification explicite des 22 contrôles. Un contrôle absent de cette table est une PRECONDITION
#: (repli sûr : un nouveau contrôle non classé bloque le démarrage tant qu'on ne l'a pas examiné).
#: `tests/test_startup_gate_contract.py` vérifie qu'aucun contrôle non-PRECONDITION n'a de chemin FAIL,
#: hors RUNTIME_ENFORCED.
STARTUP_ROLES: dict[str, StartupRole] = {
    "GATE-01": StartupRole.UNVERIFIABLE,         # version Windows : illisible si l'interop est coupée (ADR-0005)
    "GATE-02": StartupRole.PRECONDITION,         # Linux / WSL2
    "GATE-03": StartupRole.INFORMATIVE,          # distribution
    "GATE-04": StartupRole.INFORMATIVE,          # noyau
    "GATE-05": StartupRole.PRECONDITION,         # Python 3.12+
    "GATE-06": StartupRole.PRECONDITION,         # Docker / Compose (mesuré passivement)
    "GATE-07": StartupRole.OPTIONAL_CAPABILITY,  # GPU visible : CPU seul = DEGRADED, requis en phase 6
    "GATE-08": StartupRole.OPTIONAL_CAPABILITY,  # modèle du GPU
    "GATE-09": StartupRole.OPTIONAL_CAPABILITY,  # VRAM
    "GATE-10": StartupRole.OPTIONAL_CAPABILITY,  # pilote / CUDA
    "GATE-11": StartupRole.INFORMATIVE,          # disque (pire cas : DEGRADED)
    "GATE-12": StartupRole.INFORMATIVE,          # RAM
    "GATE-13": StartupRole.INFORMATIVE,          # CPU
    "GATE-14": StartupRole.PRECONDITION,         # cgroups v2 / virtualisation
    "GATE-15": StartupRole.CONSENT_REQUIRED,     # sortie réseau (APEX fonctionne hors ligne, principe 3)
    "GATE-16": StartupRole.PRECONDITION,         # git, pyyaml
    "GATE-17": StartupRole.CONSENT_REQUIRED,     # conteneur durci : revérifié à l'exécution (RUNTIME_ENFORCED)
    "GATE-18": StartupRole.CONSENT_REQUIRED,     # GPU en conteneur (pire cas : DEGRADED)
    "GATE-19": StartupRole.PRECONDITION,         # niveau d'isolation de la sandbox
    "GATE-20": StartupRole.OPTIONAL_CAPABILITY,  # runsc : Red Team, phase 21
    "GATE-21": StartupRole.PRECONDITION,         # Compose : sur-allocation mémoire (K2)
    "GATE-22": StartupRole.CONSENT_REQUIRED,     # clavier/souris (pire cas : DEGRADED)
}

#: Contrôles non-PRECONDITION qui ont pourtant un chemin FAIL : tolérés non vérifiés au démarrage
#: UNIQUEMENT parce que la garantie qu'ils mesurent est revérifiée, en refus par défaut, au moment de
#: l'utiliser. GATE-17 : `apex.sandbox.provider.select_sandbox_provider` sonde un vrai conteneur durci et
#: `ContainerSandbox.execute` lève `IsolationUnavailable` sans preuve (réseau coupé, non-root, racine en
#: lecture seule) ; l'absence de Docker est en outre un FAIL passif de GATE-06.
RUNTIME_ENFORCED: frozenset[str] = frozenset({"GATE-17"})


#: Profil AUTONOME (`apex.standalone`, application `APEX.exe`) : Docker, Compose et cgroups sont
#: l'infrastructure du profil serveur (base, cache et isolation conteneurisés) — mesurés et affichés, jamais
#: exigés pour démarrer. GATE-19 (isolation) y est une capacité revérifiée à CHAQUE exécution, en refus par
#: défaut (`SandboxProvider.execute` lève `IsolationUnavailable` sans isolation prouvée), exactement comme
#: GATE-17 : sans elle APEX démarre, mais aucun code n'est exécuté. Voir docs/PHASE_1_GATE.md §11.
STANDALONE_ROLE_OVERRIDES: dict[str, StartupRole] = {
    "GATE-06": StartupRole.OPTIONAL_CAPABILITY,
    "GATE-14": StartupRole.OPTIONAL_CAPABILITY,
    "GATE-19": StartupRole.OPTIONAL_CAPABILITY,
    "GATE-21": StartupRole.OPTIONAL_CAPABILITY,
}


def startup_role(check_id: str, profile: str = "server") -> StartupRole:
    if profile == "standalone" and check_id in STANDALONE_ROLE_OVERRIDES:
        return STANDALONE_ROLE_OVERRIDES[check_id]
    return STARTUP_ROLES.get(check_id, StartupRole.PRECONDITION)


@dataclass
class EnvironmentReport:
    checks: tuple[GateCheckResult, ...]
    probe_is_real: bool
    exceptions: tuple[GateException, ...] = ()
    generated_at: float = field(default_factory=time.time)
    machine_label: str = ""
    #: « server » (défaut, WSL2/Linux + Docker) ou « standalone » (APEX autonome) — décide des rôles de démarrage.
    profile: str = "server"

    # -- agrégation --------------------------------------------------------

    @property
    def effective_checks(self) -> tuple[GateCheckResult, ...]:
        """Dégrade tout statut concluant en SIMULATED si la sonde n'est pas réelle."""
        if self.probe_is_real:
            return self.checks
        return tuple(
            GateCheckResult(
                check_id=c.check_id, title=c.title,
                status=GateStatus.SIMULATED if c.status.is_conclusive else c.status,
                message=c.message, facts=c.facts, evidence=c.evidence,
                remediation=c.remediation, blocks_phases=c.blocks_phases,
                to_validate_on_target=True, unknown_reason=c.unknown_reason,
            )
            for c in self.checks
        )

    @property
    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in GateStatus}
        for item in self.effective_checks:
            counts[item.status.value] += 1
        return counts

    @property
    def excused(self) -> frozenset[str]:
        return frozenset(e.check_id for e in self.exceptions if e.is_active())

    @property
    def expired_exceptions(self) -> tuple[GateException, ...]:
        return tuple(e for e in self.exceptions if not e.is_active())

    @property
    def blocking(self) -> tuple[GateCheckResult, ...]:
        return tuple(
            c for c in self.effective_checks
            if c.status.is_blocking and c.check_id not in self.excused
        )

    @property
    def unresolved(self) -> tuple[GateCheckResult, ...]:
        """Contrôles ni concluants ni excusés : ils empêchent de clore le Gate."""
        return tuple(
            c for c in self.effective_checks
            if not c.status.is_conclusive and c.check_id not in self.excused
        )

    @property
    def status(self) -> GateStatus:
        if not self.probe_is_real:
            return GateStatus.SIMULATED
        if self.blocking:
            return GateStatus.FAIL
        if self.unresolved:
            return GateStatus.UNKNOWN
        if any(c.status is GateStatus.DEGRADED for c in self.effective_checks):
            return GateStatus.DEGRADED
        return GateStatus.PASS

    @property
    def phase_2_authorized(self) -> bool:
        """Le Gate autorise la phase 2 s'il est concluant et sans blocage résiduel."""
        return self.probe_is_real and not self.blocking and not self.unresolved

    # -- démarrage d'APEX (distinct de la phase 2) -------------------------

    @property
    def startup_blocking(self) -> tuple[GateCheckResult, ...]:
        """FAIL mesurés : bloquent TOUJOURS le démarrage, même excusés. Une dérogation peut ouvrir une
        phase de la feuille de route ; elle ne fait jamais démarrer APEX sur une précondition mesurée
        non satisfaite."""
        return tuple(c for c in self.effective_checks if c.status.is_blocking)

    def _not_conclusive(self) -> tuple[GateCheckResult, ...]:
        # Excusés compris : une dérogation accepte l'absence de preuve, elle ne la fournit pas.
        return tuple(c for c in self.effective_checks if not c.status.is_conclusive)

    @property
    def startup_unverified_blocking(self) -> tuple[GateCheckResult, ...]:
        """Non vérifiés qui bloquent le démarrage : UNKNOWN d'une PRECONDITION (il pourrait cacher un
        FAIL) et toute mesure SIMULATED, sauf dérogation datée. Jamais présentés comme des échecs."""
        return tuple(
            c for c in self._not_conclusive()
            if c.check_id not in self.excused
            and (c.status is not GateStatus.UNKNOWN
                 or startup_role(c.check_id, self.profile) is StartupRole.PRECONDITION)
        )

    @property
    def startup_unverified(self) -> tuple[GateCheckResult, ...]:
        """Non vérifiés qui n'empêchent pas le démarrage (tolérés par leur rôle, ou excusés), affichés
        comme tels avec leur motif."""
        blocking = {c.check_id for c in self.startup_unverified_blocking}
        return tuple(c for c in self._not_conclusive() if c.check_id not in blocking)

    @property
    def startup_authorized(self) -> bool:
        return self.probe_is_real and not self.startup_blocking and not self.startup_unverified_blocking

    # -- faits consolidés --------------------------------------------------

    def facts(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for item in self.effective_checks:
            merged.update(item.facts)
        return merged

    def capabilities(self) -> dict[str, Any]:
        """Capacités exploitables par les phases suivantes.

        `null` signifie « inconnu », jamais « absent ». Une phase qui confondrait
        les deux prendrait une décision sur une non-mesure.
        """
        facts = self.facts()
        return {
            "gpu_visible_from_wsl": facts.get("gpu_visible_from_wsl"),
            "gpu_in_container": facts.get("gpu_in_container"),
            "containers_runnable": facts.get("containers_runnable"),
            "cgroups_v2": facts.get("cgroups_v2"),
            "isolation_level": facts.get("isolation_level"),
            "red_team_allowed": facts.get("red_team_allowed"),
            "code_execution_allowed": facts.get("code_execution_allowed"),
            "runsc_functional": facts.get("runsc_functional"),
            "network_egress": facts.get("network_egress"),
            "vram_total_gb": facts.get("vram_total_gb"),
        }

    def fingerprint(self) -> str:
        """Empreinte matérielle : un changement invalide les comparaisons.

        Les scores d'expériences ne sont comparables qu'entre empreintes
        identiques. Sans ce champ, un changement de pilote ou de GPU
        contaminerait silencieusement l'historique de générations.
        """
        facts = self.facts()
        payload = {key: facts.get(key) for key in FINGERPRINT_FACTS}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return f"sha256:{digest[:32]}"

    def missing_dependencies(self) -> list[dict[str, Any]]:
        problematic = {
            c.check_id for c in self.effective_checks
            if c.status in (GateStatus.FAIL, GateStatus.DEGRADED, GateStatus.UNKNOWN)
        }
        return [d.to_dict() for d in DEPENDENCIES if d.check_id in problematic]

    # -- sérialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "apex_version": __version__,
            "phase": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.generated_at)),
            "probe": "system" if self.probe_is_real else "fake",
            "machine_label": self.machine_label or None,
            "measured_on_target_machine": self.probe_is_real,
            "gate_status": self.status.value,
            "phase_2_authorized": self.phase_2_authorized,
            "startup": {
                "authorized": self.startup_authorized,
                "blocking": [c.check_id for c in self.startup_blocking],
                "unverified_blocking": [
                    {"check_id": c.check_id, "status": c.status.value, "reason": c.unknown_reason or None}
                    for c in self.startup_unverified_blocking
                ],
                "unverified": [
                    {"check_id": c.check_id, "reason": c.unknown_reason or None,
                     "role": startup_role(c.check_id, self.profile).value}
                    for c in self.startup_unverified
                ],
            },
            "fingerprint": self.fingerprint(),
            "counts": self.counts,
            "capabilities": self.capabilities(),
            "hardware": {
                key: self.facts().get(key)
                for key in ("distro_name", "kernel", "cpu_model", "cpu_threads",
                            "ram_total_gb", "disk_free_gb", "gpus", "driver_version",
                            "cuda_version", "windows_version")
            },
            "blocking": [c.check_id for c in self.blocking],
            "unresolved": [c.check_id for c in self.unresolved],
            "exceptions": [e.to_dict() for e in self.exceptions],
            "expired_exceptions": [e.check_id for e in self.expired_exceptions],
            "missing_dependencies": self.missing_dependencies(),
            "checks": [c.to_dict() for c in self.effective_checks],
        }

    def write(self, path: str | Path = DEFAULT_REPORT_PATH) -> Path:
        """Ecrit le rapport courant et l'archive sous son empreinte.

        Deux machines produisent deux empreintes. Sans archivage, executer le
        Gate sur une machine temporaire ecraserait la mesure de la machine
        cible, et l'on perdrait la seule preuve qui autorisait une phase.
        L'archive rend les deux mesures consultables et comparables.
        """
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")

        archive_dir = target.parent / "environments"
        archive_dir.mkdir(parents=True, exist_ok=True)
        short = self.fingerprint().removeprefix("sha256:")[:12]
        (archive_dir / f"{short}.json").write_text(payload, encoding="utf-8")
        return target
