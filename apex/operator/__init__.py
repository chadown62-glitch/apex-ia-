"""Operator — couche de contrôle centrale d'APEX (N+16).

Permet à un client (future UI, API locale) de piloter le lifecycle d'APEX
et ses tâches SANS avoir besoin du terminal pour les opérations courantes.
N'introduit ni second orchestrateur, ni second Task System, ni second Gate :
`OperatorService` (apex.operator.service) délègue entièrement à
`apex.missions.service.MissionService` et `apex.scheduler.service.
SchedulerService`, déjà responsables du cycle de vie réel d'une tâche
(« Mission » dans le vocabulaire déjà établi de ce dépôt) — voir leurs
docstrings respectives, jamais dupliquées ici.
"""

from __future__ import annotations
