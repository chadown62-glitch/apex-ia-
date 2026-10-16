# Intégration du Cerveau APEX et de Game Studio — état au 16/10/2026

Ce rapport dit ce qui est prêt, ce qui ne l'est pas, et ce qu'il reste à faire pour installer APEX avec le
Cerveau et Game Studio dans le vrai APEX.exe. **Le logiciel n'est pas prêt pour une release** : le parcours
principal (une demande, une mission Game Studio réellement exécutée, des résultats dans l'application)
n'a jamais tourné de bout en bout. Il faut un GPU, le modèle planificateur et Blender, absents de
l'environnement de développement de Claude.

Parcours visé : utilisateur → Cerveau APEX → choix du spécialiste → mission → suivi → juges → corrections →
résultats dans l'application.

## 1. Versions et commits

Commits de base :

| Branche / PR | Commit | Contenu |
|---|---|---|
| `main` | `ca3479a` | base commune |
| `claude/apex-official-development-4kao8t` (Claude, PR #2) | ce commit, sur `91fd4b2` | Cerveau, assistant, délégation à Game Studio, partage du GPU côté Cerveau |
| `agent/codex/18-game-studio-integration` (PR #19) | `db58f30` | Game Studio dans le frontend et le backend |
| `agent/codex/20-game-studio-multi-agent` (PR #22) | `c0a8591` | création multi-assets, assemblage Blender |
| `agent/codex/23-game-studio-planning` (PR #24) | `10bd282` | planification dynamique, Graphics → Engineer, outils de délégation |
| `agent/codex/25-game-studio-candidate` (PR #26) | `1e2db2f` | candidate d'intégration (couverture des demandes, budgets, reprise) |

Aucune PR n'est fusionnée.

**Fusion à blanc** (`git merge-tree`, rien n'est écrit ni poussé) de l'état de ma branche avec chacune des
quatre branches Codex ci-dessus : **aucun conflit**. Les fichiers que j'ai modifiés ne sont touchés par
aucune d'elles.

## 2. Contrats compatibles (vérifiés contre le code de Codex, dans un arbre local de ma branche + `1e2db2f`)

| Contrat | Côté Codex | Côté Claude | Vérification |
|---|---|---|---|
| Outils de délégation | `register_game_studio_tools(registry)` : `game_studio.create/status/results/cancel` | enregistrés par `apex/assistant/tools.py::register_game_studio` ; absents sans le module, sans casser les autres outils | 54 tests avec ses vrais outils, plus ses 2 tests |
| Permissions | `game_studio.create/read/cancel` (`apex/tools/model.py`, ajoutées par Codex) | lues défensivement (`apex/assistant/modes.py`) ; `read` avec le mode 3D ; `create/cancel` pour le modèle, seulement avec le lancement automatique | idem |
| Appartenance | `owner_workspace_id == ctx.workspace.id` | espace de travail de la conversation ou du projet | une mission d'une autre conversation est introuvable (suivi, annulation, aperçu, lancement) |
| Service | `ctx.extra["game_studio_service"]`, sinon `get_game_studio_project_service()` | injecté par les tests, celui du processus sinon | chaîne réelle (§5) |
| Disponibilité | `apex.game_studio.capabilities.probe()` | `apex/assistant/game_studio.py::availability` traduit ce qui manque | sondes simulées ; sonde réelle dans l'arbre local |
| Aperçus | `GameStudioProjectService.artifact` et `GameStudioService.artifact` (ceux de ses routes) | servis seulement pour une image `view` que `game_studio.results` vient de rendre à cette conversation | tests |
| Bail GPU | `resource_lease()` : `flock` / `msvcrt.locking` sur `APEX_HOME/.apex/model-session.lock` | `apex/models/gpu_handoff.py` : même fichier, même primitive, tenu tant qu'un modèle principal est sur CUDA | exclusion mutuelle et transfert contre le vrai `resource_lease` |
| Quiescence | demandée par Codex dans le processus propriétaire, avec accusé de réception | `request_gpu(timeout_s, requester) -> ack`, `return_gpu(requester)`, `gpu_status()` | 10 tests (sans CUDA) |

### Protocole GPU (ce que Codex doit appeler)

```python
from apex.models.gpu_handoff import request_gpu, return_gpu

ack = request_gpu(timeout_s=120, requester=f"game_studio:{project_id}")
if not ack["released"]:
    ...  # mission BLOCKED/queued, raison : ack["reason"] ; aucun kill, pas de CPU implicite
with resource_lease(folder / "resource.lock"):  # inchangé : le bail est libre
    ...  # travail GPU du spécialiste
return_gpu(requester=f"game_studio:{project_id}")
```

**Ce que garantit le Cerveau**
- Le bail est tenu pendant toute la présence sur CUDA. L'acquisition est atomique : pas de contrôle préalable,
  donc pas de TOCTOU.
- `request_gpu` procède dans l'ordre :
  - il refuse les nouvelles admissions CUDA ;
  - il attend les générations en cours, sans les couper ;
  - il décharge le Cerveau et le moteur intégré, sous leurs verrous ;
  - il mesure la mémoire CUDA avant et après (torch, et le périphérique) ;
  - il rend le bail.
- L'accusé contient `released`, `reason`, `unloaded`, `waited_s`, `lease_held`, la mémoire `cuda` et
  `admissions_open`.
- Délai dépassé : rien n'est déchargé, les admissions sont rouvertes, et `released: false`.
- Pendant le prêt, le Cerveau répond sur le processeur, et ses mesures le disent (`device_reason`).

**Point à trancher par Codex.** Entre la libération du bail par le Cerveau et son acquisition par Game
Studio, un autre programme peut le prendre. Game Studio échoue alors en refusant, ce qui est sûr. Un transfert
sans fenêtre (le Cerveau passant son descripteur verrouillé) est possible dans le même processus ; il demande
une API commune à définir ensemble.

## 3. Conflits et points de politique

- **`CREATE_MISSION` est réservé à l'humain** dans le moteur de politique (« jamais le CORE en autonomie »).
  Or Game Studio enregistre `created_by=HUMAN` pour toute mission. D'où le choix suivant :
  - par défaut, le Cerveau **propose** la mission, et le clic de l'utilisateur est l'acte humain vérifié ;
  - le **lancement automatique** reste un réglage explicite, désactivé par défaut, et tracé
    (`authorization`, `source: "cerveau"`).

  À confirmer par l'utilisateur et par Codex. Il serait utile que Codex enregistre l'origine réelle, par
  exemple un champ `requested_by` dans le reçu.
- **Garde GPU de Codex (#26).** Le dispatch GPU de Game Studio est bloqué tant que la quiescence du Cerveau
  n'est pas qualifiée. Le côté Cerveau existe maintenant ; la garde ne doit être levée qu'après le §8.

## 4. Dépendances et permissions

| Élément | État |
|---|---|
| `jsonschema` | importé par `apex/game_graphics/judge.py`, `apex/game_engineer/{assets,tools}.py` ; **déclaré nulle part** (`pyproject.toml`, extra `game_studio`, verrouillage Windows). Sans lui, `game_studio.create` échoue (`internal_error`), constaté le 16/10 |
| Pillow, psutil | dans le verrouillage Windows de la branche Codex (`requirements-windows.in`), pas dans `pyproject.toml` |
| `llama-cpp-python` + Qwen2.5-Coder-7B Q5_K_M | planificateur épinglé ; absent du verrouillage Windows ; poids à installer explicitement (`APEX_HOME/.apex/game-studio/coder-model.json`) |
| Blender | à installer sur le PC (introuvable dans le PATH : la sonde le dit) |
| **torch CUDA** | **APEX.exe embarque `torch==2.14.0` de PyPI : sous Windows, c'est une build CPU seulement.** Ni le Cerveau, ni le moteur intégré, ni la sonde CUDA de Game Studio ne verront la RTX 4060 Ti. Il faut une roue CUDA (index `download.pytorch.org`, ~2,5 Go) : décision de packaging |
| `APEX_HOME` absolu | requis par le bail (posé par le profil autonome d'APEX.exe) |

Permissions et réglages côté utilisateur :
- Paramètres > Outils > **Lancement automatique des missions Game Studio** (désactivé par défaut) ;
- **Calcul des missions** (automatique = GPU ; processeur seulement sur choix explicite).

Le lancement et l'annulation par clic sont des actes humains (`PolicyEngine` : HUMAN `CREATE_MISSION` et
`CANCEL_MISSION`).

## 5. Ce qui a été réellement exécuté

| Chaîne | Résultat |
|---|---|
| Vrai Cerveau → analyse → proposition → clic → vrai `game_studio.create` → vrai service Game Studio (arbre local, APEX vierge) | refus honnête `local_planner_model_missing` (planificateur absent ici), expliqué à l'utilisateur |
| Idem, lancement automatique, calcul automatique | refus honnête `cuda_unavailable_choose_cpu_explicitly` (pas de GPU ici) |
| Bail du Cerveau contre le vrai `resource_lease` de Codex | jamais les deux à la fois ; transfert après l'accusé ; reprise |

**Jamais exécuté :** une mission Game Studio jusqu'à des rendus, des juges et des corrections ; une
quiescence sur un vrai GPU ; quoi que ce soit sous Windows réel.

## 6. État par composant

| Composant | État |
|---|---|
| Analyse d'intention v2 (48/50 créations, 0 lancement à tort sur 120 messages jamais vus) | **fonctionnel et testé** |
| Proposition et lancement par clic, une mission par proposition, annulation, refus expliqués | **fonctionnel et testé** (doubles du service ; chaîne réelle jusqu'au refus) |
| Ébauches du Cerveau vérifiées (17 écrites au lieu de 98, toutes de la bonne sorte) | **fonctionnel et testé** (mesure non aveugle) |
| Suivi, aperçus des rendus, disponibilité expliquée dans l'Atelier 3D | **intégré, pas encore qualifié** : aucun rendu réel n'a jamais été affiché |
| Partage du GPU, côté Cerveau | **intégré, pas encore qualifié** : protocole testé sans CUDA |
| Appel de `request_gpu` / `return_gpu` par Game Studio | **préparé contractuellement**, en attente de Codex |
| Mission Blender de bout en bout depuis le Cerveau | **bloqué** : GPU, planificateur, Blender |
| Torch CUDA dans APEX.exe | **bloqué** : décision de packaging |
| Recherche web facultative (`network.read`), sandbox Windows pour la vérification croisée | **en attente de Codex** (fichiers partagés) |

## 7. Tests

- Ma branche :
  - `tests/test_assistant_game_studio.py` : 41 tests passent, 16 sont ignorés sans Game Studio ;
  - `tests/test_gpu_handoff.py` : 9 passent, 1 est ignoré.
- Arbre d'intégration local : les mêmes plus les 2 tests de Codex, soit 69 passent.
- Frontend : 182 tests.
- Suite complète, en 12 lots : 3 274 passent et 198 sont ignorés. Un seul échec : le test Gate historique
  (`tests/test_api.py::test_gate_is_unknown_without_a_report`), qui lit le vrai `.apex/environment.json` de
  l'utilisateur. Ce fichier n'a pas été modifié (empreinte inchangée) ; l'isolation du test revient à son
  propriétaire.
- Build de production du frontend correct.

Couverture :
- créations reconnues, questions qui ne lancent rien, formulations ambiguës ;
- lancement automatique désactivé, Game Studio absent ou cassé ;
- refus et erreurs de communication de Game Studio ;
- une mission par tour et par proposition ;
- statut, résultats et aperçus ;
- isolation par espace de travail (suivi, annulation, aperçu, lancement depuis une autre conversation) ;
- contrôle anti-fuite du runtime.

## 8. Validations à faire sur Windows (PC de l'utilisateur, RTX 4060 Ti 8 Go)

1. Construire APEX.exe avec une roue torch CUDA, puis vérifier `torch.cuda.is_available()` dans le runtime
   embarqué.
2. Installer Blender, llama-cpp-python (CUDA) et le planificateur. La sonde de l'Atelier 3D doit afficher
   « Prêt sur ce PC ».
3. Envoyer « Crée une voiture sportive » en mode 3D, puis lancer la mission proposée. Suivre son état,
   voir les rendus, ouvrir les fichiers.
4. Pendant une génération du Cerveau, déclencher une mission. Mesurer l'accusé de réception :
   `released: true`, VRAM rendue, et le Cerveau qui répond sur processeur pendant la mission puis revient
   sur CUDA.
5. Mission en cours : l'annuler depuis l'Atelier 3D.
6. Vérifier le bail `msvcrt` avec une autre instance d'APEX ou Blender ouvert : refus expliqué, aucun kill.

## 9. Actions

**Codex**
- Appeler `request_gpu` / `return_gpu` autour de `resource_lease`, puis lever la garde #26 après le §8.
- Déclarer `jsonschema`, Pillow, psutil et `llama-cpp-python` (`pyproject.toml`, extra, verrouillage
  Windows).
- Répondre aux questions de l'Issue #21 :
  - (a) description = demande brute de l'utilisateur ;
  - (b) appel depuis le fil du tour (≈ 2 s de sonde CUDA) ;
  - (c) qualité `prototype` par défaut.
- Dire s'il veut un transfert de bail sans fenêtre, et un champ d'origine (`requested_by`).
- Se prononcer sur `network.read`, et sur le propriétaire de `apex/sandbox/**`.

**Claude**
- Packaging de torch CUDA pour APEX.exe (décision et taille à valider avec l'utilisateur).
- Qualifier le partage du GPU sur le PC de l'utilisateur (§8, point 4).
- Fusion contrôlée des branches, une fois Codex d'accord, puis la suite complète sur la branche réunie.
- Isolation du test Gate historique : à faire par son propriétaire, sans toucher les données réelles.
