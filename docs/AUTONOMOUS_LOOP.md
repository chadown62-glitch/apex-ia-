# APEX — Boucle autonome d'amélioration (Priorité 1)

## 0. Audit du code réel (avant toute ligne de code)

Point de départ : `03648ab`. Chaque constat ci-dessous a été vérifié dans le
code (lecture + recherche des appelants), pas dans la documentation.

### Ce qui existe et sera RÉUTILISÉ tel quel

| Composant | Où | Rôle dans la boucle |
|---|---|---|
| Modèle, tokenizer, moteur d'entraînement, checkpoints | `apex/neural/{model,tokenizer,training,checkpoint}.py` | exécution réelle d'une expérience (forward, loss, backward, optimizer) ; reprise bit-exacte |
| Dataset (provenance, dédup canonique, fuite par document, empreinte) | `apex/neural/dataset.py` | monde gelé : train / validation / held-out |
| Knowledge Agent | `apex/knowledge/` | construction des données réelles du monde |
| Évaluation BPB par document + `NeuralJudge` (StatTest apparié) | `apex/neural/evaluation.py` | cœur statistique du Judge indépendant |
| Registre des versions (chaîné, promotion sur ACCEPT) | `apex/neural/registry.py` | promotion ; à étendre avec le retour arrière |
| ArtifactStore local adressé par contenu | `apex/artifacts/local_filesystem.py` | conservation vérifiable des checkpoints (champion, candidats) |
| StatTest | `apex/stattest/` | via `NeuralJudge` |

### Ce qui existe mais ne convient PAS (et pourquoi)

| Composant | Constat | Décision |
|---|---|---|
| `apex/weakness` (`WeaknessDetector`) | classe des `EvalRun` (PASS/FAIL de tâches de code) ; aucun lien avec le modèle neuronal | non réutilisé ; même discipline (jamais de faiblesse sans preuve citable) |
| `apex/hypothesis` (`Hypothesis`, `HypothesisGenerator`) | hypothèse bâtie sur un `WeaknessReport` d'`EvalRun` ; sa propre docstring constate qu'« aucun consommateur réel (ExperimentEngine) n'existe » ; jamais appelé hors `taskgen` (qui le nomme) et ses tests | concept conservé (affirmation, prédiction, réfutation), type spécifique au modèle neuronal |
| `apex/metalearner/learning_orchestrator`, `autonomy`, `cycle` | boucles réelles, mais qui font évoluer des génomes/prompts de clones sur des tâches de code via `EvalRun` et une base SQL | autre objet d'apprentissage ; pas de second orchestrateur de génomes |
| `apex/improvement` (CI engine) | améliore le CODE d'APEX (mesure, juge épinglé, ledger) ; ne décide ni n'entraîne rien | non utilisé par la boucle ; la validation de la boucle passe par ses tests adversariaux et de mutation (§3) |
| `apex/memory` (`MemoryService`) | exige une base SQL ; conçu pour des connaissances (affirmations) | le journal d'expériences est la source de vérité ; export vers la mémoire traité comme extension, pas comme dépendance |
| `apex/neural/experiment.py` | script monolithique : chaîne fixe, corruption V2 codée en dur, aucun paramètre de modification | conservé pour les expériences manuelles ; la boucle a son propre exécuteur paramétré par un plan |

### Le gap réel

1. **Rien n'observe le modèle neuronal** : aucun code ne relit le champion,
   ses mesures par domaine, l'historique des expériences.
2. **Aucune hypothèse n'est jamais exécutée** : `HypothesisGenerator` n'a
   aucun consommateur ; rien ne transforme une faiblesse en expérience.
3. **Aucune expérience paramétrable** : `run_experiment` n'accepte ni
   baseline, ni modification, ni budget, ni critères.
4. **Aucune analyse d'échec pour le modèle neuronal** : un REJECT du
   `NeuralJudge` est une liste de raisons textuelles, rien ne l'exploite.
5. **Aucune stratégie adaptative** : rien ne choisit l'expérience suivante
   en fonction des précédentes.
6. **Aucune mémoire d'expériences** : les rapports JSON existent, mais rien
   ne sait « j'ai déjà essayé ça », ni pourquoi cela a échoué.
7. **Promotion sans retour arrière** : le registre ne sait que promouvoir ;
   les checkpoints ne sont conservés que par leur SHA-256 (fichiers dans un
   répertoire temporaire) : un retour au champion précédent est IMPOSSIBLE.
8. **Benchmark instable entre expériences** : chaque exécution reconstruit
   son dataset ; dès que les données changent, le Judge refuse (à raison) la
   comparaison. Une boucle a besoin d'un monde gelé : held-out, tokenizer et
   benchmarks protégés fixés au démarrage, jamais modifiables par une
   expérience.
9. **Aucune borne, aucun arrêt, aucune reprise** pour une boucle de ce type.

## 1. Architecture (`apex/neural/loop/`)

```
            ┌──────────── monde gelé (world.json, empreinte) ────────────┐
            │ dataset + splits train/validation/held-out, tokenizer,      │
            │ benchmarks protégés (heldout, validation, heldout:<domaine>)│
            └──────────────────────────────────────────────────────────────┘
OBSERVE ──> champion (registre + ArtifactStore, intégrité + ATTESTATION)
   │        faiblesses mesurées (écart d'une tranche held-out, preuve citée)
   │        mémoire (journal : succès, échecs, causes)
   ▼
STRATÉGIE ─> hypothèse falsifiable (cible, direction, suites protégées, critère de réfutation)
   │         UNE modification par rapport à la recette de référence ; déduplication par empreinte
   ▼
PLAN ──────> objectif, référence, modification, données (mélange, tokens, graine), budget,
   │         conditions d'arrêt, critères d'acceptation/rejet — validé, journalisé AVANT exécution
   ▼
EXÉCUTION ─> vrai entraînement (TrainingEngine), checkpoints de reprise, arrêt/délai/budget
   │         par pas, checkpoint final canonique -> ArtifactStore (adressé par contenu)
   ▼
JUDGE INDÉPENDANT ─> intégrité, contamination, mesure double, mesures déclarées = mesures
   │                 du Judge, référence inchangée, candidat ≠ référence, données déclarées
   │                 = recette, REJEU bit à bit, puis NeuralJudge (bootstrap apparié)
   ├─ ACCEPT ─> registre (refait la comparaison au champion) -> promotion
   └─ REJECT ─> analyse d'échec (ESTABLISHED / UNKNOWN, attribution, INFERRED séparé)
                -> stratégie suivante (ou arrêt si alerte d'intégrité)
```

| Module | Responsabilité |
|---|---|
| `world.py` | monde gelé : construction via le Knowledge Agent, empreinte, sauvegarde/relecture vérifiée, flux d'entraînement tiré du SEUL split train (manifeste des documents utilisés) |
| `observe.py` | état du champion (relu depuis l'ArtifactStore), faiblesses mesurées, résumé de la mémoire |
| `plan.py` | `Recipe`, `LoopHypothesis` (refus des hypothèses non falsifiables), `ExperimentPlan` (empreinte déterministe, validation : budget, domaines, UNE modification) |
| `strategy.py` | politique adaptative : première tentative informée par la mémoire, adaptation par cause, déduplication, nouvelles tentatives bornées |
| `execute.py` | exécution réelle, reprise depuis `resume.pt`, contrôle arrêt/délai/budget à chaque pas |
| `judge.py` | Judge indépendant (ne reçoit que des références et des affirmations) |
| `failure.py` | analyse d'échec : cause établie ou inconnue, jamais inventée |
| `journal.py` | mémoire d'expériences : journal JSONL chaîné par hachage, append-only, vérifié à chaque lecture |
| `controller.py` | boucle bornée, verrou, reprise, attestation du champion, retour arrière, comparaison |
| `apex/neural/registry.py` | promotion (comparaison statistique au champion) et retour arrière avec versions RETIRÉES |
| `scripts/apex_autonomous_loop.py` | CLI : `init-world`, `run`, `status`, `stop`, `rollback` |

### Garanties et où elles sont vérifiées

| Garantie | Mécanisme | Test |
|---|---|---|
| jamais de promotion sans ACCEPT du Judge | `_promote` ; registre re-compare ; attestation du champion au démarrage de chaque cycle | `test_rejected_verdict_never_reaches_the_registry`, `test_champion_promoted_without_the_judge_is_withdrawn`, `test_loop_invariants_on_a_real_run` |
| aucune fuite train/éval | flux tiré du split train ; Judge : ids déclarés ⊆ train, = ids de la recette, rejeu bit à bit | `test_token_stream_…`, `test_declared_evaluation_documents_are_contamination`, `test_candidate_secretly_trained_on_the_benchmark_is_unmasked` |
| mesures non falsifiables | le Judge mesure lui-même, deux fois | `test_falsified_metrics_are_rejected` |
| référence non modifiée après coup | mesures du champion au registre = mesures du Judge aujourd'hui | `test_baseline_modified_after_the_fact_is_detected` |
| jamais deux fois la même expérience | déduplication (politique) + garde du contrôleur | `test_policy_adapts_…`, `test_duplicate_plan_is_refused_by_the_controller` |
| boucle bornée | `LoopLimits` (cycles, expériences, pas, tokens, modifications, délai, nouvelles tentatives) | `test_limits_stop_the_loop`, `test_timeouts_are_bounded_and_never_promoted` |
| reproductibilité | graines, checkpoint canonique (même contenu -> mêmes octets) | `test_loop_is_reproducible`, `test_resume_after_stop_is_bit_identical` |
| arrêt propre, crash, reprise | journal avant/après chaque étape, `resume.pt`, reprise à la dernière étape consignée | `test_stop_request_then_resume_…`, `test_crash_mid_experiment_is_recovered`, `test_experiment_planned_but_never_started_is_recovered` |
| retour arrière | intégrité + attestation du champion à chaque observation ; versions retirées jamais restaurées | `test_corrupted_champion_triggers_rollback`, `test_registry_rollback_…` |
| un seul écrivain | verrou `fcntl` (boucle et retour arrière manuel) | `test_single_controller_per_directory` |
| monde figé | empreinte vérifiée au chargement ; journal d'un autre monde refusé | `test_world_round_trip_and_tamper_detection`, `test_journal_from_another_world_is_refused` |

## 2. Les trois cycles de développement

| Cycle | Contenu | Défauts trouvés et corrigés |
|---|---|---|
| 1 — implémentation | monde gelé, journal chaîné, plan, exécution, Judge, analyse d'échec, stratégie, contrôleur borné, CLI ; tests de composants et de bout en bout sur un monde réel réduit | l'amorçage rejeté était relancé à l'identique (pas de déduplication) -> amorçage unifié dans la politique ; pas consommés non comptés quand une expérience échouait -> progression consignée ; délai non réessayable -> `timeout` réessayable (borné) ; Judge trop lent (4 mesures) -> référence mesurée une fois ; checkpoints non reproductibles (identifiant aléatoire dans le checkpoint) -> identité = empreinte du plan |
| 2 — attaque | 17 tests adversariaux (exécuteur tricheur, mesures falsifiées, contamination, sous-déclaration des données, candidat identique, référence embellie, checkpoint corrompu, délai de rejeu, arrêt pendant le rejeu, promotion forgée, doublon, reprise d'un plan jamais démarré, STOP, délais en boucle, reprise bit à bit, promotion sans ACCEPT) ; 25 mutants sur les gardes | un exécuteur entraînant sur le held-out mais déclarant la recette honnête passait tous les contrôles -> REJEU bit à bit par le Judge + données déclarées = recette ; une promotion écrite directement au registre était acceptée comme champion -> ATTESTATION par le journal ; le retour arrière manuel pouvait écrire pendant une boucle -> verrou partagé ; doublon possible hors politique -> garde du contrôleur ; reprise non bit-exacte (mémo `pickle` par identité) -> sérialisation canonique ; 1 mutant survivant (déduplication) -> test associé identifié |
| 3 — robustesse et démonstration | exécution réelle complète avec SIGKILL pendant l'entraînement puis redémarrage, second passage indépendant, retour arrière et verrou sur une copie | l'interprétation INFERRED d'un échec invoquait « le mélange de données » alors que la seule modification était le taux d'apprentissage (cause inventée) -> interprétation construite à partir de la modification réelle, test de non-régression ; sélection du meilleur non explicite -> `selection` consignée à chaque cycle |

## 3. Validation

- Tests de la boucle : `tests/test_loop_components.py` (19), `tests/test_loop_adversarial.py` (17),
  `tests/test_loop_controller.py` (8) — entraînements réels sur un monde réduit (docstrings de
  28 modules de la bibliothèque standard + documentation du dépôt, via le Knowledge Agent).
- Suite complète du dépôt (`pytest tests`, CPU, sans PostgreSQL ni Docker) : 3247 tests,
  3068 réussis, 4 échecs, 175 ignorés (PostgreSQL/Docker absents), 0 erreur. Les 4 échecs
  (`tests/test_runtime_packaging.py` : migration citant `apex.metalearner`, contrat JSON du doctor)
  sont identiques sur le commit de départ `03648ab`, antérieurs à cette mission et hors de son
  périmètre.
- Lint (`ruff check`, `ruff format --check`) et types (`mypy`) : propres sur les 17 fichiers de la
  mission.
- Mutation (script hors dépôt, un mutant à la fois, sources restaurées et vérifiées par hachage) :
  25 gardes affaiblies (Judge : contamination ×2, mesures déclarées, identité, référence, rejeu,
  données déclarées, REJECT forcé, intégrité ; analyse : cause explicite ; contrôleur : attestation,
  doublon, nouvelles tentatives, promotion sans ACCEPT, STOP, reprise ; exécution : délai, reprise ;
  checkpoint canonique ; stratégie : déduplication, nouvelles tentatives bornées ; monde : fuite
  validation -> train ; registre : versions retirées ; journal : chaînage ; plan : une modification).
  Premier passage 24/25 ; le survivant (déduplication de la politique) était tué par un test existant
  non associé ; après correction de l'association : **25/25 tués**, 25/25 mutants compilent.

## 4. Démonstration de bout en bout (données réelles)

Monde gelé `sha256:d3f640fe…` : docstrings de la bibliothèque standard (domaine `stdlib`) et
documentation du dépôt (domaine `apex_docs`) acquises par le Knowledge Agent ; modèle 3 couches,
128 dimensions, vocabulaire 512 ; CPU, 4 cœurs. Commande :

```
.venv/bin/python scripts/apex_autonomous_loop.py init-world --workdir W
.venv/bin/python scripts/apex_autonomous_loop.py run --workdir W --bootstrap stdlib=1.0 \
    --max-cycles 3 --max-experiments-per-cycle 3 --max-total-experiments 8
# SIGKILL pendant l'entraînement de la 2e expérience, puis la même commande relancée
```

| # | expérience | parent | stratégie | modification | cible : champion -> candidat (IC 95 % du gain) | Judge | cause (statut) | promu |
|---|---|---|---|---|---|---|---|---|
| 1 | exp-c7a8457c10ec | — | bootstrap | init=scratch, mélange stdlib | heldout 5.391 -> 4.107 [+1.145, +1.542] | REJECT | protected_regression sur heldout:apex_docs (établie) | non |
| 2 | exp-7dcb18141ecb | 1 | protect_domains | mix apex_docs 25 % / stdlib 75 % | heldout 5.391 -> 3.244 [+2.116, +2.245] | ACCEPT | — | **oui** (champion) |
| 3 | exp-0063878f1149 | — | target_only | mix apex_docs 100 % | heldout:apex_docs 3.549 -> 2.954 [+0.440, +0.596] | REJECT | protected_regression : heldout, heldout:stdlib, validation (établie) | non |
| 4 | exp-a8ad53779589 | 3 | protect_domains | stdlib 25 % | heldout:apex_docs 3.549 -> 3.128 [+0.330, +0.423] | REJECT | protected_regression : heldout:stdlib, validation (établie) | non |
| 5 | exp-88510ece71b1 | 4 | protect_domains | stdlib 50 % | heldout:apex_docs 3.549 -> 3.488 [+0.059, +0.202] | REJECT | protected_regression (établie) | non |
| 6 | exp-8f0d6f411fe7 | 5 | protect_domains | stdlib 75 % | heldout:apex_docs 3.549 -> 3.642 [−0.104, −0.023] | REJECT | target_regression (établie) | non |
| 7 | exp-c95c11cab6e8 | 6 | lower_lr | lr 1e-3 -> 5e-4 | heldout:apex_docs 3.549 -> 3.568 [−0.030, +0.010] | REJECT | target_regression (établie) | non |
| 8 | exp-ac61168a1b9f | 7 | lower_lr | lr 5e-4 -> 2.5e-4 | heldout:apex_docs 3.549 -> 3.530 [+0.006, +0.026] | REJECT | protected_regression : heldout:stdlib (établie) | non |

- **Crash réel** : SIGKILL au cours de l'expérience 2 ; au redémarrage, reprise depuis `resume.pt`
  (pas 50), puis ACCEPT ; le REJEU du Judge, depuis zéro, donne un checkpoint identique bit à bit au
  candidat repris.
- **Aucune fuite** : contrôle de contamination et rejeu bit à bit OK pour les 8 expériences.
- **Comparaison et sélection** : à chaque cycle, tableau comparatif et `selection` (champion conservé ;
  meilleur candidat rejeté cité : exp-0063878f1149, 2.954 bpb sur apex_docs, rejeté pour régression
  protégée).
- **Arrêt propre** : plafond de 8 expériences ; journal de 59 entrées, chaîne vérifiée.
- **Reproductibilité** : deux exécutions indépendantes (crash à des moments différents) produisent
  les mêmes empreintes de plan et les mêmes artefacts pour les 8 expériences.
- **Retour arrière** (copie du répertoire) : checkpoint du champion altéré sur disque -> détecté
  (`ArtifactIntegrityError`), retour arrière tenté, aucune version antérieure -> `rollback_failed` et
  arrêt propre sans entraîner sur une base corrompue. Retour arrière manuel pendant qu'une boucle tient
  le verrou -> `LoopBusyError`. La restauration effective d'une version antérieure est démontrée par
  `test_corrupted_champion_triggers_rollback` (entraînement réel).

Résultat scientifique : le champion (mélange 25/75) est promu. La faiblesse mesurée sur
`apex_docs` (3.55 bpb contre 3.24 en moyenne) n'a pas pu être réduite sans dégrader une suite
protégée : c'est un résultat négatif légitime, pas un échec de la boucle.

## 5. Limites

| Catégorie | Élément |
|---|---|
| démontré | boucle complète sur données réelles ; ACCEPT, REJECT, stratégie après rejet, expérience issue de cette stratégie, comparaison, non-promotion des rejets, reprise après SIGKILL, absence de fuite, reproductibilité bit à bit, arrêt borné, retour arrière sur champion corrompu, verrou |
| implémenté, non démontré sur l'exécution réelle | restauration effective d'une version antérieure hors test (une seule promotion dans la démonstration) ; nouvelles tentatives après cause inconnue (seulement en test) |
| bloqué par l'environnement | GPU absent (CPU seulement) ; PostgreSQL et Docker non lancés : la mémoire SQL (`MemoryService`) n'est pas alimentée par le journal |
| hors périmètre / limites connues | le journal est chaîné par hachage mais non signé : une réécriture COMPLÈTE de la chaîne par un acteur ayant accès au disque n'est pas détectée ; le rejeu double le coût de calcul du jugement ; l'espace des stratégies est volontairement petit (mélange, pas, taux d'apprentissage) ; le déterminisme bit à bit est vérifié sur une même machine et une même version de PyTorch (CPU), pas entre machines |
