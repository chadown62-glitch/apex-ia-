"""Vérifications : placement disque, Docker, runtimes d'isolation, GPU.

Le check `docker.runtimes` matérialise le principe verrouillé n°4. Il ne se
contente pas de constater l'absence de runsc : il en tire la conséquence
fonctionnelle, à savoir que le Red Team (§46) reste désactivé tant qu'un hôte
d'isolation renforcée n'existe pas. Le diagnostic dit ce qui est *permis*, pas
seulement ce qui est *installé*.
"""

from __future__ import annotations

from apex.doctor.check import CheckContext, CheckResult, Severity, result

CATEGORY_FS = "stockage"
CATEGORY_DOCKER = "conteneurs"
CATEGORY_GPU = "gpu"

MIN_FREE_GB = 80.0


def check_paths_on_ext4(ctx: CheckContext) -> CheckResult:
    """Aucun chemin de travail ne doit résider sur un montage Windows."""
    if ctx.config is None:
        return result("fs.placement", CATEGORY_FS, Severity.SKIP, "configuration non chargée")

    offenders = {
        name: path
        for name, path in ctx.config.paths.all.items()
        if path.startswith("/mnt/c/") or path.startswith("/mnt/d/") or path.startswith("C:")
    }
    if offenders:
        return result(
            "fs.placement", CATEGORY_FS, Severity.FAIL,
            f"chemins sur un montage Windows : {', '.join(offenders)}",
            remediation=(
                "Déplacer ces chemins dans le système de fichiers ext4 de WSL2 "
                "(par exemple /home/<user>/apex-data). L'accès aux fichiers Windows "
                "depuis Linux est ralenti d'un ordre de grandeur sur les arborescences "
                "à nombreux petits fichiers."
            ),
            offenders=offenders,
        )
    return result("fs.placement", CATEGORY_FS, Severity.PASS,
                  "tous les chemins de travail sont hors montage Windows",
                  paths=ctx.config.paths.all)


def check_disk_space(ctx: CheckContext) -> CheckResult:
    target = ctx.config.paths.data_root if ctx.config else ctx.project_root
    free = ctx.probe.disk_free_gb(target)
    if free is None:
        return result("fs.space", CATEGORY_FS, Severity.WARN, f"espace libre indéterminé sur {target}")
    if free < MIN_FREE_GB:
        return result(
            "fs.space", CATEGORY_FS, Severity.WARN,
            f"{free:.0f} Go libres sur {target} — {MIN_FREE_GB:.0f} Go recommandés",
            remediation=(
                "Poids des modèles, images de conteneurs et journaux d'évaluation cumulent vite. "
                "Rappel : le disque virtuel WSL2 (.vhdx) croît sans se réduire seul."
            ),
            free_gb=round(free, 1),
        )
    return result("fs.space", CATEGORY_FS, Severity.PASS, f"{free:.0f} Go libres", free_gb=round(free, 1))


def check_docker(ctx: CheckContext) -> CheckResult:
    if ctx.probe.which("docker") is None:
        return result(
            "docker.present", CATEGORY_DOCKER, Severity.FAIL, "docker introuvable",
            remediation="Installer Docker Engine dans la distribution WSL2 (et non Docker Desktop).",
        )
    info = ctx.probe.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout_s=ctx.timeout_s)
    if not info.ok:
        return result(
            "docker.present", CATEGORY_DOCKER, Severity.FAIL, "démon Docker injoignable",
            remediation="sudo service docker start  (ou activer systemd dans /etc/wsl.conf)",
            stderr=info.stderr.strip()[:400],
        )
    return result("docker.present", CATEGORY_DOCKER, Severity.PASS,
                  f"Docker Engine {info.stdout.strip()}", server_version=info.stdout.strip())


def check_docker_flavour(ctx: CheckContext) -> CheckResult:
    """Docker Desktop ne permet pas de déclarer un runtime OCI alternatif."""
    if ctx.probe.which("docker") is None:
        return result("docker.flavour", CATEGORY_DOCKER, Severity.SKIP, "docker absent")
    ctx_out = ctx.probe.run(["docker", "context", "show"], timeout_s=ctx.timeout_s)
    name = ctx_out.stdout.strip() if ctx_out.ok else ""
    if "desktop" in name.lower():
        return result(
            "docker.flavour", CATEGORY_DOCKER, Severity.WARN,
            "Docker Desktop détecté — runtime OCI alternatif (runsc) non installable",
            remediation=(
                "Installer Docker Engine nativement dans la distribution WSL2. "
                "Docker Desktop ne fournit pas de moyen supporté de déclarer runsc, "
                "ce qui plafonne l'isolation disponible."
            ),
            context=name,
        )
    return result("docker.flavour", CATEGORY_DOCKER, Severity.PASS,
                  f"contexte Docker « {name or 'default'} »", context=name)


def check_isolation_runtimes(ctx: CheckContext) -> CheckResult:
    """Détermine le plafond d'isolation et ce qu'il autorise."""
    if ctx.probe.which("docker") is None:
        return result("docker.isolation", CATEGORY_DOCKER, Severity.SKIP, "docker absent")

    out = ctx.probe.run(["docker", "info", "--format", "{{.Runtimes}}"], timeout_s=ctx.timeout_s)
    runtimes = out.stdout.strip() if out.ok else ""
    has_runsc = "runsc" in runtimes

    if has_runsc:
        return result(
            "docker.isolation", CATEGORY_DOCKER, Severity.PASS,
            "runsc (gVisor) disponible — isolation renforcée possible",
            runtimes=runtimes, red_team_allowed=True, isolation_level="gvisor",
        )
    return result(
        "docker.isolation", CATEGORY_DOCKER, Severity.WARN,
        "runsc absent — plafond d'isolation : Docker durci (non-root, seccomp, réseau coupé)",
        remediation=(
            "Acceptable pour du code généré ordinaire. Conséquence verrouillée : "
            "le Red Team (§46) reste DÉSACTIVÉ jusqu'à disposer d'un hôte d'isolation "
            "renforcée (runsc fonctionnel, VM dédiée ou machine séparée). "
            "Test go/no-go prévu en phase 3."
        ),
        runtimes=runtimes, red_team_allowed=False, isolation_level="docker_hardened",
    )


def check_gpu(ctx: CheckContext) -> CheckResult:
    if ctx.probe.which("nvidia-smi") is None:
        return result(
            "gpu.present", CATEGORY_GPU, Severity.WARN,
            "nvidia-smi introuvable — APEX fonctionnera en CPU seul (mode autonome non viable)",
            remediation="Installer le pilote NVIDIA côté Windows ; WSL2 expose CUDA via /usr/lib/wsl/lib.",
        )
    out = ctx.probe.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        timeout_s=ctx.timeout_s,
    )
    if not out.ok:
        return result("gpu.present", CATEGORY_GPU, Severity.WARN, "nvidia-smi en échec",
                      remediation="Vérifier le pilote NVIDIA côté Windows.",
                      stderr=out.stderr.strip()[:300])

    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].isdigit():
            gpus.append({"model": parts[0], "vram_gb": round(int(parts[1]) / 1024, 1)})
    if not gpus:
        return result("gpu.present", CATEGORY_GPU, Severity.WARN, "aucun GPU listé")

    total = sum(g["vram_gb"] for g in gpus)
    severity = Severity.PASS if total >= 20 else Severity.WARN
    message = f"{len(gpus)} GPU, {total:.0f} Go de VRAM"
    remediation = ""
    if total < 20:
        remediation = (
            "Sous 20 Go, le rôle coder-main de référence (30B MoE) ne tient pas. "
            "Revoir le catalogue de modèles dans config.yaml."
        )
    return result("gpu.present", CATEGORY_GPU, severity, message,
                  remediation=remediation, gpus=gpus, total_vram_gb=total)


def check_gpu_matches_config(ctx: CheckContext) -> CheckResult:
    """La VRAM déclarée dans les ComputePool doit refléter le matériel réel."""
    if ctx.config is None:
        return result("gpu.config_match", CATEGORY_GPU, Severity.SKIP, "configuration non chargée")
    declared = max((p.total_vram_gb for p in ctx.config.pools.values()), default=0.0)
    if declared <= 0:
        return result("gpu.config_match", CATEGORY_GPU, Severity.WARN,
                      "aucun GPU déclaré dans les ComputePool")

    gpu_result = check_gpu(ctx)
    actual = float(gpu_result.facts.get("total_vram_gb", 0.0) or 0.0)
    if actual == 0.0:
        return result("gpu.config_match", CATEGORY_GPU, Severity.SKIP, "VRAM réelle inconnue")
    if abs(actual - declared) > max(2.0, 0.15 * declared):
        return result(
            "gpu.config_match", CATEGORY_GPU, Severity.WARN,
            f"VRAM déclarée {declared:.0f} Go contre {actual:.0f} Go mesurés",
            remediation="Aligner compute.pools.*.gpus sur le matériel réel — le budget de calcul en dépend.",
            declared_gb=declared, actual_gb=actual,
        )
    return result("gpu.config_match", CATEGORY_GPU, Severity.PASS,
                  f"configuration alignée sur le matériel ({actual:.0f} Go)",
                  declared_gb=declared, actual_gb=actual)


CHECKS = (
    ("fs.placement", check_paths_on_ext4),
    ("fs.space", check_disk_space),
    ("docker.present", check_docker),
    ("docker.flavour", check_docker_flavour),
    ("docker.isolation", check_isolation_runtimes),
    ("gpu.present", check_gpu),
    ("gpu.config_match", check_gpu_matches_config),
)
