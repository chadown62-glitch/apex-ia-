# RUN 1 → Experience → RUN 2 : audit, architecture, protocole pré-enregistré

Rédigé le 04/10/2026, **avant toute mesure avec un vrai modèle**. Hypothèses, tâches, bras, critères et règle de
décision sont figés ici ; un résultat contraire (aucune différence, régression) est un résultat valide.

## 1. Audit (code lu avant toute modification)

| | État avant ce chantier |
|---|---|
| A. Experience créée | Oui : une par tentative jugée (`run_task_loop` → `build_experience`), mais avec `generation_number=0` et `generation_config={}` : la stratégie employée n'était pas enregistrée. |
| B. Experience récupérée | Non : `run_task_loop` ne lisait aucune `Experience`. |
| C. Experience utilisée | Non. |
| D. Stratégie modifiée | Non : prompt IDENTIQUE à chaque tentative et à chaque TaskRun, température du fournisseur. |
| E/F. Comportement / performance | Jamais mesurés. |

Existant réutilisable : `apex.metalearner.adaptive_solution_loop` — règle `_mutate_from_experience` (température
± et rappel générique selon la catégorie d'échec : `incorrect_solution` → +0,4 et `_INCORRECT_SOLUTION_HINT`,
`invalid_candidate` → ×0,3 et `_INVALID_CANDIDATE_HINT`, infrastructure → inchangée) et `decide_next_strategies`
(test d'ablation existant). Application d'une stratégie : `EvalRunnerService` (suffixe ADDITIF après la
description, `GenerationRequest.temperature`).

Risques vérifiés :
- **Fuite du jeu de test** : l'Oracle ne rapporte que des indices de cas ; les entrées cachées (harnais) et les
  réponses (`expected_answer`) ne quittent jamais l'hôte. La règle n'injecte que des textes CONSTANTS bornés
  (≤ 500 caractères) : ni cas cachés, ni sortie du modèle (pas d'injection de prompt depuis un modèle). Testé.
- **Contamination entre tâches** : lecture filtrée par `task_id` ET `experiment_ref` (= id de la TaskRun) ; une
  TaskRun d'une autre tâche est refusée (422). Testé.
- **Concurrence** : une seule exécution par tâche (registre existant) ; la TaskRun source doit être terminée. Testé.
- **Limite connue** : le rappel `_INCORRECT_SOLUTION_HINT` est générique (cas limites, casse) ; il n'indique pas
  ce qui a échoué. Il n'est PAS modifié ici : l'adapter après avoir vu les échecs fabriquerait l'amélioration.

## 2. Architecture retenue (minimale, aucun second système)

- `TaskRequest.condition_on_run` / `POST /tasks/{id}/runs {"condition_on_run": <RUN 1>}` : opt-in explicite ;
  absent = comportement historique (bras baseline). Colonne `task_runs.condition_on_run` (migration e4b6d8f0a2c3).
- `apex.metalearner.adaptive_solution_loop.decide_strategy_from_experiences` : la règle existante, extraite en
  fonction pure ; `decide_next_strategies` (apex solve) et le TaskLoop l'appellent — une seule règle.
- `apex.genome.clone_config.compose_generation_prompt` : la règle d'application du suffixe, partagée avec
  `EvalRunnerService`.
- Traçabilité : chaque `Experience` du RUN 2 porte `generation_config` (stratégie réellement envoyée),
  `parent_experience_id` (Experience du RUN 1 utilisée), `generation_number`, `mutation_description` (raison) ;
  événement `strategy.decided` ; `GET /task-runs/{id}` expose tout.

Prouvé par tests (doublure de modèle qui enregistre ce qu'elle reçoit, vraie boucle/sandbox/Judge/base) :
B, C et D — dont l'ablation (Experience supprimée → stratégie baseline) qui établit la dépendance causale de la
stratégie à l'Experience persistée. E et F exigent un vrai modèle : ci-dessous.

## 3. Protocole (pré-enregistré)

- **Script** : `scripts/apex_experience_ab.py` (pilote l'API réelle, ne produit aucune donnée).
- **Tâches** (5, figées, `apex/taskgen/solution_tasks.py`) : `is_palindrome`, `rle_encode`, `count_vowels`,
  `word_count`, `second_largest` ; chacune porte une contrainte précise facile à manquer ; validées par tests
  (solution correcte = PASS, piège de l'énoncé = FAIL, vraie sandbox).
- **Par tâche et réplique** (4 répliques par défaut), une tâche neuve ; 1 tentative par TaskRun :
  RUN 1 (aucune expérience) → RUN 2 baseline (aucune expérience) et RUN 2 conditionné (`condition_on_run` = RUN 1),
  ordre des deux RUN 2 alterné. Même modèle, même routage, même sandbox, même Judge.
- **Mesures par TaskRun** : task_id, run_id, modèle, stratégie (température, suffixe), Experience source, raison,
  verdict, cas réussis / total (rapportés par l'Oracle), détail d'échec, code de sortie, isolation, durée de
  génération, jetons, code généré, empreinte, Experience produite, fitness (`runs.jsonl`).
- **Analyse primaire** : paires dont le RUN 1 a ÉCHOUÉ et dont le RUN 2 conditionné a une Experience source.
  Critère : cas réussis, conditionné contre baseline, test du signe exact bilatéral (ex aequo exclus).
  - p < 0,05 et conditionné meilleur → « amélioration mesurée » ;
  - p < 0,05 et conditionné pire → « régression mesurée » (conservée telle quelle) ;
  - sinon → « aucune différence démontrée ».
  Secondaire : nombre de PASS par bras ; codes identiques signalés.
- **Limites assumées** : la stratégie conditionnée change à la fois la température et le prompt (règle existante,
  non décomposée) ; avec 5 × 4 paires au plus, la puissance est faible — l'absence de différence n'est pas une
  preuve d'absence d'effet. Une différence mesurée sur le modèle 1.5B de la CI (A) ne vaut pas pour le 7B de la
  machine cible (B), et inversement.

## 4. Résultats

### 4.1 CI — run 25 (commit 3afe576), preuve A : modèle RÉEL `qwen2.5-coder:1.5b-instruct`, jamais la machine cible

Windows Server 2025 + WSL2 réel, 2 CPU / 3,9 Go MESURÉS, aucun GPU ; APEX démarré par le Panel publié (Startup
Gate), ContainerSandbox ; 60 TaskRuns réelles (5 tâches × 4 répliques × 3 bras), 1 tentative chacune.

| Tâche | RUN 1 (réussis / 4) | Paires analysées | Résultat des paires (baseline → conditionné, cas réussis) |
|---|---|---|---|
| is_palindrome | 1/4 | 3 | 9→9, 9→9, 9→9 (12 cas) |
| rle_encode | 1/4 | 3 | 8→8, 8→8, **8→7** (8 cas) |
| count_vowels | 4/4 | 0 | — |
| word_count | 4/4 | 0 | — |
| second_largest | 4/4 | 0 | — |

- **Analyse primaire (règle pré-enregistrée)** : 6 paires ; conditionné meilleur 0, **pire 1** (rle_encode r4,
  8 → 7), égal 5 ; test du signe p = 1,0 ; PASS : baseline 3/6, conditionné 2/6.
  **Conclusion : AUCUNE différence démontrée.** La régression observée (1 paire) est conservée ; elle n'est pas
  significative. Aucun code identique entre bras dans les paires analysées.
- 14 paires exclues comme prévu : RUN 1 réussi, aucun échec à exploiter.
- Observations (MESURÉ, non interprétées comme effet) :
  - is_palindrome : le 1.5B retire la ponctuation ; le rappel générique (casse, cas limites) ne le corrige pas
    (9/12 dans tous les bras conditionnés) — conforme à la limite documentée §1.
  - rle_encode : forte variabilité d'un tirage à l'autre au réglage par défaut (RUN 1 réussi 1/4, RUN 2
    baseline réussi 4/4) ; le bruit d'échantillonnage domine.
  - Pour deux échecs rle_encode partiels (7/8), la règle a choisi la branche « candidat invalide » (T = 0,21,
    rappel de format) — INFÉRÉ d'après la température appliquée (0,7 × 0,3) ; la catégorie d'échec exacte est
    dans `runs.jsonl` (non publié par la CI).
  - Quand le RUN 1 réussit, le bras conditionné applique T = 0,7 explicite au lieu du réglage du fournisseur :
    ces paires sont exclues de l'analyse primaire.
- Ce que ce résultat NE dit PAS : qu'il n'y a aucun effet (6 paires, puissance faible) ; quoi que ce soit sur le
  modèle 7B de la machine cible.

### 4.1 bis Réplication CI — run 26 (commit e11efe4), même protocole v1, même modèle 1.5B, rapportée séparément

Lancée automatiquement par le push des outils de mesure ; aucun changement de bras ni d'analyse. Nouvelles mesures :
le modèle tourne entièrement sur CPU (Ollama `/api/ps` : 1,2 Go, 0 Go en VRAM ; nvidia-smi absent), génération
de 1,3 à 18,5 s par TaskRun (durées rapportées par Ollama), mémoire disponible stable (~2,0–2,1 Go sur 3,9 Go).

- **Analyse primaire** : 6 paires (is_palindrome ×4, word_count ×2) ; conditionné meilleur 0, **pire 1**
  (word_count r1, 6 → 4 cas), égal 5 ; p = 1,0 ; PASS baseline 1/6, conditionné 1/6.
  **Conclusion : AUCUNE différence démontrée** — identique au run 25.
- is_palindrome : 16 échecs sur 16 RUN (tous bras confondus), toujours 9/12 — erreur systématique non corrigée
  par la stratégie actuelle.
- Bruit entre deux bras SANS expérience (RUN 1 contre RUN 2 baseline) : 6 paires discordantes sur 20 (30 %),
  après 25 % au run 25.
- Les deux runs (25 et 26) ne sont PAS agrégés : la règle pré-enregistrée s'applique à chacun séparément.

### 4.2 Machine cible (modèle de référence 7B) — UNVERIFIED

Non exécutée. Avec APEX démarré par le Panel : `python scripts/apex_experience_ab.py --replicates 4` dans la
distribution (résultats dans `~/apex-experience-ab/`).

**Aucune affirmation « APEX apprend » : aucune amélioration n'a été mesurée selon la règle ci-dessus.**

## 5. Suite

Audit des causes possibles de l'absence d'amélioration et protocole v2 (A/B/C) pré-enregistré, non exécuté :
`docs/APPRENTISSAGE_AUDIT_V2.md`. Le protocole v1 ci-dessus n'est pas modifié.
