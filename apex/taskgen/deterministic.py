"""`DeterministicTaskGenerator` — le seul `TaskGenerator` réel de cette étape.

Cinq modes réels, tous pour la spécialité `research` (la seule
`is_supported` — `apex.specialties.registry`) : `NORMAL`,
`INCREASING_DIFFICULTY`/`CURRICULUM_INTERMEDIATE` (même mécanisme,
paramétré par `level`), `TARGETED_WEAKNESS` (niveau dérivé de la sévérité
d'un `WeaknessReport`), `REGRESSION` (reproduit la commande d'une tâche
existante à l'identique). Tout le reste lève `NotImplementedYet` — jamais
un contenu inventé.

Aucun `ModelProvider` : chaque script est un script Python autonome,
déterministe, sans dépendance — même style que
`apex.missions.workloads.RESEARCH_DIAGNOSTIC_SCRIPT`, dont `NORMAL` réutilise
d'ailleurs directement `research_diagnostic_command()` (zéro duplication).

**Notion de « difficulté »** : le niveau contrôle le nombre de contrôles
exécutés (1 à 3) ET si le script ÉCHOUE explicitement (`sys.exit(1)`) quand
l'isolation attendue est violée — niveau 3 est réellement plus dur à
« réussir » qu'un niveau 1 qui ne fait qu'observer, pas un label arbitraire.
"""

from __future__ import annotations

from apex.errors import NotImplementedYet
from apex.taskgen.model import GeneratedTaskSpec, GenerationMode
from apex.taskgen.provider import TaskGenerator
from apex.tasks.model import Task, TaskSplit
from apex.weakness.model import WeaknessCategory, WeaknessReport

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
    GenerationMode.EDGE_CASE: (15, "aucun catalogue de conditions limites n'existe encore"),
    GenerationMode.BUG_FARM: (12, "CodeIntelligence, PLANNED — aucun domaine de code réel"),
    GenerationMode.ADVERSARIAL: (12, "CodeIntelligence, PLANNED — aucun domaine de code réel"),
    GenerationMode.TRANSFER: (15, "une seule spécialité (« research ») est is_supported"),
    GenerationMode.SELF_PLAY: (17, "aucune orchestration multi-clones réelle"),
    GenerationMode.FROM_HYPOTHESIS: (16, "HypothesisGenerator, PLANNED"),
}


def _diagnostic_script(level: int) -> str:
    assertions = level >= _MAX_LEVEL
    lines = [
        "import json, os, socket, sys",
        "",
        "report = {}",
        'report["python_version"] = sys.version.split()[0]',
        'report["cwd_writable"] = os.access(os.getcwd(), os.W_OK)',
    ]
    if level >= 2:
        lines += [
            "try:",
            "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
            "    s.settimeout(1.5)",
            '    s.connect(("1.1.1.1", 443))',
            '    report["network_reachable"] = True',
            "except OSError:",
            '    report["network_reachable"] = False',
        ]
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
            return self._normal(split)
        if mode in (GenerationMode.INCREASING_DIFFICULTY, GenerationMode.CURRICULUM_INTERMEDIATE):
            return self._at_level(mode, split, level)
        if mode is GenerationMode.TARGETED_WEAKNESS:
            return self._targeted_weakness(split, weakness_report)
        if mode is GenerationMode.REGRESSION:
            return self._regression(base_task)

        raise NotImplementedYet(f"TaskGenerator({mode.value})", phase=15)  # pragma: no cover — vocabulaire fermé

    def _normal(self, split: TaskSplit) -> GeneratedTaskSpec:
        from apex.missions.workloads import research_diagnostic_command

        return GeneratedTaskSpec(
            title="Diagnostic recherche — normal", description="Diagnostic d'environnement standard.",
            command=research_diagnostic_command(), specialty=_SUPPORTED_SPECIALTY, split=split,
            mode=GenerationMode.NORMAL,
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
            description=f"Reproduction exacte de la tâche « {base_task.id} » pour vérifier l'absence de régression.",
            command=base_task.command, specialty=base_task.specialty, split=base_task.split,
            mode=GenerationMode.REGRESSION, dataset_ref=base_task.id,
        )
