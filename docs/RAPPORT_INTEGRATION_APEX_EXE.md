# Intégration réelle dans APEX.exe — résultats et blocages (16/10/2026)

**Verdict : aucune release « READY ».**
- Le parcours de création 3D reste bloqué : le planificateur Qwen2.5-Coder-7B épinglé et `llama-cpp-python` ne
  sont pas fournis, et la liste Windows de Game Studio n'a pas `jsonschema`. Ces trois points sont à Codex.
- Sous Windows, la candidate combinée installée ne charge pas Game Studio : « ne fait pas partie de cette
  installation ». La cause est en cours de diagnostic (§4).
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
| Arbre combiné, `201e720` + pile Codex | runner Windows, workflow `windows-candidate` | **construit** ([run 38981526906](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38981526906)) : 51 950 406 octets, SHA-256 `8a8efb0ce77c433b…` |
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
client, ni le PC de l'utilisateur.

| Build | Parcours | Résultat |
|---|---|---|
| Ma branche, `5a694ec` et `f941d90` | installation silencieuse → « Préparer APEX » → installation du modèle → démarrage → vrais tours de l'assistant → arrêt, redémarrage, persistance → désinstallation (base conservée) | **PASS**, deux fois |
| Candidate combinée ([run 38981526906](https://github.com/chadown62-glitch/apex-ia-/actions/runs/38981526906)) | installation → « Préparer APEX » → modèle → démarrage → tours de l'assistant → parcours 3D → arrêt → désinstallation | voir ci-dessous : **job en échec**, parcours 3D BLOCKED |

**Candidate combinée sur Windows Server, mesures du run 38981526906** (annotations du job) :
- installateur construit dans le runner : 51 950 406 octets, SHA-256 `8a8efb0ce77c433b…`, depuis le commit de
  fusion `b861326` (créé dans le runner, jamais poussé) ;
- installation silencieuse : OK. « Préparer APEX » : code 0. Installation du modèle : code 0 ;
- démarrage : code 1, « APEX DEGRADED / PartialServices » ;
- assistant : **PASS** (pièce jointe texte acceptée et PDF refusé, tour Finance, tour Code, réglage
  enregistré) ;
- lignes d'état : Cerveau APEX prêt, moteur intégré prêt, sandbox `WindowsAppContainerSandbox` prête, GPU
  absent (aucun pilote NVIDIA), Blender introuvable ;
- **défaut** : les lignes Game Studio, planificateur, Game Graphics et Game Engineer disent « Game Studio ne fait
  pas partie de cette installation d'APEX ». Pourtant cette candidate le contient. Ce message ne sort que si
  `apex.game_studio` lui-même est introuvable à l'import. Sous Linux, les mêmes étapes de build empaquettent et
  importent bien `apex.game_studio` (vérifié). La cause propre à Windows est **en cours de diagnostic** : le run
  suivant liste les fichiers installés et importe Game Studio avec le Python préparé ;
- parcours 3D : **BLOCKED** dès la disponibilité, à cause de ce défaut ; la conversation n'a pas été tentée ;
- arrêt, puis désinstallation : code 0, base de l'utilisateur conservée.

Le runner rapporte « exit code 1 » pour un verdict BLOCKED. Le script sort par `exit 3`, avant la ligne
`exit $LASTEXITCODE` que GitHub ajoute ; `pwsh -Command` réduit alors le code à 1.

## 5. Exécuté avec un vrai modèle

- **Windows Server, ma branche** : le Panel a installé le moteur intégré Qwen2.5-Coder-1.5B-Instruct (révision
  figée, empreintes vérifiées), et un vrai modèle d'APEX a répondu aux tours (`assistant_e2e.py` exige
  `message.status == "real"` et un `model_id`). **Quel moteur a répondu** est écrit dans l'artefact du run,
  illisible d'ici : il n'est pas affirmé.
- **Windows Server, candidate combinée** : le moteur intégré **`qwen2.5-coder:1.5b-instruct:bf16`** a répondu
  au tour Finance (412 caractères, 173 tokens, 72,6 s, processeur). L'outil `finance.loan` n'a pas été appelé
  par le modèle ; c'est rapporté tel quel.
- **Ici, Linux** : le Cerveau APEX v2 a servi le tour 3D et a produit la proposition (§2).
- **Planificateur Qwen2.5-Coder-7B : jamais exécuté.** Il n'est pas fourni.

## 6. Exécuté avec Blender

- La sonde d'APEX a lancé le vrai Blender 4.0.2 (`blender --background --version`) : il a répondu.
- **Aucune mission Game Studio n'a tourné dans Blender** : elle s'arrête au planificateur.
- Codex qualifie Blender 4.3.2, pas 4.0.2.
- Le runner Windows n'a pas Blender.

## 7. Exécuté sur CUDA

**Rien.** Aucune carte NVIDIA n'était disponible.
- Les listes figées viennent de l'index PyTorch : métadonnées et empreintes, sans téléchargement des roues.
- Aucune compatibilité GPU n'est annoncée.
- `llama-cpp-python` CUDA (planificateur) est un sujet distinct de PyTorch CUDA (Cerveau, Game Studio). Il
  n'est pas traité ici.

## 8. Laissé à Codex (ses fichiers ; détail dans l'Issue #21)

1. `jsonschema` dans `requirements-windows.in` / `.lock`. Proposition vérifiée : 5 paquets ajoutés, aucune
   version existante changée, empreintes Windows identiques à PyPI, licences MIT.
2. Le planificateur 7B et `llama-cpp-python` : déclaration, source Windows, CPU ou CUDA.
3. Empaqueter `scripts/game_engineer/`, comme `scripts/game_graphics/`.
4. Classer `apex/vision` dans `runtime_build/selection.py` (#7).
5. Trancher #12, #16 et #17 : elles entrent en conflit avec la pile #19 → #26 dans les deux ordres, et ne sont
   donc pas intégrées.
6. La spécialité `game-graphics` déclarée `is_supported` au stade STUB : elle déclenche la garde historique.
7. Appeler `request_gpu` / `return_gpu` autour de `resource_lease`. La garde GPU de #26 n'est à lever qu'après
   le test matériel.
8. Relire ma correction de `runtime_build/build.py` (`_extract_commit`), seul fichier modifié des deux côtés.

## 9. Laissé à tester sur le PC de l'utilisateur

Configuration : RTX 4060 Ti 8 Go, i5-14400F, 16 Go, Windows 11. Procédure :
[`docs/QUALIFICATION_APEX_EXE.md`](QUALIFICATION_APEX_EXE.md).

1. Installer la candidate, « Préparer APEX », démarrer, sous Windows 11 client.
2. Les lignes d'état réelles de ce PC.
3. L'accélération GPU : installation, sonde, état « prêt » avec le nom de la carte, débit du Cerveau comparé au
   processeur.
4. Le parcours 3D jusqu'au refus attendu. Puis, une fois le planificateur fourni par Codex : missions, juges,
   corrections, rendus, export Blender, annulation, reprise.
5. Le partage du GPU pendant une génération, et le bail avec Blender ouvert.

## 10. Validation complète (P7)

| Domaine | Où | Résultat |
|---|---|---|
| Suite Python | arbre combiné frais `27a1e8d` | **en cours** au moment de ce commit. Mesure précédente, arbre `e9898bf` : 3 476 passent, 189 ignorés, 7 échouent, tous identifiés (`docs/REUNION_BRANCHES.md` §7) |
| Frontend | arbre combiné : vitest, `tsc --noEmit`, build | 183/183, OK, OK |
| Panel (`dotnet test`) | arbre combiné | 590 passent, 2 échouent. Ces 2 tests exigent `.venv/bin/python3` à la racine : absent du worktree jetable. Ils passent dans le dépôt (8/8) |
| États de préparation | ma branche | 111 passent, 17 ignorés |
| Installation, démarrage, persistance, désinstallation | Windows Server, ma branche | PASS (§4) |
| Sécurité | lancement par acte humain seulement ; jeton d'API ; bail GPU | tests dédiés dans la suite ; aucun chemin autonome de lancement |

Le rapport Gate de l'utilisateur (`.apex/environment.json`) est intact : même empreinte avant et après.
