# Reprise de Codex : activer Game Studio dans APEX.exe (dossier du 18/10/2026, pile à `550b347`, #31)

Synthèse écrite par Claude pendant la pause de Codex (limite d'utilisation atteinte le 18/10 pendant la
qualification Windows de #31), pour qu'il corrige ses derniers blocages **sans attendre de refonte du Cerveau ni
du Panel**. **Aucun fichier de Codex n'a été modifié.** Chaque point dit ce qui est **mesuré**, ce qui reste
**à vérifier**, et les **fichiers** concernés.

Les versions précédentes de ce dossier sont traitées par `c80ce11`/`d15fa02` (jsonschema, planificateur,
Blender), puis par #29 (`8fc1df9`) et #31 (`550b347`) : points marqués « fait par Codex ».

## 0. Où en est le parcours réel

- **PC Windows 11 de l'utilisateur** (RTX 4060 Ti), candidate d'avant `d15fa02` :
  - APEX.exe READY ; le Cerveau ApexLM V2 tourne sur la RTX avec PyTorch CUDA ;
  - conversations, puis la demande 3D est reconnue et **proposée** ;
  - le **clic** de l'utilisateur la confie à Game Studio, qui refuse proprement : `jsonschema` absent.
- **Runner Windows Server 2022 de Codex** (`game-studio-windows`, runtime empaqueté et API, **sans** l'installateur
  ni le Panel) :
  - runs 39111881405, 39112594053, 39113206265 et 39113464692 : échecs réels archivés dans
    `docs/game_studio/qwen-first/evidence/archives.json`. Dans l'ordre : Blender annulé par un budget Windows,
    lecture du budget refusée, contention sans `winerror`, puis voiture réussie mais plan de reprise sans
    assemblage. Les runs 1, 2 et 4 ont produit un vrai plan Qwen accepté ;
  - [run 39114782698](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39114782698) à `550b347` :
    **succès**. La qualification `--require-inference` sort avec le code 0. Dans le code de `550b347`
    (`apex/game_studio/qualification.py`), ce code exige :
    - mission `review`, planificateur épinglé (SHA), `cpu_explicit`, serveur arrêté et vérifié ;
    - une vraie réponse Qwen validée ;
    - un seul véhicule, précontrôle Engineer `pass_preflight`, mission enfant `review` ;
    - `.blend`, `.glb` et 4 vues avec le verdict `pass`, téléchargés par l'API avec leur empreinte ;
    - puis la sonde d'annulation et de reprise : même projet, nouvelle tentative, budget cumulé, plan de deux
      fauteuils, serveur arrêté.
    Claude n'a pas pu lire l'archive (stockage refusé par le proxy) : le verdict vient du code et du code de sortie.
- **Runner Windows de Claude** (`windows-candidate`, arbre combiné, **installateur et Panel**) : voir §8,
  tentatives 6 à 8.

### 0.1 Ce que Codex a poussé, et ce qui est peut-être resté dans sa session

- **Poussé** (vérifié sur GitHub) :
  - #31 en brouillon, 7 commits jusqu'à `550b347` (18/10 09:17 UTC), empilée sur #29 `8fc1df9` ;
  - preuves des runs 1 à 4 (JSON et archives `windows-run*-failure.zip`) ;
  - `docs/game_studio/qwen-first/INTEGRATION.md` et `probe_governor_v2.py` ;
  - Issue #30 et deux commentaires ; dans l'Issue #21, le dernier commentaire date de 09:16 UTC.
- **Absent de GitHub** (à reprendre par Codex, sans le réinventer) :
  - le **résultat** du run 39114782698 (succès, 09:34 UTC). La description de #31 dit encore
    « Requalification complète en cours », et aucun commentaire ne le rapporte ;
  - ses **preuves** : aucune entrée `run5-*` dans `evidence/`. Elles ne sont que dans l'artefact
    `game-studio-windows-39114782698` (41 069 636 octets, 225 fichiers, SHA-256 `ed798d49f72df90a…`).
    **Il expire le 01/11/2026** : il faut l'archiver avant ;
  - le **rapport final**, annoncé « à compléter après la dernière qualification » dans #31 ;
  - tout travail local après `550b347` : invisible d'ici, ni commit ni branche plus récents.

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
| `apex.game_graphics.resources.resource_lease` | prêt du GPU au lancement, piles sans adaptateur (§3) ; `lease_check.py` | prend `APEX_HOME/.apex/model-session.lock`, même primitive que le Cerveau ; refus `SharedResourceBusy` (`shared_resource_busy`) depuis #31 |
| `apex.game_studio.gpu.resource_lease(path, compute_backend, cancel_event, timeout_s)` (#29, #31) | `studio_hands_off_gpu()` (l'assistant ne prête rien), `lease_check.py` | processeur : `game-studio-workload.lock` + verrou de l'étape, attente bornée à 30 s, **sans** `model-session.lock` ni prêt ; GPU : protocole 2 du gouverneur (`lenders`, retour atomique) |
| `apex.models.gpu_handoff` (Claude) | adaptateur de Codex | protocole 2, vérifié par Codex sur le fichier de `cf76ff1` (SHA-256 `78add5edbaffdd6a…`), inchangé depuis |

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
| Protocole 2 du gouverneur ; aucun prêt de l'assistant quand votre adaptateur existe | `98c7ebb` | votre adaptateur et ce gouverneur dans l'arbre combiné |
| Raisons d'arrêt de Game Studio traduites : la classe (`RuntimeError: `) est retirée avant la recherche du code ; `shared_resource_busy` reconnu ; codes du planificateur, des budgets, de l'annulation et du prêt ajoutés | `8fc89a3` | `views.test.tsx` |
| `lease_check.py` : une étape sur le processeur passe par votre adaptateur pendant que le Cerveau tient CUDA, sans prêt | `8fc89a3` | arbre combiné avec `550b347` (7/7) ; Windows, tentative 8 |
| Candidate : poids du planificateur par le bouton, Blender 4.3.2 portable épinglé sur le runner, `.blend`/`.glb` téléchargés et vérifiés | `8fc89a3` | tentative 8 (§8) |

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
    qu'au dernier retour, et une demande pendant un prêt est accordée sans attente.
- **Ajusté le 18/10 après la lecture de #29 par Codex** :
  - **mesuré** dans l'arbre combiné avec `8fc1df9` : le prêt de l'assistant pour toute la mission fait refuser
    chaque étape par votre adaptateur (`brain_handoff_busy`, il exige `lent_to` vide) ;
  - désormais, l'assistant ne prête **rien** quand `apex.game_studio.gpu` est présent : votre adaptateur fait
    la mise au repos autour de chaque étape. Il ne prête que pour une pile sans adaptateur (`d15fa02`, celle de
    la candidate de la tentative 7). L'Atelier 3D lit qui prête (`availability.gpu_loan` :
    `game_studio` ou `assistant`) ;
  - **protocole 2 du gouverneur**, comme vous l'avez demandé : `protocol_version: 2`, `lenders`
    (`{demandeur: nombre}`) dans l'état, l'accusé et le retour. Un retour d'un inconnu rend `returned: false`
    et ne change rien. Un retour connu décrémente exactement et rend `returned: true`, avec `resumed: true`
    seulement au dernier prêt. Seule une demande acceptée est comptée. `lent_to` reste un texte d'affichage
    (le demandeur seul quand il n'y en a qu'un : vos contrôles actuels passent) ;
  - **vérifié** avec votre vrai adaptateur et ce vrai gouverneur (Cerveau sur un « CUDA » simulé) : accusé
    accepté, bail obtenu, Cerveau sur le processeur pendant l'étape, retour accepté, GPU repris. Vos 12 tests
    `test_game_studio_gpu.py` et les miens passent dans le même arbre.
- **Non couvert sans votre adaptateur** : une mission lancée **hors de l'assistant**, par la page ou les routes
  de Game Studio (`POST /api/v1/game-studio/missions`, `/projects`, `/projects/{id}/resume`,
  `/missions/{id}/resume`). Votre adaptateur de #29 les couvre.
- **Depuis #31 (`550b347`)** : une étape **sur le processeur** prend `game-studio-workload.lock` et le verrou de
  son dossier, jamais `model-session.lock`, et n'emprunte rien. Le Cerveau garde donc la RTX pendant une
  mission sur le processeur : c'est la meilleure issue pour la configuration de l'utilisateur. **Vérifié** dans
  l'arbre combiné (Claude `8fc89a3` + #19 à #31 + #5, #11, #13) :
  - `lease_check.py`, 7 contrôles PASS : l'étape processeur est obtenue, Cerveau sur un « CUDA » simulé, aucun
    prêt (`lenders: {}`) ;
  - 274 tests passent, 1 ignoré : `test_gpu_handoff`, `test_assistant_game_studio`, `test_studio_setup`, et vos
    `test_game_studio_*` (gpu, lifecycle, planning, qualification, api, delegation, dependencies…) et
    `test_game_graphics_models`, avec `llama-cpp-python` 0.3.36 compilé pour Linux.
  Le prêt de l'assistant ne sert plus qu'aux piles sans adaptateur.

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
- **Fait par Codex** (#31, runtime empaqueté et API sur Windows Server, run 39114782698) : serveur du
  planificateur possédé, démarré puis arrêté et vérifié ; plan Qwen réel ; Blender ; juges ; correction ; fichiers
  et vues téléchargés ; annulation pendant le chargement puis reprise.
- **Fait par Claude** (tentatives 8 et 9, §8) : le même parcours par l'installateur, le Panel et l'assistant, sur
  le runner, puis par l'interface avec de vrais clics (Edge), annulation et reprise comprises. Mission livrée,
  `.blend` et `.glb` vérifiés.
- **Reste** : le PC de l'utilisateur (Windows 11, la fenêtre d'APEX.exe, Cerveau sur la RTX pendant la mission).
- **Risque traité par Codex** (#31) : le port 8082 est protégé par la sérialisation des travaux, et seul le
  serveur possédé est arrêté.

### 4.4 Missions sur GPU : lever la garde

- **Garde actuelle** (fichiers de Codex) :
  - `apex/game_studio/service.py:198-199` ;
  - `apex/game_studio/project.py:167-168` et `228-229` ;
  - `apex/game_studio/capabilities.py:162` (`dispatch_qualified: False`).
- **À faire avant de la lever** :
  1. un `llama-cpp-python` **CUDA** pour Windows : source, licence, empreintes. La roue GitHub actuelle est
     CPU (aucune DLL CUDA). Il faut aussi passer `n_gpu_layers` selon `compute_backend` ;
  2. la mise au repos autour de chaque étape : **faite par votre adaptateur** (`apex/game_studio/gpu.py`, #29,
     protocole 2 depuis #31), vérifiée avec le gouverneur réel (§3) ;
  3. un test sur la RTX 4060 Ti de l'utilisateur. La mémoire CUDA avant et après se lit dans l'accusé de
     `request_gpu` (`cuda.before`, `cuda.after`).
- **Fait par Codex** (#31) : le processeur prend un verrou de travaux séparé du bail CUDA (`n_gpu_layers=0`,
  Blender sur le processeur). Le choix automatique ou CUDA ne bascule jamais en silence vers le processeur.

### 4.5 Un code pour le bail refusé

- **Fait par Codex** (#31) : `SharedResourceBusy`, une sous-classe de `BlockingIOError` avec le message
  `shared_resource_busy`, sous Linux comme sous Windows. Un refus d'accès à un fichier reste distinct.
- **Côté Claude** (`8fc89a3`) : l'Atelier 3D reconnaît ce code et propose de reprendre la mission. Il explique
  aussi les anciens textes bruts des piles plus anciennes.

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
  `legacy_budget_unknown_new_mission_required` ;
- **arrêt d'une mission** (raison du dernier événement, `RuntimeError: code`) : `shared_resource_busy`,
  `user_cancelled`, `user_cancelled_waiting_for_resources`, `project_time_budget_exhausted`,
  `planner_attempt_budget_exhausted`, `multi_asset_assembly_required`, `brain_handoff_busy`.

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
  - tentative 7 (`20a7167` + `d15fa02`,
    [run 39077478748](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39077478748)) :
    - mêmes résultats que la tentative 6, plus `llama_cpp.server.app` importé (fastapi, sse-starlette,
      starlette-context, pydantic-settings) ;
    - partage du GPU, `lease_check.py`, 6 contrôles PASS. Cerveau sur un « CUDA » simulé, bail et verrous Windows
      réels : votre vrai `resource_lease` est refusé tant que le Cerveau tient le bail
      (`PermissionError: [Errno 13] Permission denied`, texte de `msvcrt.locking`), puis obtenu après
      `request_gpu`. Le Cerveau passe sur le processeur pendant le prêt et reprend le GPU après ;
    - installateur `APEX-Setup-x64-v0.6.0-candidate-991388a4860d.exe`, SHA-256 `e0ced6225aca7628…` : celui à
      essayer sur le PC de l'utilisateur.
  - tentative 8 (`8fc89a3` + `550b347`, #29 et #31,
    [run 39140125328](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39140125328)) : **job vert, parcours
    3D PASS dans l'APEX.exe installé** :
    - moteur, puis poids du planificateur, installés par l'API des boutons et vérifiés par votre
      `install-planner` (poids téléchargés sur ce runner seulement) ;
    - Blender 4.3.2 portable officiel, même empreinte que votre qualification, par `APEX_BLENDER_EXECUTABLE` ;
    - proposition par le Cerveau, lancement par l'appel du bouton, mission `running` 13 min, puis `review`,
      livraison `technical_review` ;
    - 13 fichiers annoncés par `game_studio.results`. `asset.blend` (3 767 004 octets) et `asset.glb`
      (1 108 632 octets) sont téléchargés par vos routes `…/artifacts/{digest}`, identiques à leur empreinte ;
      4 aperçus ;
    - `lease_check.py`, 7 contrôles PASS avec les verrous Windows réels, dont l'étape processeur par votre
      adaptateur pendant que le Cerveau tient CUDA (simulé) : obtenue, sans prêt ;
    - assistant PASS, accélération GPU sans GPU PASS, base conservée à la désinstallation ;
    - installateur `APEX-Setup-x64-v0.6.0-candidate-e8d79fcb7aeb.exe`, 51 981 009 octets, SHA-256
      `bbdfb6ccf849fb8a6a379c3c541801729e0f544e01f901d3df39213a001bd6fe` : celui à essayer sur le PC de
      l'utilisateur. Preuves : artefact `windows-candidate-evidence` (`studio-candidate.json`, aperçus,
      `studio-files/`).
    - **Pas couvert ici** : l'annulation et la reprise dans l'APEX installé (couvertes par votre sonde dans le
      runtime empaqueté), Windows 11, les vrais clics dans la fenêtre, la RTX.
  - tentative 9 (`7d2a401` + `550b347`,
    [run 39150876510](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39150876510)) : **job vert, le
    parcours 3D par l'interface PASS, puis par l'API PASS** :
    - `studio_ui_e2e.mjs` (Playwright, Edge, la page qu'ouvre le Panel) : « Processeur » enregistré, proposition,
      moteur puis poids installés par leurs boutons (confirmations acceptées), « Lancer la mission » (18:07:11),
      « Annuler » (annulée à 18:07:42), « Reprendre » (en cours à 18:07:48), « terminée, à revoir » à 18:22:01 ;
    - 4 aperçus affichés ; `asset.blend` et `asset.glb` téléchargés par les boutons de la version retenue de votre
      page de projet, identiques à leur empreinte ;
    - seconde mission par l'API : `review`, 13 fichiers, `.blend` et `.glb` vérifiés ;
    - `lease_check.py` 7/7, assistant PASS, base conservée ;
    - installateur `APEX-Setup-x64-v0.6.0-candidate-0c3f90d92bd3.exe`, 51 982 779 octets, SHA-256
      `f18509220b303f08f9aeceef690177a27e3df3fcd236587b013eafab95cada59`.
    - **Pas couvert ici** : Windows 11, la fenêtre d'APEX.exe elle-même (WebView2), la RTX.
- **PC de l'utilisateur** (manuel) : la première création réelle chez lui, étapes dans
  `docs/QUALIFICATION_APEX_EXE.md`. C'est la seule preuve sur son matériel.

## Vérifier après chaque correction

1. Modifier `setup/packaging/windows/ci/candidate-request.json` (champ `attempt`) sur la branche de Claude, ou
   lancer `windows-candidate` manuellement une fois le workflow sur la branche par défaut. `studio_planner` et
   `studio_blender` fournissent les poids et Blender au runner jetable.
2. Lire les annotations du run : imports, composants (`DEPENDENCY`), moteur, poids, partage du GPU, proposition,
   clic, mission, téléchargements (`CHECK download`).
3. Le verdict PASS exige une mission livrée (`review`), `.blend` et `.glb` vérifiés et un aperçu. Avec tous les
   prérequis fournis, une mission non livrée est FAIL.
