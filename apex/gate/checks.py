"""Les 20 vérifications du Hardware & Environment Gate.

Trois notes de conception :

1. **Une vérification impossible pour des raisons de sécurité reste
   impossible.** GATE-01 (version de Windows) ne peut pas être lue depuis WSL2
   si l'interopérabilité est désactivée — ce qui est la configuration
   recommandée. Le Gate déclare alors NOT_TESTED et indique la lecture
   manuelle. Il ne réactive pas l'interop pour faire passer un test.

2. **Les vérifications qui exécutent un conteneur exigent un consentement
   explicite** (`--allow-container-exec`). Sans lui : NOT_TESTED. Le Gate reste
   en lecture seule par défaut, comme le doctor.

3. **Les faits publiés distinguent `false` de `null`.** Une capacité non testée
   vaut `null`, jamais `false` : les phases suivantes doivent pouvoir faire la
   différence entre « absent » et « inconnu ».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from apex.config.schema import ApexConfig, HardwareThresholds, PoolStatus
from apex.version import MIN_PYTHON
from apex.gate.compose import analyse_compose_file
from apex.gate.model import Evidence, GateCheckResult, GateStatus
from apex.sandbox.detect import detect_capabilities
from apex.sandbox.model import IsolationLevel, SandboxCapabilityReport
from apex.util.probe import Probe

#: Repli utilisé seulement quand aucune configuration n'est chargée
#: (`ctx.config is None`) — la source de vérité normale est
#: `ApexConfig.gate.hardware` (voir `apex.config.schema.HardwareThresholds`),
#: configurable précisément parce que ces seuils décrivent un choix de
#: déploiement, pas une limite fixe de l'architecture APEX.
_DEFAULT_THRESHOLDS = HardwareThresholds.parse({})
MIN_FREE_GB = _DEFAULT_THRESHOLDS.min_free_disk_gb
MIN_RAM_GB = _DEFAULT_THRESHOLDS.min_ram_gb
MIN_VRAM_GB = _DEFAULT_THRESHOLDS.min_vram_gb
MIN_CORES = _DEFAULT_THRESHOLDS.min_cpu_threads


def _thresholds(ctx: "GateContext") -> HardwareThresholds:
    return ctx.config.gate.hardware if ctx.config else _DEFAULT_THRESHOLDS

PROBE_IMAGE = "docker.io/library/alpine:3.20"
CUDA_PROBE_IMAGE = "docker.io/nvidia/cuda:12.4.1-base-ubuntu22.04"


@dataclass
class GateContext:
    probe: Probe
    config: ApexConfig | None
    allow_container_exec: bool = False
    allow_network_probe: bool = False
    timeout_s: float = 20.0
    #: Racine du depot, pour les controles qui lisent un fichier du projet.
    project_root: str = "."
    #: Détection RÉELLE de l'isolation namespaces/cgroups v1 (apex.sandbox.
    #: detect — SEUL backend SandboxProvider implémenté, étape 7), injectable
    #: pour les tests exactement comme `probe` l'est déjà. Par défaut, la
    #: vraie fonction : GATE-19 mesure alors CETTE machine, jamais une
    #: simulation, sauf override explicite par un test.
    detect_sandbox_capabilities: Callable[[], SandboxCapabilityReport] = detect_capabilities


def _ok(check_id: str, title: str, message: str, **kw: Any) -> GateCheckResult:
    return GateCheckResult(check_id=check_id, title=title, status=GateStatus.PASS, message=message, **kw)


def _fail(check_id: str, title: str, message: str, **kw: Any) -> GateCheckResult:
    return GateCheckResult(check_id=check_id, title=title, status=GateStatus.FAIL, message=message, **kw)


def _degraded(check_id: str, title: str, message: str, **kw: Any) -> GateCheckResult:
    return GateCheckResult(check_id=check_id, title=title, status=GateStatus.DEGRADED, message=message, **kw)


def _unknown(
    check_id: str, title: str, message: str, *, reason: str = "not_applicable", **kw: Any
) -> GateCheckResult:
    """UNKNOWN exige toujours une raison.

    « Inconnu » sans motif est indistinguable d'un oubli. Les trois motifs
    possibles n'appellent pas la même action : NOT_AUTHORIZED se lève en
    relançant avec un drapeau, NOT_APPLICABLE est définitif sur cette machine,
    UNMEASURABLE signale que la mesure est empêchée par une contrainte
    délibérée (typiquement une garantie de sécurité).
    """
    assert reason in {"not_authorized", "not_applicable", "unmeasurable", "dependency_missing"}, reason
    return GateCheckResult(
        check_id=check_id, title=title, status=GateStatus.UNKNOWN,
        message=message, unknown_reason=reason, **kw
    )


def _is_wsl(ctx: GateContext) -> bool:
    release = ctx.probe.platform_info().get("release", "")
    return "microsoft" in release.lower() or ctx.probe.env("WSL_DISTRO_NAME") is not None


def _interop_disabled(ctx: GateContext) -> bool:
    content = (ctx.probe.read_text("/etc/wsl.conf") or "").replace(" ", "")
    return "enabled=false" in content


# ---------------------------------------------------------------------------
# 01–04 : hôte, WSL2, distribution, noyau
# ---------------------------------------------------------------------------


def gate_01_windows_host(ctx: GateContext) -> GateCheckResult:
    title = "Version de l'hôte Windows"
    if not _is_wsl(ctx):
        return _unknown("GATE-01", title, "hors WSL2 — pas d'hôte Windows à interroger")

    if _interop_disabled(ctx):
        return _unknown(
            "GATE-01", title,
            "interopérabilité Windows désactivée — la version de l'hôte n'est pas lisible depuis WSL2",
            reason="unmeasurable",
            remediation=(
                "Comportement attendu et souhaitable : l'interop désactivée est une mesure de "
                "durcissement (ADR-0005). Relever la version manuellement côté Windows "
                "(winver, ou : cmd /c ver) et la consigner. Le Gate ne réactive pas l'interop "
                "pour satisfaire une vérification."
            ),
            facts={"interop_disabled": True, "windows_version": None},
        )

    out = ctx.probe.run(["cmd.exe", "/c", "ver"], timeout_s=ctx.timeout_s)
    if not out.ok:
        return _degraded("GATE-01", title, "version de Windows illisible malgré l'interop active",
                     evidence=(Evidence.from_output("cmd.exe /c ver", out.stderr),))

    version = out.stdout.strip()
    build = re.search(r"(\d+\.\d+\.\d+\.\d+)", version)
    encoding = getattr(out, "encoding", "utf-8")
    facts = {
        "windows_version": build.group(1) if build else version,
        "interop_disabled": False,
        # cmd.exe écrit dans la page de code OEM de la console, pas en UTF-8.
        # L'encodage retenu est publié : un décodage approximatif doit rester
        # visible dans le rapport plutôt que d'être masqué.
        "output_encoding": encoding,
    }
    if not build:
        return _degraded(
            "GATE-01", title,
            f"sortie de cmd.exe non exploitable (décodée en {encoding})",
            remediation="Relever la version manuellement côté Windows : winver.",
            facts=facts, evidence=(Evidence.from_output("cmd.exe /c ver", version),),
        )
    return _ok("GATE-01", title, f"{version} (encodage : {encoding})",
               facts=facts, evidence=(Evidence.from_output("cmd.exe /c ver", version),))


def gate_02_wsl2(ctx: GateContext) -> GateCheckResult:
    title = "WSL2 actif"
    info = ctx.probe.platform_info()
    if info.get("system") != "Linux":
        return _fail("GATE-02", title, f"système « {info.get('system')} » — APEX s'exécute sous Linux",
                     remediation="Lancer APEX depuis la distribution WSL2.", blocks_phases=(1,))
    if not _is_wsl(ctx):
        return _degraded("GATE-02", title, "Linux natif détecté (pas WSL2)",
                     remediation="Configuration valide et même préférable ; "
                                 "les vérifications spécifiques à WSL2 seront ignorées.",
                     facts={"wsl2": False, "native_linux": True})
    distro = ctx.probe.env("WSL_DISTRO_NAME") or "inconnue"
    return _ok("GATE-02", title, f"WSL2 actif, distribution « {distro} »",
               facts={"wsl2": True, "wsl_distro": distro})


def gate_03_distribution(ctx: GateContext) -> GateCheckResult:
    title = "Distribution Linux"
    content = ctx.probe.read_text("/etc/os-release")
    if not content:
        return _degraded("GATE-03", title, "/etc/os-release illisible")
    fields = {}
    for line in content.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key] = value.strip().strip('"')
    name = fields.get("PRETTY_NAME") or fields.get("NAME", "inconnue")
    return _ok("GATE-03", title, name,
               facts={"distro_id": fields.get("ID"), "distro_version": fields.get("VERSION_ID"),
                      "distro_name": name},
               evidence=(Evidence.from_output("/etc/os-release", content, 200),))


def gate_04_kernel(ctx: GateContext) -> GateCheckResult:
    title = "Noyau"
    release = ctx.probe.platform_info().get("release", "")
    if not release:
        return _degraded("GATE-04", title, "version de noyau indéterminée")
    match = re.match(r"(\d+)\.(\d+)", release)
    version = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    facts = {"kernel": release, "kernel_major_minor": f"{version[0]}.{version[1]}"}
    # gVisor exige x86_64 Linux >= 3.17 ; tout noyau WSL2 moderne satisfait ce seuil.
    if version < (5, 10):
        return _degraded("GATE-04", title, f"noyau {release} — ancien, mettre à jour WSL2 (wsl --update)",
                     facts=facts)
    return _ok("GATE-04", title, f"noyau {release}", facts=facts)


# ---------------------------------------------------------------------------
# 05–06 : Python, Docker
# ---------------------------------------------------------------------------


def gate_05_python(ctx: GateContext) -> GateCheckResult:
    title = "Python"
    version = ctx.probe.platform_info().get("python", "")
    parts = tuple(int(p) for p in re.findall(r"\d+", version)[:3]) or (0,)
    facts = {"python_version": version}
    if parts[:2] < MIN_PYTHON:
        return _fail("GATE-05", title, f"Python {version} — 3.12+ requis",
                     remediation="sudo apt install python3.12 python3.12-venv (dans WSL2)",
                     facts=facts, blocks_phases=(1,))
    return _ok("GATE-05", title, f"Python {version}", facts=facts)


def gate_06_docker(ctx: GateContext) -> GateCheckResult:
    title = "Docker Engine et Compose"
    if ctx.probe.which("docker") is None:
        return _fail("GATE-06", title, "docker introuvable",
                     remediation="curl -fsSL https://get.docker.com | sh ; sudo usermod -aG docker $USER",
                     facts={"docker": None, "compose": None}, blocks_phases=(1, 2, 3))

    server = ctx.probe.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout_s=ctx.timeout_s)
    if not server.ok:
        return _fail("GATE-06", title, "démon Docker injoignable",
                     remediation="sudo service docker start (ou systemd=true dans /etc/wsl.conf)",
                     evidence=(Evidence.from_output("docker info", server.stderr),),
                     facts={"docker": None}, blocks_phases=(1, 2, 3))

    compose = ctx.probe.run(["docker", "compose", "version", "--short"], timeout_s=ctx.timeout_s)
    context = ctx.probe.run(["docker", "context", "show"], timeout_s=ctx.timeout_s)
    flavour = context.stdout.strip() if context.ok else "inconnu"
    facts = {
        "docker": server.stdout.strip(),
        "compose": compose.stdout.strip() if compose.ok else None,
        "docker_context": flavour,
        "docker_desktop": "desktop" in flavour.lower(),
    }
    if not compose.ok:
        return _degraded("GATE-06", title, f"Docker {facts['docker']} présent, plugin compose absent",
                     remediation="sudo apt install docker-compose-plugin", facts=facts)
    if facts["docker_desktop"]:
        return _degraded(
            "GATE-06", title,
            f"Docker Desktop détecté (contexte « {flavour} »)",
            remediation=(
                "Docker Desktop ne fournit pas de moyen supporté de déclarer un runtime OCI "
                "alternatif : il plafonne l'isolation atteignable (GATE-20). Installer Docker "
                "Engine nativement dans la distribution WSL2."
            ),
            facts=facts,
        )
    return _ok("GATE-06", title, f"Docker {facts['docker']}, Compose {facts['compose']}", facts=facts)


# ---------------------------------------------------------------------------
# 07–10 : GPU
# ---------------------------------------------------------------------------


def _nvidia_smi(ctx: GateContext):
    return ctx.probe.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
         "--format=csv,noheader,nounits"],
        timeout_s=ctx.timeout_s,
    )


def _parse_gpus(output: str) -> list[dict[str, Any]]:
    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4 and parts[1].isdigit():
            gpus.append({
                "model": parts[0],
                "vram_total_gb": round(int(parts[1]) / 1024, 2),
                "vram_free_gb": round(int(parts[2]) / 1024, 2) if parts[2].isdigit() else None,
                "driver_version": parts[3],
            })
    return gpus


def detect_gpu_vendor(ctx: GateContext) -> dict[str, Any]:
    """Identifie le fournisseur GPU sans supposer NVIDIA.

    Le catalogue de modèles d'APEX suppose CUDA, mais le Gate ne doit pas
    confondre « pas de NVIDIA » et « pas de GPU ». Un GPU AMD intégré
    (typiquement un APU Ryzen avec Vega) est bien présent : il n'est simplement
    pas exploitable par la pile actuelle. Enregistrer « aucun GPU » dans
    l'empreinte d'une telle machine serait un faux négatif durable.

    Sous WSL2, la détection d'un iGPU AMD est peu fiable : lspci n'expose rien
    d'utile et le passage graphique se fait via /dev/dxg. On rapporte donc ce
    qu'on observe, avec le degré de certitude correspondant, plutôt qu'une
    conclusion ferme.
    """
    facts: dict[str, Any] = {
        "gpu_vendor": None,
        "gpu_usable_by_apex": False,
        "gpu_detection_confidence": "certain",
    }

    if ctx.probe.which("nvidia-smi") is not None:
        facts.update(gpu_vendor="nvidia", gpu_usable_by_apex=True)
        return facts

    if ctx.probe.which("rocm-smi") is not None:
        facts.update(gpu_vendor="amd_rocm", gpu_usable_by_apex=False)
        return facts

    # Indices d'un GPU présent mais non pilotable par la pile actuelle.
    dri = ctx.probe.exists("/dev/dri")
    dxg = ctx.probe.exists("/dev/dxg")
    if dri or dxg:
        facts.update(
            gpu_vendor="other_or_integrated",
            gpu_usable_by_apex=False,
            gpu_detection_confidence="indices_seulement",
            dev_dri=dri,
            dev_dxg=dxg,
        )
        return facts

    facts.update(gpu_vendor="none", gpu_detection_confidence="indices_seulement")
    return facts


def gate_07_gpu_from_wsl(ctx: GateContext) -> GateCheckResult:
    title = "Accès GPU depuis WSL2"
    vendor = detect_gpu_vendor(ctx)

    if vendor["gpu_vendor"] == "amd_rocm":
        return _degraded(
            "GATE-07", title,
            "GPU AMD détecté (rocm-smi) — présent mais non exploitable par APEX",
            remediation=(
                "Le catalogue de modèles et le ModelProvider actuels supposent CUDA. "
                "Aucun ModelProvider ROCm n'existe (il en faudrait un derrière la même "
                "interface, cf. ADR-0003).\n"
                "Cette machine peut servir à éprouver l'instrument et les phases 1 à 5, "
                "pas à exécuter le mode autonome."
            ),
            facts={"gpu_visible_from_wsl": True, **vendor}, blocks_phases=(6,),
        )

    if vendor["gpu_vendor"] == "other_or_integrated":
        return _degraded(
            "GATE-07", title,
            "GPU présent mais non identifié comme NVIDIA — non exploitable par APEX",
            remediation=(
                "Indices de présence relevés (/dev/dri ou /dev/dxg) sans pilote de calcul "
                "utilisable. Cas typique : APU AMD avec iGPU intégré, dont la mémoire est "
                "partagée avec la RAM système et dont le support de calcul est absent ou "
                "non supporté en amont.\n"
                "Ce n'est PAS équivalent à « aucun GPU » : la distinction est conservée "
                "dans l'empreinte matérielle."
            ),
            facts={"gpu_visible_from_wsl": False, **vendor}, blocks_phases=(6,),
        )

    if vendor["gpu_vendor"] == "none":
        return _degraded(
            "GATE-07", title, "aucun GPU détecté — CPU seul",
            remediation=(
                "Installer le pilote NVIDIA côté HÔTE WINDOWS (jamais dans la distribution). "
                "WSL2 expose ensuite les bibliothèques dans /usr/lib/wsl/lib.\n"
                "Repli : CPU seul — mode interactif utilisable, mode autonome non viable."
            ),
            facts={"gpu_visible_from_wsl": False, **vendor}, blocks_phases=(6,),
        )
    out = _nvidia_smi(ctx)
    if not out.ok:
        return _degraded("GATE-07", title, "nvidia-smi présent mais en échec",
                     evidence=(Evidence.from_output("nvidia-smi", out.stderr),),
                     facts={"gpu_visible_from_wsl": False}, blocks_phases=(6,))
    wsl_libs = ctx.probe.exists("/usr/lib/wsl/lib")
    return _ok("GATE-07", title, "GPU NVIDIA visible depuis WSL2",
               facts={"gpu_visible_from_wsl": True, "wsl_cuda_libs": wsl_libs, **vendor},
               evidence=(Evidence.from_output("nvidia-smi", out.stdout),))


def gate_08_gpu_model(ctx: GateContext) -> GateCheckResult:
    title = "Modèle exact du GPU"
    if ctx.probe.which("nvidia-smi") is None:
        vendor = detect_gpu_vendor(ctx)
        return _unknown(
            "GATE-08", title,
            f"modèle non identifiable — fournisseur détecté : {vendor['gpu_vendor']}",
            reason="not_applicable", facts={"gpus": [], **vendor},
        )
    out = _nvidia_smi(ctx)
    gpus = _parse_gpus(out.stdout) if out.ok else []
    if not gpus:
        return _unknown("GATE-08", title, "modèle indéterminé", facts={"gpus": []})
    names = ", ".join(g["model"] for g in gpus)
    return _ok("GATE-08", title, names, facts={"gpus": gpus, "gpu_count": len(gpus)})


def gate_09_vram(ctx: GateContext) -> GateCheckResult:
    title = "VRAM réellement disponible"
    if ctx.probe.which("nvidia-smi") is None:
        vendor = detect_gpu_vendor(ctx)
        note = (
            "mémoire partagée avec la RAM système, non comparable à de la VRAM dédiée"
            if vendor["gpu_vendor"] in ("amd_rocm", "other_or_integrated")
            else "aucun GPU détecté"
        )
        return _unknown("GATE-09", title, f"VRAM non mesurable — {note}",
                        reason="not_applicable",
                        facts={"vram_total_gb": None, "vram_free_gb": None, **vendor})
    out = _nvidia_smi(ctx)
    gpus = _parse_gpus(out.stdout) if out.ok else []
    if not gpus:
        return _unknown("GATE-09", title, "VRAM indéterminée",
                         facts={"vram_total_gb": None, "vram_free_gb": None})

    total = sum(g["vram_total_gb"] for g in gpus)
    free = sum(g["vram_free_gb"] or 0 for g in gpus)
    facts: dict[str, Any] = {"vram_total_gb": round(total, 1), "vram_free_gb": round(free, 1)}

    declared = 0.0
    if ctx.config:
        # PoolStatus.TARGET (machine visée, pas encore construite) est exclu :
        # comparer la VRAM mesurée sur CE poste à un pool délibérément déclaré
        # pour la future machine dédiée d'APEX produirait un DEGRADED sur une
        # non-concordance attendue, pas une vraie erreur de configuration.
        declared = max(
            (p.total_vram_gb for p in ctx.config.pools.values() if p.status is PoolStatus.ACTIVE),
            default=0.0,
        )
        facts["vram_declared_gb"] = declared

    if total < _thresholds(ctx).min_vram_gb:
        return _degraded(
            "GATE-09", title,
            f"{total:.1f} Go de VRAM — le rôle coder_main de référence (30B MoE) ne tient pas",
            remediation=(
                "Revoir models.roles dans config.yaml pour un catalogue adapté. "
                "Le budget nocturne en minutes-GPU doit être réduit en conséquence."
            ),
            facts=facts,
        )
    if declared and abs(total - declared) > max(2.0, 0.15 * declared):
        return _degraded("GATE-09", title,
                     f"{total:.1f} Go mesurés contre {declared:.1f} Go déclarés dans config.yaml",
                     remediation="Aligner compute.pools.*.gpus : le budget de calcul en dépend.",
                     facts=facts)
    return _ok("GATE-09", title, f"{total:.1f} Go au total, {free:.1f} Go libres", facts=facts)


def gate_10_cuda_driver(ctx: GateContext) -> GateCheckResult:
    title = "Pilote et runtime CUDA"
    if ctx.probe.which("nvidia-smi") is None:
        return _unknown("GATE-10", title, "nvidia-smi absent — pas de pile CUDA",
                        reason="not_applicable",
                        facts={"driver_version": None, "cuda_version": None,
                               **detect_gpu_vendor(ctx)})
    out = ctx.probe.run(["nvidia-smi"], timeout_s=ctx.timeout_s)
    driver = cuda = None
    if out.ok:
        driver_match = re.search(r"Driver Version:\s*([\d.]+)", out.stdout)
        cuda_match = re.search(r"CUDA Version:\s*([\d.]+)", out.stdout)
        driver = driver_match.group(1) if driver_match else None
        cuda = cuda_match.group(1) if cuda_match else None
    facts = {"driver_version": driver, "cuda_version": cuda,
             "wsl_cuda_libs": ctx.probe.exists("/usr/lib/wsl/lib")}
    if not driver:
        return _degraded("GATE-10", title, "version de pilote illisible", facts=facts,
                     evidence=(Evidence.from_output("nvidia-smi", out.stdout),))
    return _ok("GATE-10", title, f"pilote {driver}, CUDA {cuda or 'n/d'}", facts=facts)


# ---------------------------------------------------------------------------
# 11–14 : disque, RAM, CPU, virtualisation
# ---------------------------------------------------------------------------


def gate_11_disk(ctx: GateContext) -> GateCheckResult:
    """Espace disque du systeme de fichiers qui portera les donnees d'APEX.

    K1 : la version precedente mesurait directement `paths.data_root`. Ce
    repertoire est cree a l'usage, pas a l'installation — il n'existe donc pas
    sur une machine neuve, `shutil.disk_usage` levait, et le controle
    rapportait « espace libre indetermine ». C'etait un faux DEGRADED : la
    machine pouvait disposer de largement assez de place.

    La sonde remonte desormais au premier ancetre existant. C'est le meme
    point de montage, donc la mesure est valide pour le futur chemin, et
    aucun repertoire n'est cree.
    """
    title = "Espace disque"
    target = ctx.config.paths.data_root if ctx.config else "/"
    disk = ctx.probe.disk_probe(target)

    facts: dict[str, Any] = {
        "path_requested": target,
        "path_measured": disk.measured_path,
        "path_exists": disk.requested_exists,
        "measured_on_ancestor": disk.fell_back_to_ancestor,
        "disk_free_gb": round(disk.free_gb, 1) if disk.free_gb is not None else None,
        "disk_total_gb": round(disk.total_gb, 1) if disk.total_gb is not None else None,
        "disk_error": disk.error,
    }

    # Cas 4 : erreur systeme reelle. Ne jamais la masquer.
    if disk.error and disk.error != "aucun ancetre existant":
        return _degraded(
            "GATE-11", title,
            f"mesure impossible sur {disk.measured_path} : {disk.error}",
            remediation=(
                "Erreur systeme, distincte d'un repertoire absent. Verifier les permissions "
                "et l'etat du peripherique avant de conclure sur l'espace disponible."
            ),
            facts=facts,
        )

    # Cas 3 : aucune mesure possible.
    if not disk.measured:
        return _unknown(
            "GATE-11", title, f"aucun systeme de fichiers mesurable pour {target}",
            reason="unmeasurable", facts=facts,
        )

    free = disk.free_gb or 0.0
    origin = (
        f"{free:.0f} Go libres sur {disk.measured_path}"
        if not disk.fell_back_to_ancestor
        else f"{free:.0f} Go libres sur {disk.measured_path} "
             f"(le chemin cible {target} n'existe pas encore)"
    )

    # Cas 2 : espace reellement insuffisant.
    min_free_gb = _thresholds(ctx).min_free_disk_gb
    if free < min_free_gb:
        return _degraded(
            "GATE-11", title, f"{origin} — {min_free_gb:.0f} Go recommandes",
            remediation=(
                "Poids de modeles (~20 Go par modele Q4), images de conteneurs et journaux "
                "d'evaluation cumulent vite. Le disque virtuel WSL2 (.vhdx) croit sans se "
                "reduire seul : prevoir un compactage periodique."
            ),
            facts=facts,
        )

    # Cas 1 : mesure valide et suffisante.
    return _ok("GATE-11", title, origin, facts=facts)


def gate_12_ram(ctx: GateContext) -> GateCheckResult:
    title = "RAM disponible"
    content = ctx.probe.read_text("/proc/meminfo")
    if not content:
        return _degraded("GATE-12", title, "/proc/meminfo illisible", facts={"ram_total_gb": None})
    total = available = None
    for line in content.splitlines():
        if line.startswith("MemTotal:"):
            total = int(re.findall(r"\d+", line)[0]) / (1024 * 1024)
        elif line.startswith("MemAvailable:"):
            available = int(re.findall(r"\d+", line)[0]) / (1024 * 1024)
    facts = {"ram_total_gb": round(total, 1) if total else None,
             "ram_available_gb": round(available, 1) if available else None}
    if total and total < _thresholds(ctx).min_ram_gb:
        return _degraded(
            "GATE-12", title, f"{total:.0f} Go de RAM visibles depuis WSL2",
            remediation=(
                "Vérifier la clé memory= dans %UserProfile%\\.wslconfig. Sans limite explicite, "
                "WSL2 réclame environ la moitié de la RAM et la rend mal."
            ),
            facts=facts,
        )
    return _ok("GATE-12", title,
               f"{total:.0f} Go au total, {available:.0f} Go disponibles" if total and available
               else "RAM détectée", facts=facts)


def gate_13_cpu(ctx: GateContext) -> GateCheckResult:
    title = "CPU et threads"
    content = ctx.probe.read_text("/proc/cpuinfo", max_bytes=200_000) or ""
    threads = content.count("processor\t:") or content.count("processor  :")
    model = ""
    match = re.search(r"model name\s*:\s*(.+)", content)
    if match:
        model = match.group(1).strip()
    facts = {"cpu_model": model or None, "cpu_threads": threads or None}
    min_cores = _thresholds(ctx).min_cpu_threads
    if threads and threads < min_cores:
        return _degraded(
            "GATE-13", title, f"{threads} threads — {min_cores} recommandés",
            remediation=(
                "Compilation, tests, lint et mutation testing tournent sur CPU en parallèle du "
                "GPU. Un CPU étroit sérialise l'évaluation derrière la génération et réduit le "
                "nombre de task-runs par nuit. Ajuster processors= dans .wslconfig."
            ),
            facts=facts,
        )
    return _ok("GATE-13", title, f"{threads} threads — {model or 'modèle inconnu'}", facts=facts)


def gate_14_virtualization(ctx: GateContext) -> GateCheckResult:
    """Infrastructure de virtualisation/durcissement — KVM, virtualisation
    imbriquée, cgroups v2 unifié. ADR-0005 : cette infrastructure sert la
    « VM dédiée » d'isolation renforcée du Red Team (phase 21), pas le cœur
    fonctionnel de la phase 3 (Sandbox). La capacité RÉELLE de Sandbox
    (namespaces + cgroups v1, suffisante pour l'exécution ordinaire de code
    généré) est mesurée séparément par GATE-19
    (`apex.sandbox.detect.detect_capabilities`, ADR-0012) — jamais par ce
    contrôle. Un FAIL ici documente honnêtement l'absence de cgroups v2 sur
    CETTE machine ; il ne signifie PAS que Sandbox est cassé ou dépourvu de
    limites réelles (voir GATE-19 pour ce qui est réellement appliqué)."""
    title = "Virtualisation et cgroups"
    cgroups_v2 = ctx.probe.exists("/sys/fs/cgroup/cgroup.controllers")
    kvm = ctx.probe.exists("/dev/kvm")
    flags = ctx.probe.read_text("/proc/cpuinfo", max_bytes=100_000) or ""
    nested = "vmx" in flags or "svm" in flags
    facts = {"cgroups_v2": cgroups_v2, "kvm_available": kvm, "nested_virtualization": nested}

    if not cgroups_v2:
        return _fail(
            "GATE-14", title,
            "cgroups v2 (chemin unifié standard) absent sur cette machine — infrastructure "
            "d'isolation renforcée (VM dédiée, ADR-0005) indisponible ; voir GATE-19 pour la "
            "capacité Sandbox réellement mesurée (namespaces + cgroups v1)",
            remediation=(
                "wsl --update puis wsl --shutdown. Nécessaire pour l'infrastructure "
                "d'isolation renforcée du Red Team (phase 21) — PAS un prérequis du cœur "
                "fonctionnel de la phase 3 (Sandbox), déjà validé via NAMESPACED (GATE-19)."
            ),
            facts=facts, blocks_phases=(21,),
        )
    if not nested:
        return _degraded("GATE-14", title,
                     "cgroups v2 disponible ; virtualisation imbriquée non détectée",
                     remediation="nestedVirtualization=true dans .wslconfig si une micro-VM "
                                 "d'isolation renforcée est envisagée (ADR-0005).",
                     facts=facts)
    return _ok("GATE-14", title, "cgroups v2 et virtualisation imbriquée disponibles", facts=facts)


# ---------------------------------------------------------------------------
# 15–16 : réseau, outils
# ---------------------------------------------------------------------------


def gate_15_network(ctx: GateContext) -> GateCheckResult:
    title = "Accès réseau"
    if not ctx.allow_network_probe:
        return _unknown(
            "GATE-15", title, "sonde réseau non autorisée", reason="not_authorized",
            remediation="Relancer avec --allow-network-probe pour tester la résolution et la sortie.",
            facts={"network_egress": None},
        )
    reachable = ctx.probe.tcp_open("1.1.1.1", 443, timeout_s=5.0)
    facts = {"network_egress": reachable}
    if not reachable:
        return _degraded(
            "GATE-15", title, "aucune sortie réseau détectée",
            remediation=(
                "APEX est conçu pour fonctionner intégralement hors ligne (principe 3). "
                "Une sortie réseau reste nécessaire pour le téléchargement initial des modèles "
                "et des images de conteneurs."
            ),
            facts=facts,
        )
    return _ok("GATE-15", title, "sortie réseau disponible depuis l'hôte WSL2", facts=facts)


def gate_16_tools(ctx: GateContext) -> GateCheckResult:
    title = "Outils requis"
    import importlib.util

    tools = {name: ctx.probe.which(name) is not None for name in ("git", "curl", "ollama", "make")}
    tools["pyyaml"] = importlib.util.find_spec("yaml") is not None
    missing = [name for name, present in tools.items() if not present]
    hard_missing = [name for name in missing if name in {"git", "pyyaml"}]

    if hard_missing:
        return _fail("GATE-16", title, f"outils indispensables absents : {', '.join(hard_missing)}",
                     remediation="sudo apt install git ; pip install pyyaml",
                     facts={"tools": tools}, blocks_phases=(1,))
    if missing:
        return _degraded("GATE-16", title, f"outils absents : {', '.join(missing)}",
                     remediation="ollama est requis à partir de la phase 6 ; "
                                 "repli : llama.cpp derrière la même interface ModelProvider.",
                     facts={"tools": tools})
    return _ok("GATE-16", title, "tous les outils présents", facts={"tools": tools})


# ---------------------------------------------------------------------------
# 17–20 : exécution réelle de conteneurs et isolation
# ---------------------------------------------------------------------------


def gate_17_container_exec(ctx: GateContext) -> GateCheckResult:
    title = "Exécution réelle d'un conteneur"
    facts: dict[str, Any] = {"containers_runnable": None}
    if not ctx.allow_container_exec:
        return _unknown(
            "GATE-17", title, "exécution de conteneur non autorisée", reason="not_authorized",
            remediation=(
                "Relancer avec --allow-container-exec. Le Gate tirera une image minimale "
                f"({PROBE_IMAGE}) et exécutera une commande sans effet. C'est la seule action "
                "non purement passive du Gate, d'où le consentement explicite."
            ),
            facts=facts,
        )
    if ctx.probe.which("docker") is None:
        return _fail("GATE-17", title, "docker absent", facts={"containers_runnable": False},
                     blocks_phases=(3,))

    out = ctx.probe.run(
        ["docker", "run", "--rm", "--network", "none", "--user", "65534:65534",
         "--read-only", "--cap-drop", "ALL", "--pids-limit", "64",
         PROBE_IMAGE, "echo", "apex-gate-ok"],
        timeout_s=max(ctx.timeout_s, 120.0),
    )
    if out.ok and "apex-gate-ok" in out.stdout:
        return _ok("GATE-17", title,
                   "conteneur exécuté avec le profil durci (non-root, réseau coupé, rootfs RO)",
                   facts={"containers_runnable": True, "hardened_profile_ok": True},
                   evidence=(Evidence.from_output("docker run (profil durci)", out.stdout),))
    return _fail("GATE-17", title, "échec d'exécution du conteneur durci",
                 remediation="Vérifier les droits Docker et la disponibilité de l'image.",
                 facts={"containers_runnable": False, "hardened_profile_ok": False},
                 evidence=(Evidence.from_output("docker run", out.stderr),), blocks_phases=(3,))


def gate_18_gpu_in_container(ctx: GateContext) -> GateCheckResult:
    title = "GPU utilisable dans un conteneur"
    facts: dict[str, Any] = {"gpu_in_container": None}
    if not ctx.allow_container_exec:
        return _unknown("GATE-18", title, "exécution de conteneur non autorisée", reason="not_authorized",
                         remediation="Relancer avec --allow-container-exec.", facts=facts)
    if ctx.probe.which("docker") is None or ctx.probe.which("nvidia-smi") is None:
        return _unknown("GATE-18", title, "docker ou GPU absent", facts=facts)

    out = ctx.probe.run(
        ["docker", "run", "--rm", "--gpus", "all", CUDA_PROBE_IMAGE, "nvidia-smi", "-L"],
        timeout_s=max(ctx.timeout_s, 300.0),
    )
    if out.ok and "GPU" in out.stdout:
        return _ok("GATE-18", title, "GPU accessible depuis un conteneur",
                   facts={"gpu_in_container": True},
                   evidence=(Evidence.from_output("docker run --gpus all", out.stdout),))
    return _degraded(
        "GATE-18", title, "GPU inaccessible depuis un conteneur",
        remediation=(
            "sudo apt install nvidia-container-toolkit && "
            "sudo nvidia-ctk runtime configure --runtime=docker && sudo service docker restart\n"
            "Repli : exécuter Ollama sur l'hôte WSL2 plutôt que dans un conteneur. "
            "Fonctionnel, mais l'environnement d'inférence devient moins reproductible."
        ),
        facts={"gpu_in_container": False},
        evidence=(Evidence.from_output("docker run --gpus all", out.stderr),),
    )


def gate_19_isolation_state(ctx: GateContext) -> GateCheckResult:
    """Détermine le plafond d'isolation et ce qu'il autorise fonctionnellement.

    Deux familles de mécanismes, jamais confondues :

    - conteneurisation (Docker + runsc), mesurée directement ici, comme avant ;
    - namespaces Linux directs (`apex.sandbox.detect.detect_capabilities`,
      injectable via `ctx.detect_sandbox_capabilities` — exactement comme
      `ctx.probe` l'est déjà). C'est le SEUL backend `SandboxProvider`
      réellement implémenté (étape 7) ; sa détection n'est jamais dupliquée
      ici, seulement consultée.

    Avant cette révision, l'absence de Docker (ou l'absence de cgroups v2 au
    chemin unifié standard) faisait conclure « insuffisant »/UNKNOWN SANS
    jamais consulter cette détection — sous-déclarant une isolation réelle,
    déjà utilisée en production par `apex.missions.execution.launch_mission`
    depuis l'étape 7. Correction de détection, pas un relâchement de
    politique : `NAMESPACED` n'est JAMAIS promu au niveau `docker_hardened`/
    `gvisor` — ni seccomp ni jail complet du filesystem (voir
    `apex.sandbox.model.IsolationLevel`). `code_execution_allowed=True` pour
    NAMESPACED reflète la politique déjà opérante de `SandboxCapabilityReport.
    is_available` (NAMESPACED = seuil minimal d'exécution réelle) ;
    `red_team_allowed` reste False, exactement comme pour `docker_hardened`.
    """
    title = "État de l'isolation sandbox"

    docker_present = ctx.probe.which("docker") is not None
    has_runsc = False
    cgroups_v2 = ctx.probe.exists("/sys/fs/cgroup/cgroup.controllers")
    if docker_present:
        runtimes_out = ctx.probe.run(["docker", "info", "--format", "{{.Runtimes}}"], timeout_s=ctx.timeout_s)
        runtimes = runtimes_out.stdout.strip() if runtimes_out.ok else ""
        has_runsc = "runsc" in runtimes
    else:
        runtimes = ""

    namespace_facts: dict[str, Any] | None = None
    if has_runsc:
        level = "gvisor"
    elif docker_present and cgroups_v2:
        level = "docker_hardened"
    else:
        # Ni gVisor ni Docker durci : avant de conclure « insuffisant », on
        # consulte la détection RÉELLE des namespaces Linux — le mécanisme
        # que SandboxProvider utilise réellement, indépendant de Docker.
        sandbox_report = ctx.detect_sandbox_capabilities()
        namespace_facts = sandbox_report.facts
        level = "namespaced" if sandbox_report.level is IsolationLevel.NAMESPACED else "insufficient"

    facts: dict[str, Any] = {
        "isolation_level": level,
        "runsc_declared": has_runsc,
        "cgroups_v2": cgroups_v2,
        "red_team_allowed": level == "gvisor",
        "code_execution_allowed": level in ("gvisor", "docker_hardened", "namespaced"),
    }
    if namespace_facts is not None:
        facts["namespace_isolation_facts"] = namespace_facts

    if level == "insufficient":
        return _fail(
            "GATE-19", title,
            "isolation insuffisante — ni conteneur (Docker durci/gVisor) ni namespaces Linux fonctionnels",
            remediation=(
                "L'exécution de code généré reste DÉSACTIVÉE. Corriger Docker+cgroups v2 OU "
                "les namespaces Linux (unshare/cgroups v1/setpriv/isolation réseau — voir "
                "apex.sandbox.detect.detect_capabilities) avant la phase 3. Aucune garantie "
                "de sécurité n'est abaissée pour débloquer la phase."
            ),
            facts=facts, blocks_phases=(3,),
        )
    if level == "namespaced":
        return _degraded(
            "GATE-19", title,
            "plafond d'isolation : namespaces Linux (NAMESPACED) — suffisant pour du code "
            "généré ordinaire, PAS un niveau conteneur",
            remediation=(
                "NAMESPACED (namespaces Linux + cgroups v1 + abaissement de privilège réel + "
                "réseau coupé, apex.sandbox.linux_namespace) n'est ni docker_hardened ni "
                "gvisor : ni seccomp, ni jail complet du filesystem (pas de pivot_root). "
                "Conséquence verrouillée, comme pour docker_hardened : le Red Team (§46) "
                "reste DÉSACTIVÉ jusqu'à une isolation renforcée (Docker+cgroups v2, runsc, "
                "ou machine séparée). L'exécution ordinaire de code généré reste autorisée — "
                "c'est déjà la politique réellement appliquée par "
                "apex.missions.execution.launch_mission."
            ),
            facts=facts, blocks_phases=(21,),
        )
    if level == "docker_hardened":
        return _degraded(
            "GATE-19", title,
            "plafond d'isolation : Docker durci — suffisant pour du code généré ordinaire",
            remediation=(
                "Conséquence verrouillée : le Red Team (§46) reste DÉSACTIVÉ jusqu'à "
                "disposer d'une isolation renforcée (runsc opérationnel, VM dédiée ou "
                "machine séparée). Ce n'est pas un échec de phase 1 : c'est une capacité "
                "différée, conformément au principe 4."
            ),
            facts=facts, blocks_phases=(21,),
        )
    return _ok("GATE-19", title, "isolation gVisor disponible", facts=facts)


def gate_20_runsc_gonogo(ctx: GateContext) -> GateCheckResult:
    """Test empirique de runsc : présent ne signifie pas fonctionnel."""
    title = "runsc (gVisor) — go/no-go"
    facts: dict[str, Any] = {"runsc_functional": None, "runsc_overhead_note": None}

    if ctx.probe.which("docker") is None:
        return _unknown("GATE-20", title, "docker absent", facts=facts)

    runtimes_out = ctx.probe.run(["docker", "info", "--format", "{{.Runtimes}}"], timeout_s=ctx.timeout_s)
    if "runsc" not in (runtimes_out.stdout or ""):
        return _degraded(
            "GATE-20", title, "NO-GO — runsc non déclaré comme runtime Docker",
            remediation=(
                "Pourquoi : isolation en espace utilisateur, condition d'activation du Red Team.\n"
                "Installation : voir gvisor.dev/docs/user_guide/install ; exige un Docker Engine "
                "natif (Docker Desktop ne permet pas de déclarer un runtime OCI alternatif).\n"
                "Phase concernée : 21 (durcissement) ; la phase 3 fonctionne sans.\n"
                "Repli : Docker durci — non-root, cap-drop ALL, seccomp, rootfs RO, réseau coupé."
            ),
            facts={"runsc_functional": False, "runsc_declared": False},
        )

    if not ctx.allow_container_exec:
        return _unknown(
            "GATE-20", title, "runsc déclaré mais non testé — exécution de conteneur non autorisée",
            reason="not_authorized",
            remediation=(
                "runsc déclaré ne signifie pas runsc fonctionnel : les toolchains Rust et C++ "
                "sollicitent des appels système que gVisor n'implémente pas toujours. "
                "Relancer avec --allow-container-exec pour le verdict réel."
            ),
            facts={"runsc_functional": None, "runsc_declared": True},
        )

    out = ctx.probe.run(
        ["docker", "run", "--rm", "--runtime", "runsc", "--network", "none",
         PROBE_IMAGE, "sh", "-c", "uname -a && echo apex-runsc-ok"],
        timeout_s=max(ctx.timeout_s, 180.0),
    )
    if out.ok and "apex-runsc-ok" in out.stdout:
        return _ok(
            "GATE-20", title, "GO — runsc exécute un conteneur",
            remediation=(
                "Verdict partiel : le test valide un conteneur minimal. Avant d'activer le "
                "Red Team, mesurer le surcoût de compilation sur les toolchains Rust et C++ "
                "en phase 3 — c'est là que gVisor échoue le plus souvent."
            ),
            facts={"runsc_functional": True, "runsc_declared": True,
                   "runsc_toolchain_validated": False},
            evidence=(Evidence.from_output("docker run --runtime runsc", out.stdout),),
        )
    return _degraded(
        "GATE-20", title, "NO-GO — runsc déclaré mais échoue à l'exécution",
        remediation=(
            "Configuration non supportée en amont sous WSL2. Repli : Docker durci ; "
            "Red Team différé jusqu'à un hôte d'isolation dédié (ADR-0005)."
        ),
        facts={"runsc_functional": False, "runsc_declared": True},
        evidence=(Evidence.from_output("docker run --runtime runsc", out.stderr),),
    )


def gate_21_compose_memory(ctx: GateContext) -> GateCheckResult:
    """Confronte les limites memoire de docker-compose a la RAM visible.

    K2 : le fichier declarait 9,5 Go de limites pour une machine exposant
    6,7 Go a WSL2. `docker compose up` aurait echoue ou declenche l'OOM killer,
    avec un diagnostic sans rapport apparent avec la cause.

    Le controle est volontairement independant de la presence de Docker : la
    sur-allocation est une propriete du fichier et de la machine, pas du
    runtime. En revanche, savoir si les limites seront *appliquees* exige de
    connaitre le runtime — cette partie reste UNKNOWN sans Docker, et n'est
    jamais supposee.
    """
    title = "Compose : limites memoire contre RAM visible"
    policy = ctx.config.gate.compose_memory if ctx.config else None
    compose_path = Path(ctx.project_root) / "docker-compose.yml"
    analysis = analyse_compose_file(compose_path)

    facts: dict[str, Any] = {
        "compose_path": str(compose_path),
        "compose_error": analysis.error,
        "compose_limit_total_gb": None,
        "compose_reservation_total_gb": None,
        "ram_visible_gb": None,
        "overcommit_ratio": None,
        "services_without_limit": [],
        "services_ignored_on_compose_v1": [],
        "compose_runtime": None,
        "limits_enforced": None,
    }

    if analysis.error:
        if analysis.error == "fichier introuvable":
            return _unknown("GATE-21", title, "docker-compose.yml absent",
                            reason="not_applicable", facts=facts)
        return _fail(
            "GATE-21", title, f"docker-compose.yml illisible : {analysis.error}",
            remediation="Corriger le fichier avant toute tentative de demarrage.",
            facts=facts, blocks_phases=(2,),
        )

    if analysis.parse_errors:
        facts["parse_errors"] = list(analysis.parse_errors)
        return _fail(
            "GATE-21", title,
            f"valeurs memoire illisibles : {', '.join(analysis.parse_errors)}",
            remediation="Une valeur memoire ininterpretable n'est pas une absence de limite.",
            facts=facts, blocks_phases=(2,),
        )

    # --- RAM visible -------------------------------------------------------
    ram_result = gate_12_ram(ctx)
    ram_gb = ram_result.facts.get("ram_total_gb")
    facts["compose_limit_total_gb"] = round(analysis.total_limit_gb, 2)
    facts["compose_reservation_total_gb"] = round(analysis.total_reservation_gb, 2)
    facts["services_without_limit"] = list(analysis.services_without_limit)
    facts["services_ignored_on_compose_v1"] = list(analysis.services_ignored_on_v1)
    facts["top_consumers"] = analysis.top_consumers()

    # --- Runtime : declare n'est pas applique ------------------------------
    if ctx.probe.which("docker") is not None:
        version = ctx.probe.run(["docker", "compose", "version", "--short"], timeout_s=ctx.timeout_s)
        if version.ok:
            facts["compose_runtime"] = f"v2 ({version.stdout.strip()})"
            facts["limits_enforced"] = True
        elif ctx.probe.which("docker-compose") is not None:
            facts["compose_runtime"] = "v1"
            # Sur V1, deploy.resources est ignore hors Swarm.
            facts["limits_enforced"] = not analysis.services_ignored_on_v1
        else:
            facts["compose_runtime"] = "indetermine"

    if ram_gb is None:
        return _unknown("GATE-21", title, "RAM visible non mesurable — ratio incalculable",
                        reason="unmeasurable", facts=facts)

    ratio = analysis.total_limit_gb / ram_gb if ram_gb else None
    facts["ram_visible_gb"] = round(ram_gb, 2)
    facts["overcommit_ratio"] = round(ratio, 2) if ratio is not None else None

    warn_ratio = policy.warn_ratio if policy else 0.75
    fail_ratio = policy.fail_ratio if policy else 1.0
    expect_limits = policy.expect_memory_limits if policy else True
    facts["policy"] = {"warn_ratio": warn_ratio, "fail_ratio": fail_ratio}

    consumers = ", ".join(
        f"{c['service']} {c['limit_gb']} Go" for c in analysis.top_consumers()
    )
    summary = (
        f"{analysis.total_limit_gb:.1f} Go de limites pour {ram_gb:.1f} Go visibles "
        f"(ratio {ratio:.2f})"
    )

    # --- Sur-allocation : verdict arithmetique, independant du runtime -----
    if ratio is not None and ratio > fail_ratio:
        return _fail(
            "GATE-21", title, f"sur-allocation memoire — {summary}",
            remediation=(
                f"Principaux consommateurs : {consumers}.\n"
                "Reduire les limites, ou augmenter la memoire allouee a WSL2 via la cle "
                "memory= dans %UserProfile%\\.wslconfig, puis wsl --shutdown.\n"
                "Ne pas demarrer la pile en l'etat : l'OOM killer se declencherait avec "
                "un diagnostic sans rapport apparent avec la cause."
            ),
            facts=facts, blocks_phases=(2,),
        )

    if expect_limits and analysis.services_without_limit:
        return _degraded(
            "GATE-21", title,
            f"services sans limite memoire : {', '.join(analysis.services_without_limit)}",
            remediation=(
                "Un service sans plafond peut consommer toute la RAM de l'hote. "
                "Le total declare est alors une borne inferieure, pas une garantie."
            ),
            facts=facts,
        )

    if analysis.services_ignored_on_v1 and facts["compose_runtime"] != "v2":
        return _degraded(
            "GATE-21", title,
            f"{summary} — limites declarees uniquement via deploy.resources",
            remediation=(
                f"Concerne : {', '.join(analysis.services_ignored_on_v1)}.\n"
                "deploy.resources.limits n'est honore que par Compose V2 hors Swarm ; "
                "docker-compose V1 l'ignore SILENCIEUSEMENT. Declarer aussi mem_limit, "
                "honore par les deux."
            ),
            facts=facts,
        )

    if ratio is not None and ratio > warn_ratio:
        return _degraded(
            "GATE-21", title, f"marge memoire faible — {summary}",
            remediation=(
                f"Principaux consommateurs : {consumers}. Au-dela de {warn_ratio:.0%} de la RAM "
                "visible, il ne reste plus assez pour l'hote et les processus APEX."
            ),
            facts=facts,
        )

    # Arithmetique saine, mais application non verifiable sans Docker.
    if facts["limits_enforced"] is None:
        return _unknown(
            "GATE-21", title,
            f"{summary} — allocation coherente, application des limites non verifiable",
            reason="dependency_missing",
            remediation=(
                "Le ratio est sain, mais determiner si les limites seront reellement "
                "appliquees exige de connaitre le runtime Compose, donc Docker installe. "
                "Ne pas conclure que les limites sont actives."
            ),
            facts=facts,
        )

    return _ok("GATE-21", title, f"{summary} — limites appliquees par {facts['compose_runtime']}",
               facts=facts)


ALL_GATE_CHECKS: tuple[tuple[str, Callable[[GateContext], GateCheckResult]], ...] = (
    ("GATE-01", gate_01_windows_host),
    ("GATE-02", gate_02_wsl2),
    ("GATE-03", gate_03_distribution),
    ("GATE-04", gate_04_kernel),
    ("GATE-05", gate_05_python),
    ("GATE-06", gate_06_docker),
    ("GATE-07", gate_07_gpu_from_wsl),
    ("GATE-08", gate_08_gpu_model),
    ("GATE-09", gate_09_vram),
    ("GATE-10", gate_10_cuda_driver),
    ("GATE-11", gate_11_disk),
    ("GATE-12", gate_12_ram),
    ("GATE-13", gate_13_cpu),
    ("GATE-14", gate_14_virtualization),
    ("GATE-15", gate_15_network),
    ("GATE-16", gate_16_tools),
    ("GATE-17", gate_17_container_exec),
    ("GATE-18", gate_18_gpu_in_container),
    ("GATE-19", gate_19_isolation_state),
    ("GATE-20", gate_20_runsc_gonogo),
    ("GATE-21", gate_21_compose_memory),
)
