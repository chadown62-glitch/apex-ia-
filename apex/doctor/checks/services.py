"""Vérifications : services d'infrastructure, configuration, état des abstractions.

Le check `ollama.models` mérite un mot. Il compare le catalogue déclaré aux
modèles réellement présents, mais **n'échoue pas** si un candidat secondaire
manque : le principe verrouillé n°1 impose que le modèle de référence ne soit
pas traité comme définitivement supérieur, donc l'absence d'un concurrent est
une information, pas une faute. En revanche, l'absence du modèle de référence
d'un rôle résident est bloquante — **sauf si ce modèle de référence ne peut
structurellement pas tenir sur le matériel réellement disponible** (voir
`_reference_is_hardware_compatible`) : dans ce cas précis, l'absence n'est pas
un défaut d'installation mais une conséquence attendue du dimensionnement
matériel, et devient un WARN.
"""

from __future__ import annotations

from apex.config.schema import ApexConfig, ModelEndpoint, PoolLocation, PoolStatus
from apex.doctor.check import CheckContext, CheckResult, Severity, result
from apex.interfaces.registry import ABSTRACTIONS, ImplStatus, module_importable

CATEGORY_SVC = "services"
CATEGORY_CFG = "configuration"


def check_config_loaded(ctx: CheckContext) -> CheckResult:
    if ctx.config is not None:
        return result(
            "config.valid", CATEGORY_CFG, Severity.PASS,
            f"configuration valide (version {ctx.config.version}, phase {ctx.config.phase})",
            pools=list(ctx.config.pools),
            roles=list(ctx.config.model_roles),
            languages=list(ctx.config.languages),
        )
    return result(
        "config.valid", CATEGORY_CFG, Severity.FAIL,
        ctx.config_error or "configuration non chargée",
        remediation="Corriger config.yaml. Aucune valeur par défaut n'est devinée.",
    )


def check_external_policy(ctx: CheckContext) -> CheckResult:
    """L'autonomie ne doit dépendre d'aucun fournisseur externe (principe 3)."""
    if ctx.config is None:
        return result("config.external", CATEGORY_CFG, Severity.SKIP, "configuration non chargée")
    policy = ctx.config.external
    if not policy.enabled:
        return result("config.external", CATEGORY_CFG, Severity.PASS,
                      "modèles externes désactivés — fonctionnement 100 % local")
    return result(
        "config.external", CATEGORY_CFG, Severity.PASS,
        f"externe autorisé sur : {', '.join(sorted(policy.allowed_contexts))}",
        contexts=sorted(policy.allowed_contexts),
        daily_budget_eur=policy.daily_budget_eur,
        autonomous_dependency=False,
    )


def _service_check(name: str, required_default: bool = True):
    def run(ctx: CheckContext) -> CheckResult:
        if ctx.config is None:
            return result(f"service.{name}", CATEGORY_SVC, Severity.SKIP, "configuration non chargée")
        endpoint = ctx.config.services.get(name)
        if endpoint is None:
            return result(f"service.{name}", CATEGORY_SVC, Severity.SKIP, "service non déclaré")
        reachable = ctx.probe.tcp_open(endpoint.host, endpoint.port, timeout_s=ctx.timeout_s)
        if reachable:
            return result(f"service.{name}", CATEGORY_SVC, Severity.PASS,
                          f"{name} joignable sur {endpoint.host}:{endpoint.port}",
                          host=endpoint.host, port=endpoint.port)
        severity = Severity.FAIL if endpoint.required else Severity.WARN
        return result(
            f"service.{name}", CATEGORY_SVC, severity,
            f"{name} injoignable sur {endpoint.host}:{endpoint.port}",
            remediation=(
                "docker compose up -d — si le conteneur est déjà démarré et "
                "« healthy » mais reste injoignable ICI (TCP réellement refusé "
                "malgré un healthcheck interne réussi), la cause n'est plus "
                "APEX mais le réseau hôte : vérifier le mappage réel du port "
                f"(`docker port <conteneur {name}>`), puis le backend "
                "iptables/nftables de WSL2 (`sudo update-alternatives --config "
                "iptables`) — un mode incohérent avec celui utilisé par le "
                "démon Docker empêche silencieusement la règle DNAT du port "
                "publié vers 127.0.0.1 de s'appliquer, sans qu'aucune erreur "
                "ne remonte au niveau du conteneur."
            ),
            host=endpoint.host, port=endpoint.port,
        )

    return run


check_postgres = _service_check("postgres")
check_redis = _service_check("redis")
check_minio = _service_check("minio")
check_ollama = _service_check("ollama")


def check_postgres_auth(ctx: CheckContext) -> CheckResult:
    """Authentification RÉELLE contre Postgres — `service.postgres` ne fait
    qu'un `tcp_open`, jamais une connexion authentifiée : un conteneur
    joignable mais dont le rôle applicatif a un mot de passe désynchronisé
    de `POSTGRES_PASSWORD` (.env) passait donc pour PASS ici alors que
    chaque requête SQL réelle du serveur API échouait (cas réel diagnostiqué :
    le volume Postgres ne réapplique JAMAIS `POSTGRES_PASSWORD` après sa toute
    première initialisation — changer `.env` ensuite ne resynchronise rien).

    `skip` si les extras `db` (sqlalchemy/psycopg) ne sont pas installés, ou
    si `service.postgres` lui-même serait `SKIP`/injoignable — cette
    vérification n'a de sens qu'en complément d'une connexion TCP déjà
    établie, jamais en doublon de son diagnostic."""
    name = "service.postgres.auth"
    if ctx.config is None:
        return result(name, CATEGORY_SVC, Severity.SKIP, "configuration non chargée")
    endpoint = ctx.config.services.get("postgres")
    if endpoint is None:
        return result(name, CATEGORY_SVC, Severity.SKIP, "service non déclaré")
    try:
        import psycopg

        from apex.db.engine import database_url_from_config
    except ImportError:
        return result(name, CATEGORY_SVC, Severity.SKIP, "extras « db » non installés")

    from apex.errors import ConfigError

    try:
        url = database_url_from_config(ctx.config)
    except ConfigError as exc:
        return result(name, CATEGORY_SVC, Severity.SKIP, str(exc))
    dsn = url.replace("postgresql+psycopg://", "postgresql://", 1)

    try:
        # `connect_timeout` de psycopg/libpq est un entier de secondes (pas
        # de fraction) — jamais `ctx.timeout_s` (float) transmis tel quel.
        with psycopg.connect(dsn, connect_timeout=max(1, round(ctx.timeout_s))) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except psycopg.OperationalError as exc:
        detail = str(exc).strip()
        if "password authentication failed" in detail:
            return result(
                name, CATEGORY_SVC, Severity.FAIL,
                "authentification Postgres échouée — le mot de passe réel du "
                "rôle ne correspond plus à POSTGRES_PASSWORD (.env)",
                remediation=(
                    "Cause connue : le volume Postgres retient le mot de passe "
                    "de sa toute première initialisation ; changer "
                    "POSTGRES_PASSWORD dans .env après coup ne le "
                    "resynchronise jamais. Réparation sûre : ALTER ROLE "
                    "exécuté depuis l'intérieur du conteneur (authentification "
                    "locale de confiance, jamais via le mot de passe cassé "
                    "lui-même) — ne touche à aucune donnée."
                ),
                exc_type=exc.__class__.__name__,
            )
        return result(
            name, CATEGORY_SVC, Severity.WARN,
            f"connexion authentifiée impossible : {detail[:200]}",
            exc_type=exc.__class__.__name__,
        )
    except Exception as exc:  # sonde best-effort — jamais bloquant hors ce cas précis
        return result(
            name, CATEGORY_SVC, Severity.WARN,
            f"vérification impossible : {exc.__class__.__name__}: {exc}"[:200],
        )
    return result(name, CATEGORY_SVC, Severity.PASS, "authentification Postgres réelle réussie (SELECT 1)")


def _reference_is_hardware_compatible(endpoints: list[ModelEndpoint], config: ApexConfig) -> bool | None:
    """Le matériel local ACTIF a-t-il assez de VRAM pour l'un de ces endpoints ?

    Seuls les pools `location=local` et `status=ACTIVE` comptent (un pool
    `TARGET`, machine future non construite, ne rend jamais un modèle
    « compatible » avec une machine qui n'existe pas encore). La VRAM requise
    est `ModelEndpoint.effective_vram_gb` (la mesure prime toujours sur la
    déclaration).

    TROUVÉ EN DIAGNOSTIC (doctor bloquant sur une machine à 8 Go réelle) :
    comparer la VRAM requise à la VRAM TOTALE brute d'un pool (`required <=
    available`, sans réserve) juge « compatible » un modèle dont la
    consommation déclarée égale déjà la totalité de la carte — le contexte
    CUDA, le runtime d'inférence et l'OS retiennent toujours une part de la
    VRAM totale rapportée par le pilote, jamais disponible pour les poids du
    modèle. `HardwareThresholds.vram_headroom_fraction` (défaut 10 %,
    reconfigurable par déploiement) applique cette réserve de façon
    PROPORTIONNELLE, jamais un montant fixe : reste cohérent aussi bien sur
    une carte de 8 Go que sur une future carte de 48 Go.

    Retourne `None` quand l'information est insuffisante pour trancher (aucun
    pool local ACTIVE, ou aucune VRAM déclarée/mesurée pour ces endpoints) —
    l'appelant ne doit JAMAIS interpréter `None` comme une incompatibilité :
    le comportement conservateur existant (FAIL sur absence) s'applique.
    """
    local_active = [
        p for p in config.pools.values()
        if p.location is PoolLocation.LOCAL and p.status is PoolStatus.ACTIVE
    ]
    if not local_active:
        return None
    available = max(p.total_vram_gb for p in local_active)
    if available <= 0:
        return None
    usable = available * (1.0 - config.gate.hardware.vram_headroom_fraction)

    required = [e.effective_vram_gb for e in endpoints if e.effective_vram_gb is not None]
    if not required:
        return None
    return min(required) <= usable


def check_ollama_models(ctx: CheckContext) -> CheckResult:
    """Compare le catalogue déclaré aux modèles réellement disponibles."""
    if ctx.config is None:
        return result("ollama.models", CATEGORY_SVC, Severity.SKIP, "configuration non chargée")
    endpoint = ctx.config.services.get("ollama")
    if endpoint is None:
        return result("ollama.models", CATEGORY_SVC, Severity.SKIP, "ollama non déclaré")

    response = ctx.probe.http_get(
        f"http://{endpoint.host}:{endpoint.port}/api/tags", timeout_s=ctx.timeout_s
    )
    if response is None or response.status != 200:
        return result("ollama.models", CATEGORY_SVC, Severity.SKIP, "ollama injoignable")

    try:
        present = {m.get("name", "") for m in response.json().get("models", [])}
    except (ValueError, AttributeError):
        return result("ollama.models", CATEGORY_SVC, Severity.WARN, "réponse Ollama illisible")

    def served_endpoints(family: str) -> list[ModelEndpoint]:
        return [e for e in ctx.config.endpoints if e.served_model.startswith(family)]

    missing_reference: list[str] = []
    missing_reference_incompatible: list[str] = []
    missing_candidates: list[str] = []
    for role in ctx.config.model_roles.values():
        for index, candidate in enumerate(role.candidates):
            endpoints = served_endpoints(candidate.family)
            if not endpoints:
                continue
            names = {e.served_model for e in endpoints}
            if any(any(p.startswith(n.split(":")[0]) for p in present) for n in names):
                continue
            label = f"{role.role}:{candidate.id}"
            if index != 0:
                missing_candidates.append(label)
                continue
            # Candidat de référence absent : bloquant, sauf si le matériel
            # local ACTIF ne peut structurellement pas le faire tourner —
            # dans ce cas précis, ce n'est pas un défaut d'installation.
            compatible = _reference_is_hardware_compatible(endpoints, ctx.config)
            if compatible is False:
                missing_reference_incompatible.append(label)
            else:
                missing_reference.append(label)

    if missing_reference:
        return result(
            "ollama.models", CATEGORY_SVC, Severity.FAIL,
            f"modèles de référence absents : {', '.join(missing_reference)}",
            remediation="ollama pull <modèle> pour chaque rôle manquant.",
            missing_reference=missing_reference,
            missing_reference_incompatible=missing_reference_incompatible,
            missing_candidates=missing_candidates,
        )
    if missing_reference_incompatible or missing_candidates:
        parts = []
        if missing_reference_incompatible:
            parts.append(
                "modèles de référence incompatibles avec la VRAM du pool local ACTIVE : "
                + ", ".join(missing_reference_incompatible)
            )
        if missing_candidates:
            parts.append(f"candidats concurrents absents : {', '.join(missing_candidates)}")
        return result(
            "ollama.models", CATEGORY_SVC, Severity.WARN,
            " ; ".join(parts),
            remediation=(
                "Non bloquant. Un modèle de référence incompatible ne tient pas sur la VRAM "
                "locale ACTIVE déclarée/mesurée — son installation n'est pas requise pour un "
                "doctor vert ; il reste dans le catalogue pour un matériel futur plus capable. "
                "Rappel : le modèle de référence n'est pas réputé supérieur ; sans concurrent "
                "installé, aucun benchmark comparatif n'est possible."
            ),
            missing_reference=missing_reference,
            missing_reference_incompatible=missing_reference_incompatible,
            missing_candidates=missing_candidates,
        )
    return result("ollama.models", CATEGORY_SVC, Severity.PASS,
                  f"{len(present)} modèles disponibles", present=sorted(present))


def check_interfaces_integrity(ctx: CheckContext) -> CheckResult:
    """Le registre des abstractions doit refléter la réalité du code.

    Une abstraction déclarée IMPLEMENTED sans module importable est un
    mensonge du registre, et donc un échec bloquant.
    """
    lying: list[str] = []
    for item in ABSTRACTIONS:
        if item.status in (ImplStatus.IMPLEMENTED, ImplStatus.STUB) and not module_importable(item):
            lying.append(item.name)

    counts: dict[str, int] = {}
    for item in ABSTRACTIONS:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1

    if lying:
        return result(
            "interfaces.integrity", CATEGORY_CFG, Severity.FAIL,
            f"abstractions déclarées disponibles sans implémentation : {', '.join(lying)}",
            remediation="Corriger apex/interfaces/registry.py — le registre ne doit jamais anticiper.",
            lying=lying, counts=counts,
        )
    return result(
        "interfaces.integrity", CATEGORY_CFG, Severity.PASS,
        f"{len(ABSTRACTIONS)} abstractions déclarées, registre cohérent avec le code",
        counts=counts,
    )


CHECKS = (
    ("config.valid", check_config_loaded),
    ("config.external", check_external_policy),
    ("interfaces.integrity", check_interfaces_integrity),
    ("service.postgres", check_postgres),
    ("service.postgres.auth", check_postgres_auth),
    ("service.redis", check_redis),
    ("service.minio", check_minio),
    ("service.ollama", check_ollama),
    ("ollama.models", check_ollama_models),
)
