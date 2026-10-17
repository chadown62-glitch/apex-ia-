# Reprise de Codex : ce qui bloque Game Studio dans APEX.exe (17/10/2026)

Synthèse écrite par Claude pendant la pause de Codex. **Aucun fichier de Codex n'a été modifié.** Chaque point dit :
- ce qui a été **mesuré** ;
- la **cause** dans le code ;
- une **correction proposée**, que Codex reste libre de faire autrement ;
- comment la **vérifier**.

## Où en est le parcours réel

Mesuré sur un vrai Windows Server (runner GitHub), avec la candidate combinée : ma branche, #19, #22, #24, #26,
#5, #11 et #13, reconstruites dans le runner sans rien pousser
([run 38987535861](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38987535861)).

1. installation, « Préparer APEX », modèle intégré, démarrage : **READY** ;
2. mode 3D : « Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export Blender. » ;
3. APEX **propose** la mission ; le **clic humain** la confie à `game_studio.create` ;
4. Game Studio **refuse** : `ModuleNotFoundError: No module named 'jsonschema'`.

Les blocages ci-dessous sont rangés **dans l'ordre où le parcours les rencontre**. Chacun masque les suivants.

## 1. `jsonschema` absent de la liste Windows

- **Mesuré** : le refus ci-dessus. Le Python préparé par APEX importe `apex.game_studio` et sa délégation, mais
  pas `jsonschema`.
- **Cause** : `apex/game_graphics/judge.py` et `apex/game_engineer/{assets,tools}.py` l'importent. Il n'est
  déclaré :
  - ni dans `setup/packaging/runtime/requirements-windows.in` ;
  - ni dans l'extra `game_studio` de `runtime_build/pyproject.runtime.toml` (qui déclare psutil, numpy, pillow et
    `llama-cpp-python`).
- **Correction proposée** :
  - ajouter `jsonschema>=4.20` aux deux fichiers ;
  - puis régénérer la liste avec `setup/packaging/runtime/lock_runtime.sh`.
  - Je l'ai fait sur une copie jetable. Résultat : 5 paquets ajoutés, aucune version existante changée, licences
    MIT, empreintes Windows identiques à PyPI :
    `attrs==26.1.0`, `jsonschema==4.26.0`, `jsonschema-specifications==2025.9.1`, `referencing==0.37.0`,
    `rpds-py==2026.9.1` (détail dans l'Issue #21).
- **Vérification** : le workflow `windows-candidate`. Le refus suivant doit être celui du planificateur (§2).

## 2. Planificateur des missions : rien ne permet de l'installer dans APEX.exe

- **Mesuré** :
  - dans l'arbre combiné local, avec `jsonschema` présent, le refus suivant est `local_planner_model_missing` ;
  - sous Windows, la sonde dit « Qwen2.5-Coder-7B épinglé absent ; llama-cpp-python absent ».
- **Causes** :
  - toute mission proposée par le Cerveau passe par la planification dynamique. Elle exige le 7B épinglé et
    refuse le moteur intégré (`dynamic_planner_requires_pinned_7b_until_native_handoff_is_qualified`) ;
  - `fetch_coder()` (`apex/game_graphics/models.py`) télécharge le GGUF épinglé, révision `13fb94bf…`, fichier
    Q5_K_M de 5 444 831 232 octets, SHA-256 `586844ea…`, après vérification de la licence. Il écrit ensuite
    `coder-model.json`. Mais **rien ne l'appelle** dans l'arbre combiné : ni commande, ni bouton. Le descripteur
    est attendu dans `APEX_HOME/.apex/game-studio/coder-model.json` (`apex/game_studio/capabilities.py`) ;
  - `CoderSession` lance `python -m llama_cpp.server`, donc `llama-cpp-python[server]`. Or **PyPI ne publie
    `llama-cpp-python` qu'en sources** : 0.3.36, licence MIT, aucune roue Windows pour aucune version (vérifié le
    17/10/2026). « Préparer APEX » installe avec `--only-binary=:all:`, donc il ne peut pas l'installer.
- **Décisions pour Codex** (je ne suppose pas qu'une variante CUDA existe) :
  - **source binaire** de `llama-cpp-python` pour Windows x64 et CPython 3.12. Options : roues construites et
    figées par une CI Windows, comme `gpu-locks`, ou index tiers vérifié par empreinte. Préciser processeur ou
    CUDA, et la licence de distribution ;
  - **un point d'entrée** pour installer le planificateur sur un geste de l'utilisateur. Par exemple une
    sous-commande de `python -m apex.game_graphics` qui appelle `fetch_coder(APEX_HOME/.apex/game-studio)`. Le
    bouton du Panel viendra côté Claude, une fois ce contrat fixé. Il faut une confirmation explicite : 5,4 Go
    plus la licence. Aucun téléchargement implicite ;
  - éventuellement, le **moteur intégré** comme planificateur, une fois son transfert qualifié. Il est déjà
    installé ; c'est le blocage nommé par le code.
- **Côté Claude, prêt** : la ligne « Planificateur 3D » du Panel lit `coder_manifest_present` et
  `llama_cpp_present` de la sonde, et passe d'« absent » à « dégradé » puis à « prêt » sans changement.

## 3. Blender : cherché seulement dans le PATH

- **Mesuré** : le runner n'a pas Blender, et la ligne Blender le dit. Codex qualifie Blender 4.3.2.
- **Cause** : `apex/game_studio/capabilities.py` utilise `shutil.which("blender")`. Sous Windows, l'installateur
  de Blender n'ajoute pas son dossier au PATH (à ma connaissance ; à confirmer sur le PC de l'utilisateur).
- **Correction proposée** :
  - accepter aussi un chemin explicite (variable d'environnement ou réglage) ;
  - et/ou chercher les emplacements standard (`%ProgramFiles%\Blender Foundation\Blender 4.3\blender.exe`).
  - Sinon, documenter l'ajout au PATH pour l'utilisateur.
- **Côté Claude** : la ligne Blender du Panel exécute `blender --background --version` sur le chemin que
  rapporte la sonde de Game Studio. Elle suivra donc la correction.

## 4. Empaquetage : `scripts/game_engineer/` absent du runtime

- **Mesuré** : la candidate embarque `scripts/game_graphics/`, pas `scripts/game_engineer/`.
- **Cause** :
  - `_assemble_common` (`runtime_build/build.py`) ne copie que `scripts/game_graphics` ;
  - `apex/game_engineer/__main__.py:102` lit `scripts/game_engineer/import_vehicle.py`.
- **Effet** : la préparation de l'import UE5 échouerait dans APEX.exe. Les missions Blender n'en dépendent pas.
- **Correction proposée** : le même `copytree` pour `scripts/game_engineer`.

## 5. Partage du GPU pendant une mission

- **État** : côté Cerveau, `request_gpu` / `return_gpu` existent et sont testés. Côté Game Studio, ils ne sont pas
  encore appelés autour de `resource_lease`. La garde GPU de #26 reste en place.
- **À faire** : appeler les deux autour de `resource_lease`. Ne lever la garde qu'après un test sur la RTX 4060 Ti
  de l'utilisateur.

## 6. Points de la réunion des branches, sans effet sur le parcours

- `apex/vision` (#7) à classer dans `runtime_build/selection.py`.
- #12, #16 et #17 sont en conflit avec la pile #19 → #26 dans les deux ordres : les fermer ou les refaire sur la pile.
- La spécialité `game-graphics` est déclarée `is_supported` au stade STUB. Cela fait échouer la garde historique
  `test_no_second_specialty_task_provider_is_registered`, l'un des 7 échecs connus de l'arbre combiné.
- `test_game_graphics_contract.py` : 2 échecs liés à `llama_cpp` et à psutil dans le sous-processus.

## 7. Ce qui a changé côté Claude, et qui touche Game Studio

- **Panel** : chaque Python reçoit `-P` (`24edcbc`). Sans cela, un dossier courant contenant `apex\` (un clone du
  dépôt) passait avant l'application installée. Dans la CI, Game Studio paraissait absent pour cette raison.
- **Fichier partagé** `runtime_build/build.py` : seule `_extract_commit` a changé (correction Windows `eb2713b`).
  Votre `_assemble_common` est intact et fusionne sans conflit.
- **États de préparation** (`apex/obs/component_checks.py`) : je lis le rapport de `capabilities.probe()` sans le
  réimplémenter. Les clés lues sont listées dans l'Issue #21.

## Vérifier après chaque correction

1. Modifier `setup/packaging/windows/ci/candidate-request.json` (champ `attempt`) sur la branche de Claude, ou
   lancer `windows-candidate` manuellement une fois le workflow sur la branche par défaut.
2. Lire les annotations du run : programme exécuté, imports, lignes d'état, proposition, clic, refus ou mission.
3. Le verdict PASS exige que la mission produise des fichiers. Ce n'est pas faisable sur le runner, qui n'a ni
   Blender ni GPU, mais sur le PC de l'utilisateur.
