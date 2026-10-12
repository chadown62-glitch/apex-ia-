# Game Graphics : démonstrateur réel

Le contrôleur local Qwen2.5-Coder-7B-Instruct Q5_K_M produit une spécification JSON typée. Un script Blender de confiance réalise la géométrie procédurale, les UV, les matériaux et quatre rendus. Le texte du modèle n’est jamais exécuté comme du Python sur l’hôte. Il s’agit d’un contrôleur neuronal de paramètres, pas d’un générateur neuronal de géométrie spécialisé ou entraîné par APEX.

Le modèle est arrêté, puis sa fin vérifiée avant Blender. APEX_HOME doit nommer un profil absolu commun avec la vision ; le verrou `.apex/model-session.lock` de ce profil est acquis même avec des répertoires runtime distincts. Une réservation coopérative contrôle les profils publics du core et refuse les processus Blender/Unreal/llama connus. Le core de Claude ne participe pas à cette réservation ; elle ne garantit pas l’exclusion globale de tous les moteurs IA. CUDA et Windows ne sont pas qualifiés ici.

## Installation

Python 3.12, les dépendances APEX existantes, Blender 4.3.2 et Docker pour le MVP #8. Les poids restent hors Git, environ 5,44 Go. Dans ce cloud, l’environnement Python partagé est `/workspace/apex-ia-/.venv` et ce lot est dans `/workspace/apex-game-graphics`.

```bash
cd /workspace/apex-game-graphics
export UV_CACHE_DIR=/workspace/.cache/uv
uv pip install --python /workspace/apex-ia-/.venv/bin/python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu 'llama-cpp-python[server]==0.3.36'
uv pip install --python /workspace/apex-ia-/.venv/bin/python -r docs/game_graphics/requirements.txt
export PYTHONPATH=.
export APEX_HOME=/workspace/apex-ia-/apex-data/cloud-home
export HF_HUB_DISABLE_XET=1
export HF_HUB_DISABLE_TELEMETRY=1
/workspace/apex-ia-/.venv/bin/python -c 'from pathlib import Path; from apex.game_graphics.models import fetch_coder; print(fetch_coder(Path("/workspace/game-studio-runtime")))'
blender --version
```

`fetch_coder` vérifie la licence Apache-2.0 officielle à la révision immuable avant les poids, puis leur SHA-256 et leur taille. Un changement de licence ou de contenu bloque. L’inférence ne télécharge rien. `LICENSES.md` contient la décision pour la France.

## Exécution

Les répertoires de sortie doivent être nouveaux. Les anciens artefacts sont conservés.

```bash
PYTHONPATH=. /workspace/apex-ia-/.venv/bin/python -m apex.game_graphics \
  --runtime /workspace/game-studio-runtime \
  --root /workspace/game-studio-output/new-red-coupe \
  --level prototype \
  --prompt 'Coupé rouge reconnaissable, 4.5 m par 1.9 m, hauteur 1.35 m, empattement 2.65 m.'
```

Code 0 : critères graphiques objectifs satisfaits. Code 2 : BLOCKED/échec, avec raison. Un modèle absent ne déclenche aucun remplacement procédural automatique. Le mode explicite `--procedural-spec spec.json` teste seulement l’outil. `--criteria overrides.json` surcharge les budgets et tolérances ; aucun niveau ne baisse automatiquement.

| Niveau | Critères supplémentaires par défaut |
|---|---|
| Prototype | ≤100 000 triangles, dimensions ±4 cm, pivots ±2 mm, quatre vues distinctes non vides |
| Jouable, côté assets | ≤60 000 triangles, un LOD réellement plus petit ; conduite UE non certifiée |
| Soigné | Deux LOD, textures PBR 2048, jugement visuel calibré contre références |
| Photoréaliste | Deux LOD, textures PBR 4096, jugement visuel calibré contre références |

Les deux derniers niveaux restent UNKNOWN/BLOCKED : le prototype ne fournit pas les textures ni le juge visuel calibré. Les assertions esthétiques du producteur ne peuvent pas lever ce blocage.

Le `Tier0Judge` existant appelle `BlenderGeometryOracle`. Deux processus Blender indépendants rouvrent `.blend` et GLB ; un troisième contrôle le LOD. Les UV/normales du GLB créent des sommets de couture : le contrôle topologique soude les positions à 1 µm, sans assouplir les limites. Contrôles : empreintes, dimensions, triangles, faces dégénérées, volume signé, topologie fermée, UV, matériaux, roues et pivots. Les tests de pixels détectent des vues vides ou répétées ; ils ne prouvent pas l’authenticité de toute image ou une qualité esthétique générale.

Une roue dont la géométrie est décentrée déclenche une correction ciblée du maillage local, puis réexport et nouvelles inspections. Le lot réutilise `apex.recovery.policy.decide`, avec trois tentatives maximales par défaut, un plafond conservateur de trois exécutions par artefact, arrêt sans amélioration ou en cas de nouveau défaut. Les réparations non implémentées restent BLOCKED. Aucun nouveau Scheduler, ModelProvider, TaskLoop ou moteur de juges.

## Contrat UE5

Le bundle contient `VehicleAssetContract` v1 dans `manifest.json`, le schéma JSON, `.blend`, GLB, LOD, spécification et quatre PNG, tous hachés. Blender : mètres, +X avant, +Z haut, repère droit. glTF : +Y haut, conversion par l’exporteur. UE : cible centimètres, +Z haut, +X avant, conversion d’échelle 100 ; l’import réel doit vérifier l’orientation et les matériaux. Les roues sont des objets séparés avec centres et axes de rotation.

Ce bundle contient des maillages statiques séparés, sans squelette ni PhysicsAsset. Il n’est pas un Chaos Vehicle prêt à conduire. Le lot #10 doit consommer le JSON, sans dépendre du code Python d’une branche non fusionnée.

## Validation

```bash
/workspace/apex-ia-/.venv/bin/python -m pytest tests/test_game_graphics_contract.py tests/test_game_graphics_blender.py -ra
/workspace/apex-ia-/.venv/bin/ruff check apex/game_graphics scripts/game_graphics tests/test_game_graphics*
/workspace/apex-ia-/.venv/bin/ruff format --check apex/game_graphics scripts/game_graphics tests/test_game_graphics*
/workspace/apex-ia-/.venv/bin/mypy --follow-imports=silent apex/game_graphics
PYTHONPATH=. /workspace/apex-ia-/.venv/bin/python scripts/game_graphics/benchmark.py \
  --runtime /workspace/game-studio-runtime --root /workspace/game-studio-output/new-benchmark
```

Les tests Blender invoquent le vrai exécutable et rouvrent les fichiers. Son absence est un skip explicite, jamais un remplacement. Le benchmark préassigne deux demandes inédites à heldout, les génère avec le modèle, vérifie l’arrêt puis produit réellement leurs assets, avant un défaut de roue et sa correction. Aucune adaptation des poids n’est lancée. Voir `REPORT.md` pour les preuves et `TRAINING.md` pour les limites de la spécialisation.
