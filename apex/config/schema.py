"""Schéma de configuration APEX — dataclasses stdlib, validation stricte.

Pourquoi pas Pydantic ici : ce module est importé par `apex doctor`, qui doit
fonctionner **avant** l'installation des dépendances, sur un environnement
cassé. Pydantic entre en phase 2 pour les schémas de base de données et d'API,
où il apporte un gain réel.

Décisions de Phase 0 encodées ici comme structures de données :

- `ModelRef` (identité logique) et `ModelEndpoint` (lieu d'exécution) sont deux
  types distincts — principe 7. Le routeur choisira des `ModelRef` ; le
  placement résoudra des `ModelEndpoint`.
- `ComputePool` existe dès maintenant avec `trust_level`, même s'il n'y a qu'un
  pool local — principe 6.
- Un rôle de modèle porte une **liste de candidats ordonnée**, pas un modèle
  unique : le défaut est un candidat de référence, pas un vainqueur déclaré
  (principe 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from apex.errors import ConfigError



def as_bool(value: Any, *, field: str) -> bool:
    """Coercition booléenne stricte.

    La substitution d'environnement produit toujours des chaînes :
    `${APEX_EXTERNAL_ENABLED:-false}` devient la chaîne "false", dont la valeur
    de vérité Python est True. Sans cette fonction, la politique externe
    s'activerait silencieusement — exactement le type de dérive que le
    principe 3 interdit. Une valeur non reconnue est une erreur, pas un défaut.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0", ""}:
            return False
    raise ConfigError(f"valeur booléenne invalide : {value!r}", field=field)


class TrustLevel(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class PoolLocation(str, Enum):
    LOCAL = "local"
    LAN = "lan"
    CLOUD = "cloud"


class PoolStatus(str, Enum):
    """Distingue ce qui tourne réellement de ce qui est visé.

    Corrige un risque architectural précis : sans ce champ, un `ComputePool`
    décrivant la future machine dédiée d'APEX (bien plus puissante que le
    poste de développement actuel) est indiscernable d'un pool mal déclaré,
    et déclenche les mêmes avertissements « déclaré ≠ mesuré » (gate_09_vram,
    doctor.gpu.config_match) qu'une véritable erreur de configuration.

    - ACTIVE : pool réellement disponible aujourd'hui — mesuré, comparé au
      matériel réel, compte pour le fallback local obligatoire (principe 6).
    - TARGET : machine visée mais pas encore construite/équipée. Déclarable
      dès maintenant (l'architecture n'attend pas le matériel pour exister),
      mais explicitement exclue de toute comparaison déclaré-vs-mesuré et du
      calcul de fallback local : le hardware de développement actuel ne doit
      jamais devenir un plafond architectural, et une cible ne doit jamais
      se faire passer pour une disponibilité actuelle.
    """

    ACTIVE = "active"
    TARGET = "target"


class Capability(str, Enum):
    INFERENCE = "inference"
    SANDBOX = "sandbox"
    CPU_EVAL = "cpu_eval"


# --------------------------------------------------------------------------
# Modèles : identité logique vs point de service
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRef:
    """Identité **logique** et versionnée d'un modèle.

    C'est l'objet que manipulent le routeur, les génomes et les empreintes
    d'expérience. Il ne contient aucune information de localisation : le même
    ModelRef peut être servi par Ollama en local aujourd'hui et par vLLM sur un
    pool cloud demain, sans invalider les comparaisons.
    """

    family: str            # qwen3-coder, gemma4, gpt-oss...
    version: str           # 30b-a3b-instruct, 12b...
    quantization: str      # q4_k_m, fp16, external
    capability_class: str  # coder, generalist, judge, fast, embed

    @property
    def id(self) -> str:
        return f"{self.family}:{self.version}:{self.quantization}"

    @classmethod
    def parse(cls, raw: dict[str, Any], *, where: str) -> ModelRef:
        missing = [k for k in ("family", "version", "quantization", "capability_class") if k not in raw]
        if missing:
            raise ConfigError(f"champs manquants {missing}", field=where)
        return cls(
            family=str(raw["family"]),
            version=str(raw["version"]),
            quantization=str(raw["quantization"]),
            capability_class=str(raw["capability_class"]),
        )



@dataclass(frozen=True)
class MeasuredCapability:
    """Capacité RÉELLEMENT mesurée d'un modèle sur une machine donnée.

    Règle §59 de la vision : ne jamais supposer les performances documentaires
    d'un modèle. Une valeur documentée est une hypothèse ; seule une mesure
    engage le budget de calcul.

    `environment_fingerprint` rattache la mesure à une configuration matérielle
    précise. Un changement de GPU ou de pilote produit une empreinte différente
    et invalide la mesure : elle redevient une hypothèse à remesurer, plutôt
    que de contaminer silencieusement les estimations de budget.
    """

    vram_gb: float
    tokens_per_s: float
    measured_at: str
    environment_fingerprint: str
    max_context_verified: int | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any], *, where: str) -> MeasuredCapability:
        for key in ("vram_gb", "tokens_per_s", "measured_at", "environment_fingerprint"):
            if key not in raw:
                raise ConfigError(f"mesure incomplète : « {key} » manquant", field=where)
        return cls(
            vram_gb=float(raw["vram_gb"]),
            tokens_per_s=float(raw["tokens_per_s"]),
            measured_at=str(raw["measured_at"]),
            environment_fingerprint=str(raw["environment_fingerprint"]),
            max_context_verified=(
                int(raw["max_context_verified"]) if raw.get("max_context_verified") else None
            ),
        )


@dataclass(frozen=True)
class ModelEndpoint:
    """Point de service **physique** d'un ModelRef sur un pool donné."""

    pool_id: str
    runtime: str             # ollama, vllm, external, openai_compatible
    url: str
    served_model: str        # nom tel que connu du runtime
    max_context: int
    vram_gb: float | None = None          # DOCUMENTÉ — hypothèse, pas une mesure
    measured: MeasuredCapability | None = None
    #: Clé LOGIQUE d'un secret (`apex.secrets.SecretBroker`), JAMAIS la
    #: valeur elle-même — `None` pour un backend sans authentification
    #: (Ollama local). Résolue par le `ModelProvider` au moment de l'appel,
    #: jamais stockée ni journalisée par cette dataclass.
    secret_key: str | None = None
    #: Désactivation explicite en configuration — distincte d'une
    #: indisponibilité constatée (`CapabilityStatus`) : un endpoint
    #: `enabled=False` n'est jamais sondé, jamais candidat, quel que soit
    #: son état réel (`apex.models.catalog.Availability.DISABLED`).
    enabled: bool = True

    @property
    def is_measured(self) -> bool:
        return self.measured is not None

    @property
    def effective_vram_gb(self) -> float | None:
        """VRAM à utiliser pour planifier : la mesure prime toujours sur la doc."""
        if self.measured is not None:
            return self.measured.vram_gb
        return self.vram_gb

    def measurement_is_valid_for(self, environment_fingerprint: str) -> bool:
        """Une mesure faite sur un autre matériel ne vaut pas pour celui-ci."""
        return (
            self.measured is not None
            and self.measured.environment_fingerprint == environment_fingerprint
        )

    @classmethod
    def parse(cls, raw: dict[str, Any], *, where: str) -> ModelEndpoint:
        for key in ("pool_id", "runtime", "url", "served_model", "max_context"):
            if key not in raw:
                raise ConfigError(f"champ « {key} » manquant", field=where)
        return cls(
            pool_id=str(raw["pool_id"]),
            runtime=str(raw["runtime"]),
            url=str(raw["url"]),
            served_model=str(raw["served_model"]),
            max_context=int(raw["max_context"]),
            vram_gb=float(raw["vram_gb"]) if raw.get("vram_gb") is not None else None,
            measured=(
                MeasuredCapability.parse(raw["measured"], where=f"{where}.measured")
                if raw.get("measured")
                else None
            ),
            secret_key=str(raw["secret_key"]) if raw.get("secret_key") is not None else None,
            enabled=as_bool(raw.get("enabled", True), field=f"{where}.enabled"),
        )


@dataclass(frozen=True)
class ModelRole:
    """Rôle logique (`apex/coder-main`) et ses candidats ordonnés.

    Principe 1 : `candidates[0]` est le **candidat de référence**, pas un
    optimum établi. `selection` indique comment le choix évoluera : en phase 1
    il est statique ; à partir de la phase 17 il est piloté par les benchmarks.
    """

    role: str
    candidates: tuple[ModelRef, ...]
    selection: str = "static_prior"
    resident: bool = False
    notes: str = ""

    @property
    def reference(self) -> ModelRef:
        return self.candidates[0]

    @classmethod
    def parse(cls, role: str, raw: dict[str, Any]) -> ModelRole:
        raw_candidates = raw.get("candidates") or []
        if not raw_candidates:
            raise ConfigError("au moins un candidat requis", field=f"models.roles.{role}")
        selection = str(raw.get("selection", "static_prior"))
        if selection not in {"static_prior", "benchmark_driven"}:
            raise ConfigError(f"selection inconnue « {selection} »", field=f"models.roles.{role}")
        return cls(
            role=role,
            candidates=tuple(
                ModelRef.parse(c, where=f"models.roles.{role}.candidates[{i}]")
                for i, c in enumerate(raw_candidates)
            ),
            selection=selection,
            resident=as_bool(raw.get("resident", False), field=f"models.roles.{role}.resident"),
            notes=str(raw.get("notes", "")),
        )


# --------------------------------------------------------------------------
# Calcul
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GpuSpec:
    model: str
    vram_gb: float


@dataclass(frozen=True)
class ComputePool:
    """Ressource de calcul. Abstraction de premier niveau dès la phase 1."""

    pool_id: str
    location: PoolLocation
    trust_level: TrustLevel
    capabilities: tuple[Capability, ...]
    gpus: tuple[GpuSpec, ...] = ()
    cpu_cores: int = 0
    ram_gb: float = 0.0
    cost_per_hour: float = 0.0
    endpoints: dict[str, str] = field(default_factory=dict)
    status: PoolStatus = PoolStatus.ACTIVE

    @property
    def total_vram_gb(self) -> float:
        return sum(g.vram_gb for g in self.gpus)

    @property
    def may_hold_secrets(self) -> bool:
        """Seul un pool de confiance reçoit des secrets ou le split HELDOUT."""
        return self.trust_level is TrustLevel.TRUSTED

    @classmethod
    def parse(cls, pool_id: str, raw: dict[str, Any]) -> ComputePool:
        where = f"compute.pools.{pool_id}"
        try:
            location = PoolLocation(str(raw.get("location", "local")))
            trust = TrustLevel(str(raw.get("trust_level", "untrusted")))
            capabilities = tuple(Capability(str(c)) for c in raw.get("capabilities", []))
            status = PoolStatus(str(raw.get("status", "active")))
        except ValueError as exc:
            raise ConfigError(str(exc), field=where) from exc
        if not capabilities:
            raise ConfigError("au moins une capability requise", field=where)
        gpus = tuple(
            GpuSpec(model=str(g.get("model", "unknown")), vram_gb=float(g.get("vram_gb", 0)))
            for g in raw.get("gpus", [])
        )
        return cls(
            pool_id=pool_id,
            location=location,
            trust_level=trust,
            capabilities=capabilities,
            gpus=gpus,
            cpu_cores=int(raw.get("cpu_cores", 0)),
            ram_gb=float(raw.get("ram_gb", 0)),
            cost_per_hour=float(raw.get("cost_per_hour", 0)),
            endpoints=dict(raw.get("endpoints", {})),
            status=status,
        )


# --------------------------------------------------------------------------
# Externe, budgets, langages, chemins
# --------------------------------------------------------------------------


#: Contextes où un modèle externe est autorisé (principe 3). Toute autre
#: valeur dans la configuration est une erreur, pas un avertissement.
ALLOWED_EXTERNAL_CONTEXTS = frozenset(
    {"interactive", "hypothesis_generation", "research", "judge_calibration"}
)


@dataclass(frozen=True)
class ExternalPolicy:
    enabled: bool
    allowed_contexts: frozenset[str]
    daily_budget_eur: float
    monthly_budget_eur: float
    circuit_breaker_failures: int
    circuit_breaker_cooldown_s: int

    def allows(self, context: str) -> bool:
        return self.enabled and context in self.allowed_contexts

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> ExternalPolicy:
        contexts = frozenset(str(c) for c in raw.get("allowed_contexts", []))
        illegal = contexts - ALLOWED_EXTERNAL_CONTEXTS
        if illegal:
            raise ConfigError(
                f"contextes externes interdits {sorted(illegal)} ; "
                f"autorisés : {sorted(ALLOWED_EXTERNAL_CONTEXTS)}",
                field="external.allowed_contexts",
            )
        return cls(
            enabled=as_bool(raw.get("enabled", False), field="external.enabled"),
            allowed_contexts=contexts,
            daily_budget_eur=float(raw.get("daily_budget_eur", 0)),
            monthly_budget_eur=float(raw.get("monthly_budget_eur", 0)),
            circuit_breaker_failures=int(raw.get("circuit_breaker_failures", 3)),
            circuit_breaker_cooldown_s=int(raw.get("circuit_breaker_cooldown_s", 900)),
        )


@dataclass(frozen=True)
class ComputeBudget:
    """Budget de calcul, exprimé en temps GPU — principe 9.

    Le scheduler raisonnera en budget disponible et en valeur attendue, pas en
    nombre fixe de tâches. Ces valeurs sont donc des enveloppes, pas des quotas
    de tâches.
    """

    nightly_gpu_minutes: int
    max_experiment_gpu_minutes: int
    min_expected_value_to_start: float
    reserve_fraction: float

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> ComputeBudget:
        reserve = float(raw.get("reserve_fraction", 0.1))
        if not 0.0 <= reserve < 1.0:
            raise ConfigError("reserve_fraction doit être dans [0, 1)", field="budget.reserve_fraction")
        return cls(
            nightly_gpu_minutes=int(raw.get("nightly_gpu_minutes", 600)),
            max_experiment_gpu_minutes=int(raw.get("max_experiment_gpu_minutes", 240)),
            min_expected_value_to_start=float(raw.get("min_expected_value_to_start", 0.0)),
            reserve_fraction=reserve,
        )


@dataclass(frozen=True)
class LanguageToolchain:
    language: str
    wave: int
    image: str
    oracle_strength: str
    separate_metrics: bool
    commands: dict[str, str]

    @classmethod
    def parse(cls, language: str, raw: dict[str, Any]) -> LanguageToolchain:
        where = f"languages.{language}"
        strength = str(raw.get("oracle_strength", "unknown"))
        if strength not in {"very_strong", "strong", "medium", "weak", "unknown"}:
            raise ConfigError(f"oracle_strength inconnue « {strength} »", field=where)
        image = str(raw.get("image", ""))
        if "@sha256:" not in image and ":" not in image:
            raise ConfigError("image de conteneur requise (tag ou digest)", field=where)
        return cls(
            language=language,
            wave=int(raw.get("wave", 1)),
            image=image,
            oracle_strength=strength,
            separate_metrics=as_bool(raw.get("separate_metrics", False), field=f"{where}.separate_metrics"),
            commands=dict(raw.get("commands", {})),
        )


@dataclass(frozen=True)
class Paths:
    data_root: str
    workspaces: str
    artifacts: str
    logs: str

    @property
    def all(self) -> dict[str, str]:
        return {
            "data_root": self.data_root,
            "workspaces": self.workspaces,
            "artifacts": self.artifacts,
            "logs": self.logs,
        }


@dataclass(frozen=True)
class ServiceEndpoint:
    host: str
    port: int
    required: bool = True


@dataclass(frozen=True)
class SandboxDefaults:
    provider: str
    network: str
    cpu_limit: float
    memory_mb: int
    pids_limit: int
    timeout_s: int
    read_only_rootfs: bool
    required_isolation_for_red_team: str

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> SandboxDefaults:
        network = str(raw.get("network", "none"))
        if network not in {"none", "proxy"}:
            raise ConfigError(
                "network doit valoir « none » ou « proxy » — aucun accès direct autorisé",
                field="sandbox.network",
            )
        return cls(
            provider=str(raw.get("provider", "docker")),
            network=network,
            cpu_limit=float(raw.get("cpu_limit", 2.0)),
            memory_mb=int(raw.get("memory_mb", 4096)),
            pids_limit=int(raw.get("pids_limit", 256)),
            timeout_s=int(raw.get("timeout_s", 900)),
            read_only_rootfs=as_bool(raw.get("read_only_rootfs", True), field="sandbox.read_only_rootfs"),
            required_isolation_for_red_team=str(
                raw.get("required_isolation_for_red_team", "gvisor_or_dedicated_host")
            ),
        )


@dataclass(frozen=True)
class ComposeMemoryPolicy:
    """Politique de sur-allocation memoire pour docker-compose.

    Le ratio compare la somme des LIMITES memoire declarees a la RAM visible
    par l'hote. Les reservations sont exclues : elles reservent sans plafonner,
    et ne peuvent donc pas provoquer de depassement.

    Les seuils sont configurables parce qu'ils dependent de la machine : un
    poste de 64 Go tolere une marge differente d'un portable de 8 Go.
    """

    warn_ratio: float
    fail_ratio: float
    expect_memory_limits: bool

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> ComposeMemoryPolicy:
        warn = float(raw.get("warn_ratio", 0.75))
        fail = float(raw.get("fail_ratio", 1.0))
        if not 0.0 < warn <= fail:
            raise ConfigError(
                "warn_ratio doit etre positif et inferieur ou egal a fail_ratio",
                field="gate.compose_memory",
            )
        return cls(
            warn_ratio=warn,
            fail_ratio=fail,
            expect_memory_limits=as_bool(
                raw.get("expect_memory_limits", True), field="gate.compose_memory.expect_memory_limits"
            ),
        )


@dataclass(frozen=True)
class HardwareThresholds:
    """Seuils recommandés pour CE déploiement — pas une limite universelle
    d'APEX.

    Corrige un risque architectural précis : ces valeurs vivaient comme des
    constantes de module dans `apex/gate/checks.py` (MIN_FREE_GB, MIN_RAM_GB,
    MIN_VRAM_GB, MIN_CORES) et un doublon partiellement divergent dans
    `apex/doctor/checks/platform.py` (MIN_FREE_GB=80 au lieu de 150, un « 20 »
    non nommé au lieu de MIN_VRAM_GB). Un poste de développement plus modeste
    que ces seuils déclenchait DEGRADED/WARN comme si le seuil était une
    propriété fixe de l'architecture, plutôt qu'un choix de déploiement
    reconfigurable — même raisonnement que `ComposeMemoryPolicy` ci-dessus.

    Les valeurs par défaut sont IDENTIQUES à celles qui existaient avant ce
    correctif (150/24/20/8) : aucune limite existante n'est modifiée, elle
    devient seulement configurable.

    `vram_headroom_fraction` : réserve appliquée à la VRAM TOTALE d'un pool
    avant de comparer sa capacité à la VRAM déclarée/mesurée d'un modèle de
    référence (voir `apex.doctor.checks.services._reference_is_hardware_
    compatible`). La VRAM totale rapportée par le pilote n'est jamais
    intégralement disponible pour les poids d'un modèle : le contexte CUDA,
    le runtime d'inférence et le système d'exploitation en retiennent une
    part fixe. Comparer une VRAM déclarée à la capacité BRUTE d'une carte
    (`required <= available`, sans réserve) juge donc « compatible » un
    modèle dont la consommation déclarée égale déjà la totalité de la carte
    — ce qui ne tient pas en pratique. Même raisonnement que
    `ComposeMemoryPolicy` ci-dessus (une réserve proportionnelle, jamais un
    montant fixe arbitraire, pour rester raisonnable aussi bien sur une
    carte de 8 Go que sur une future carte de 48 Go). Valeur par défaut
    conservatrice (10 %) ; reconfigurable par déploiement.
    """

    min_free_disk_gb: float
    min_ram_gb: float
    min_vram_gb: float
    min_cpu_threads: int
    vram_headroom_fraction: float = 0.1

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> HardwareThresholds:
        headroom = float(raw.get("vram_headroom_fraction", 0.1))
        if not 0.0 <= headroom < 1.0:
            raise ConfigError(
                "vram_headroom_fraction doit être dans [0, 1)", field="gate.hardware.vram_headroom_fraction"
            )
        return cls(
            min_free_disk_gb=float(raw.get("min_free_disk_gb", 150.0)),
            min_ram_gb=float(raw.get("min_ram_gb", 24.0)),
            min_vram_gb=float(raw.get("min_vram_gb", 20.0)),
            min_cpu_threads=int(raw.get("min_cpu_threads", 8)),
            vram_headroom_fraction=headroom,
        )


@dataclass(frozen=True)
class GatePolicy:
    compose_memory: ComposeMemoryPolicy
    hardware: HardwareThresholds

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> GatePolicy:
        return cls(
            compose_memory=ComposeMemoryPolicy.parse(raw.get("compose_memory") or {}),
            hardware=HardwareThresholds.parse(raw.get("hardware") or {}),
        )


@dataclass(frozen=True)
class SchedulerPolicy:
    """Politique de déploiement du Scheduler (étape 8) — PAS une propriété
    architecturale d'APEX, même discipline que `HardwareThresholds` : un
    choix reconfigurable, jamais dérivé du matériel de développement actuel.

    `max_concurrent_missions` borne le nombre d'exécutions sandboxées
    réellement concurrentes qu'UNE instance de `SchedulerService` accepte de
    lancer (voir `apex.scheduler.service`). Ce n'est PAS un plafond partagé
    entre plusieurs instances/processus Scheduler — chacune applique sa
    propre borne locale ; seule l'absence de double-lancement d'une même
    mission est garantie globalement (réclamation atomique en base), pas
    l'addition exacte des capacités entre instances. Documenté honnêtement,
    pas résolu ici : voir le rapport de l'étape 8.
    """

    max_concurrent_missions: int

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> SchedulerPolicy:
        value = int(raw.get("max_concurrent_missions", 2))
        if value < 1:
            raise ConfigError(
                "max_concurrent_missions doit être >= 1", field="scheduler.max_concurrent_missions"
            )
        return cls(max_concurrent_missions=value)


@dataclass(frozen=True)
class AutonomousLearningPolicy:
    """Politique de déclenchement automatique d'`apex.metalearner.cycle.
    run_experiment_cycle` (passe « Autonomous Learning Trigger »). Même
    discipline que `SchedulerPolicy` : un choix d'opérateur reconfigurable,
    jamais une propriété architecturale figée dans le code.

    **`enabled` par défaut à `False`** — exigence explicite de cette passe :
    installer/mettre à jour APEX ne doit JAMAIS démarrer un apprentissage
    autonome de façon inattendue. Un opérateur humain doit l'activer
    explicitement (`config.yaml` ou `APEX_AUTONOMOUS_LEARNING_ENABLED=1`).

    `max_concurrent_cycles` documente honnêtement la limite RÉELLE
    (`apex.metalearner.autonomy.AutonomousLearningService` ne suit qu'UN
    cycle actif à la fois dans ce processus, même limite qu'`ExecutionRegistry`
    pour les missions) — accepter une valeur > 1 ici mentirait sur ce que le
    code fait réellement, donc rejeté à la validation plutôt que toléré en
    silence. `specialty`/`current_level`/`method`/`split` sont les paramètres
    par défaut d'un cycle déclenché par le Scheduler (jamais utilisés par un
    déclenchement CLI/API explicite, qui les fournit toujours lui-même)."""

    enabled: bool
    interval_s: float
    max_concurrent_cycles: int
    specialty: str
    current_level: int
    method: str
    split: str

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> AutonomousLearningPolicy:
        enabled = as_bool(raw.get("enabled", False), field="autonomous_learning.enabled")
        interval_s = float(raw.get("interval_s", 3600.0))
        if interval_s <= 0:
            raise ConfigError("interval_s doit être > 0", field="autonomous_learning.interval_s")
        max_concurrent_cycles = int(raw.get("max_concurrent_cycles", 1))
        if max_concurrent_cycles != 1:
            raise ConfigError(
                "max_concurrent_cycles doit valoir 1 — aucune exécution concurrente "
                "de cycles autonomes n'est implémentée dans ce processus",
                field="autonomous_learning.max_concurrent_cycles",
            )
        current_level = int(raw.get("current_level", 1))
        if current_level < 1:
            raise ConfigError("current_level doit être >= 1", field="autonomous_learning.current_level")
        return cls(
            enabled=enabled, interval_s=interval_s, max_concurrent_cycles=max_concurrent_cycles,
            specialty=str(raw.get("specialty", "research")), current_level=current_level,
            method=str(raw.get("method", "practice")), split=str(raw.get("split", "train")),
        )


@dataclass(frozen=True)
class RouterScoringPolicy:
    """Poids du score `apex.routing.router.select_model` — passe « Adaptive
    Model Router V1 ». Même discipline que `SchedulerPolicy` : un choix
    d'opérateur reconfigurable, jamais une propriété architecturale figée
    dans le code.

    **Justification de chaque poids, jamais un chiffre choisi à
    l'intuition** :
    - `capability_match_weight` (défaut `1.0`) : reproduit EXACTEMENT la
      base « 1.0 » déjà attribuée depuis la première version de
      `select_model` à tout candidat qui satisfait les filtres stricts
      (capacité/contexte/disponibilité/`ExternalPolicy`) — un changement de
      cette valeur par défaut changerait un comportement déjà en
      production, jamais fait sans preuve.
    - `local_preference_weight` (défaut `0.5`) : reproduit EXACTEMENT le
      bonus « +0.5 » déjà existant pour `RoutingRequest.prefer_local`.
    - `reliability_weight` (défaut `0.3`) : multiplie un
      `ModelPerformanceStats.success_rate` RÉEL (jamais un candidat sans
      historique — `usage_count=0` ⇒ aucune contribution, voir `apex.
      routing.model.ModelPerformanceStats`). Une contribution strictement
      proportionnelle au taux de succès observé, bornée par ce poids.
    - `latency_weight` (défaut `0.2`) : pondère une décroissance
      harmonique `1 / (1 + mean_latency_s)` d'un `ModelPerformanceStats.
      mean_latency_s` RÉEL — une forme monotone décroissante standard
      (jamais négative, jamais indéfinie), pas un seuil arbitraire choisi
      pour produire un résultat souhaité.

    Les deux nouveaux poids (`reliability_weight`/`latency_weight`) ne
    changent AUCUNE décision de routage tant qu'aucun historique réel
    n'existe pour un candidat (l'environnement actuel — un seul candidat
    local Ollama, aucune `AgentExecution` avec `model_id` renseigné avant
    cette passe) : leur contribution est alors structurellement nulle, pas
    parce que le poids serait nul.
    """

    capability_match_weight: float = 1.0
    local_preference_weight: float = 0.5
    reliability_weight: float = 0.3
    latency_weight: float = 0.2

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> RouterScoringPolicy:
        """`math.isfinite` rejette `NaN`/`+inf`/`-inf` en un seul test —
        passe « Adaptive Model Router V1 — Hardening » : `value < 0` seul
        ne détectait jamais `NaN` (toute comparaison avec `NaN` vaut `False`
        en Python) et n'excluait pas explicitement `+inf` (positif, donc
        accepté par `< 0`), qui rendrait `ScoreBreakdown.total` infini pour
        tout candidat avec un historique réel — un score qui n'a plus de
        sens ordonnable. Une configuration mathématiquement invalide ne
        doit jamais pouvoir entrer dans le router."""
        import math

        defaults = cls()
        values = {
            "capability_match_weight": float(raw.get("capability_match_weight", defaults.capability_match_weight)),
            "local_preference_weight": float(raw.get("local_preference_weight", defaults.local_preference_weight)),
            "reliability_weight": float(raw.get("reliability_weight", defaults.reliability_weight)),
            "latency_weight": float(raw.get("latency_weight", defaults.latency_weight)),
        }
        for field_name, value in values.items():
            if not math.isfinite(value):
                raise ConfigError(
                    f"{field_name} doit être un nombre fini (ni NaN, ni +inf, ni -inf)",
                    field=f"routing.{field_name}",
                )
            if value < 0:
                raise ConfigError(f"{field_name} doit être >= 0", field=f"routing.{field_name}")
        return cls(**values)


@dataclass(frozen=True)
class ApexConfig:
    version: int
    phase: int
    paths: Paths
    pools: dict[str, ComputePool]
    model_roles: dict[str, ModelRole]
    endpoints: tuple[ModelEndpoint, ...]
    external: ExternalPolicy
    budget: ComputeBudget
    languages: dict[str, LanguageToolchain]
    services: dict[str, ServiceEndpoint]
    sandbox: SandboxDefaults
    gate: GatePolicy
    scheduler: SchedulerPolicy
    routing: RouterScoringPolicy
    autonomous_learning: AutonomousLearningPolicy

    # -- invariants transverses -------------------------------------------

    def validate(self) -> None:
        """Invariants qui traversent plusieurs sections.

        Ils encodent les principes verrouillés ; une violation est une erreur
        de configuration, pas un avertissement.
        """
        if not self.pools:
            raise ConfigError("au moins un ComputePool est requis", field="compute.pools")

        # Principe 6 : le pool local doit exister et être de confiance.
        # PoolStatus.TARGET (machine visée, pas encore construite) ne compte
        # jamais pour ce fallback : une déclaration de matériel futur ne doit
        # jamais se faire passer pour une disponibilité réelle aujourd'hui.
        local = [
            p for p in self.pools.values()
            if p.location is PoolLocation.LOCAL and p.status is PoolStatus.ACTIVE
        ]
        if not local:
            raise ConfigError(
                "un pool local ACTIVE est obligatoire (fallback 100 % local) — "
                "un pool status=target ne compte pas comme disponibilité réelle",
                field="compute.pools",
            )

        # Les endpoints ne peuvent référencer qu'un pool déclaré.
        for endpoint in self.endpoints:
            if endpoint.pool_id not in self.pools:
                raise ConfigError(
                    f"endpoint rattaché au pool inconnu « {endpoint.pool_id} »", field="models.endpoints"
                )

        # Principe 2 : le Judge doit être d'une famille différente du coder.
        coder = self.model_roles.get("coder_main")
        judge = self.model_roles.get("judge")
        if coder and judge and coder.reference.family == judge.reference.family:
            raise ConfigError(
                "le Judge doit appartenir à une famille de modèles différente du coder "
                f"(les deux valent « {coder.reference.family} ») — l'indépendance du Judge "
                "est une condition, pas une préférence",
                field="models.roles.judge",
            )

        # Principe 3 : un rôle utilisé par la boucle autonome ne peut pas être externe.
        for name in ("coder_main", "judge", "fast", "embed"):
            role = self.model_roles.get(name)
            if role and role.reference.quantization == "external":
                raise ConfigError(
                    f"le rôle « {name} » est utilisé par la boucle autonome et doit être local",
                    field=f"models.roles.{name}",
                )

        # Principe 4 : aucun accès réseau direct depuis la sandbox.
        if self.sandbox.network not in {"none", "proxy"}:
            raise ConfigError("réseau de sandbox non autorisé", field="sandbox.network")

        # Cohérence VRAM : les modèles résidents doivent tenir sur le pool local.
        vram = max((p.total_vram_gb for p in local), default=0.0)
        resident = [
            e
            for e in self.endpoints
            for role in self.model_roles.values()
            if role.resident and e.served_model.startswith(role.reference.family)
        ]
        needed = sum(e.effective_vram_gb or 0.0 for e in resident)
        if vram and needed > vram:
            raise ConfigError(
                f"les modèles résidents demandent {needed:.1f} Go de VRAM pour {vram:.1f} Go disponibles",
                field="models.roles",
            )
