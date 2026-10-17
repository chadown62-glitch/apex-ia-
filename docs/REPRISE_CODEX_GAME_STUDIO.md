# Reprise de Codex : activer Game Studio dans APEX.exe (dossier du 17/10/2026, pile à `d15fa02`)

Synthèse écrite par Claude pendant la pause de Codex, pour qu'il corrige ses derniers blocages **sans attendre
de refonte du Cerveau ni du Panel**. **Aucun fichier de Codex n'a été modifié.** Chaque point dit ce qui est
**mesuré**, ce qui reste **à vérifier**, et les **fichiers** concernés.

La version précédente de ce dossier (jsonschema, planificateur, Blender, `scripts/game_engineer`) est traitée
par `c80ce11`/`d15fa02` : les points correspondants sont marqués « fait par Codex ».

## 0. Où en est le parcours réel

- **PC Windows 11 de l'utilisateur** (RTX 4060 Ti), candidate d'avant `d15fa02` :
  - APEX.exe READY ; le Cerveau ApexLM V2 tourne sur la RTX avec PyTorch CUDA ;
  - conversations, puis la demande 3D est reconnue et **proposée** ;
  - le **clic** de l'utilisateur la confie à Game Studio, qui refuse proprement : `jsonschema` absent.
- **Runner Windows** (`windows-candidate`, arbre combiné avec `d15fa02`) : voir §8, résultats des tentatives 6
  et 7.

## 1. Les contrats de Codex que Claude consomme

| Contrat (Codex) | Lu ou appelé par (Claude) | Ce qui est lu |
|---|---|---|
| `apex.game_studio.capabilities.probe()` | `apex/assistant/game_studio.py` (`availability`, `missing_from`), `apex/obs/component_checks.py` | `vehicle_blender.{blender, blender_discovery, workers_present, coder_manifest_present, llama_cpp_present, python_dependencies, status}`, `cuda.{available, dispatch_qualified, reason}`, `specialist.gpu_policy`, `environment.status` |
| `apex.game_studio.dependencies.planner_installation()` | `apex/assistant/studio_setup.py` (`_planner`) | `manifest_present`, `server_module_present`, `download_bytes`, `required_free_bytes`, `license`, `repo`, `revision`, `sha256`, `reason` |
| `python -m apex.game_studio install-planner --accept-download --accept-apache-2-license` | bouton « Installer » du planificateur (`studio_setup._command`), après confirmation (5,4 Go, Apache-2.0) | code 0 et `status` `installed_verified` ou `already_verified` ; code 2 et `{status: "blocked", error}` |
| `setup/packaging/runtime/requirements-planner-cpu-windows.lock` | bouton « Installer » du moteur : `pip install --require-hashes --only-binary=:all: --no-deps -r` dans le Python préparé | proposé seulement si chaque version partagée avec la liste de base est identique (`engine_offer`) ; copié dans `app/runtime` par `build_desktop_release.sh` |
| outils `game_studio.create/status/results/cancel` | moteur d'outils, `ToolContext.extra["game_studio_service"]` | `project_id`, `status`, `steps`, `last_event`, artefacts (empreintes) |
| `ProjectService.resume(project_id)` | bouton « Reprendre la mission » | `ValueError(code)` expliqué (§5) |
| `ProjectService.artifact(project_id, digest)` | aperçus de l'Atelier 3D | images seulement, 4 Mo au plus |
| `apex.game_graphics.resources.resource_lease` | prêt du GPU au lancement (§3) | prend `APEX_HOME/.apex/model-session.lock`, même primitive que le Cerveau |

États lus : `queued`, `running`, `review`, `failed`, `cancelled`, `interrupted` (mission `running` sans
processus vivant).

## 2. Ce que Claude a fait et vérifié

| Quoi | Commit | Preuve |
|---|---|---|
| Liste des composants (planificateur, moteur, Blender, bibliothèques) : état, taille, provenance ; bouton seulement quand un contrat d'installation existe, sinon « En attente du composant Game Studio » et la raison | `d3658db`, `55c8f81` | `tests/test_studio_setup.py` ; vraie CLI de Codex refusant sans consentement |
| Reprise d'une mission interrompue (APEX arrêté pendant la mission) ou échouée, par l'utilisateur seulement | `d3658db` | `tests/test_assistant_game_studio.py` |
| Garde GPU de Codex expliquée avant le clic (« choisissez Processeur ») | `55c8f81` | idem |
| **Prêt du GPU pendant toute la mission** (§3) | `20a7167` | tests avec le vrai `resource_lease` ; `lease_check.py` sous Windows |
| Raison d'arrêt d'une mission affichée (dernier événement de Game Studio) | `20a7167` | `views.test.tsx` |
| Candidate Windows : moteur installé comme par le bouton, import de `llama_cpp` et de `llama_cpp.server.app` | `b7e8b53`, `20a7167` | `windows-candidate`, tentatives 6 et 7 |

## 3. Défaut trouvé et corrigé côté Claude : le bail partagé pendant une mission sur le processeur

- **Mesuré** (arbre combiné, vrai `resource_lease`) : quand le Cerveau est chargé sur CUDA, il tient le bail
  `model-session.lock`. Chaque étape de mission prend ce même bail, **même avec `compute_backend: "cpu"`**.
  Elle échoue alors avec `BlockingIOError: [Errno 11] Resource temporarily unavailable`, et la mission passe
  `failed`. C'est exactement la configuration validée chez l'utilisateur.
- **Correction (fichiers de Claude seulement)** :
  - au clic (lancement ou reprise), l'assistant demande le GPU au Cerveau : `request_gpu(30 s)`. S'il ne le
    libère pas, rien n'est lancé (409 « réessayez ») ;
  - le GPU reste prêté tant que la mission est `queued` ou `running` (lu par `game_studio.status`), puis il est
    rendu. Il l'est aussi aussitôt après un refus, et après trois lectures en échec ;
  - pendant le prêt, le Cerveau répond sur le processeur, et ses mesures le disent ;
  - les prêts sont **comptés par demandeur** (`apex/models/gpu_handoff.py`). Les admissions CUDA ne rouvrent
    qu'au dernier retour, et une demande pendant un prêt est accordée sans attente. Vos futurs appels
    `request_gpu`/`return_gpu` autour de chaque étape restent donc compatibles avec le prêt de l'assistant.
- **Non couvert** : une mission lancée **hors de l'assistant**, par la page ou les routes de Game Studio
  (`POST /api/v1/game-studio/missions`, `/projects`, `/projects/{id}/resume`, `/missions/{id}/resume`). Voir
  §4.4.

## 4. Ce qui reste à Codex, dans l'ordre du parcours

### 4.1 Moteur du planificateur (CPU) sous Windows

- **Fait par Codex** : `requirements-planner-cpu-windows.{in,lock}`, roue officielle GitHub v0.3.36 (MIT,
  empreinte vérifiée), serveur et dépendances du serveur dans la liste.
- **Mesuré par Claude** : installation et import sur le runner, voir §8.
- **Reste** : exécution réelle du serveur avec les poids, sur le PC de l'utilisateur (§7). Ne pas déclarer le
  moteur qualifié avant.

### 4.2 Poids du planificateur (5,4 Go)

- **Fait par Codex** : `install-planner`, avec double consentement, vérification du disque, de la licence et
  des empreintes.
- **Côté Claude** : le bouton n'apparaît que si `server_module_present` est vrai ; confirmation avec la taille
  et la licence.
- **Reste** : premier téléchargement réel chez l'utilisateur. Vérifier ensuite que `coder-model.json` est
  écrit dans `APEX_HOME/.apex/game-studio/` et que la sonde passe `coder_manifest_present: true`.

### 4.3 Première mission réelle sur le processeur

- **Prérequis** : Blender 4.3.2 trouvé (`APEX_BLENDER_EXECUTABLE`, PATH ou Program Files : fait par Codex),
  calcul des missions sur « Processeur ».
- **À vérifier** sur le PC de l'utilisateur, dans l'ordre : serveur `llama_cpp.server` démarré par
  `CoderSession`, plan, base Blender, juge, fichiers livrés, aperçus.
- **Risque à vérifier, non mesuré** : `check_external_workloads` refuse une étape si un `llama_cpp.server`
  tourne encore. Un serveur d'une étape précédente encore vivant ferait refuser les suivantes
  (`external_model_workload_active`).

### 4.4 Missions sur GPU : lever la garde

- **Garde actuelle** (fichiers de Codex) :
  - `apex/game_studio/service.py:198-199` ;
  - `apex/game_studio/project.py:167-168` et `228-229` ;
  - `apex/game_studio/capabilities.py:162` (`dispatch_qualified: False`).
- **À faire avant de la lever** :
  1. un `llama-cpp-python` **CUDA** pour Windows : source, licence, empreintes. La roue GitHub actuelle est
     CPU (aucune DLL CUDA). Il faut aussi passer `n_gpu_layers` selon `compute_backend` ;
  2. pour les missions lancées hors de l'assistant (§3), appeler `request_gpu(timeout_s, requester=...)`
     avant la première étape et `return_gpu(requester=...)` dans un `finally`. Avec un accusé
     `released: false`, la mission reste en attente : jamais de déchargement direct, jamais de processus tué ;
  3. un test sur la RTX 4060 Ti de l'utilisateur. La mémoire CUDA avant et après se lit dans l'accusé de
     `request_gpu` (`cuda.before`, `cuda.after`).
- **Décision à prendre par Codex** : les étapes sur le processeur ont-elles besoin du bail partagé ? Si elles
  prenaient seulement le verrou de leur dossier, le Cerveau garderait le GPU pendant une mission sur le
  processeur (meilleure expérience). Le prêt de l'assistant reste sans effet nuisible dans les deux cas.

### 4.5 Un code pour le bail refusé

- Aujourd'hui, la mission échoue avec le texte brut de l'exception (`BlockingIOError: …` sous Linux ; le texte
  Windows est dans le run de la tentative 7).
- **Proposé** : un code stable, par exemple `shared_resource_busy`. L'Atelier 3D explique déjà le texte brut et
  propose de reprendre la mission.

### 4.6 Hors parcours

- Benchmark du modèle à refaire : les poids et les preuves non poussés ont été perdus à la restauration.
- #12, #16, #17 : décision avec l'intégrateur humain, sans fusion aveugle après #19 → #26.
- `apex/vision` (#7) à classer dans `runtime_build/selection.py`.
- Les 11 échecs sandbox (Docker/AppContainer) : Codex propose que Claude en porte un lot séparé. Il n'est pas
  commencé, et il ne bloque pas le parcours 3D.

## 5. Refus expliqués à l'utilisateur

Codes de Game Studio traduits dans l'assistant et l'Atelier 3D (`REFUSALS`, `GAME_STUDIO_REFUSALS`) :

- **matériel et ressources** : `cuda_unavailable_choose_cpu_explicitly`,
  `cuda_game_studio_requires_qualified_brain_quiescence_handoff`, `shared_resource_profile_required`,
  `core_active_handoff_required`, `external_graphics_workload_active`, `external_model_workload_active` ;
- **composants absents** : `local_planner_model_missing`, `pinned_7b_model_missing_no_download_performed`,
  `blender_worker_or_local_coder_missing`, `dynamic_planner_requires_pinned_7b_until_native_handoff_is_qualified` ;
- **missions** : `game_studio_project_missing`, `game_studio_project_process_interrupted`,
  `game_studio_process_interrupted` ;
- **reprise** : `only_interrupted_failed_or_incomplete_projects_can_resume`, `project_already_running`,
  `project_already_delivered_for_review`, `cumulative_time_budget_exhausted`,
  `legacy_budget_unknown_new_mission_required`.

Un nouveau code non traduit s'affiche tel quel, jamais remplacé par une explication inventée.

## 6. Dépendances

| Composant | État | Qui l'installe |
|---|---|---|
| `jsonschema` (+4 dépendances) | dans la liste de base (fait par Codex) | « Préparer APEX » |
| `llama-cpp-python` 0.3.36 CPU et son serveur | liste facultative figée (Codex) | bouton « Installer » (Claude), sur confirmation |
| Qwen2.5-Coder-7B Q5_K_M (5 444 831 232 octets) | `install-planner` (Codex) | bouton « Installer » (Claude), sur confirmation |
| Blender 4.3.2 | découverte (Codex) | l'utilisateur, installateur officiel |
| `llama-cpp-python` CUDA | **absent** | à décider (Codex, §4.4) |

## 7. Tests nécessaires

- **Côté Codex** :
  - `request_gpu`/`return_gpu` autour des étapes, y compris un accusé `released: false` ;
  - le code du bail refusé ;
  - le serveur du planificateur arrêté entre deux étapes ;
  - la levée de la garde, derrière un test matériel.
- **Côté Claude** (en place) :
  - `tests/test_gpu_handoff.py` : prêts comptés, bail exclusif, vrai bail de Game Studio ;
  - `tests/test_assistant_game_studio.py` : proposition, clic, suivi, fichiers, refus, annulation, reprise,
    aperçus, prêt du GPU, Cerveau occupé ;
  - `tests/test_studio_setup.py` : composants et contrats d'installation ;
  - `frontend/src/assistant/views.test.tsx`.
  Ces tests tournent sur des **doubles** des services de Game Studio : aucun ne prouve une génération Blender
  réelle.

## 8. Validations sur Windows

- **Runner** (`windows-candidate`, automatique, sans GPU ni Blender) :
  - tentative 6 (`b7e8b53` + `d15fa02`,
    [run 39076155603](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39076155603)) :
    - `jsonschema`, `PIL`, `psutil`, `apex.game_studio.delegation` importés par le Python préparé ;
    - moteur installé par l'API du bouton (pip, empreintes vérifiées : `llama-cpp-python` 0.3.36,
      `diskcache`, `pydantic-settings`, `sse-starlette`, `starlette-context`), puis importé :
      `gpu_offload False` (build CPU), module `llama_cpp.server` présent ;
    - le bouton du planificateur passe de « En attente du composant Game Studio : installez d'abord le
      moteur » à « installable » ;
    - proposition, clic, puis refus `local_planner_model_missing` : attendu, les poids ne sont pas
      téléchargés sur le runner ;
    - assistant PASS, accélération GPU sans GPU PASS, désinstallation sans perte de la base ;
    - installateur `APEX-Setup-x64-v0.6.0-candidate-e44da1f76cb6.exe`, SHA-256 `366ffc6a6f402cce…`. Il n'a
      pas le prêt du GPU : préférer celui de la tentative 7 ;
  - tentative 7 (`20a7167`) : partage du GPU avec les verrous Windows réels, import du serveur du
    planificateur.
- **PC de l'utilisateur** (manuel) : la première création réelle, étapes dans
  `docs/QUALIFICATION_APEX_EXE.md`. C'est la seule preuve d'une génération Blender, du planificateur et des
  fichiers livrés.

## Vérifier après chaque correction

1. Modifier `setup/packaging/windows/ci/candidate-request.json` (champ `attempt`) sur la branche de Claude, ou
   lancer `windows-candidate` manuellement une fois le workflow sur la branche par défaut.
2. Lire les annotations du run : imports, composants (`DEPENDENCY`), moteur, partage du GPU, proposition, clic,
   refus ou mission.
3. Le verdict PASS exige des fichiers produits par une mission : seulement sur le PC de l'utilisateur.
