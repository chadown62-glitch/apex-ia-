# Game Graphics 9 bis : choix des outils et création directe UE5

Le spécialiste graphique reçoit désormais un contrat de tâche et choisit Blender, Unreal ou un pipeline hybride. Les contraintes de destination, de qualité et de capacités requises passent avant la vitesse. Un résultat final UE ne peut pas être remplacé par un rendu Blender. Les coûts inconnus restent inconnus ; des mesures de coût sont comparables uniquement pour la tâche et le niveau demandés. Le contrôleur local propose des opérations artistiques typées, sans Python arbitraire, C++, commande console ou Blueprint de gameplay.

La présence du moteur est sondée sur PATH, `APEX_UE_ROOT`/`--engine-root`, racines connues, version et fichiers de plugins. Leur présence ne certifie aucune capacité. Le connecteur éditeur est une candidate UE5.6+, fondée sur les API publiques documentées, qui reste non qualifiée tant qu’elle n’a pas été exécutée avec le vrai moteur. Aucun Unreal n’est disponible dans ce cloud : toutes les créations, modifications et captures UE sont **BLOCKED** ici.

Suite du 13 octobre : le [lanceur Windows UE5.8](WINDOWS.md) prépare un lancement local
sur le PC, avec détection des installations, projet TEMP neuf, juge existant, réouvertures,
défaut natif ciblé/correction et archive des preuves. Il rejoue le plan Qwen accepté, sans
nouvelle inférence. Ce parcours Windows n'a pas encore été exécuté ; il ne fournit donc
aucun résultat Unreal validé. Construire l'archive avec `scripts/game_graphics/package_ue_windows.py`,
puis extraire et double-cliquer `Run-APEX-UE58.cmd`.

## Choix du logiciel

| Travail demandé | Méthode recommandée |
|---|---|
| Véhicule, maillage, UV/retopologie | Blender ; seul le coupé procédural de PR #12 est déjà exécuté, pas la modélisation libre |
| Maillage complexe destiné à un niveau UE | Hybride : Blender/neuronal selon capacité réelle, puis import/contrôle UE |
| Niveaux, éclairage, matériaux, assemblage d’environnement | UE direct |
| Modélisation native UE simple | UE ou Blender/hybride selon outils qualifiés, destination et mesures comparables |
| Paysage natif, PCG | UE, BLOCKED si l’adaptateur/capacité manque ; aucune primitive n’est présentée comme un Landscape/PCG |

`selection.select_method(GraphicsTask(...), capabilities, costs=...)` est une fonction de planification testée. Elle ne constitue pas une certification de rendu et n’invente pas une supériorité de performance. L’orchestration du cerveau et le dispatch de tous les workflows restent à raccorder ; le CLI ci-dessous exécute uniquement la candidate de petite scène native. Les autres demandes retournent la recommandation et un blocage, sans produire un environnement de substitution.

## Première scène, préparée

Une route 24×6 m, un bâtiment 6×5×6 m, deux potelets, un bac de décor, une lumière directionnelle et une lumière de remplissage ; trois matériaux PBR. Géométrie initiale : meshes simples fournis par le moteur. Il s’agit de l’assemblage natif restreint demandé, pas d’un quartier photoréaliste ni d’un maillage neuronal.

Le plan peut provenir de Qwen Coder Q5 local, d’un JSON utilisateur validé ou de la fixture procédurale explicite `--demo`. Le modèle peut varier les opérations/objets, positions, rotations, dimensions et paramètres PBR dans la whitelist (40 acteurs/12 matériaux max). La fixture ne remplace jamais silencieusement un modèle absent.

Depuis ce worktree empilé sur PR #12, avec l’environnement partagé :

```bash
cd /workspace/apex-game-graphics-ue
export APEX_HOME=/workspace/apex-ia-/apex-data/cloud-home
PYTHONPATH=. /workspace/apex-ia-/.venv/bin/python -m apex.game_graphics.ue \
  --runtime /workspace/game-studio-runtime \
  --root /workspace/game-studio-output/a-new-native-scene \
  --level prototype --demo
```

Code 2 attendu ici : projet et opérations préparés, diagnostic moteur absent, aucun `.umap`, `.uasset` ou PNG UE créé. Code 0 est réservé au parcours réel du moteur passant les contrôles objectifs. Le connecteur n’a pas encore produit ce résultat sur une machine UE ; sa qualification demeure BLOCKED.

`--engine-root`/`APEX_UE_ROOT` sélectionne une installation existante. Le runner lance le véritable UnrealEditor avec un script fixe et un job à nonce ; il utilise le rendu normal, jamais NullRHI. Il possède et arrête ses processus, conserve logs/mesures, applique un timeout et une limite de RSS échantillonnée. L’isolation est NONE (éditeur local de confiance, pas une sandbox OS). Windows et libération de VRAM ne sont pas qualifiés.

Pour préparer un plan réellement neuronal sans prétendre l’exécuter dans Unreal :

```bash
PYTHONPATH=. /workspace/apex-ia-/.venv/bin/python -m apex.game_graphics.ue \
  --runtime /workspace/game-studio-runtime \
  --root /workspace/game-studio-output/a-new-neural-scene-plan \
  --level prototype --prepare-only \
  --prompt 'Une route, un bâtiment, deux potelets, un bac de décor et deux lumières.'
```

Le ModelProvider existant est utilisé sur loopback. Poids officiels Qwen Q5 déjà vérifiés en PR #12 (Apache-2.0, révision/SHA immuables), aucun téléchargement à l’inférence. Le serveur possédé est arrêté et sa fin vérifiée avant Unreal. Même profil/verrou que Blender/vision ; refus du core actif et des processus graphiques connus. Le core de Claude ne participe pas au verrou et l’exclusion globale des applications tierces n’est pas garantie.

## Contrats et frontières Graphics/Engineer

`SceneAssetContract` v1 : scene_id, revision, parent SHA, IDs stables d’acteurs/matériaux, owner, dépendances engineer, unités/axes, chemins de packages et fichiers hachés/ArtifactStore. `SceneIntegrationReport` v1 : choix, critères conservés, exécutions, observations, verdicts, défauts, corrections et blocages. `scene-contract-v1.schema.json` décrit la frontière JSON, sans importer le code d’une autre branche.

Les IDs sont des tags `APEX_ID`/`APEX_SCENE`; les paths et GUID d’acteurs UE ne sont pas supposés stables entre versions. Les versions précédentes et leurs fichiers restent conservés. Un nouveau plan révision N+1 exige l’empreinte de la révision N et un scene_id identique. Game Engineer devra résoudre les IDs et revalider les références fonctionnelles avant promotion d’une nouvelle scène.

`C:\GameProject` et `/mnt/c/GameProject`, variantes de casse et chemins étendus, sont refusés. Le projet doit être neuf et privé, avec un marqueur/nonce vérifié aussi dans le moteur. Aucun Actor/Pawn gameplay, C++ ou Blueprint fonctionnel n’est créé. Des acteurs engineer ou un niveau déjà intégré déclenchent un refus ; l’adaptateur de mutation d’un monde partagé avec tests fonctionnels n’est pas encore qualifié. La candidate de première scène reste dans son propre niveau graphique.

## Contrôles dans Unreal et références

Le builder prépare le niveau, les matériaux et les acteurs ; un **second processus éditeur** doit rouvrir le niveau. L’inspecteur mesure les acteurs, rotations, dimensions issues des bounds réels, compte les triangles/vertices/LOD, lit les matériaux et leurs connexions, les intensités et collisions simples. Une caméra transitoire propre au juge capture quatre angles avec AutomationLibrary. Les résultats Blender et les déclarations du modèle ne satisfont pas ces contrôles.

Tier0Judge/OracleProvider existants sont réutilisés. Exécutions/versions/nonce/empreintes, erreurs Python/shaders/loaders, acteurs, dimensions, PBR, lumière et captures sont contrôlés. Les niveaux Prototype/Jouable/Soigné/Photoréaliste conservent leurs critères. Jouable exige une mesure FPS qualifiée qui manque actuellement. Soigné/photoréaliste ajoutent textures et référence/vision calibrée : UNKNOWN/BLOCKED tant que ces mesures manquent.

`--references manifest.json` accepte une liste `{view: 0..3, path: ..., sha256: ...}`. Le comparateur attend des captures provenant d’une vraie inspection native ; il produit une RMSE brute par vue, sans modifier les images. Les poses, l’exposition et la qualité sémantique exigent une calibration. La métrique ne certifie pas la composition, le photoréalisme ou la fidélité artistique ; la présence de références active l’exigence calibrée et conserve BLOCKED tant que cette évaluation manque.

Les défauts indiquent leur origine Unreal et l’acteur concerné. La candidate de correction réapplique les paramètres graphiques convenus dans une **nouvelle version isolée du niveau**, avec trois essais max, RecoveryPolicy existante, arrêt sur nouveau défaut ou absence d’amélioration. Elle ne fait pas une mutation chirurgicale d’un niveau intégré. Les défauts non pris en charge restent bloqués. Aucun cycle de correction UE réel n’a été observé faute de moteur.

## État des capacités

| Capacité | État actuel |
|---|---|
| Choix Blender/UE/hybride et contrats/ownership | TESTED, planification |
| Petits niveaux natifs, placement de meshes simples, paramètres PBR/lumières/collisions | Connecteur IMPLEMENTED, exécution UE BLOCKED/UNVERIFIED |
| Réouverture indépendante et captures UE | Code préparé, jamais exécuté ici |
| Import et configuration de GLB / assets artistiques dans ce connecteur | Pas encore implémentés ; préparation d’import distincte en PR #13, aucun import UE qualifié |
| Modification d’un niveau contenant de la logique Engineer | Refusée tant que contrat/dépendances et tests fonctionnels ne sont pas qualifiés |
| Landscape, routes splines/intersections/villes, végétation, outils de modélisation, PCG | Non implémentés dans cette première candidate ; BLOCKED pour ces demandes |
| Nanite/Lumen, instancing, LOD UE, textures/shaders complexes, budgets GPU/FPS | Non qualifiés ; Lumen désactivé explicitement dans la fixture de prototype, aucun niveau élevé accepté |
| Juge vision calibré, corrections esthétiques, photoréalisme | BLOCKED |

## Vérifications locales

```bash
/workspace/apex-ia-/.venv/bin/python -m pytest tests/test_game_graphics_ue_contract.py tests/test_game_graphics_ue_execution.py -ra
/workspace/apex-ia-/.venv/bin/ruff check apex/game_graphics/ue scripts/game_graphics/ue_*.py tests/test_game_graphics_ue*
/workspace/apex-ia-/.venv/bin/ruff format --check apex/game_graphics/ue scripts/game_graphics/ue_*.py tests/test_game_graphics_ue*
/workspace/apex-ia-/.venv/bin/mypy --follow-imports=silent apex/game_graphics/ue
```

Ces tests couvrent fichiers, contrats, choix, protections, absence de moteur et nettoyage d’un vrai processus Python Linux. Ils n’importent pas de faux module `unreal` et ne remplacent aucun test Unreal. Voir REPORT.md et les preuves.
