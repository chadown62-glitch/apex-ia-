# Audit technique global d'APEX — 05/10/2026

Base : branche `claude/apex-official-development-4kao8t`, HEAD `5ab53c8`, environ 80 000 lignes Python
dans `apex/`, un frontend React. Méthode : lecture du code (pas des seules docstrings), du graphe d'imports
réel, du registre de capacités `apex/interfaces/registry.py` (5 abstractions IMPLEMENTED, 41 STUB, 2 DEFINED,
9 PLANNED), des routes API, du frontend et des tests. Le protocole v2 et son audit ne sont pas modifiés.

## 1. Ce qui existe réellement (avant ce chantier)

| Étape de la boucle visée | Composant réel | État constaté |
|---|---|---|
| OBSERVE / UNDERSTAND | `TaskLoop` lit une `Task` (énoncé + harnais caché) | seules les tâches « écrire une fonction Python » existent ; 15 tâches de référence, aucune tâche saisissable par l'utilisateur |
| PLAN | `task.planning.*` dans `apex/taskloop/loop.py` | **vide** : concaténation énoncé + contexte, mais des événements « planning » laissaient croire à une planification |
| SELECT MODEL | `apex.routing` (sonde physique + score), `apex.models` (Ollama, OpenAI-compatible) | réel |
| SELECT TOOLS | `ToolProvider`, `ToolRegistry` | **absents** (PLANNED) |
| EXECUTE | `AgentRuntime` → sandbox (Docker durci, réseau coupé, limites) | réel, fail-closed |
| VERIFY / JUDGE | `ResultContractOracle` (résultats comparés hors sandbox), `Tier0Judge` | réel |
| RECORD EXPERIENCE | `build_experience` à chaque tentative | réel |
| LEARN / ADAPT | règle `decide_strategy_from_experiences`, faits d'échec (v2) | seulement si l'appelant passe `condition_on_run` ; **jamais entre deux tentatives d'une même exécution** |
| NEXT ACTION | `apex.recovery.decide` (RETRY/REASSIGN/BLOCK/ESCALATE) | réel, mais la décision RETRY annonçait « feedback du judge incorporé » alors que **le même prompt était renvoyé** |

Autres constats : le Chat ne transmettait au modèle que le dernier message (aucune mémoire de conversation) ;
aucune page de l'interface n'utilisait `/task-runs` (la boucle réelle n'était pilotable que par l'API) ; la trace
d'une exécution n'était pas exposée ; `MemoryProvider` écrit et relit des souvenirs avec provenance mais aucun
module du runtime ne l'utilise et il n'y a ni recherche par pertinence ni embeddings (pas de RAG).

## 2. Problèmes classés

### P0 — empêchent APEX d'être un système fonctionnel de bout en bout
1. **Nouvelle tentative sans correction** (`loop.py`) : la politique de reprise affirmait incorporer le retour du
   Judge, la boucle renvoyait le même prompt à la même température. → **corrigé** (§3.1).
2. **Impossible de soumettre son propre problème** : seules les 15 tâches de référence étaient exécutables
   (« l'écriture libre de tâches reste à concevoir »). → **corrigé** pour les fonctions Python à entrées/sorties
   JSON (§3.2).
3. **La boucle réelle n'était pas atteignable depuis l'interface**. → **corrigé** (§3.6).
4. **Frontière Runtime/Research incohérente avec le produit** : `release.sh` exécute `runtime_build.verify`, qui
   échoue avec 75 imports interdits dans 12 modules « runtime » (`apex/api/dependencies.py`, routes
   `task_runs`, `evals`, `learning*`, `population`, `gauntlets`, `capability`, `brain`, `apex/cli/main.py`,
   `apex/obs/component_checks.py`) vers `taskloop`, `metalearner`, `tasks`, `taskgen`, `evalrunner`, `neural`…
   L'artefact Runtime construit n'aurait donc ni la boucle de tâche ni la plupart des routes. Le Setup n'embarque
   pas encore d'artefact Runtime : le produit tourne aujourd'hui depuis le dépôt complet. → **NON corrigé :
   décision de périmètre requise** (que livre-t-on : la boucle de tâche, l'apprentissage, `neural`/torch ?). La
   fermeture d'imports de `apex.api` atteint 18 des 20 paquets « research » (mesuré, graphe d'imports statique).

### P1 — capacités majeures manquantes ou incomplètes
1. PLAN non réel. → **corrigé en option** (`plan_first`, §3.4) ; effet sur la réussite non mesuré.
2. Chat sans mémoire. → **corrigé** (§3.5).
3. Trace d'exécution non exposée. → **corrigé** (§3.3).
4. Expérience → décision entre exécutions seulement manuelle. → **partiellement** : `condition_on_run: "latest"`
   (§3.3) ; aucune politique n'apprend automatiquement d'une tâche à l'autre ; le moteur de recommandation
   (`recommendation_engine`) n'est consommé par rien (choix documenté « learning ≠ decision ») ; l'apprentissage
   « recherche » (populations, génomes, `AutonomousLearningService`) est un circuit séparé des Experience du
   TaskLoop.
5. **Aucune couche d'outils** : le modèle ne peut ni lire des fichiers, ni lancer ses propres tests, ni
   chercher. Le seul outil est le harnais fixe exécuté en sandbox. → non fait.
6. **Mémoire / RAG absents du runtime** : pas de récupération par pertinence, pas d'`EmbeddingProvider`, ni le
   Chat ni le TaskLoop ne lisent `MemoryProvider`. → non fait.
7. **Types de tâches** : une fonction Python, sorties comparables en JSON. Pas de projet multi-fichiers, pas
   d'autre langage (les chaînes `rust`/`typescript`/`cpp` de la configuration sont DEFINED seulement).
8. **Reprise après interruption** : les TaskRun interrompues sont marquées `interrupted` au démarrage, jamais
   reprises (`CheckpointStore` PLANNED).
9. **Deux moteurs d'exécution** : `MissionEngine`/`Scheduler` (commande en sandbox, sans Experience) et le
   TaskLoop (génération, Judge, Experience) — récupération et traçabilité dupliquées.
10. **Backends** : Ollama et OpenAI-compatible (couvre aussi vLLM, llama.cpp server). Aucun modèle de
    « variante » (quantification, adaptateur LoRA) dans le routage ; `ModelEndpoint.runtime` est le point
    d'extension prévu pour AirLLM ou un serveur d'adaptateurs — à n'ajouter qu'avec un backend réel.
11. **Aucun effet d'apprentissage mesuré** : v1 sans différence (runs 25/26) ; v2 BLOCKED (machine cible) ; les
    mécanismes ajoutés ici (adaptation, plan) ne sont pas mesurés.

### P2 — robustesse, qualité, cohérence
1. Notes du registre périmées pour `TaskLoop` (« aucune table TaskRun », branches « inatteignables »). →
   **corrigé**.
2. `recovery.decided` annonçait un changement de stratégie indépendant du comportement réel. → **corrigé** :
   l'événement porte `retry_adaptation`, et `strategy.adapted` / `strategy.unchanged` disent ce qui a été fait.
3. 4 tests de packaging échouent : l'anti-fuite (P0-4) et un « contrat JSON Doctor phase 1 » (`schema_version`,
   `apex_version`, `generated_at`) attendu par un test mais jamais implémenté dans `apex/doctor` (le parseur C#
   d'ApexSetup le traite comme optionnel).
4. `ruff`/`mypy` absents de la CI ; erreurs préexistantes.
5. `TaskLoopRegistry` par processus seulement ; `MANUAL`/`ASSISTED` identiques.
6. Le rappel `_INCORRECT_SOLUTION_HINT` de la règle (bras B) parle de chaînes (« chaîne vide, casse ») mais est
   appliqué à toutes les tâches (laissé tel quel : définition de B figée par le protocole v2).
7. L'API renvoie `expected_answer` des tâches (acceptable pour un propriétaire unique ; à revoir en multi-
   utilisateur).

### P3 — secondaire
Page « Tâches » sobre (pas de diff de code entre tentatives, suivi par interrogation périodique et non en flux) ;
`Experience` ne porte ni objectif ni contexte (limite du moteur de recommandation).

## 3. Implémenté dans ce chantier

### 3.1 Diagnostic → correction → nouvelle tentative (`apex/taskloop/loop.py`, `request.py`)
`TaskRequest.retry_adaptation` (`failure_facts` par défaut, ou `none`). Après un échec JUGÉ, si la politique de
reprise relance, la tentative suivante reçoit les faits d'échec autorisés de la tentative qui vient d'échouer
(`apex.metalearner.failure_facts` : catégorie, k cas sur N, classes d'exception natives, terminaison, code de
sortie — jamais entrée cachée, réponse attendue, message d'exception ni code). La température en cours est
conservée. Lignée : l'Experience de la tentative N+1 a pour parent celle de la tentative N
(`adaptation: "in_run"`, `source_eval_run_id`). Catégorie sans fait (délai, infrastructure) : stratégie gardée,
événement `strategy.unchanged` avec la raison. Une exécution à une seule tentative n'est jamais adaptée : les
protocoles RUN 1 → RUN 2 (v1, v2) ne sont pas touchés.

### 3.2 Tâches définies par l'utilisateur (`apex/taskgen/solution_tasks.py`, `POST /tasks/function`)
Énoncé + nom de fonction + cas `{args, expected}`. APEX écrit le harnais avec le gabarit des tâches de
référence : l'utilisateur ne fournit jamais de code exécuté ; les arguments n'entrent dans la sandbox que comme
littéraux Python (vérifié par analyse AST, y compris pour une chaîne hostile) ; les valeurs attendues restent sur
l'hôte. Refus explicites (422) : nom non identifiant, réservé ou mot-clé, préfixe `_apex`, plus de 50 cas, plus
de 10 arguments, valeurs non JSON ou non finies, clés non textuelles, cas dupliqués, énoncé vide ou > 4000
caractères, JSON > 100 000 caractères. Les 15 tâches de référence produisent exactement les mêmes spécifications
qu'avant (empreinte SHA-256 identique).

### 3.3 Trace et apprentissage inter-exécutions (`apex/api/routes/task_runs.py`, `apex/taskloop/runs.py`)
`GET /task-runs/{id}` expose `events` : la trace réelle (`apex.taskloop`, même `trace_id`), dans l'ordre.
`condition_on_run: "latest"` résout la dernière TaskRun terminée de la même tâche et l'enregistre par son
identifiant réel. `GET /tasks/reference/kinds`. TaskRun persiste `retry_adaptation` et `plan_first` (migration
`d8f1a3c5e7b9`, colonnes nullables, aucune ligne réécrite).

### 3.4 Étape PLAN réelle, optionnelle (`plan_first`)
Un appel au modèle sélectionné, borné (temps de génération, 2000 caractères), sans exécution de code, journalisé
(`task.plan.generated` avec le texte), réinjecté dans le prompt et conservé à travers les tentatives adaptées,
enregistré dans `generation_config.plan`. Échec ou réponse vide : `task.plan.failed`, la génération part de
l'énoncé seul (jamais un plan inventé). Les événements `task.planning.*` disent désormais ce qui est fait
(`prompt_composition` ou `model_plan`). Désactivé par défaut : son effet n'est pas mesuré.

### 3.5 Mémoire de conversation (`apex/conversations/service.py`)
Le modèle reçoit les échanges RÉELS précédents de la même conversation (jamais une réponse en erreur), les plus
récents d'abord dans un budget de 6000 caractères (2000 par message), puis le nouveau message. Sans historique :
prompt inchangé. Aucune fuite entre conversations.

### 3.6 Interface « Tâches » (`frontend/src/pages/TaskRunsPage.tsx`)
Créer une tâche de référence ou sa propre tâche, lancer une exécution (tentatives, plan d'abord, adaptation,
apprendre de la dernière exécution), suivre statut, tentatives (verdict, catégorie, code généré) et trace
(plan, verdicts, adaptations). Les refus du serveur sont affichés tels quels.

## 4. Preuves

- **TESTÉ** (modèle doublure ; vraie boucle, sandbox, Judge, base) : `tests/test_taskloop_adaptation.py`,
  `tests/test_taskloop_plan.py`, `tests/test_user_tasks_api.py`, `tests/test_conversations_service.py`
  (`TestConversationMemory`), `frontend/src/pages/TaskRunsPage.test.tsx` ; non-régression : suites existantes.
- **MESURÉ** : graphe d'imports (frontière Runtime) ; empreinte des 15 tâches de référence inchangée.
- **JUGÉ** : rien sur un vrai modèle dans ce chantier.
- **INFÉRÉ** : les faits d'échec et le plan peuvent aider un vrai modèle — non démontré.
- **UNVERIFIED** : comportement avec un vrai modèle (1.5B CI ou 7B) de l'adaptation et du plan.
- **BLOCKED** : expérience primaire v2 (machine cible) ; réparation de la release Runtime (décision de
  périmètre, P0-4).

## 5. Reste à construire (sans minimiser)
Couche d'outils sandboxée ; mémoire/RAG branchés sur le Chat et le TaskLoop ; tâches au-delà d'une fonction
Python ; reprise d'exécution ; unification Missions/TaskLoop ; mesure réelle de chaque mécanisme
d'adaptation ; frontière Runtime cohérente et release verte ; modèle de variantes (quantification, adaptateurs)
le jour où un backend réel le justifie.
