# Protocole v2 (A/B/C) — implémenté, NON exécuté

Rédigé le 05/10/2026. Question : **donner au modèle des faits structurés sur l'échec de sa tentative précédente
améliore-t-il causalement la réussite de la tentative suivante ?** Conclusions possibles, décidées par la règle
du §7 et par elle seule : amélioration mesurée ; régression mesurée ; aucune différence démontrée (puissance
suffisante) ; protocole insuffisant pour conclure. Le protocole est conçu pour trancher, pas pour produire un
résultat positif.

Pré-enregistrement d'origine : `docs/APPRENTISSAGE_AUDIT_V2.md` §2–§3 (inchangé). Les écarts d'implémentation
sont listés au §4, tous décidés **avant toute donnée v2** (il n'en existe aucune). Les runs 25 et 26 (protocole v1)
restent des expériences distinctes ; leurs résultats et leur interprétation ne sont pas modifiés.

## 0. État — ce qui est prêt et ce qui ne l'est pas

| Élément | Statut |
|---|---|
| Bras A, B, C, température de base explicite, faits autorisés, provenance | Implémenté — **TESTÉ** (doublure de modèle, vraie boucle/sandbox/Judge) |
| Correction de la classification (§4 b) | Implémentée — **TESTÉ** |
| 10 tâches nouvelles, validation indépendante | Implémentées — **TESTÉ** (solution juste PASSE, piège ÉCHOUE, vraie sandbox) |
| Script calibration / expérience / analyse | Implémenté — **TESTÉ** (enchaînement de bout en bout avec une doublure) |
| Validation de la machine cible (7B, RTX 4060 Ti) | **UNVERIFIED** — jamais exécutée (exige la machine de l'utilisateur) |
| Calibration RUN 1 (8 tirages × 15 tâches) | **BLOCKED** tant que la machine cible n'est pas validée (primaire) ; non lancée |
| Expérience principale A/B/C | **NON LANCÉE.** Le script refuse `run --label primaire` sans rapport de validation de la machine cible (`--target-validated`) |
| Effet de C ou de B sur un vrai modèle | **À MESURER** — aucune donnée |

L'expérience v2 n'est **pas** prête à être mesurée sur le 7B : il faut d'abord la validation machine cible
(`docs/APPRENTISSAGE_AUDIT_V2.md` §0). Une réplication 1.5B en CI resterait une réplication, rapportée à part,
jamais une preuve pour le 7B.

## 1. Bras (tels qu'implémentés)

Chaque instance : une tâche NEUVE, un RUN 1 (`base_temperature = T0 = 0,7`, 1 tentative, 1 candidat), puis — si
le RUN 1 est éligible — trois RUN 2 sur la même tâche, chacun 1 tentative :

| Bras | Requête TaskRun | Ce que le modèle reçoit |
|---|---|---|
| A | `base_temperature: 0.7`, aucun `condition_on_run` | l'énoncé seul, T = 0,7 |
| B | `condition_on_run: RUN1`, `strategy: "rule"` | la règle actuelle `decide_strategy_from_experiences`, INCHANGÉE (température et rappel générique qu'elle choisit) |
| C | `condition_on_run: RUN1`, `strategy: "failure_facts"`, `base_temperature: 0.7` | l'énoncé + le texte du §2, T = 0,7 |

`strategy: "failure_facts"` sans `base_temperature` est refusé (la température de C n'est jamais implicite).

### Audit des différences cachées entre bras

| Paramètre | A vs C | Preuve |
|---|---|---|
| Requête au modèle (`GenerationRequest` : endpoint, `max_tokens`, `temperature`, `timeout_s`) | identique sauf `prompt` | **TESTÉ** : `test_a_and_c_differ_only_by_the_allowed_facts_suffix` compare champ par champ |
| Température | 0,7 explicite des deux côtés | **TESTÉ** (0,0 / 0,7 / 1,3 transmis exactement) ; contrôlé à l'exécution par le script |
| Fournisseur, modèle | même routage, mêmes candidats ; le script arrête tout si le modèle diffère de la calibration | code lu ; contrôle à l'exécution |
| Paramètres Ollama | `/api/generate` reçoit `model`, `prompt`, `stream`, `options{num_predict, temperature}` : ni graine, ni `context`, ni prompt système | code lu (`apex/models/ollama.py`) |
| Contexte | sans état : aucun `context` Ollama ; chaque TaskRun est indépendante | code lu |
| Ordre des tâches | tourniquet fixe sur les tâches retenues, fonction du seul rang du RUN 1 | **TESTÉ** |
| Ordre des bras | carré latin ABC / BCA / CAB tournant par instance (équilibre l'effet modèle « chaud ») | **TESTÉ** |
| Graine | aucune, pour aucun bras (échantillonnage stochastique à T = 0,7, identique pour tous) | code lu |
| Délai, reprises | mêmes `max_wall_clock_s`, `generation_timeout_s`, 1 tentative, 0 reprise, 1 candidat | code du script |

B diffère de A par le texte ET la température : c'est la règle existante, comparée telle quelle (définition
documentée inchangée).

## 2. Bras C — faits autorisés, texte figé (`apex/metalearner/failure_facts.py`, `PROMPT_VERSION = "v2-facts-1"`)

Liste fermée (`ALLOWED_FIELDS`) : `failure_category`, `failed_cases`, `total_cases`, `exception_class_count`,
`exception_classes`, `script_terminated`, `exit_code`. Sources : la catégorie de l'`Experience` du RUN 1 ; le
décompte « k/N cas incorrects » du détail de l'Oracle (SANS les indices) ; les noms de classes des exceptions
publiées par le harnais — **classes natives de Python seulement** ; une classe définie par le candidat devient
« classe non standard » (son nom est écrit par le modèle et pourrait même être une entrée cachée) ; le statut
sandbox ; le code de sortie. Aucun message d'exception, aucune entrée ou sortie de cas, aucun indice, aucune valeur
publiée, aucun code généré ne peut sortir du module.

Texte (observations, aucune consigne), ajouté après l'énoncé :

> Observations factuelles sur une tentative précédente à cette même tâche (jugée par des tests cachés) :
> catégorie d'échec : {libellé} ; cas en échec : {k} sur {N} ; classes d'exception levées : {nombre}{ (Classe, …)} ;
> script terminé normalement : {oui|non} ; code de sortie : {code}.

Libellés : `incorrect_solution` → « résultats incorrects » ; `invalid_candidate` → « exception ou résultat
illisible ». Autre catégorie (succès, infrastructure, génération, ...) : C ne transmet RIEN (requête identique à A).
Valeur absente → « non disponible », jamais une estimation.

Provenance enregistrée dans l'`Experience` du RUN 2 C (`generation_config`) : `failure_facts` (les valeurs
exactes), `prompt_version`, `source_eval_run_id` (EvalRun du RUN 1), `prompt_suffix` (texte exact) ; parent =
l'`Experience` du RUN 1. L'API expose aussi, pour chaque EvalRun, `failure_category` et `failure_facts` recalculés :
le script vérifie que les faits reçus par C sont exactement ceux du RUN 1.

## 3. Tâches

15 tâches : `is_palindrome`, `rle_encode`, `count_vowels`, `word_count`, `second_largest` (v1) + `roman_to_int`,
`merge_intervals`, `luhn_valid`, `balanced`, `camel_to_snake`, `median`, `chunk`, `parse_duration`, `title_case`,
`dedupe_keep_last` (ajoutées le 05/10/2026, AVANT toute calibration). Même contrat : une contrainte précise par
énoncé, 8 cas cachés, réponses comparées hors sandbox ; l'exemple de l'énoncé n'est jamais un cas caché.
Validation indépendante : `tests/test_protocol_v2_tasks.py` (solution écrite à la main PASSE, solution tombant
dans le piège ÉCHOUE, vraie sandbox, vrai Judge). Cela prouve que les tâches sont bien formées, pas leur difficulté
pour un modèle : celle-ci est MESURÉE par la calibration.

## 4. Écarts par rapport au pré-enregistrement (tous antérieurs à toute donnée v2)

a. **Texte de C** : gabarit « Observations factuelles … » au lieu de « Information sur une tentative … » ; il
   ajoute le nombre de classes, la terminaison du script et le code de sortie (liste autorisée par la demande du
   05/10/2026) et exclut les noms de classes non natives (fuite possible).
b. **Classification** (`apex/metalearner/failure_taxonomy.py`, `_contract_category`) : une réponse dont UNE
   PARTIE des cas lève une exception, les autres renvoyant des valeurs, est désormais `incorrect_solution` ;
   `invalid_candidate` est réservé à « tous les cas lèvent » ou au contrat de résultat violé. Correction de
   validité de l'évaluation (avant : une seule exception sur 8 cas classait toute la réponse « candidat
   invalide »), pas une optimisation pour C. Le format historique (`_harness_summary`) garde sa règle.
   Effets : la catégorie est calculée UNE fois, à la création de l'`Experience` ; les Experience existantes
   (runs 25/26 compris) gardent la leur, rien n'est réinterprété. Pour les échecs partiellement levés, la règle
   B reçoit maintenant `incorrect_solution` (T + 0,4 et rappel « résultats incorrects ») au lieu de
   `invalid_candidate` (T × 0,3 et rappel de format) : la définition de B est inchangée, sa catégorie d'entrée
   est corrigée. Comparer B v2 à B v1 doit en tenir compte.
c. **Température de C** obligatoire (refus sinon).
d. **Erreur d'infrastructure d'un RUN 2** (instance exclue des comparaisons) : aucun EvalRun, ou catégorie
   `infrastructure_failure` ou `generation_failure` (aucun candidat jugé). `timeout`, `execution_failure`,
   `resource_failure`, `policy_security_rejection` sont des échecs JUGÉS.
e. **Calibration** : 8 tirages JUGÉS par tâche ; un tirage d'infrastructure est re-tiré (au plus 4 fois par tâche),
   sinon la tâche n'est pas retenue.
f. **Intégrité** : chaque TaskRun est contrôlée à l'exécution (température, absence de texte pour A et le RUN 1,
   conditionnement sur le RUN 1 de l'instance, stratégie, gabarit, faits identiques à ceux du RUN 1, texte de C
   égal au gabarit, texte de B égal à l'un des deux rappels constants de la règle, aucune entrée cachée ni réponse
   attendue dans le texte, modèle de la calibration). Une chaîne déjà publique n'est pas comptée comme entrée
   cachée (« Python » est une entrée cachée de `count_vowels` et un mot de chaque énoncé). Une violation
   arrête l'expérience ; l'analyse conclut alors « protocole insuffisant ». Idem si des modèles ou labels sont
   mélangés, si une instance est incomplète ou si la règle d'arrêt n'est pas atteinte.
g. **Expérience primaire** refusée sans rapport de validation de la machine cible.

## 5. Procédure

```
python scripts/apex_experience_v2.py calibrate --label primaire --out ~/apex-v2   # RUN 1 seuls
python scripts/apex_experience_v2.py run --label primaire --target-validated <rapport> --out ~/apex-v2
python scripts/apex_experience_v2.py analyse --out ~/apex-v2
```

Calibration : sont retenues les tâches réussies 1 à 6 fois sur 8 (jamais de RUN 2 à ce stade). Expérience : RUN 1
en tourniquet ; instance éligible si le RUN 1 échoue en `incorrect_solution` ou `invalid_candidate` ; bras en
carré latin ; arrêt à 60 instances éligibles ou 240 RUN 1 — la règle ne lit que des RUN 1. Reprise possible
(relancer `run` complète l'instance interrompue). Aucune analyse intermédiaire.

## 6. Mesures enregistrées (`runs.jsonl`, une ligne par TaskRun)

Tâche, label, bras, instance, task_id, run_id, modèle, `condition_on_run`, stratégie, température demandée,
configuration de génération appliquée (température, texte exact), version du gabarit, faits transmis et leur
source (`source_eval_run_id`, expérience parente), verdict du Judge, réussite, verdict et détail de l'Oracle,
catégorie d'échec, faits de l'EvalRun, code de sortie, isolation de la sandbox, durée de génération, durées Ollama
(chargement, évaluation du prompt, génération), jetons, empreinte de la solution, Experience créée, code généré ;
pour chaque RUN 2, l'état de la machine juste après. Machine (début, calibration, fin, après chaque RUN 2) : CPU,
nombre de cœurs, RAM totale et disponible, GPU, pilote, VRAM utilisée et totale (`nvidia-smi`), modèles chargés
par Ollama, taille chargée et parts VRAM/RAM (`/api/ps`). Toute mesure indisponible vaut `null` — jamais une
estimation.

## 7. Analyse (`analyse`) et décision

Instances complètes (A, B, C jugés). Pour C contre A et B contre A : paires, discordantes, concordantes, gains,
pertes, réussites par bras, différence, p de McNemar exact bilatéral, p ajusté de Holm (2 tests, α global 0,05),
décision :
- p ajusté < 0,05 et gains > pertes → **amélioration mesurée** ;
- p ajusté < 0,05 et pertes > gains → **régression mesurée** ;
- sinon, au moins 20 paires discordantes → **aucune différence démontrée** ;
- sinon (moins de 20 discordantes, plus de 10 % d'instances exclues pour infrastructure, moins de 60 éligibles au
  plafond de 240 RUN 1, ou intégrité compromise — §4 f) → **protocole insuffisant pour conclure**.

« APEX apprend de ses expériences » ne pourra être écrit que sur une amélioration mesurée avec le modèle primaire.

## 8. Tests (doublure de modèle : composition et intégrité prouvées, aucun effet mesuré)

- `tests/test_protocol_v2.py` : A et C ne diffèrent que par le texte ; B = règle inchangée ; température de C
  exacte ; faits et provenance ; aucune entrée cachée, réponse, indice, message ou code dans le texte ;
  classes non natives masquées ; observations sans impératif ; C sans fait si la catégorie n'est pas couverte ; le
  Judge décide seul ; validation des requêtes et de l'API.
- `tests/test_protocol_v2_tasks.py` : 15 tâches, validation indépendante des 10 nouvelles.
- `tests/test_experience_v2_script.py` : constantes figées, carré latin, arrêt, McNemar, Holm, décisions, parseurs
  machine sans invention, contrôles d'intégrité (8 défauts injectés), calibration → expérience → analyse de bout
  en bout, refus de l'expérience primaire sans validation.
- `tests/test_failure_taxonomy.py` : sémantique corrigée (§4 b).
- Mutations vérifiées (chacune fait échouer au moins un test, puis code restauré) : message d'exception transmis ;
  nom de classe non native transmis ; température de C modifiée ; indices ajoutés au texte ; consigne ajoutée ;
  code précédent ajouté ; texte ajouté au bras A ; réussite décidée sans le Judge ; retour à l'ancienne
  classification.

## 9. Problèmes hors périmètre signalés (non corrigés)

- La CI n'exécute ni `ruff` ni `mypy` ; le dépôt contient déjà des écarts (lignes longues, `rowcount` dans
  `apex/taskloop/runs.py`). Les nouveaux fichiers ne gardent que des lignes longues dans des littéraux, docstrings et commentaires.
- Aucune graine n'est transmise à Ollama : les tirages ne sont pas reproductibles à l'identique (c'est voulu pour
  mesurer une distribution, mais une réexécution ne redonnera pas les mêmes réponses).
- `keep_alive` n'est pas fixé : le modèle peut être déchargé entre deux TaskRuns lentes (durées de chargement
  enregistrées, effet sur les réponses : aucun attendu ; le carré latin équilibre l'ordre).
