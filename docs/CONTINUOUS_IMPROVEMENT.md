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

- `ImplementerClaim(author_id, summary, claimed_verdict)` est enregistrée,
  jamais crue ; `claim_contradicted` est vrai si le verdict la contredit.
- `author_id == judge_id` lève `SelfConfirmationError` avant toute mesure.
- Les sondes de la première capacité ont été committées avec la baseline
  (`b7cccf1`), avant tout correctif.
- Limite : sondes et correctifs ont été écrits par le même agent. Pour le
  cycle 2, un agent indépendant (sans accès en écriture) a été chargé de
  réfuter le correctif ; il y est parvenu, voir plus bas.

## Utilisation

```bash
L=docs/improvement/generated_solution.ledger.jsonl
python -m apex.improvement measure --capability generated_solution --version v0 --ledger $L
python -m apex.improvement judge   --capability generated_solution --version v0 --kind adversarial --author me --ledger $L
# ... modifier le code ...
python -m apex.improvement cycle   --capability generated_solution --kind adversarial --index 1 \
    --before v0 --version v1 --author me --claim improved --hypothesis "..." --change "..." --test "..." --ledger $L
python -m apex.improvement report  --capability generated_solution --ledger $L
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

Le ledger contient aussi un snapshot `cycle2@7cf3abd:flaky64` **invalide**
(le premier des deux) : mesuré depuis un worktree sous /tmp, il a révélé ce
défaut. Il n'a servi à aucune comparaison.

## Limites

- Aucun modèle réel n'a été exercé (Ollama absent) : `learning_gain` porte
  sur un modèle scripté ; un gain d'apprentissage avec un vrai modèle est
  UNVERIFIED.
- Le sandbox `ContainerSandbox` (celui sélectionné sur une machine WSL2 sans
  namespaces complets) n'a pas été exercé ici : Docker absent.
- Une expérience issue d'une exécution chanceuse garde la catégorie SUCCESS
  et reste lue par `decide_next_strategies` ; seule la décision d'arrêt et
  le choix du meilleur candidat tiennent compte de la confirmation.
- Douze cas cachés booléens : un candidat qui devine au hasard passe avec
  une probabilité 2^-12 ; aucun jeu de tests « held-out » séparé n'existe
  encore pour cette tâche.
