# Validation du MVP #8

Base `bd2edd285ad2f796991ce5460715cb4ee9f3f06c`, branche
`agent/codex/8-project-orchestrator`. Nouveaux fichiers déclarés seulement.
Cloud Linux, 32 GiB RAM, quota quatre CPU ; Blender 4.3.2 présent, aucun UE5
ni NVIDIA observé. Clone Windows inaccessible depuis ce workspace.

IMPLEMENTED : recette limitée à deux Tasks, Mission de gouvernance, délégation
TaskRun/TaskLoop, gate Scheduler existant, reçus atomiques, ArtifactStore et
provenance, reprise sans regénérer un succès, modèles indisponibles refusés.
La Mission attend une décision humaine en REVIEW ; aucun merge/promotion.

TESTED : vraie SQLite, vrais AgentRuntime/TaskRunService/TaskLoop/Router,
vrai Docker avec isolation conservée, vrais oracles et recovery. Le modèle
de ces tests est une doublure explicitement identifiée ; ce câblage ne prouve
aucune compétence neuronale. Tests de correction d'un candidat faux, borne de
tentatives, dépendance, corruption d'artefact, substitution d'un hash pourtant
valide, reprise partielle et perte du reçu. Un endpoint localhost absent est
réellement sondé et retourne BLOCKED avant exécution.

Validation finale : **10 tests PASS**, zéro échec/skip, 37.718 s. Ruff/format et mypy (quatre fichiers) passent. CLI sans serveur : code 2, `blocked`, `model_unavailable:game_graphics`, Mission `queued`.

Commandes : `python -m pytest tests/test_project_orchestration.py -ra`,
`ruff check apex/project_orchestration tests/test_project_orchestration.py scripts/project_orchestration`,
`mypy --follow-imports=silent --ignore-missing-imports apex/project_orchestration`.

Preuve du parcours réel sous génération fixture : TaskRun graphique
`e33b083a-6809-46eb-926f-a4a66553c3cb`, deux tentatives (candidat incorrect puis
PASS), artefact `sha256:b7fbc73257754f968237eaae944f9bb4494d070ca5784e0c11c8caa2e414fe7e` ;
TaskRun conversion `14e4419e-e0ad-4536-b6b9-66cfbec16140`, une tentative PASS,
artefact `sha256:11a4e9d9f20a014349d7d1807f3b9662e3070f9a718dc3b172f9c9dc011eb5c9`.
Trois générations fixture au total ; reprise normale, reprise partielle et perte
du reçu ne régénèrent pas un succès. La Mission reste REVIEW, jamais validée
automatiquement. Ces identifiants sont ceux de ce test, pas des preuves d'IA.
L'objectif de ce lot est le MVP réduit ; les spécialistes, les critères
graphiques/UE5 et la baseline neuronale sont développés dans #9/#10.

UNVERIFIED : Windows, réservation/bascule GPU, usages de plusieurs instances
du runtime central, jugement esthétique. Le garde de dépendance utilise un
helper privé du Scheduler, contractuellement lié à cette base. L'arrêt d'une
TaskRun reste géré par le TaskLoop existant ; un échec terminal n'est pas
automatiquement remis à zéro. Aucun fichier de Claude ou de PR #5/#7 modifié.
