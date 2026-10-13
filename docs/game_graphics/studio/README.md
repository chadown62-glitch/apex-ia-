# Game Graphics — atelier Blender, mission #14

Extension de PR #12 : mêmes primitives Blender, `ModelProvider` local Qwen, `Workspace`/Tool Engine, `OracleProvider`, `Tier0Judge`, `RecoveryPolicy` et verrou de ressources. Aucun changement du cœur, de PR #16 ou d'un projet Unreal.

Le catalogue construit trois familles originales : coupé sportif, atelier de rue et fauteuil. Le contrôleur choisit ensuite des opérations bornées sur leurs composants. Les géométries de ces trois assembleurs sont **procédurales**, même lorsque Qwen commande une modification. La reconstruction **neuronale TripoSR** est conservée séparément avec la provenance de ses poids.

## Reproduire dans un répertoire neuf

Prérequis : Blender 4.3.2 avec Cycles CPU, Python 3.12, dépendances APEX/PR #12 (`psutil`, `numpy`, `Pillow`, `jsonschema`, `llama-cpp-python[server]`). Les poids Qwen déjà provisionnés doivent correspondre au descripteur épinglé `coder-model.json` de PR #12. Aucun téléchargement implicite pendant le benchmark.

```bash
python -m apex.game_graphics.studio.benchmark \
  --root /chemin/neuf/benchmark \
  --runtime /chemin/runtime-apex \
  --level prototype
```

`--stage baseline` exécute la calibration native et les trois assemblages sans modèle de langage. `--stage improve` reprend ces créations et fait choisir les ajouts par Qwen. `--stage correction` injecte un déplacement d'accoudoir, déclenche le juge puis la correction Qwen. Les étapes conservent leurs fichiers et refusent d'écraser une révision existante.

Les niveaux **Prototype, Jouable, Soigné, Photoréaliste** et les personnalisations JSON `--criteria` réutilisent `QualityCriteria`. Les seuils de ce benchmark sont fixés dans [benchmark-v1.json](benchmark-v1.json), avant les essais. Un niveau exigeant des LOD, textures cuites ou une évaluation esthétique calibrée reste `UNKNOWN` quand ces preuves manquent ; le système ne réduit pas ses exigences après un échec.

## Opérations artistiques

`inspect_scene`, `repair_degenerate_topology`, `bevel_edges`, `subdivide_surface`, `set_location`, `set_material_pbr`, `add_component`, `save_revision_and_render` sont les seules opérations admises. Le schéma documentaire est [operations-v1.schema.json](operations-v1.schema.json) ; la validation Python stricte complète les contraintes entre opérations et familles.

Les plans passent par le Tool Engine avec permission d'écriture du seul espace du plan. Le worker Blender applique uniquement le code fixe du dépôt, avec auto-exécution des fichiers désactivée et contrôle des propriétaires des objets. **Ce worker natif n'est pas une sandbox** : `IsolationLevel.NONE` est explicite. Aucun Python, shell, chemin ou code produit par le LLM n'est exécuté. Ce lot est réservé aux scènes locales produites par ces outils ; les `.blend` tiers non fiables nécessitent une isolation supplémentaire.

Chaque révision produit : `.blend`, GLB, hiérarchie, traces avant/après, quatre images Cycles, silhouettes natives, paramètres caméra/lumière, SHA, logs et RAM échantillonnée. Deux nouveaux processus inspectent séparément le `.blend` et le GLB. Les shaders procéduraux de bois/cuir sont présents dans Blender ; leur cuisson en textures exportables n'est pas réalisée.

## Portée du juge

Le juge technique contrôle réellement les fichiers réouverts, la topologie, les UV, les matériaux séparés, les dimensions, le budget de triangles et le cadrage. La sonde visuelle mesure l'alignement d'une paire de composants dans deux passes natives d'identifiants isolés. Elle est calibrée sur des formes séparées avec déplacements connus, seuil fixé à quatre pixels. Elle ne mesure ni la beauté, ni l'occlusion dans le rendu principal, ni une ressemblance photoréaliste.

Un défaut précise objet, observation, référence, amélioration attendue, confiance limitée à la mesure et critère de correction. Les corrections utilisent la politique existante, un budget d'essais et l'arrêt en cas de régression ou d'absence de progrès. Les images avant/après restent disponibles. L'interface APEX et l'option globale des juges ne sont pas raccordées par ce lot.
