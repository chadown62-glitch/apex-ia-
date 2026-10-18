# Intégration réelle dans APEX.exe — résultats et blocages (mis à jour le 18/10/2026)

**Verdict : aucune release « READY ».**
- **Première création 3D complète dans l'APEX.exe installé**, sur un runner Windows Server sans GPU (§4,
  tentative 8, [run 39140125328](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39140125328)) :
  - proposition par le Cerveau, lancement par l'appel du bouton, plan du Qwen 7B sur le processeur ;
  - voiture construite par Blender 4.3.2, précontrôle Engineer réussi ;
  - `.blend` et `.glb` téléchargés et identiques à leur empreinte, 4 aperçus.
- Ce n'est **pas** le PC de l'utilisateur : ni Windows 11, ni de vrais clics dans la fenêtre, ni la RTX. Game
  Studio refuse toujours le GPU. La release attend cet essai et la décision de l'utilisateur.
- Historique : le 16/10, le parcours s'arrêtait au refus `jsonschema`. Codex a fourni `jsonschema`, le moteur
  et le planificateur (#26), puis le partage du GPU (#29) et le verrou de travaux séparé (#31). En chemin, des
  défauts ont été trouvés et corrigés des deux côtés ; le principal côté Claude était dans le Panel, qui
  exécutait le code du dossier courant.
- Rien n'a été fusionné dans `apex/integration`. La PR [#27](https://github.com/chadown62-glitch/apex-ia-/pull/27)
  est un **brouillon** ; l'utilisateur est l'intégrateur final.
- Aucune release n'a été publiée.

Chaque fait ci-dessous est rangé dans **une seule** catégorie. Un test Python réussi n'est jamais compté comme
une preuve de l'exécutable Windows.

## 1. Code — écrit et testé (Python, .NET, frontend)

| Domaine | Ce qui existe | Tests |
|---|---|---|
| Cerveau APEX v2 | ApexLM 15,7 M paramètres, Q5 ; calculatrice ; code vérifié par exécution et contre-vérifié par Python ; confiance mesurée et délégation | `tests/test_brain_*` |
| Assistant | conversations, projets, modes (dont 3D), paramètres, choix du moteur, outils Game Studio chargés si présent | `tests/test_assistant*` |
| Lancement des missions | **acte humain seulement** : le Cerveau propose, le clic lance, annulation et reprise par l'utilisateur | `tests/test_assistant_game_studio.py` |
| États de préparation | 8 lignes mesurées dans `GET /api/v1/status`, le Panel et les Paramètres, hors verdict global | `tests/test_application_components.py`, `StatusEngineTests.cs` |
| Partage du GPU | bail `model-session.lock`, `request_gpu` / `return_gpu` côté Cerveau | `tests/test_gpu_handoff.py` |
| Accélération GPU | détecter, proposer, installer au clic, tester, activer, se désactiver avec sa raison | `tests/test_standalone_gpu.py` (47) |
| Listes figées CUDA | générateur `lock_gpu.py` (léger : métadonnées par HTTP Range ; complet : roue entière) | idem |
| Parcours 3D de bout en bout | `setup/packaging/windows/ci/studio_e2e.py` contre un APEX installé | exécuté (§2, §4) |
| Qualification Windows | workflow `windows-candidate` : arbre combiné reconstruit dans le runner, rien n'est poussé | exécuté (§3, §4) |

## 2. Runtime réel — exécuté par le vrai backend APEX (ici : Linux, processeur)

**Arbre combiné** : ma branche, puis #19, #22, #24, #26, #5, #11, #13. Backend `python -m apex.standalone serve`,
avec le vrai Cerveau et les vrais services de Game Studio (Codex).

Parcours 3D, demande « Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export
Blender. » :

1. Tour en mode 3D servi par le **Cerveau** (`apex-brain:v2:apex-q5`, processeur, 129,8 tokens/s, 6,0 s).
2. Proposition `proposed` : « à lancer par vous : une mission n'est jamais lancée sans votre clic ».
3. Clic (`POST …/game-studio/launch`), puis `game_studio.create` (calcul : processeur).
4. Refus de Game Studio : `local_planner_model_missing` — « le modèle planificateur local de Game Studio
   (Qwen2.5-Coder-7B) n'est pas installé ».
5. Verdict **BLOCKED**, rapporté tel quel. **La création 3D n'est pas validée.**

États mesurés au même moment :

| Composant | État |
|---|---|
| Cerveau APEX | prêt |
| Moteur intégré | absent (poids non téléchargeables d'ici) |
| GPU CUDA | absent (aucun pilote NVIDIA) |
| Game Studio | absent, avec la raison |
| Planificateur | absent, avec la raison |
| Blender | prêt (4.0.2, exécuté par la sonde) |
| Game Graphics | absent |
| Game Engineer | dégradé (UE5 bloqué) |

## 3. Construit en .exe

| Build | Où | Résultat |
|---|---|---|
| Ma branche, `5a694ec` | runner Windows de GitHub, `build_desktop_release.sh` | **construit** ([run 38979850221](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38979850221)) |
| Ma branche, `f941d90` | idem | **construit** ([run 38980883566](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38980883566)) |
| Arbre combiné + pile Codex | runner Windows, workflow `windows-candidate` | **construit** quatre fois ; le dernier, `cb4bf22` ([run 38987535861](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38987535861)) : 51 947 368 octets, SHA-256 `2f16488024b44b21…` |
| Ma branche, `cb4bf22` | runner Windows | **construit** ([run 38987535866](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38987535866)) |
| Arbre combiné `e9898bf` (antérieur) | ici, Inno Setup sous Wine | construit (52 Mo) ; contrôle anti-fuite sans violation |

Chaîne de fabrication auditée :

| Maillon | Fait par | État |
|---|---|---|
| Source | commit exact, `git archive` (correction Windows `eb2713b`) | OK |
| Runtime Python | NuGet python.org 3.12.10 (empreinte figée) ; bibliothèques de `requirements-windows.lock` (empreintes) | OK pour ma branche. Dans l'arbre combiné, il manque `jsonschema` et `llama-cpp-python` (Codex) |
| Backend | `runtime_build` : sélection des paquets, contrôle anti-fuite | OK. `scripts/game_engineer/` non empaqueté (Codex) |
| Frontend | `npm ci` + `npm run build` | OK |
| Panel | `dotnet publish` win-x64, autonome | OK |
| Accélération GPU | listes figées cu130 et cu126 embarquées | OK (cu128 absent de l'index : non proposé) |
| Installateur | Inno Setup 6.4.3 (empreinte figée) | OK, **non signé** |

## 4. Démarré réellement sous Windows

Il s'agit de **Windows Server sur un runner GitHub** : processeur, sans GPU ni Blender. Ce n'est ni Windows 11
client, ni le PC de l'utilisateur. Dans ces runs, le Panel est lancé depuis le checkout du dépôt. Chaque run lit la
ligne « programme : … » du journal de démarrage d'APEX et échoue si ce n'est pas l'application installée.

| Build | Parcours | Résultat |
|---|---|---|
| Ma branche `cb4bf22` ([run 38987535866](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38987535866)) | installation silencieuse → « Préparer APEX » → installation du modèle → démarrage → vrais tours de l'assistant → arrêt → redémarrage → persistance → désinstallation | **PASS**. Programme : l'application installée. Démarrage READY deux fois. Tours A1, A2, A4, A6 PASS. Persistance V1 à V3 PASS (messages, réglages, projet). Base conservée à la désinstallation |
| Candidate combinée `cb4bf22` + pile Codex ([run 38987535861](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38987535861)) | installation → « Préparer APEX » → modèle → démarrage → tours de l'assistant → **parcours 3D réel** → arrêt → désinstallation | **job en échec, parcours 3D BLOCKED** (détail ci-dessous). Programme : l'application installée. Démarrage READY. Assistant PASS, dont A3 (`finance.loan` exact : 1 159,92 €). Base conservée |
| Candidate combinée `20a7167` + pile Codex à `d15fa02` ([run 39077478748](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39077478748)) | même parcours, plus le moteur du planificateur installé par l'API du bouton et le partage du GPU | **job en échec, parcours 3D BLOCKED** au refus attendu `local_planner_model_missing` (poids non téléchargés). `jsonschema` importé. Moteur `llama-cpp-python` 0.3.36 installé (empreintes vérifiées) et importé, avec `llama_cpp.server.app` ; `gpu_offload False` (build CPU). Partage du GPU : 6 contrôles PASS avec les verrous Windows réels. Assistant PASS, base conservée. Codes : modèle 0, GPU 0, assistant 0, parcours 3D 3, partage du GPU 0 |
| Candidate combinée `8fc89a3` + pile Codex à `550b347` (#29, #31) ([run 39140125328](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39140125328), tentative 8) | même parcours, plus les poids du planificateur installés par le bouton (5,4 Go, sur le runner seulement) et Blender 4.3.2 portable officiel (empreinte épinglée) par `APEX_BLENDER_EXECUTABLE` | **job vert, parcours 3D PASS**. Moteur et poids installés et vérifiés. Proposition, lancement, mission `running` 13 min, puis `review` en livraison `technical_review`. 13 fichiers, dont `asset.blend` (3 767 004 octets) et `asset.glb` (1 108 632 octets) téléchargés par l'API de Game Studio, empreintes vérifiées ; 4 aperçus. Partage du GPU : 7 contrôles PASS, dont l'étape processeur obtenue pendant que le Cerveau tient CUDA (simulé), sans prêt. Assistant PASS, base conservée. Codes : modèle 0, GPU 0, assistant 0, parcours 3D 0, partage du GPU 0. Installateur `APEX-Setup-x64-v0.6.0-candidate-e8d79fcb7aeb.exe`, SHA-256 `bbdfb6ccf849fb8a…` |
| Ma branche `5a694ec`, `f941d90` (avant le correctif `-P`) | même parcours que la première ligne | PASS, mais **le code Python d'APEX exécuté venait très probablement du checkout**, au même commit, et non des fichiers installés |

**Parcours 3D réel de la candidate sous Windows (run 38987535861)** :
1. demande « Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export Blender. » ;
2. APEX **propose** la mission : « à lancer par vous : une mission n'est jamais lancée sans votre clic » ;
3. **clic humain** (`authorization : confirmée par l'utilisateur`) ;
4. Game Studio **refuse** : « une bibliothèque nécessaire à Game Studio manque sur ce PC
   (ModuleNotFoundError: No module named 'jsonschema') ».

Verdict **BLOCKED**, affiché tel quel. C'est le premier blocage ; le planificateur 7B et Blender viendraient
ensuite.

Lignes d'état mesurées par cet APEX installé :
- Cerveau APEX et moteur intégré : prêts ;
- sandbox : `WindowsAppContainerSandbox` prête ;
- GPU : absent (pas de NVIDIA) ;
- Blender : introuvable ;
- Game Studio et Game Graphics : « manque : Blender…, le planificateur 7B…, bibliothèques Python… » ;
- planificateur : absent, avec la raison ;
- Game Engineer : « bibliothèque jsonschema absente ».

Le paquet de qualification qui a passé ce run est en artefact (`apex-qualification-installer`, 30 jours) : SHA-256
`2f16488024b44b21…`, 51 947 368 octets. Ce n'est pas une release.

**Trois défauts trouvés par ces runs, et leurs corrections :**
1. **Le Panel exécutait le code du dossier courant** (mon code, corrigé dans `24edcbc`).
   - Avec `python -m`, Python met le dossier courant en tête de `sys.path`. Lancé depuis un dossier qui contient
     `apex\` (le checkout de la CI, un clone du dépôt), APEX exécutait ce code-là. La candidate disait donc Game
     Studio absent alors qu'elle le contenait.
   - Les raccourcis de l'installateur (`WorkingDir: "{app}"`) n'étaient pas touchés.
   - Correction : chaque Python lancé par le Panel reçoit `-P`. Un test couvre les six lancements.
2. **Le script de test plantait sur une console cp1252** (`cb4bf22`). Le « − » (U+2212) du résultat de
   `finance.loan` ne pouvait pas s'afficher. Le script de test écrit désormais en UTF-8, et une exception imprévue
   devient une ligne FAIL explicite. APEX n'était pas touché : son journal est écrit en UTF-8.
3. **La candidate transmettait `PYTHONUTF8` à APEX** (`cb4bf22`). Ce n'est plus le cas : APEX tourne comme sur le
   PC d'un utilisateur.

**Mode WSL d'APEX.exe (`windows-desktop`) : de nouveau vert depuis le 17/10/2026**
([run 39040344678](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39040344678), `9ffcbd2`) : 48 PASS,
0 FAIL, 5 BLOCKED. Les BLOCKED sont des limites du runner : administrateur, SmartScreen manuel, aucun backend lancé à
la main, une seule distribution, ni Ollama ni GPU. Il était rouge depuis le 08/10 (21 FAIL), pour quatre raisons :
1. depuis le mode natif (`2a38110`), l'APEX.exe installé ne regardait plus WSL. Le workflow impose maintenant
   `APEX_PANEL_MODE=wsl` ; le mode natif est validé par `windows-native` ;
2. le contrôle C03 cherchait un texte de l'interface d'avant la 0.4.0 ;
3. **défaut réel de l'installateur** (I14) : avant une mise à jour, il lance `APEX.exe --stop` de la version
   installée et attend qu'il se termine. La 0.2.1 ne connaît pas `--stop` : elle ouvrait sa fenêtre, et la mise à
   jour ne se terminait jamais. L'installateur n'appelle plus `--stop` qu'à partir de la 0.3.0, qui l'a introduit.
   Une mise à jour depuis la 0.3.0 ou plus récente ne change pas ;
4. S04 observait le Panel resté ouvert par B06 (il affichait l'assistant), et non un Panel neuf face à
   l'enregistrement hostile. Ses exigences n'ont pas changé.

Le runner rapporte « exit code 1 » pour un verdict BLOCKED. Le script sort par `exit 3`, avant la ligne
`exit $LASTEXITCODE` que GitHub ajoute ; `pwsh -Command` réduit alors le code à 1.

## 5. Exécuté avec un vrai modèle

- **Windows Server** : le moteur intégré **`qwen2.5-coder:1.5b-instruct:bf16`** (Qwen2.5-Coder-1.5B-Instruct,
  révision figée, empreintes vérifiées, installé par le Panel) a répondu aux tours réels, sur processeur :
  - ma branche : tour Finance en 120 s ;
  - candidate : tour Finance en 289 s. Le modèle y a appelé `finance.loan`, et la mensualité de 1 159,92 € est
    exacte (A3 PASS).
- **Ici, Linux** : le Cerveau APEX v2 a servi le tour 3D et a produit la proposition (§2). Sous Windows, la
  proposition est venue de la détection d'intention d'APEX (`served_by_brain: false`).
- **Planificateur Qwen2.5-Coder-7B Q5_K_M**, sur le processeur, sous Windows Server :
  - par Codex, dans le runtime empaqueté : vraies inférences et plans acceptés (runs 39111881405, 39112594053,
    39113464692 et 39114782698) ;
  - par Claude, dans l'APEX.exe installé (tentative 8) : poids installés par le bouton, puis plan de la mission.
    Dans le code de #31, un projet dynamique n'a pas d'autre source de plan que `generate_plan` sur ce modèle.
    Le contenu du plan reste dans l'artefact `windows-candidate-evidence` (`studio-candidate.json`, preuves du
    profil), que l'API ne permet pas de lire d'ici.

## 6. Exécuté avec Blender

- La sonde d'APEX a lancé le vrai Blender 4.0.2 (`blender --background --version`) : il a répondu.
- **Sous Windows Server, Blender 4.3.2 portable officiel** (empreinte épinglée) :
  - par Codex, runtime empaqueté (run 39114782698) : voiture, juges, correction, `.blend`, `.glb` et 4 vues ;
  - par Claude, dans l'APEX.exe installé (tentative 8) : `asset.blend` et `asset.glb` livrés, téléchargés et
    vérifiés, 4 aperçus.
- Jamais encore sur le PC de l'utilisateur (Blender `.msi` dans Program Files).

## 7. Exécuté sur CUDA

**Rien sur CUDA.** Aucune carte NVIDIA n'était disponible. Aucune compatibilité GPU n'est annoncée.
- Les listes figées viennent de l'index PyTorch : métadonnées et empreintes.
- **Premier essai de l'utilisateur** (17/10/2026, Windows 11, RTX 4060 Ti, pilote 610.62) :
  - le Cerveau répond, environ 131 tokens/s, sur le processeur ;
  - « Accélération GPU » refusait aussitôt (code 2, « version CUDA du pilote illisible »). Ce pilote écrit
    « CUDA UMD Version: 13.3 », et APEX ne lisait que l'ancien libellé « CUDA Version ».
- **Correction (`1a7fc23`)** : la version CUDA acceptée est demandée au pilote lui-même (`cuDriverGetVersion`)
  et relue dans `nvidia-smi`, avec les deux libellés. Si les deux diffèrent, la plus basse est retenue. Les
  tests de régression reprennent les valeurs relevées par l'utilisateur.
- **Vérifié sans GPU sur Windows réel**
  ([run 39068337396](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39068337396)), avec le Python et
  le code d'APEX installés :
  - les roues cu130 et cu126 existent, à la taille figée près ;
  - cu130 est choisie pour CUDA 13.3 ;
  - la roue cu130 est téléchargée, son empreinte vérifiée, et installée à part en 38 s ;
  - la sonde charge `torch 2.14.0+cu130` depuis la surcouche, et refuse faute de GPU.
- **Constaté par l'utilisateur** (17/10/2026, candidate du run 39068337396) : le Cerveau ApexLM V2 tourne sur
  la RTX 4060 Ti avec PyTorch CUDA. C'est sa mesure, sur son PC ; aucun runner n'a de GPU.
- Conséquence trouvée ensuite (§8) : chargé sur CUDA, le Cerveau tient le bail `model-session.lock`. Jusqu'à
  #29, chaque étape le prenait aussi, même sur le processeur : le Cerveau devait prêter le GPU. Depuis #31,
  une étape sur le processeur ne le prend plus, et le Cerveau garde le GPU (vérifié avec les verrous Windows
  réels et un « CUDA » simulé, tentative 8).
- `llama-cpp-python` CUDA (planificateur) est un sujet distinct de PyTorch CUDA (Cerveau, Game Studio). Il
  n'est pas traité ici.

## 8. Laissé à Codex (ses fichiers ; détail dans l'Issue #21)

Dossier complet : [`docs/REPRISE_CODEX_GAME_STUDIO.md`](REPRISE_CODEX_GAME_STUDIO.md). Il donne les contrats
consommés, ce qui est fait, et ce qui reste, dans l'ordre du parcours.

- **Fait par Codex** (`c80ce11`, `d15fa02`) :
  - `jsonschema` dans la liste Windows ;
  - `scripts/game_engineer` empaqueté ;
  - Blender cherché dans `APEX_BLENDER_EXECUTABLE`, le PATH et Program Files ;
  - contrats `dependencies` et `install-planner` ;
  - liste facultative du moteur CPU `llama-cpp-python` 0.3.36.
- **Branché par Claude sur ces contrats** : liste des composants, boutons d'installation confirmés, reprise des
  missions.
- **Défaut trouvé et corrigé côté Claude** (`20a7167`) : le Cerveau sur CUDA tient le bail
  `model-session.lock`, que chaque étape de mission prend aussi, même sur le processeur. Sans correction, la
  mission échoue (`BlockingIOError`, mesuré avec le vrai `resource_lease`). Désormais, l'assistant demande le
  GPU au Cerveau au clic, et le lui rend quand la mission ne tourne plus. Les prêts sont comptés.
- **Fait par Codex ensuite** (#29 `8fc1df9`, #31 `550b347`) : mise au repos du Cerveau autour de chaque étape
  (protocole 2 du gouverneur de Claude), verrou de travaux du processeur séparé du bail CUDA, code
  `shared_resource_busy`, serveur du planificateur possédé et arrêté, budgets Windows robustes, qualification
  réelle Qwen → Blender sur son runner (run 39114782698).
- **Ajusté par Claude** (`98c7ebb`, `8fc89a3`) : protocole 2, aucun prêt de l'assistant quand l'adaptateur de
  Codex existe, raisons d'arrêt de Game Studio traduites, candidate avec le parcours complet.
- **Reste à Codex** :
  1. publier le résultat et les preuves du run 39114782698 (artefact seulement, expire le 01/11/2026) et le
     rapport final de #31 ;
  2. un `llama-cpp-python` CUDA, puis un test sur la RTX, avant de lever sa garde GPU ;
  3. refaire le benchmark du modèle ;
  4. hors parcours : #12/#16/#17 avec l'intégrateur, `apex/vision`.

## 9. Laissé à tester sur le PC de l'utilisateur

Configuration : RTX 4060 Ti 8 Go, i5-14400F, 16 Go, Windows 11. Procédure :
[`docs/QUALIFICATION_APEX_EXE.md`](QUALIFICATION_APEX_EXE.md).

1. Installer la candidate de la tentative 8, « Préparer APEX » (installe `jsonschema`), démarrer.
2. Composants de Game Studio : le moteur (8 Mo), puis le planificateur (5,4 Go), puis Blender 4.3.2.
3. Calcul des missions : « Processeur ».
4. La première création 3D : proposition, vrai clic, suivi, aperçus, fichiers. Puis annulation et reprise.
5. Pendant la mission, le Cerveau reste sur la RTX : à confirmer par la ligne de mesures sous ses réponses.

## 10. Validation complète (P7)

| Domaine | Où | Résultat |
|---|---|---|
| Suite Python | arbre combiné frais `27a1e8d` (43 min) | **3 497 passent, 189 ignorés, 7 échouent : les 7 déjà identifiés, aucun nouveau.** 4 viennent du worktree jetable (`test_apex_preflight_script` ×3, `test_evalrunner_generated_solution::…test_g_…`) ; 2 sont `test_game_graphics_contract` (`llama_cpp`, psutil : Codex) ; 1 est la garde de spécialité `test_no_second_specialty_task_provider_is_registered` (Codex). Aucun test n'a été modifié pour les faire disparaître |
| Frontend | arbre combiné : vitest, `tsc --noEmit`, build | 183/183, OK, OK |
| Panel (`dotnet test`) | arbre combiné ; ma branche | 590 passent, 2 échouent. Ces 2 tests exigent `.venv/bin/python3` à la racine, absent du worktree jetable ; ils passent dans le dépôt (8/8). Sur ma branche après le correctif `-P` : Dashboard 251/251 |
| États de préparation | ma branche | 111 passent, 17 ignorés |
| Installation, démarrage, persistance, désinstallation | Windows Server : ma branche ; candidate | PASS sur l'application installée (§4) ; candidate : PASS sauf le parcours 3D, BLOCKED |
| Sécurité | lancement par acte humain seulement ; jeton d'API ; bail GPU | tests dédiés dans la suite ; aucun chemin autonome de lancement |

Le rapport Gate de l'utilisateur (`.apex/environment.json`) est intact : même empreinte avant et après.
