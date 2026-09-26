# Continuous Improvement Engine

Moteur générique (`apex/improvement/`) qui applique à une capacité APEX la
boucle : baseline, modification, mesure, jugement externe, debug, verdict,
historique. Il ne modifie jamais de code lui-même : un implémenteur change
le code entre deux mesures, le moteur décide si le changement est une
amélioration.

## Ce qui est réutilisé (aucune seconde architecture)

| Besoin | Composant existant réutilisé |
|---|---|
| Agrégation du juge, véto, juge en panne jamais PASS | `apex.metalearner.gauntlet.GauntletRunner.run_stage` |
| Vocabulaire des preuves du juge | `apex.metalearner.judge_model` (`JudgeResult`, `JudgeFinding`, `JudgeEvidence`) |
| Significativité d'une différence bruitée | `apex.stattest.StatTest.compare_paired` (bootstrap apparié) |
| Robustesse d'un candidat (ré-exécution) | `apex.metalearner.judges.RobustnessJudge` |

## Ce qui a été ajouté (manquait à l'audit)

- `CapabilitySpec` : description statique d'une capacité (composants,
  dépendances, préconditions, tests, métriques, critères, risques).
- `EvidenceLevel` : OBSERVED, TESTED, MEASURED, JUDGED, INFERRED,
  UNVERIFIED, BLOCKED — chaque cycle produit une liste d'`Evidence` typées.
- `ImprovementVerdict` : IMPROVED, UNCHANGED, REGRESSED, BLOCKED,
  INCONCLUSIVE, FAILED.
- `Finding` avec `HypothesisStatus` : PROBABLE, CONFIRMED, REJECTED.
- `ImprovementLedger` : historique append-only (JSON Lines).

## Cycle de vie

```
measure(V0)   -> snapshot mesuré DEUX fois ; reproductibilité constatée
[implémenteur modifie le code]
run_cycle(kind, before=V0, version_after=V1, claim=...)
   1. mesure V1
   2. comparaison métrique par métrique (measure.py)
   3. juge externe : sondes de réfutation du type de cycle -> GauntletRunner
   4. debugger : ré-exécution indépendante de chaque sonde qui réfute
   5. verdict combiné -> ledger
```

Types de cycle (`CycleKind`) : FUNCTIONALITY (est-ce que ça marche
réellement ?), ADVERSARIAL (comment le casser ?), IMPROVEMENT (qu'est-ce qui
peut encore être meilleur ?). Chaque sonde déclare les types de cycle
auxquels elle appartient.

## Règles de verdict (stop criteria)

Par métrique (`measure.compare_metric`) :
- métrique bloquée ou absente -> BLOCKED ;
- moins de `min_samples` échantillons -> INCONCLUSIVE ;
- métrique DÉTERMINISTE : comparaison exacte, seulement si les deux
  snapshots sont reproductibles (sinon INCONCLUSIVE) ;
- métrique STOCHASTIQUE : `StatTest` ; non significatif -> UNCHANGED.

Agrégation : REGRESSED > BLOCKED > INCONCLUSIVE > IMPROVED > UNCHANGED.

Combinaison avec le juge (`engine.combine_verdict`) : une régression mesurée
prime ; sinon un REJECT du juge donne FAILED même si les métriques
progressent ; un juge INCONCLUSIVE empêche de confirmer un gain.

Tests verts ne signifie jamais IMPROVED : trois cycles sans gain mesurable
donnent UNCHANGED.

## Séparation implémenteur / juge

Qui fait quoi dans un cycle :

| Élément | Produit par | Ce que l'implémenteur ne peut pas faire |
|---|---|---|
| Version candidate | l'implémenteur (code + libellé de version) | — |
| Métriques | `CapabilitySpec` de l'adaptateur | changer leur définition entre avant et après : l'empreinte (`definition_digest`) diffère -> INCONCLUSIVE |
| Mesures | `ContinuousImprovementEngine.measure` (deux fois) | fournir un snapshot : `run_cycle` exige un snapshot mesuré par le moteur ET présent dans le ledger |
| Résultats de sondes | le juge (`Judge.assess`) | — |
| Décision du juge | le moteur, via `GauntletRunner.run_stage` | un juge ne rend que des résultats par sonde, jamais un PASS global |
| Verdict IMPROVED/... | `combine_verdict` (métriques + juge) | son affirmation (`ImplementerClaim`) est enregistrée, jamais crue |
| Ledger | le moteur | le modifier sans que `verify` le signale (sauf recalcul complet de la chaîne, voir limites) |

- `author_id == judge_id` lève `SelfConfirmationError` avant toute mesure.
- `SubprocessJudge` exécute le juge dans un autre processus ; avec
  `--judge-ref R`, la définition du juge est lue à la révision git R : le
  changement jugé ne peut pas réécrire ses propres sondes. La provenance
  consignée ne contient que des faits vérifiés (empreinte de la définition,
  R ancêtre de HEAD ou non, définition différente de l'arbre de travail).
- Limite : sondes et correctifs restent écrits par le même agent. Pour le
  cycle 2, un agent indépendant (sans accès en écriture) a été chargé de
  réfuter le correctif ; il y est parvenu, voir plus bas.

## Invariants (tests/test_improvement_invariants.py)

Le moteur refuse ou ne convertit jamais en IMPROVED/PASS : une amélioration
sans mesure, une simple affirmation de l'implémenteur, un juge en panne, une
métrique non reproductible, un succès non confirmé utilisé comme confirmé
(sonde de la capacité), une baseline absente, deux environnements
incompatibles, un benchmark redéfini, une régression masquée par un gain
ailleurs, un INCONCLUSIVE. Chaque garde a été retirée une à une pour
vérifier que le test correspondant échoue.

## Utilisation

```bash
L=docs/improvement/generated_solution.ledger.jsonl
python -m apex.improvement measure --capability generated_solution --version v0 --ledger $L
python -m apex.improvement judge   --capability generated_solution --version v0 --kind adversarial --author me --ledger $L
# ... modifier le code ...
python -m apex.improvement cycle   --capability generated_solution --kind adversarial --index 1 \
    --before v0 --version v1 --author me --claim improved --hypothesis "..." --change "..." --test "..." --ledger $L
python -m apex.improvement report  --capability generated_solution --ledger $L
python -m apex.improvement verify  --capability generated_solution --ledger $L
# juge dans un processus séparé, définition figée au commit de la baseline :
python -m apex.improvement cycle ... --judge-ref <commit-baseline>
```

Ajouter une capacité : écrire un adaptateur (`spec` + `measure(version)`)
et un juge (`ExternalJudge` avec des `FunctionProbe`), puis l'enregistrer
dans `CAPABILITIES` (`apex/improvement/__main__.py`).

## Première capacité : Generated-Solution Evaluation Loop

Choisie parce que c'est le seul chemin qui traverse réellement TASK ->
MODEL -> SOLUTION -> SANDBOX -> ORACLE -> EXPERIENCE -> DÉCISION SUIVANTE,
avec une preuve causale d'apprentissage déjà existante
(`tests/test_adaptive_solution_loop.py`).

Conditions de mesure : sandbox `LinuxNamespaceSandbox` NAMESPACED réel,
EvalRunner/oracle/Experience réels, base SQLite en mémoire, **modèle =
double scripté** (aucun Ollama joignable). Ce qui est mesuré est la
capacité d'APEX à juger et à apprendre, pas la qualité d'un modèle.

| Métrique | Baseline | Cycle 1 | Cycle 2 | Cycle 3 |
|---|---|---|---|---|
| false_positive (candidats non solutions acceptés) | 0.44 | 0.44 | **0.00** | 0.00 |
| false_negative | 0.00 | 0.00 | 0.00 | 0.00 |
| verdict_accuracy | 0.67 | 0.67 | **1.00** | 1.00 |
| cross_run_isolation | 0.50 | **1.00** | 1.00 | 1.00 |
| learning_gain | 0.50 | 0.50 | 0.50 | 0.50 |
| flaky_acceptance (64 essais) | — | — | 0.45 | **0.08** |

Cycle 4 (même benchmark étendu, 16 essais flaky) :

| Métrique | Avant (`277edb4`) | Après (`f58b7f3`) |
|---|---|---|
| unconfirmed_success_exploited | 1.00 | **0.00** |
| confirmed_success_exploited (garde-fou) | 1.00 | 1.00 |
| flaky_acceptance | 0.06 | 0.19 (non significatif, IC 95 % [-0.13, 0.38]) |
| autres métriques | inchangées | inchangées |

- **Cycle 1 (functionality)** : la G1 d'une exécution lisait les
  expériences de toutes les exécutions de la tâche (trace de provenance
  fausse) ; la boucle n'avait aucun appelant hors tests. Correctifs :
  `experiment_ref` propre à chaque exécution, commande `apex solve`.
  Verdict IMPROVED (`79bdbec`).
- **Cycle 2 (adversarial)** : quatre candidats qui ne résolvent pas la tâche
  obtenaient PASS (verdict = code de sortie contrôlé par le candidat).
  Correctif : contrat de résultat (`apex/oracles/result_contract.py`),
  comparaison hors sandbox. Le réviseur indépendant a ensuite réfuté ce
  correctif (cas cachés lisibles dans les sources depuis le sandbox, cas
  trop faibles) ; correctifs : racine du projet masquée dans le sandbox,
  six cas discriminants. Verdict IMPROVED (`7cf3abd`).
- **Cycle 3 (improvement)** : un champion était retenu sur une seule
  exécution. Correctif : ré-exécution du même code jugée par
  `RobustnessJudge`. Première mesure (16 essais) : 0.44 -> 0.19, non
  significatif -> **UNCHANGED, affirmation de l'implémenteur contredite**.
  Re-mesure des deux versions avec 64 essais : 0.45 -> 0.08, IC 95 %
  [-0.52, -0.25] -> IMPROVED. En mesurant, un défaut du correctif du cycle 2
  a été trouvé (sandbox inutilisable si le dépôt est sous /tmp ou installé
  comme paquet) et corrigé.

- **Cycle 4 (improvement)** : une expérience issue d'une exécution
  chanceuse restait SUCCESS et la génération suivante l'exploitait. Correctif
  (`d1c86f1`) : `SuccessEvidence` (OBSERVED / CONFIRMED / REFUTED)
  persistée après confirmation ; seul CONFIRMED est exploité. Le juge sur le
  code d'avant : REJECT, défaut CONFIRMÉ par ré-exécution. Cycle : IMPROVED,
  juge en processus séparé. **Erreur consignée** : la provenance de ce cycle
  dit « sondes lues à la révision f58b7f3, antérieure au candidat » — c'est
  faux (f58b7f3 est postérieur au correctif `d1c86f1`, et la sonde a été
  écrite après lui par le même agent). Le texte était codé en dur ; corrigé
  dans `012fdc1`/`08c4100`, l'entrée reste (ledger append-only).

Le ledger contient aussi un snapshot `cycle2@7cf3abd:flaky64` **invalide**
(le premier des deux) : mesuré depuis un worktree sous /tmp, il a révélé ce
défaut. Il n'a servi à aucune comparaison.

## Deuxième capacité : Learning / Model Routing

Même moteur, seul l'adaptateur est nouveau (`capabilities/model_routing.py`)
— aucune modification du moteur n'a été nécessaire. Le banc mesure le point
d'entrée de production `apex solve` (`run_solution_loop`) : des historiques
produits par de vraies exécutions (EvalRunner, sandbox, oracle, Experience)
sur deux modèles contrôlés, puis le modèle réellement appelé par le code de
production. Chaque cycle est jugé dans un processus séparé, avec une
définition du juge épinglée à un commit ANTÉRIEUR au correctif
(`60bd2cb` pour le cycle 1, `3ad2c6c` pour les cycles 2 et 3).

| Métrique | Baseline | C1 | C2 | C3 |
|---|---|---|---|---|
| picks_proven_model | 0 | **1** | 1 | 1 |
| keeps_proven_reference (garde-fou) | 1 | 1 | 1 | 1 |
| reference_without_history (garde-fou) | 1 | 1 | 1 | 1 |
| avoids_dead_model | 0 | **1** | 1 | 1 |
| infra_not_blamed_on_model | 1 | 1 | 1 | 1 |
| unconfirmed_success_not_credited | 0 | **1** | 1 | 1 |
| abandons_failing_for_untried | 0 | 0 | 0 | **1** |
| strong_record_beats_single_success | — | 0 | **1** | 1 |
| dead_reference_abandoned | — | 0 | 0 | **1** |

- **Baseline** : le juge confirme 4 défauts ; `apex solve` prend toujours le
  premier modèle de `config.yaml`. Les garde-fous « passent » seulement parce
  que rien ne change jamais.
- **Cycle 1 (functionality)** `7222249` : `performance_from_experiences` +
  `choose_endpoint` (`select_model(performance=...)`). Test causal : même
  configuration, historique A -> référence, historique B -> autre modèle.
- **Cycle 2 (adversarial)** : attaques trouvées APRÈS le cycle 1 et ajoutées
  au banc avant correctif (`3ad2c6c`) : un succès isolé (1/1) surclassait
  9/10 ; une référence injoignable restait appelée. Correctif `78d6b1a` :
  lissage de Laplace (`reliability_prior_strength`).
- **Cycle 3 (improvement)** `7120a26` : attribution de l'échec de génération
  au modèle demandé + fiabilité relative à l'a priori (un modèle en échec
  passe sous un modèle jamais essayé).
- Non résolu : aucune exploration (un modèle écarté n'est jamais ré-essayé) ;
  les autres sites d'appel du routeur (missions, conversations, TaskLoop)
  ne transmettent toujours pas `performance=`.

## Validation inter-systèmes

Statut d'une connexion : CONNECTED (code relié), TESTED (test exécuté qui la
traverse), MEASURED (métrique avant/après au ledger), UNVERIFIED, BROKEN.

| Connexion | Statut | Preuve |
|---|---|---|
| Learning -> Evaluation | TESTED, MEASURED | `tests/test_adaptive_solution_loop.py` ; ledger A |
| Learning -> Experience | TESTED, MEASURED | `TestLuckySuccessDoesNotContaminateLearning` ; ledger A cycle 4 |
| Learning -> ModelRouter (`apex solve`) | TESTED, MEASURED | `tests/test_model_performance.py` ; ledger B |
| Learning -> ModelRouter (missions, conversations, TaskLoop) | BROKEN (non relié) | aucun `performance=` transmis ; `agent_executions` sans `model_id` en échec |
| ModelRouter -> Models | TESTED (double de backend), UNVERIFIED (vrai modèle) | banc B : endpoint réellement appelé ; aucun Ollama joignable |
| Models -> Generated Solutions | TESTED (double), UNVERIFIED (vrai modèle) | `tests/test_evalrunner_generated_solution.py` |
| Generated Solutions -> Sandbox | TESTED, MEASURED | namespaces : suite complète ; conteneur : `tests/test_container_generated_solution.py` |
| Sandbox -> Evaluation | TESTED, MEASURED | `TestResultContract` ; métriques false_positive/verdict_accuracy |
| Evaluation -> Judges | TESTED | `TestChampionConfirmation` (RobustnessJudge) ; `tests/test_improvement_invariants.py` |
| Judges -> Experience | TESTED, MEASURED | `SuccessEvidence` persistée après confirmation ; ledger A cycle 4 |
| Experience -> Future Decision | TESTED, MEASURED | `decide_next_strategies` et `choose_endpoint` ; ledgers A et B |

## Validations d'environnement

- **ContainerSandbox : TESTÉ** (démon Docker démarré dans le bac à sable,
  image `python:3.12-slim`) — `tests/test_container_generated_solution.py` :
  solution valide, fausse, sortie anticipée, dépôt hôte et secrets
  invisibles, timeout, limite mémoire, conteneur impossible à créer puis
  reprise, boucle adaptative complète. A révélé un défaut (conteneur jamais
  démarré classé comme faute du candidat), corrigé dans `fa7374e`.
  Ce n'est pas une validation de la machine utilisateur.
- **Modèle réel : UNVERIFIED — REAL_MODEL.** La politique réseau de
  l'environnement refuse ollama.com, registry.ollama.ai et huggingface.co
  (403 du proxy) : ni Ollama ni un modèle ne peuvent être installés ici.

## Limites

- Aucun modèle réel n'a été exercé : `learning_gain` porte sur un modèle
  scripté ; un gain d'apprentissage avec un vrai modèle est UNVERIFIED.
- Le chaînage du ledger détecte une modification naïve ; quelqu'un qui peut
  écrire le fichier peut recalculer toute la chaîne — l'ancrage externe est
  l'historique git.
- `tests/test_sandbox_detect.py` affirme qu'aucun démon Docker n'est
  joignable « sur cette machine » : ces deux tests échouent sur toute
  machine où Docker tourne (dont celle de l'utilisateur).
- Douze cas cachés booléens : un candidat qui devine au hasard passe avec
  une probabilité 2^-12 ; aucun jeu de tests « held-out » séparé n'existe
  encore pour cette tâche.
