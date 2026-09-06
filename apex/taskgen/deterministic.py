"""`DeterministicTaskGenerator` — le seul `TaskGenerator` réel de cette étape.

Huit modes réels, tous pour la spécialité `research` (la seule
`is_supported` — `apex.specialties.registry`) : `NORMAL`,
`INCREASING_DIFFICULTY`/`CURRICULUM_INTERMEDIATE` (même mécanisme,
paramétré par `level`), `TARGETED_WEAKNESS` (niveau dérivé de la sévérité
d'un `WeaknessReport`), `REGRESSION` (reproduit la commande d'une tâche
existante à l'identique), `EDGE_CASE`/`ADVERSARIAL` (RÉELS depuis N+10 —
voir `apex.taskgen.model.EdgeCaseKind`/`AdversarialKind`), `BUG_FARM` (RÉEL
depuis N+11 — voir plus bas). Tout le reste lève `NotImplementedYet` —
jamais un contenu inventé.

**`BUG_FARM` (passe « Universal Task + Curriculum Engine », N+11, § Phase
12 de la mission)** : N+10 déclarait ce mode `PLANNED` (« nécessite
CodeIntelligence »). Correction : la reproduction d'un bug RÉEL
(`apex.metalearner.bug_farm_model.BugRecord`) n'a JAMAIS eu besoin d'un
domaine de code — exactement comme `REGRESSION`, elle reproduit la
commande EXACTE de la tâche RÉELLE qui a servi de preuve à ce bug
(`base_task`, jamais un contenu réinventé). Seul le `dataset_ref` diffère
de `REGRESSION` (`bug_farm:<base_task.id>` au lieu de l'id nu) pour rester
traçable jusqu'au `BugRecord` d'origine (`apex.metalearner.bug_farm_
service.BugFarmService.build_bug_farm_challenges`).

Aucun `ModelProvider` : chaque script est un script Python autonome,
déterministe, sans dépendance — même style que
`apex.missions.workloads.RESEARCH_DIAGNOSTIC_SCRIPT`, dont `NORMAL` réutilise
d'ailleurs directement `research_diagnostic_command()` (zéro duplication).

**Notion de « difficulté »** : le niveau contrôle le nombre de contrôles
exécutés (1 à 3) ET si le script ÉCHOUE explicitement (`sys.exit(1)`) quand
l'isolation attendue est violée — niveau 3 est réellement plus dur à
« réussir » qu'un niveau 1 qui ne fait qu'observer, pas un label arbitraire.

**`EDGE_CASE`/`ADVERSARIAL` (N+10)** : chaque variante dérive d'une
propriété RÉELLE de ce MÊME domaine diagnostic — jamais un cas
arbitraire (§ Phase 3/4 de la mission). Un adversarial ne demande JAMAIS
plus que la politique déjà résolue (`limits`, fournie par l'appelant) —
`_ADVERSARIAL_TIMEOUT_FRACTION`/`_ADVERSARIAL_MEMORY_FRACTION` restent
STRICTEMENT < 1.0, documentés, jamais ajustés pour produire un résultat
souhaité à l'avance (même discipline que `apex.metalearner.weakness_engine.
_CONFIDENCE_SATURATION_COUNT`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from apex.errors import NotImplementedYet
from apex.taskgen.model import (
    AdversarialKind,
    EdgeCaseKind,
    GeneralizationVariant,
    GeneratedTaskSpec,
    GenerationMode,
)
from apex.taskgen.provider import TaskGenerator
from apex.tasks.model import Task, TaskSplit
from apex.weakness.model import WeaknessCategory, WeaknessReport

if TYPE_CHECKING:
    from apex.sandbox.model import ResourceLimits

_SUPPORTED_SPECIALTY = "research"

_MIN_LEVEL = 1
_MAX_LEVEL = 3

#: Sévérité -> niveau à générer. `NO_WEAKNESS`/`INSUFFICIENT_DATA` n'ont
#: rien à cibler — `generate()` lève `ValueError` pour ces catégories.
_SEVERITY_TO_LEVEL = {
    WeaknessCategory.ISOLATED_FAILURE: 2,
    WeaknessCategory.REPEATED_WEAKNESS: 2,
    WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS: _MAX_LEVEL,
}

_UNSUPPORTED_MODE_DEPENDENCY = {
    GenerationMode.TRANSFER: (15, "une seule spécialité (« research ») est is_supported"),
    GenerationMode.SELF_PLAY: (17, "aucune orchestration multi-clones réelle"),
    GenerationMode.FROM_HYPOTHESIS: (16, "HypothesisGenerator, PLANNED"),
}

#: Répétitions RÉELLES du bloc de contrôle — documentées, jamais un compte
#: arbitraire : `_EDGE_CASE_REPEATS` suffit à détecter une non-déterminisme
#: grossière sans coût d'exécution significatif ; `_ADVERSARIAL_REPEATS`
#: est délibérément plus élevé (pression réelle sur la répétition, §4).
_EDGE_CASE_REPEATS = 3
_ADVERSARIAL_REPEATS = 25

#: Fractions RÉELLES, STRICTEMENT < 1.0, de la limite résolue — un
#: adversarial ne demande jamais la limite elle-même, seulement s'en
#: approcher (§4 : jamais au-delà des policies). `_EDGE_CASE_...` reste
#: plus conservateur que `_ADVERSARIAL_...` (une pression plus forte est
#: précisément ce qui distingue un adversarial d'un simple edge case).
_EDGE_CASE_TIMEOUT_FRACTION = 0.5
_ADVERSARIAL_TIMEOUT_FRACTION = 0.85
_ADVERSARIAL_MEMORY_FRACTION = 0.8

#: Cibles de sonde réseau RÉELLES et DISTINCTES — `DEFAULT_PROBE` (déjà
#: utilisée par `_diagnostic_script`, tout TRAIN) vs `ALTERNATE_PROBE`
#: (réservée HELDOUT, §6 de la mission N+10 : jamais un doublon byte-à-byte
#: entre TRAIN et HELDOUT).
_PROBE_TARGETS = {
    GeneralizationVariant.DEFAULT_PROBE: "1.1.1.1",
    GeneralizationVariant.ALTERNATE_PROBE: "8.8.8.8",
}

#: Chemin RÉEL, hors `cwd`, utilisé par `FILESYSTEM_BOUNDARY_PROBE` — un
#: sandbox correctement isolé DOIT refuser cette écriture (§5 : « boundary
#: violations »). Jamais un chemin contenant un secret ou une donnée réelle
#: — seule l'échec/succès de l'écriture elle-même est observé.
_FILESYSTEM_PROBE_PATH = "/apex_adversarial_filesystem_probe.tmp"


def _network_probe_lines(target: str) -> list[str]:
    return [
        "try:",
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
        "    s.settimeout(1.5)",
        f'    s.connect(("{target}", 443))',
        '    report["network_reachable"] = True',
        "except OSError:",
        '    report["network_reachable"] = False',
    ]


def _diagnostic_script(level: int, *, probe_target: str = "1.1.1.1") -> str:
    assertions = level >= _MAX_LEVEL
    lines = [
        "import json, os, socket, sys",
        "",
        "report = {}",
        'report["python_version"] = sys.version.split()[0]',
        'report["cwd_writable"] = os.access(os.getcwd(), os.W_OK)',
    ]
    if level >= 2:
        lines += _network_probe_lines(probe_target)
    if level >= 3:
        lines.append(
            'report["postgres_env_present"] = any('
            'k.startswith(("POSTGRES_", "APEX_DB_")) for k in os.environ)'
        )
    lines.append("print(json.dumps(report))")
    if assertions:
        lines += [
            "failed = False",
            'if report.get("network_reachable", False):',
            '    print("ECHEC: isolation reseau violee", file=sys.stderr)',
            "    failed = True",
            'if report.get("postgres_env_present", False):',
            '    print("ECHEC: variables POSTGRES_/APEX_DB_ visibles", file=sys.stderr)',
            "    failed = True",
            "if failed:",
            "    sys.exit(1)",
        ]
    return "\n".join(lines) + "\n"


def _diagnostic_command(level: int) -> tuple[str, ...]:
    return ("python3", "-c", _diagnostic_script(level))


def _empty_script() -> str:
    return 'import json\nprint(json.dumps({}))\n'


def _repeated_calls_script(repeats: int) -> str:
    return "\n".join(
        [
            "import json, os, sys",
            "",
            "def check():",
            "    r = {}",
            '    r["python_version"] = sys.version.split()[0]',
            '    r["cwd_writable"] = os.access(os.getcwd(), os.W_OK)',
            "    return r",
            "",
            f"reports = [check() for _ in range({repeats})]",
            "if any(r != reports[0] for r in reports):",
            '    print("ECHEC: resultats non identiques entre appels repetes", file=sys.stderr)',
            "    sys.exit(1)",
            "print(json.dumps({\"repeats\": len(reports), \"consistent\": True}))",
            "",
        ],
    )


def _boundary_sleep_script(sleep_s: float) -> str:
    return "\n".join(
        [
            "import json, time",
            "",
            f"time.sleep({sleep_s!r})",
            f'print(json.dumps({{"slept_s": {sleep_s!r}, "completed": True}}))',
            "",
        ],
    )


def _resource_pressure_script(allocation_mb: float) -> str:
    allocation_bytes = int(allocation_mb * 1024 * 1024)
    return "\n".join(
        [
            "import json",
            "",
            f"buf = bytearray({allocation_bytes})",
            'print(json.dumps({"allocated_bytes": len(buf), "released": True}))',
            "del buf",
            "",
        ],
    )


def _network_boundary_probe_script() -> str:
    return "\n".join(
        [
            "import json, socket, sys",
            "",
            "report = {}",
            *_network_probe_lines("1.1.1.1"),
            "print(json.dumps(report))",
            'if report.get("network_reachable", False):',
            '    print("ECHEC: frontiere reseau non appliquee", file=sys.stderr)',
            "    sys.exit(1)",
            "",
        ],
    )


def _filesystem_boundary_probe_script(path: str) -> str:
    return "\n".join(
        [
            "import json, sys",
            "",
            "report = {}",
            "try:",
            f'    with open({path!r}, "w") as f:',
            '        f.write("probe")',
            '    report["write_blocked"] = False',
            "except OSError:",
            '    report["write_blocked"] = True',
            "print(json.dumps(report))",
            'if not report["write_blocked"]:',
            '    print("ECHEC: frontiere systeme de fichiers non appliquee", file=sys.stderr)',
            "    sys.exit(1)",
            "",
        ],
    )


class DeterministicTaskGenerator(TaskGenerator):
    def generate(
        self,
        *,
        mode: GenerationMode,
        specialty: str,
        split: TaskSplit = TaskSplit.TRAIN,
        level: int | None = None,
        weakness_report: WeaknessReport | None = None,
        base_task: Task | None = None,
        edge_case_kind: EdgeCaseKind | None = None,
        adversarial_kind: AdversarialKind | None = None,
        generalization_variant: GeneralizationVariant | None = None,
        limits: ResourceLimits | None = None,
    ) -> GeneratedTaskSpec:
        if mode in _UNSUPPORTED_MODE_DEPENDENCY:
            phase, reason = _UNSUPPORTED_MODE_DEPENDENCY[mode]
            raise NotImplementedYet(f"TaskGenerator({mode.value})", phase=phase, fallback=reason)

        if specialty != _SUPPORTED_SPECIALTY:
            raise NotImplementedYet(
                f"TaskGenerator pour la spécialité « {specialty} »", phase=15,
                fallback="seule « research » est générée automatiquement — écrire la tâche à la main",
            )

        if mode is GenerationMode.NORMAL:
            return self._normal(split, generalization_variant)
        if mode in (GenerationMode.INCREASING_DIFFICULTY, GenerationMode.CURRICULUM_INTERMEDIATE):
            return self._at_level(mode, split, level)
        if mode is GenerationMode.TARGETED_WEAKNESS:
            return self._targeted_weakness(split, weakness_report)
        if mode is GenerationMode.REGRESSION:
            return self._regression(base_task)
        if mode is GenerationMode.BUG_FARM:
            return self._bug_farm(base_task)
        if mode is GenerationMode.EDGE_CASE:
            return self._edge_case(split, edge_case_kind, limits)
        if mode is GenerationMode.ADVERSARIAL:
            return self._adversarial(split, adversarial_kind, limits)

        raise NotImplementedYet(f"TaskGenerator({mode.value})", phase=15)  # pragma: no cover

    def _normal(
        self, split: TaskSplit, generalization_variant: GeneralizationVariant | None,
    ) -> GeneratedTaskSpec:
        if generalization_variant is None or generalization_variant is GeneralizationVariant.DEFAULT_PROBE:
            from apex.missions.workloads import research_diagnostic_command

            return GeneratedTaskSpec(
                title="Diagnostic recherche — normal", description="Diagnostic d'environnement standard.",
                command=research_diagnostic_command(), specialty=_SUPPORTED_SPECIALTY, split=split,
                mode=GenerationMode.NORMAL,
            )
        # §6 de la mission N+10 : variante STRUCTURELLEMENT distincte (cible
        # de sonde réseau différente) réservée aux challenges HELDOUT — même
        # propriété réelle testée (isolation réseau), jamais un doublon
        # byte-à-byte d'une tâche TRAIN.
        probe_target = _PROBE_TARGETS[generalization_variant]
        return GeneratedTaskSpec(
            title="Diagnostic recherche — normal (variante généralisation)",
            description=(
                f"Diagnostic d'environnement standard, sonde réseau alternative ({probe_target}) — "
                "jamais utilisée pour une tâche TRAIN, réservée aux challenges HELDOUT (§6 N+10)."
            ),
            command=("python3", "-c", _diagnostic_script(2, probe_target=probe_target)),
            specialty=_SUPPORTED_SPECIALTY, split=split, mode=GenerationMode.NORMAL,
            dataset_ref=f"generalization_variant:{generalization_variant.value}",
        )

    def _at_level(self, mode: GenerationMode, split: TaskSplit, level: int | None) -> GeneratedTaskSpec:
        if level is None:
            raise ValueError(f"level est obligatoire pour {mode.value}")
        if not (_MIN_LEVEL <= level <= _MAX_LEVEL):
            raise ValueError(f"level={level} hors bornes [{_MIN_LEVEL}, {_MAX_LEVEL}]")
        return GeneratedTaskSpec(
            title=f"Diagnostic recherche — niveau {level}",
            description=f"Diagnostic d'environnement, {level} contrôle(s), niveau {level}/{_MAX_LEVEL}.",
            command=_diagnostic_command(level), specialty=_SUPPORTED_SPECIALTY, split=split, mode=mode,
        )

    def _targeted_weakness(self, split: TaskSplit, weakness_report: WeaknessReport | None) -> GeneratedTaskSpec:
        if weakness_report is None:
            raise ValueError("weakness_report est obligatoire pour TARGETED_WEAKNESS")
        level = _SEVERITY_TO_LEVEL.get(weakness_report.category)
        if level is None:
            raise ValueError(
                f"rien à cibler pour la catégorie « {weakness_report.category.value} » "
                "(NO_WEAKNESS/INSUFFICIENT_DATA)"
            )
        return GeneratedTaskSpec(
            title=f"Diagnostic recherche — ciblé (niveau {level})",
            description=(
                f"Diagnostic renforcé, niveau {level}/{_MAX_LEVEL}, généré depuis une faiblesse "
                f"« {weakness_report.category.value} » ({weakness_report.reason})."
            ),
            command=_diagnostic_command(level), specialty=_SUPPORTED_SPECIALTY, split=split,
            mode=GenerationMode.TARGETED_WEAKNESS,
        )

    def _regression(self, base_task: Task | None) -> GeneratedTaskSpec:
        if base_task is None:
            raise ValueError("base_task est obligatoire pour REGRESSION")
        return GeneratedTaskSpec(
            title=f"Régression — {base_task.title}",
            description=(
                f"Reproduction exacte de la tâche « {base_task.id} » pour vérifier l'absence de régression."
            ),
            command=base_task.command, specialty=base_task.specialty, split=base_task.split,
            mode=GenerationMode.REGRESSION, dataset_ref=base_task.id,
        )

    def _bug_farm(self, base_task: Task | None) -> GeneratedTaskSpec:
        """§ Phase 12 de la mission N+11 : reproduit la commande EXACTE de la
        tâche RÉELLE liée au bug (`base_task`, fournie par
        `apex.metalearner.bug_farm_service.BugFarmService.
        build_bug_farm_challenges` depuis un `BugRecord` réel) — même
        discipline que `_regression`, jamais un contenu réinventé. Seul le
        `dataset_ref` diffère (`bug_farm:<task_id>`) pour rester traçable
        jusqu'au bug d'origine."""
        if base_task is None:
            raise ValueError("base_task est obligatoire pour BUG_FARM")
        return GeneratedTaskSpec(
            title=f"Bug farm — {base_task.title}",
            description=(
                f"Reproduction exacte de la tâche « {base_task.id} » associée à un bug réel "
                "(vérifie que la correction reste durable — apex.metalearner.bug_farm_service)."
            ),
            command=base_task.command, specialty=base_task.specialty, split=base_task.split,
            mode=GenerationMode.BUG_FARM, dataset_ref=f"bug_farm:{base_task.id}",
        )

    def _edge_case(
        self, split: TaskSplit, kind: EdgeCaseKind | None, limits: ResourceLimits | None,
    ) -> GeneratedTaskSpec:
        if kind is None:
            raise ValueError("edge_case_kind est obligatoire pour EDGE_CASE")
        if kind is EdgeCaseKind.EMPTY:
            return GeneratedTaskSpec(
                title="Cas limite — vide",
                description="Script minimal, aucun contrôle au-delà du strict nécessaire.",
                command=("python3", "-c", _empty_script()), specialty=_SUPPORTED_SPECIALTY, split=split,
                mode=GenerationMode.EDGE_CASE, dataset_ref=f"edge_case:{kind.value}",
            )
        if kind is EdgeCaseKind.MINIMAL:
            return GeneratedTaskSpec(
                title="Cas limite — minimal",
                description=f"Diagnostic au niveau minimal ({_MIN_LEVEL}/{_MAX_LEVEL}).",
                command=_diagnostic_command(_MIN_LEVEL), specialty=_SUPPORTED_SPECIALTY, split=split,
                mode=GenerationMode.EDGE_CASE, dataset_ref=f"edge_case:{kind.value}",
            )
        if kind is EdgeCaseKind.MAXIMAL:
            return GeneratedTaskSpec(
                title="Cas limite — maximal",
                description=f"Diagnostic au niveau maximal ({_MAX_LEVEL}/{_MAX_LEVEL}).",
                command=_diagnostic_command(_MAX_LEVEL), specialty=_SUPPORTED_SPECIALTY, split=split,
                mode=GenerationMode.EDGE_CASE, dataset_ref=f"edge_case:{kind.value}",
            )
        if kind is EdgeCaseKind.REPEATED_CALLS:
            return GeneratedTaskSpec(
                title="Cas limite — appels répétés",
                description=(
                    f"Exécute le bloc de contrôle {_EDGE_CASE_REPEATS} fois dans le même processus et "
                    "exige un résultat identique à chaque fois (non-déterminisme réel détecté sinon)."
                ),
                command=("python3", "-c", _repeated_calls_script(_EDGE_CASE_REPEATS)),
                specialty=_SUPPORTED_SPECIALTY, split=split, mode=GenerationMode.EDGE_CASE,
                dataset_ref=f"edge_case:{kind.value}",
            )
        if kind is EdgeCaseKind.BOUNDARY_TIMEOUT:
            if limits is None:
                raise ValueError("limits est obligatoire pour EdgeCaseKind.BOUNDARY_TIMEOUT")
            sleep_s = round(limits.timeout_s * _EDGE_CASE_TIMEOUT_FRACTION, 3)
            return GeneratedTaskSpec(
                title="Cas limite — proximité du délai",
                description=(
                    f"Dort {sleep_s}s ({_EDGE_CASE_TIMEOUT_FRACTION:.0%} du timeout_s réellement résolu, "
                    f"{limits.timeout_s}s) puis complète normalement."
                ),
                command=("python3", "-c", _boundary_sleep_script(sleep_s)), specialty=_SUPPORTED_SPECIALTY,
                split=split, mode=GenerationMode.EDGE_CASE, dataset_ref=f"edge_case:{kind.value}",
            )
        raise NotImplementedYet(f"TaskGenerator(edge_case={kind.value})", phase=15)  # pragma: no cover

    def _adversarial(
        self, split: TaskSplit, kind: AdversarialKind | None, limits: ResourceLimits | None,
    ) -> GeneratedTaskSpec:
        if kind is None:
            raise ValueError("adversarial_kind est obligatoire pour ADVERSARIAL")
        if kind is AdversarialKind.TIMEOUT_PRESSURE:
            if limits is None:
                raise ValueError("limits est obligatoire pour AdversarialKind.TIMEOUT_PRESSURE")
            sleep_s = round(limits.timeout_s * _ADVERSARIAL_TIMEOUT_FRACTION, 3)
            return GeneratedTaskSpec(
                title="Adversarial — pression sur le délai",
                description=(
                    f"Dort {sleep_s}s ({_ADVERSARIAL_TIMEOUT_FRACTION:.0%} du timeout_s réellement résolu, "
                    f"{limits.timeout_s}s, jamais une demande de délai supplémentaire) puis complète."
                ),
                command=("python3", "-c", _boundary_sleep_script(sleep_s)), specialty=_SUPPORTED_SPECIALTY,
                split=split, mode=GenerationMode.ADVERSARIAL, dataset_ref=f"adversarial:{kind.value}",
            )
        if kind is AdversarialKind.REPEATED_EXECUTION_STRESS:
            return GeneratedTaskSpec(
                title="Adversarial — répétition intensive",
                description=(
                    f"Exécute le bloc de contrôle {_ADVERSARIAL_REPEATS} fois — pression réelle sur la "
                    "répétition/l'ordonnancement, exige une cohérence stricte."
                ),
                command=("python3", "-c", _repeated_calls_script(_ADVERSARIAL_REPEATS)),
                specialty=_SUPPORTED_SPECIALTY, split=split, mode=GenerationMode.ADVERSARIAL,
                dataset_ref=f"adversarial:{kind.value}",
            )
        if kind is AdversarialKind.RESOURCE_PRESSURE:
            if limits is None:
                raise ValueError("limits est obligatoire pour AdversarialKind.RESOURCE_PRESSURE")
            allocation_mb = round(limits.memory_mb * _ADVERSARIAL_MEMORY_FRACTION, 3)
            return GeneratedTaskSpec(
                title="Adversarial — pression mémoire",
                description=(
                    f"Alloue {allocation_mb}Mo ({_ADVERSARIAL_MEMORY_FRACTION:.0%} du memory_mb réellement "
                    f"résolu, {limits.memory_mb}Mo, jamais au-delà) puis libère."
                ),
                command=("python3", "-c", _resource_pressure_script(allocation_mb)),
                specialty=_SUPPORTED_SPECIALTY, split=split, mode=GenerationMode.ADVERSARIAL,
                dataset_ref=f"adversarial:{kind.value}",
            )
        if kind is AdversarialKind.NETWORK_BOUNDARY_PROBE:
            return GeneratedTaskSpec(
                title="Adversarial — frontière réseau",
                description=(
                    "Tente une connexion réseau sortante — un sandbox correctement isolé DOIT la refuser ; "
                    "l'échec de cette tentative est le comportement attendu (frontière de sécurité)."
                ),
                command=("python3", "-c", _network_boundary_probe_script()), specialty=_SUPPORTED_SPECIALTY,
                split=split, mode=GenerationMode.ADVERSARIAL, dataset_ref=f"adversarial:{kind.value}",
            )
        if kind is AdversarialKind.FILESYSTEM_BOUNDARY_PROBE:
            return GeneratedTaskSpec(
                title="Adversarial — frontière système de fichiers",
                description=(
                    f"Tente une écriture hors du répertoire de travail ({_FILESYSTEM_PROBE_PATH}) — "
                    "un sandbox correctement isolé DOIT la refuser ; jamais une demande de privilège élevé."
                ),
                command=("python3", "-c", _filesystem_boundary_probe_script(_FILESYSTEM_PROBE_PATH)),
                specialty=_SUPPORTED_SPECIALTY, split=split, mode=GenerationMode.ADVERSARIAL,
                dataset_ref=f"adversarial:{kind.value}",
            )
        raise NotImplementedYet(f"TaskGenerator(adversarial={kind.value})", phase=15)  # pragma: no cover
