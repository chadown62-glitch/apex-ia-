# Réunion des branches Claude et Codex dans `apex/integration` — dossier de préparation (16/10/2026)

Ce dossier prépare une décision humaine. **Rien n'est fusionné, aucune PR n'est fusionnée, aucune release
n'est publiée.** Toutes les fusions décrites ici ont été répétées dans un worktree jetable
(`scripts/integration/rehearse_merge.sh`), jamais poussées.

Objectif de la première intégration : le Cerveau et Game Studio utilisables ensemble dans APEX.exe. Les
améliorations des modèles viennent après.

## 1. Règles qui s'appliquent

Ces règles viennent de `AGENTS.md` et `docs/coordination/WORKFLOW.md`, sur `apex/integration` :
- la branche commune est `apex/integration` ; on y intègre **une PR à la fois**, avec un intégrateur
  **humain** ;
- avant chaque fusion, on reprend la dernière `apex/integration` dans la branche, on relance les tests, puis
  toutes les autres branches revalident ;
- jamais de force-push, jamais de réécriture de `main` ni de `claude/apex-official-development-4kao8t` ;
- l'entrée de cette branche historique dans `apex/integration` **se décide à part** (`CLAUDE.md`).

## 2. État des branches

| Branche | PR | Base de la PR | Tête | Propriétaire | Contenu |
|---|---|---|---|---|---|
| `apex/integration` | — | — | `bd2edd2` | humain | `fab616d` + 4 fichiers de coordination (AGENTS.md, CLAUDE.md, WORKFLOW.md, gabarit de PR) |
| `claude/apex-official-development-4kao8t` | [#2](https://github.com/chadown62-glitch/apex-ia-/pull/2) | `main` | celle de ce commit | Claude | Cerveau v2, assistant (modes, outils, délégation à Game Studio, proposition puis clic), partage du GPU, profil autonome, Panel, accélération GPU facultative |
| `agent/codex/18-game-studio-integration` | [#19](https://github.com/chadown62-glitch/apex-ia-/pull/19) | `apex/integration` | `db58f30` | Codex | Game Studio dans APEX : backend, frontend, missions Blender |
| `agent/codex/20-game-studio-multi-agent` | [#22](https://github.com/chadown62-glitch/apex-ia-/pull/22) | #19 | `c0a8591` | Codex | scène multi-assets, assemblage Blender jugé |
| `agent/codex/23-game-studio-planning` | [#24](https://github.com/chadown62-glitch/apex-ia-/pull/24) | #22 | `10bd282` | Codex | plans Qwen bornés, Graphics → Engineer, outils de délégation |
| `agent/codex/25-game-studio-candidate` | [#26](https://github.com/chadown62-glitch/apex-ia-/pull/26) | #24 | `1e2db2f` | Codex | candidate : couverture des demandes, reprise bornée, garde GPU |
| `agent/codex/4-inference-diagnostics` | [#5](https://github.com/chadown62-glitch/apex-ia-/pull/5) | `apex/integration` | `21a34f8` | Codex | garde d'appartenance des tâches |
| `agent/codex/8-project-orchestrator` | [#11](https://github.com/chadown62-glitch/apex-ia-/pull/11) | `apex/integration` | `e475a8a` | Codex | MVP projets |
| `agent/codex/6-vision-specialist` | [#7](https://github.com/chadown62-glitch/apex-ia-/pull/7) | `apex/integration` | `bd48096` | Codex | prototype vision et OCR |
| `agent/codex/10-game-engineer` | [#13](https://github.com/chadown62-glitch/apex-ia-/pull/13) | `apex/integration` | `3fb175e` | Codex | Game Engineer, contrat Blender |
| `agent/codex/9-game-graphics` | [#12](https://github.com/chadown62-glitch/apex-ia-/pull/12) | `apex/integration` | `b2334f4` | Codex | Game Graphics, première version |
| `agent/codex/14-neural-3d` | [#17](https://github.com/chadown62-glitch/apex-ia-/pull/17) | #12 | `a990ebf` | Codex | opérations Blender, TripoSR sur processeur |
| `agent/codex/15-graphics-ue` | [#16](https://github.com/chadown62-glitch/apex-ia-/pull/16) | #12 | `04af232` | Codex | lanceur Windows pour UE5.8 |

Les quatre PR de la pile Game Studio s'empilent : #19 → #22 → #24 → #26.

`main` (`ca3479a`) a divergé : il porte 11 commits absents de ma branche. Ce n'est pas l'objet de cette
première intégration ; la réconciliation se fera séparément, sur autorisation (WORKFLOW.md).

**Un seul fichier est modifié à la fois par ma branche et par une branche de Codex** depuis leur base
commune `fab616d` : `runtime_build/build.py`. Ce point a été vérifié branche par branche
(`git diff --name-only`).
- Je l'ai écrit (`a0b1e1e`), fonction `_extract_commit` comprise.
- La pile de Codex (#26) ajoute du code dans `_assemble_common`.
- Ma correction `eb2713b` ne touche que `_extract_commit`. Sous Windows, `git archive -o` ne pouvait pas
  rouvrir le fichier temporaire ; l'extraction passe désormais par un dossier temporaire. Le défaut a été
  constaté sur le runner Windows.
- Les deux modifications fusionnent sans conflit (`git merge-tree`, vérifié). Codex relit ce changement
  (Issue #21).

## 3. Répétition des fusions

Commande :

```
scripts/integration/rehearse_merge.sh [--order "…"] [--deps DOSSIER] [--pytest "…"] [--keep]
```

Le script crée un worktree jetable depuis `origin/apex/integration`, puis fusionne les branches une à une
(`--no-ff`). Il rapporte pour chacune : fusion propre, ou conflits avec la liste des fichiers. Il ne pousse
rien et ne télécharge rien. Pour une branche dont le travail n'est pas encore poussé, il prend la version
locale si elle contient la version distante.

**Ordre proposé — toutes ces fusions sont propres :**

| # | Branche | Résultat |
|---|---|---|
| 1 | `claude/apex-official-development-4kao8t` | fusion propre |
| 2 | #19 `18-game-studio-integration` | fusion propre |
| 3 | #22 `20-game-studio-multi-agent` | fusion propre |
| 4 | #24 `23-game-studio-planning` | fusion propre |
| 5 | #26 `25-game-studio-candidate` | fusion propre |
| 6 | #5 `4-inference-diagnostics` | fusion propre |
| 7 | #11 `8-project-orchestrator` | fusion propre |
| 8 | #13 `10-game-engineer` | fusion propre |

**Tests dans l'arbre ainsi fusionné.** Les bibliothèques de Game Studio (Pillow, psutil, jsonschema) étaient
installées à part, dans un dossier isolé.
- 248 tests ciblés, 248 passent :
  - assistant et Game Studio, dont mes 16 tests qui ne tournent qu'avec ses vrais outils ;
  - partage du GPU, accélération GPU, profil autonome, packaging du runtime ;
  - les tests de Codex : `test_game_studio_{delegation,api,lifecycle,requirements,planning,controller_guard}`.
- Suite complète : 3 476 passent ; aucun des 7 échecs ne vient de la fusion (§7).

La répétition a trouvé deux défauts de **mes** tests, corrigés avant ce dossier (commit « GPU activation:
torch-loaded check injectable… ») :
- mes tests GPU retiraient torch de `sys.modules` ; avec les routes de Game Studio, torch se réimportait et
  refusait (TORCH_LIBRARY en double) ;
- mon test « paquet facultatif » attendait l'absence tolérée de `game_studio`. Or Codex le classe dans le
  Runtime : son absence est alors, à juste titre, une violation.

**Ce qui ne fusionne pas, et à qui c'est.** Ce sont des points pour Codex : ses propres fichiers.
- **Les deux lignes Game Graphics de Codex se contredisent.**
  - #12 (`9-game-graphics`), puis #17 et #16 qui s'appuient dessus, entrent en conflit avec la pile #19→#26,
    **dans les deux ordres**.
  - Fichiers en conflit : `apex/game_graphics/models.py`, `resources.py`, `studio/{benchmark,controller,
    execution,neural}.py`, `scripts/game_graphics/studio/{neural_cpu,render_asset}.py`.
  - La pile en porte une version plus récente, qui diverge. À Codex de dire si #12, #16 et #17 sont remplacées
    par la pile (à fermer), ou à refaire sur elle.
- **#7 (vision)** fusionne proprement, mais ajoute `apex/vision` sans le classer dans
  `runtime_build/selection.py`, un fichier partagé que la pile de Codex modifie. Le test
  `test_every_apex_subpackage_is_classified` échoue alors (38 erreurs en cascade dans
  `test_runtime_packaging.py`). Correction : une ligne dans `selection.py`, par Codex.

## 4. Ordre d'intégration proposé

0. **Décision humaine — prise : option (B).** La branche `agent/claude/brain-integration`, partie
   d'`apex/integration`, reçoit ma branche par fusion. Une PR **brouillon** va vers `apex/integration`.
   L'utilisateur est l'intégrateur final : rien n'y est fusionné sans lui. Les deux options gardaient
   l'historique, sans réécriture :
   - (A) une nouvelle PR de `claude/apex-official-development-4kao8t` vers `apex/integration`. La PR #2, vers
     `main`, reste telle quelle.
   - (B) une branche `agent/claude/<issue>-brain-integration`, partie d'`apex/integration`, qui reçoit ma
     branche par fusion, puis une PR vers `apex/integration`. C'est la forme du protocole.
1. Ma branche, en premier : Game Studio s'enregistre ensuite de lui-même dans l'assistant (import gardé).
   Sans Game Studio, l'assistant reste complet ; c'est testé.
2. #19, puis #22, #24, #26. Après chaque fusion, la base de la PR suivante est à repointer vers
   `apex/integration`.
3. #5, #11, #13.
4. #7, une fois `apex/vision` classé.
5. #12, #16, #17 : selon la décision de Codex (§3).

À chaque étape :
- reprendre la dernière `apex/integration` dans la branche ;
- relancer la répétition et les tests du §6 ;
- fusion par l'intégrateur humain ;
- les autres branches revalident.

## 5. Fichiers partagés et propriétaires

| Fichier | Modifié par | Règle |
|---|---|---|
| `setup/packaging/runtime/requirements-windows.in` / `.lock` | Codex (pile : pillow, psutil) | Codex ; je n'y touche pas. `jsonschema` et `llama-cpp-python` restent à y déclarer (§6) |
| `runtime_build/build.py` | Claude (`_extract_commit`, correction Windows `eb2713b`) ; Codex (pile : `_assemble_common`) | partagé : chacun sa fonction, fusion propre (§2) |
| `runtime_build/pyproject.runtime.toml`, `selection.py` | Codex (pile) | Codex ; `apex/vision` à classer |
| `runtime_build/verify.py` | Claude (`OPTIONAL_PACKAGES`) | Claude ; compatible avec le classement de Codex (testé) |
| `apex/tools/model.py` (permissions `GAME_STUDIO_*`) | Codex (#24, #26) | Codex ; lues défensivement par `apex/assistant/modes.py` |
| `apex/api/app.py`, `apex/api/dependencies.py` | Codex (routes Game Studio) | Codex |
| `apex/assistant/**`, `apex/api/routes/assistant.py`, `apex/models/**`, `apex/brain/**`, `apex/standalone/**` | Claude | Claude |
| `setup/src/**`, `setup/packaging/ApexSetup.iss`, `build_desktop_release.sh`, `setup/packaging/runtime/gpu/**` | Claude | Claude |
| `apex/game_studio/**`, `apex/game_graphics/**`, `apex/game_engineer/**`, `scripts/game_*` | Codex | Codex |
| `.github/workflows/windows-native.yml`, `windows-desktop.yml`, `gpu-locks.yml` | Claude (CI Windows ; `gpu-locks` validée par l'utilisateur) | Claude ; déclenchées par un push sur ma branche de développement seulement |
| `AGENTS.md`, `CLAUDE.md`, `docs/coordination/**`, `.github/PULL_REQUEST_TEMPLATE.md` | intégration (humain) | inchangés |

Contrats entre les deux côtés (détail : `docs/INTEGRATION_CERVEAU_GAME_STUDIO.md` §2) :
- `register_game_studio_tools(registry)` ;
- `ctx.extra["game_studio_service"]` ;
- l'appartenance `owner_workspace_id` ;
- `capabilities.probe()` ;
- `artifact(…)` ;
- le bail `APEX_HOME/.apex/model-session.lock`, avec `request_gpu` et `return_gpu`.

## 6. Dépendances, précisément

Relevées dans le code, par analyse des imports de chaque paquet de l'arbre fusionné, puis comparées aux
déclarations. Statuts : IMPLEMENTED, TESTED, MEASURED, INFERRED, UNVERIFIED, BLOCKED.

**Bibliothèques Python** (versions : `requirements-windows.lock`, celui de la pile Codex pour Pillow et
psutil) :

| Composant | Imports tiers | Déclarées | Manque |
|---|---|---|---|
| Cerveau (`apex/brain`) | torch, numpy, safetensors | torch 2.14.0, numpy 2.5.3, safetensors 0.8.0 | — |
| Modèles (`apex/models`) | torch, transformers, huggingface_hub | transformers 4.57.6, huggingface-hub 0.36.2 | — |
| Assistant, API (`apex/assistant`, `apex/api`) | pydantic, fastapi, sqlalchemy (+ uvicorn, alembic) ; PIL (route Game Studio) | pydantic 2.13.5, fastapi 0.141.1, sqlalchemy 2.1.1 ; pillow 12.3.0 (Codex) | — |
| Profil autonome, accélération GPU (`apex/standalone`) | aucune (bibliothèque standard) | — | — |
| Game Studio (`apex/game_studio`) | PIL, psutil, torch | pillow 12.3.0, psutil 7.2.2 (pile Codex) | — |
| Game Graphics (`apex/game_graphics`) | PIL, numpy, psutil, torch, huggingface_hub, **jsonschema**, **llama_cpp** | les cinq premières | **`jsonschema`**, **`llama-cpp-python`** : déclarés nulle part. Ni `pyproject.toml`, ni la liste Windows ; et `pyproject.toml` n'a pas d'extra Game Studio. Sans `jsonschema`, `game_studio.create` échoue (constaté) |
| Game Engineer (`apex/game_engineer`) | psutil, **jsonschema** | psutil | **`jsonschema`** |

**Programmes et poids hors Python :**

| Élément | Fourni par | État |
|---|---|---|
| Python 3.12.10 (NuGet python.org, SHA-256 figé) | « Préparer APEX » | IMPLEMENTED |
| Visual C++ récent (`msvc-runtime` dans la liste) | « Préparer APEX » | IMPLEMENTED |
| Cerveau APEX v2 (`brain/apex-brain-v2`, 19 Mo, Q5) | l'installateur | IMPLEMENTED |
| Moteur intégré : Qwen2.5-Coder-1.5B-Instruct (révision `2e1fd39…`) | « Installer le modèle » | IMPLEMENTED |
| PyTorch CUDA 2.14.0 (cu130 : 1,99 Go ; cu126 : 2,60 Go) | « Accélération GPU (facultatif) » — `docs/ACCELERATION_GPU.md` | listes figées produites sur un vrai runner Windows (workflow `gpu-locks`, métadonnées seulement, empreintes de l'index). cu128 est absent de l'index : non proposé. UNVERIFIED sur matériel : aucun test sur une carte NVIDIA |
| Pilote NVIDIA | l'utilisateur | règle mesurée par `nvidia-smi` |
| Blender (4.3.2 utilisé par Codex) | l'utilisateur (`docs/game_graphics/`) | à installer ; la sonde le dit |
| Planificateur Qwen2.5-Coder-7B Q5_K_M (environ 5,4 Go d'après Codex) et `llama-cpp-python` CUDA | Codex (`APEX_HOME/.apex/game-studio/coder-model.json`) | BLOCKED (Codex). Toute mission proposée par le Cerveau passe par la planification dynamique, qui refuse le moteur intégré (`dynamic_planner_requires_pinned_7b_until_native_handoff_is_qualified`). Sans ce 7B, le parcours « voiture sportive » s'arrête au clic, avec la raison de Game Studio (mesuré dans l'arbre combiné) |
| `scripts/game_engineer/` (dont `import_vehicle.py`) | Codex | absent du runtime empaqueté : la candidate combinée embarque `scripts/game_graphics/`, pas `scripts/game_engineer/` (constaté). La préparation de l'import UE5 (`python -m apex.game_engineer`) lit ce script et échouerait dans APEX.exe. Les missions Blender de Game Studio n'en dépendent pas |

Effet d'une fusion sur un PC déjà préparé : la liste Windows de la pile Codex change d'empreinte, et le Panel
affiche « préparé pour une autre version d'APEX ». Un clic sur « Préparer APEX » complète la préparation.
L'accélération GPU n'est pas touchée tant que torch ne change pas de version. Si torch change, elle se
désactive d'elle-même, avec sa raison, jusqu'à de nouvelles listes GPU.

## 7. Plan de tests après chaque fusion

**Automatiques, dans l'arbre fusionné :**
- `scripts/integration/rehearse_merge.sh --deps <deps> --pytest "<lots ciblés du §3>"` ;
- la suite Python complète ;
- `ruff` et `mypy` sur les fichiers modifiés ;
- frontend : `npx vitest run`, `npx tsc --noEmit`, `npm run build` ;
- Panel : `dotnet build` et `dotnet test` de `setup/ApexSetup.sln` ;
- runtime : `python -m runtime_build.build`, puis `runtime_build.verify` (contrôle anti-fuite).

**Suite complète dans l'arbre fusionné de l'ordre proposé** (étapes 1 à 8, 44 min) : 3 476 passent, 189 sont
ignorés, 7 échouent. **Aucun de ces 7 échecs ne vient de la fusion** : les 7 échouent aussi sur la branche
`25-game-studio-candidate` seule.

| Tests | Cause | À qui |
|---|---|---|
| `test_apex_preflight_script.py` (3), `test_evalrunner_generated_solution.py::…test_g_…` (1) | effets du worktree jetable : pas de `.venv` à sa racine, et un contrôle du chemin du projet. Ils échouent aussi dans un worktree de ma branche seule, et passent dans le dépôt | aucun (environnement de répétition) |
| `test_game_graphics_contract.py` (2) | `llama_cpp` absent, et psutil absent du sous-processus : dépendances de Game Studio non déclarées (§6) | Codex |
| `test_universal_curriculum_n11.py::…test_no_second_specialty_task_provider_is_registered` | la pile de Codex déclare la spécialité `game-graphics` prise en charge (`is_supported`), au stade `STUB`. Le test de garde historique l'interdit : pas de deuxième spécialité sans moteur réel derrière. Il passe sur ma branche | Codex : soit la spécialité n'est pas déclarée prise en charge, soit le moteur réel est démontré et la garde mise à jour en conséquence |

Le test Gate historique, qui lit le vrai `.apex/environment.json` du dépôt, n'échoue pas dans le worktree, où
ce fichier n'existe pas. Dans le dépôt, il reste le seul échec connu, et son isolation revient à son
propriétaire.

**Sur Windows, PC de l'utilisateur** (`docs/INTEGRATION_CERVEAU_GAME_STUDIO.md` §8 et
`docs/ACCELERATION_GPU.md` §6) :
1. installation de la candidate, « Préparer APEX », installation du modèle ;
2. accélération GPU : installation, sonde, état `active` ;
3. mode 3D : « Crée une voiture sportive » → proposition → clic → suivi → rendus ;
4. partage du GPU pendant une génération ;
5. annulation ;
6. bail `msvcrt` avec Blender ouvert.

## 8. Ce qui reste avant la réunion

**Codex**
- Partage du GPU : appeler `request_gpu` / `return_gpu` autour de `resource_lease`. Lever la garde de #26
  seulement après le test matériel.
- Déclarer `jsonschema` et `llama-cpp-python` : `pyproject.toml`, et liste Windows produite par son
  générateur. Une proposition pour `jsonschema` est jointe à l'Issue #21 : lignes figées, avec les
  empreintes de PyPI. Je ne l'applique pas, la liste est à Codex.
- Empaqueter `scripts/game_engineer/` avec le runtime, comme `scripts/game_graphics/`.
- Relire ma correction Windows de `runtime_build/build.py` (`_extract_commit`, §2).
- Classer `apex/vision` dans `runtime_build/selection.py`.
- Trancher #12, #16, #17 face à la pile.
- Régler la spécialité `game-graphics` (`is_supported` au stade STUB) face au test de garde historique.
- Préparer la candidate Windows.
- Répondre sur (a) la description, (b) l'appel depuis le fil du tour, (c) la qualité `prototype`, ainsi que
  sur `network.read` et sur `apex/sandbox/**` (Issue #21).

**Claude**
- Garder ma branche stable ; aucune fusion, aucune release.
- ~~Committer les listes GPU dès qu'elles sont produites.~~ Fait : cu130 et cu126, workflow `gpu-locks`.
- Rejouer la répétition après chaque mouvement de Codex, et reporter ma branche dans
  `agent/claude/brain-integration` par fusion.
- Qualifier le GPU sur le PC de l'utilisateur.

**Utilisateur**
- ~~Choisir l'option (A) ou (B) du §4~~ : (B), avec l'utilisateur comme intégrateur final.
- ~~Dire où produire les listes figées de PyTorch CUDA~~ : CI Windows manuelle, validée.
- Sur son PC : les essais du §7, avec la candidate.
