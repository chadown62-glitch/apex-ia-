# Game Engineer : réception d’assets et blocage objectif UE5

Le lot #10 consomme le `VehicleAssetContract` JSON v1 et les vrais fichiers de la PR #12. Il vérifie le schéma convenu, les SHA-256, le format GLB réel, les buffers embarqués, la présence des UV/normales/matériaux et quatre roues indépendantes avec leurs pivots glTF. Aucun import Python du lot #9 non fusionné. Les vérifications passent par le ToolEngine existant et ses permissions Workspace. Les fichiers sont conservés et relus dans l’ArtifactStore existant avant copie dans un projet neuf.

Ce lot ne fournit pas encore de véhicule pilotable. Le moteur et son SDK sont absents dans ce cloud. Le juge technique est une OracleProvider de prérequis : UNKNOWN/BLOCKED même si le modèle prétend avoir compilé ou conduit. Les contrôles d’import et de conduite dans le moteur ne sont pas qualifiés. Aucune classe n’est compilée contre des faux headers.

## Commande vérifiée

Dépendances : Python/APEX, `jsonschema==4.26.0`, `psutil`. Utiliser l’environnement partagé préparé par #9.

```bash
cd /workspace/apex-game-engineer
PYTHONPATH=. /workspace/apex-ia-/.venv/bin/python -m apex.game_engineer \
  --bundle /workspace/apex-game-graphics/docs/game_graphics/evidence/red \
  --root /workspace/game-studio-output/new-ue-probe \
  --level prototype
```

Le répertoire doit être nouveau. Code 2 : BLOCKED, avec rapport JSON, diagnostics réels, copie de fichiers intacts, objet ArtifactStore et projet d’import préparé. La présence d’un `.uproject` ne valide pas son chargement dans UE. Aucun processus Unreal n’est lancé par cette commande. `--engine-root` ou `APEX_UE_ROOT` indique une installation existante à sonder ; aucun moteur propriétaire n’est téléchargé automatiquement.

`C:\GameProject`, sa variante WSL `/mnt/c/GameProject`, leurs sous-répertoires, variantes de casse et chemins étendus Windows sont refusés en écriture. Les résultats précédents ne sont pas remplacés. Le script d’import préparé refuse aussi le projet protégé et exige le marqueur/nonce du projet isolé avant les opérations d’éditeur.

Les fichiers du projet contiennent le GLB reçu, un `.uproject` avec Python Editor Scripting, un script fixe `Tools/import_vehicle.py` et une requête locale. Le rapport montre une commande d’import future, non exécutée. Le script appelle `unreal.AssetImportTask` et conserve les objets retournés, sans prétendre vérifier scale/axes/physique. Son API et le comportement de l’importeur Interchange/glTF selon la version UE restent à tester réellement. Il s’agit de Python d’éditeur, aucun gameplay runtime Python.

## Critères conservés

Tous les niveaux exigent une compilation réelle sans erreur bloquante, un import mesuré (cm, axes, pivots, matériaux), un niveau chargé, composants véhicule/collisions/suspension/caméra/inputs, et un scénario automatisé de déplacement ≥1 m en 5 s par défaut. Prototype : pas de FPS minimum. Jouable : ≥30 FPS. Soigné : ≥60 FPS, scénario 10 s. Photoréaliste : ≥60 FPS, scénario 15 s et exigences graphiques qualifiées en amont. `--criteria fichier.json` permet des surcharges explicites (durées, déplacement, tolérances, nombre d’essais, FPS). Aucun niveau ne baisse silencieusement.

Le bundle actuel est statique : aucun skeletal rig, wheel bones ou PhysicsAsset pour Chaos. Les correspondances de latéralité/axes et les pivots importés doivent être mesurés dans UE, même si le contrat glTF est valide. Le juge produit des éléments indisponibles précis et n’accepte aucune affirmation modèle comme preuve. Un échec réel de contrat produit une demande de correction via RecoveryPolicy ; les prérequis absents ne déclenchent pas de répétition vide. Le budget maximal initial est trois essais par défaut. La boucle de corrections de compilation/physique n’est pas exercée faute de moteur.

Le TaskLoop existant exécute des tâches Python FUNCTION/PROJECT avec tests cachés ; il n’a pas d’adaptateur UE natif qualifié. Le MVP #8 a deux TaskRuns réels, mais sa démo de dimensions/conversion ne déclenche pas encore automatiquement les spécialistes #9/#10. La liaison concrète réalisée ici est le bundle de #9 reçu, haché, conservé et jugé par #10. Mission/Scheduler restent ceux du core ; aucun deuxième moteur d’exécution/jugement ou ModelProvider n’est créé.

## Baseline modèle

`apex.game_engineer.controller.propose_unreal_code` utilise le ModelProvider existant sur une URL loopback. Qwen officiel Q5_K_M a réellement généré une proposition Pawn/Chaos/caméra à partir de cette demande. Le serveur possédé est chargé et arrêté par la session qualifiée de #9, en lecture seule depuis ce lot ; aucun chargeur dupliqué dans #10. Modèle et licences immuables : voir PR #12, `docs/game_graphics/LICENSES.md`. La réponse n’est pas exécutée ; compilation/API/Blueprints/Chaos restent BLOCKED. `evidence/model/` conserve demande, réponse, tokens, temps, SHA et arrêt réel. Pas de fine-tuning.

## Tests

```bash
/workspace/apex-ia-/.venv/bin/python -m pytest tests/test_game_engineer_assets.py tests/test_game_engineer_gate.py -ra
/workspace/apex-ia-/.venv/bin/ruff check apex/game_engineer scripts/game_engineer tests/test_game_engineer*
/workspace/apex-ia-/.venv/bin/ruff format --check apex/game_engineer scripts/game_engineer tests/test_game_engineer*
/workspace/apex-ia-/.venv/bin/mypy --follow-imports=silent apex/game_engineer
```

Ces tests vérifient de vrais octets GLB, SHA, permissions, stockage/copies et blocages. Ils n’exécutent pas Unreal. Voir REPORT.md pour les limites et TRAINING.md pour la stratégie d’adaptation.
