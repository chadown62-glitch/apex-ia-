"""WeaknessDetectorService — rassemble les `EvalRun` réels d'une compétence
(spécialité) depuis TaskBank/EvalRunner, puis délègue à `WeaknessDetector`
(pur) pour la classification.

Compose `apex.tasks.repository.TaskRepository` et
`apex.evalrunner.repository.EvalRunRepository` — les deux couches domaine
déjà réelles depuis la Phase 5 — jamais `AgentRuntime` : ce module ne fait
que LIRE l'historique déjà persisté, il n'exécute rien et n'a donc aucun
besoin du runtime d'exécution. Aucune nouvelle table, aucun nouveau champ
sur `Task`/`EvalRun` : la spécialité (`Task.specialty`) est le seul proxy de
« compétence/domaine » disponible à ce stade (`SkillRegistry`, `PLANNED`,
phase 14, n'existe pas encore).
"""

from __future__ import annotations

from typing import Any

from apex.evalrunner.repository import EvalRunRepository
from apex.tasks.repository import TaskRepository
from apex.weakness.detector import WeaknessDetector
from apex.weakness.model import WeaknessReport


class WeaknessDetectorService:
    def __init__(
        self, session_factory: Any, *, detector: WeaknessDetector | None = None,
    ) -> None:
        self._task_repo = TaskRepository(session_factory)
        self._eval_repo = EvalRunRepository(session_factory)
        self._detector = detector or WeaknessDetector()

    def detect_for_specialty(
        self, specialty: str, *, clone_id: str | None = None, limit: int = 200,
    ) -> WeaknessReport:
        """Rassemble tous les `EvalRun` des tâches de `specialty` (filtrés
        sur `clone_id` si fourni), puis classifie. `limit` borne le nombre
        de TÂCHES considérées et, pour chacune, le nombre de RUNS — même
        limitation de volume que le reste d'`apex.tasks`/`apex.evalrunner`
        (aucune pagination ailleurs à ce stade du projet)."""
        tasks = self._task_repo.list_by_specialty(specialty, limit=limit)
        eval_runs = []
        for task in tasks:
            runs = self._eval_repo.list_by_task(task.id, limit=limit)
            if clone_id is not None:
                runs = [r for r in runs if r.clone_id == clone_id]
            eval_runs.extend(runs)
        return self._detector.detect(eval_runs)
