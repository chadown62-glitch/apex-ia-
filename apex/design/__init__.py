"""APEX Design Engine (N+17) — pipeline de conception assistée : d'une
demande en langage naturel à un `DesignPackage` traçable (brief, recherche,
concepts, prompts visuels, résultats visuels, validation, artefacts).

Ne duplique rien de l'existant :
- Persistance de contenu -> `apex.artifacts` (réutilisé, jamais réécrit).
- Autorisation -> `apex.policy` (une seule action nouvelle, `SUBMIT_DESIGN`,
  même motif que `CREATE_MISSION`/`CREATE_TASK`).
- Observabilité -> `apex.db.repositories.EventRepository`/`AuditLogRepository`
  (réutilisés, jamais un second journal).
- Pilotage sans terminal -> `apex.operator` (N+16), qui expose ce moteur via
  `submit_design`/`get_design_status`/`get_design_result`/`resume_design`.

Ce module ne simule JAMAIS une étape qu'il ne peut pas réellement exécuter :
la recherche web (`apex.design.research`) et la génération visuelle
(`apex.design.visual_provider`) n'ont aujourd'hui aucun backend réel
connecté dans ce dépôt (voir `apex.interfaces.registry`, entrées
`SearchProvider` PLANNED et `VisualGenerationProvider` STUB) — l'abstraction
et l'orchestration sont réelles et testées, le résultat de ces deux étapes
est honnêtement marqué indisponible plutôt que fabriqué.
"""
