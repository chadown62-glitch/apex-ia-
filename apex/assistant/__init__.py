"""Assistant APEX (0.4.0) — ce que l'application montre à l'utilisateur : projets, conversations, quatre modes de
travail, paramètres. Rien n'est réimplémenté ici : chaque tour passe par les composants existants d'APEX —
routage et génération (`apex.routing`, `apex.models`, runtime natif compris), mémoire (`apex.memory`), moteur
d'outils (`apex.tools`, permissions explicites) et sandbox (`apex.tools.sandbox_runner` -> `AgentRuntime`,
PolicyEngine, réseau coupé).

- `modes` : Normal, Code, Design, Finance — consignes, outils offerts et vue propres à chacun, et ce que le mode
  ne fait PAS (jamais une capacité annoncée qui n'existe pas) ;
- `tools` : outils déterministes des modes Design et Finance (calcul, projections, prêt, contraste WCAG) ;
- `settings` : paramètres de l'application, validés et persistés ;
- `attachments` : pièces jointes TEXTE d'un projet, réellement lues et jointes aux tours dans un budget ;
- `turns` : exécution asynchrone d'un tour (file d'attente, étapes d'outils, annulation, reprise après arrêt).
"""
