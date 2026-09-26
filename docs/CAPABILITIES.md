# APEX — Capacités apprenables (Priority 2 : CODE + REASONING + CYBERSECURITY)

## 0. Audit avant code

Point de départ : `51b77d2` (Priority 1 complète). Constats vérifiés dans le code et par exécution :

| Existant | Constat | Décision |
|---|---|---|
| Sandbox `LinuxNamespaceSandbox` | réel sur cet hôte (namespaces, cgroups v1, privilège abaissé, réseau coupé ; ~90 ms par lancement) | réutilisé pour TOUTE exécution de code candidat |
| `oracles/result_contract` | principe juste : sorties attendues hors sandbox | principe repris dans le harness de la capacité |
| `evalrunner` / `taskgen` (« solution générée ») | exige Ollama + PostgreSQL, une seule vraie tâche (palindrome), modèle EXTERNE : APEX n'y apprend rien | non réutilisé |
| `improvement.CapabilitySpec` | décrit des capacités du CODE d'APEX (tests du dépôt), pas une compétence apprenable | non réutilisé (même esprit de contrat) |
| `taskloop` | interfaces sans consommateur | non réutilisé |
| Boucle P1 (`apex/neural/loop`) | monde gelé, Judge indépendant avec rejeu, journal, stratégie, registre, retour arrière | MOTEUR d'apprentissage de la capacité |

Défauts réels trouvés à l'audit (et corrigés) :

1. **Le Knowledge Agent détruisait le code** : `normalize` retirait l'indentation de chaque ligne, `chunks_of`
   recollait les lignes : un fichier `.py` arrivait au modèle sur une seule ligne logique. Les filtres de prose
   rejetaient la moitié des modules valides (`low_value`) et le détecteur de contradictions lisait `n = 0` puis
   `n = 1` comme deux affirmations contradictoires. -> chemin CODE : normalisation qui conserve l'indentation,
   qualité = syntaxe Python valide (`ast`), découpage par blocs de premier niveau sans perte, pas de contrôle
   d'affirmations sur du code.
2. **Le Judge et le registre ne savaient comparer qu'en bits par octet**, avec une seule suite d'acceptation et
   une seule tolérance ; l'agrégat `EvalReport.bits_per_byte` était pondéré par les octets (faux pour un score
   de tâche). -> `EvalReport.metric`, `value` (moyenne simple hors BPB), tolérance de garde PAR MÉTRIQUE.
3. La détection de faiblesses ne voyait que les tranches BPB ; le monde ne pouvait contenir aucune tâche exécutable.

## 1. Faisabilité (avant de construire)

Risque principal : un modèle d'environ 0,7 M de paramètres entraîné sur CPU peut-il apprendre quelque chose
d'EXÉCUTABLE ? Génération libre : non réaliste. Réparation guidée par le modèle : mesurée d'abord.

Sur le benchmark (288 bugs réels, 81 candidats en moyenne dont 1,24 passent les tests), chaque candidat a été
exécuté une fois (≈ 23 000 exécutions sandbox) puis chaque règle de classement évaluée sur ces résultats
(coût held-out, plus bas = mieux) :

| classement | train | validation | held-out | transfert |
|---|---|---|---|---|
| hasard (espérance exacte) | 0,955 | 0,934 | 0,962 | 0,952 |
| « le plus court » (caractères) | 0,857 | 0,784 | 0,901 | 0,743 |
| modèle non entraîné, NLL **somme** | 0,844 | 0,821 | 0,864 | 0,764 |
| modèle non entraîné, NLL **moyenne** | 0,984 | 0,981 | 0,930 | 0,944 |
| entraîné 800 pas sur code stdlib, NLL moyenne | 0,714 | 0,796 | 0,823 | 0,701 |

Conséquences :
- **faux signal détecté** : la somme des NLL donne une « performance » sans aucun apprentissage (elle
  favorise les candidats courts) ; la moyenne par jeton met le modèle non entraîné au niveau du hasard. Le
  contrat fixe la moyenne, AVANT toute expérience d'apprentissage ;
- l'entraînement sur du vrai code (jamais sur les tâches) améliore la réparation réelle, y compris en
  transfert vers la famille sécurité, et bat l'heuristique triviale.

## 2. Architecture

```
                         APEX
          CODE REPAIR (1re capacité réelle)   REASONING / SECURITY (suivantes, voir §8)
                           │
            apex/capabilities : contrat + registre
                           │
      monde gelé P1 (world.json) : benchmark de capacité + empreinte + contrat
                           │
  OBSERVE (faiblesse = coût held-out > objectif du contrat)
    -> STRATÉGIE P1 (domaine d'entraînement de la capacité : « code »)
    -> PLAN (cible pré-enregistrée : cap:code_repair:heldout)
    -> ENTRAÎNEMENT réel (TrainingEngine)
    -> JUDGE indépendant : re-mesure PAR EXÉCUTION SANDBOX, rejeu bit à bit, garde de toutes les suites
    -> ACCEPT (registre : même suite principale, mêmes tolérances) / REJECT (analyse, mémoire par tâche)
```

| Module | Rôle |
|---|---|
| `apex/capabilities/contract.py` | `CapabilityContract` (entrée, sortie, environnement, outils, critères de succès/échec, sécurité, provenance, reproductibilité, métrique, tolérance, objectif, domaines d'entraînement, suite d'acceptation, limites) et interface `Capability` |
| `apex/capabilities/registry.py` | registre des capacités ; sandbox réel obligatoire (`IsolationUnavailable` sinon) |
| `code_repair/corpus.py` | 96 fonctions de référence écrites pour APEX + entrées de test (jamais de sorties) |
| `code_repair/mutation.py` | opérateurs d'édition d'un jeton (injection de bugs ET énumération des correctifs) |
| `code_repair/benchmark.py` | construction protégée : sorties attendues par exécution sandbox, bugs gardés seulement s'ils sont détectés, répartition PAR FONCTION en grappes de similarité, empreintes |
| `code_repair/harness.py` | exécution sandbox : un processus enfant par candidat, résultats sur un pipe dédié, nonce, jugement hors sandbox |
| `code_repair/solver.py` | classement par le modèle (NLL moyenne), exécution des K=3 premiers, coût par tâche, score par fonction |
| `code_repair/capability.py` | le contrat et l'implémentation de l'interface |

Intégration P1 (sans système parallèle) : `WorldSpec.capabilities` / `train_only_domains`, décontamination du
corpus contre le code d'évaluation, `measure_with_details`, `IndependentJudge.primary_for` (la cible
pré-enregistrée ne devient suite principale que si le monde la déclare suite d'acceptation), `task_changes`
par tâche, `ModelRegistry(acceptance=…, tolerances=…)`, faiblesses de capacité dans `observe`, domaines des
suites dans la stratégie, détail des tentatives dans le journal (`capability_details`).

## 3. Contrat de la capacité `code_repair` (v1.0)

Défini dans `code_repair/capability.py` et FIGÉ dans l'empreinte du monde (modifier un critère de succès,
une tolérance ou l'objectif change l'empreinte : un journal produit sous l'ancien contrat est refusé).

| Champ | Valeur |
|---|---|
| entrée | source d'une fonction boguée, son nom, les ENTRÉES de test (jamais les sorties) |
| sortie | correctif (source complète) choisi parmi les éditions d'un jeton, et son exécution |
| environnement | sandbox NAMESPACED (namespaces, cgroups, privilège abaissé, réseau coupé), python3 |
| outils autorisés | classement par le modèle APEX ; exécution sandbox de K = 3 candidats au plus |
| succès (tâche) | un candidat exécuté passe TOUS les tests cachés ; coût = (rang du premier qui passe − 1) / K |
| échec (tâche) | aucun des K candidats ne passe (coût 1) ; sortie fausse, exception, délai, exécution invalide |
| succès (modèle) | baisse SIGNIFICATIVE du coût held-out (bootstrap apparié par fonction) sans régression protégée |
| tolérance de garde | 0,05 de coût moyen sur les suites de capacité (le BPB garde 0,01) |
| objectif | coût held-out ≤ 0,25 (au-dessus : faiblesse observée par la boucle) |
| sécurité | pas d'exécution hors sandbox (sinon `IsolationUnavailable`) ; sorties attendues jamais transmises ; exercices de sécurité DÉFENSIFS sur fonctions pures |
| reproductibilité | benchmark gelé et empreinté ; classement déterministe ; `PYTHONHASHSEED=0` ; entrées littérales |
| suites | train (mémorisation), validation, held-out (acceptation, généralisation), transfert (famille sécurité) |

## 4. Benchmark protégé

Construit par `build_benchmark` à partir des 96 fonctions de référence : 1 144 mutants exécutés, **288 bugs
retenus** (seulement ceux qu'au moins un test détecte ET dont le correctif exact est énumérable).

| suite | fonctions | bugs | contenu |
|---|---|---|---|
| train | 35 | 105 | familles partagées (chaînes, listes, maths, recherche/tri, dictionnaires) |
| validation | 18 | 54 | familles partagées |
| held-out | 27 | 81 | familles partagées + **parsing** (famille jamais vue à l'entraînement) |
| transfert | 16 | 48 | **sécurité défensive** (chemins, rôles, ports, listes blanches, échappement, requêtes paramétrées, comparaison à temps constant, redirections, quotas) |

Protections :
- **sorties attendues** calculées en exécutant la référence DANS le sandbox, puis gardées sur l'hôte ; la vue
  donnée au solveur (`RepairTaskView`) ne contient ni la référence, ni l'édition injectée, ni les sorties ;
- **répartition par fonction** et par grappe de similarité (confinement de 6-grammes de jetons normalisés
  > 0,6 : union-find) : deux variantes d'un même algorithme ne peuvent pas être de part et d'autre d'une
  frontière de suite ; la construction échoue si une paire trop proche traverse les suites ;
- **niveaux de difficulté** 1 à 3 (longueur) et 7 catégories ;
- **empreintes** par suite et globale, vérifiées à la relecture (`from_dict`) et incluses dans l'empreinte
  du monde avec le contrat ;
- **décontamination** : tout fragment d'entraînement partageant une séquence de 12 jetons de code avec une
  référence ou une variante boguée d'évaluation est retiré (listé dans le monde) ; seules les solutions
  TRAIN entrent dans le domaine d'entraînement `code_tasks` (forcé dans la partition train du dataset).

**Tâches fraîches** (`code_repair/fresh.py`) : 14 fonctions écrites APRÈS le gel du monde de démonstration
(7 catégories, 42 bugs retenus), jamais dans aucun monde ; `apex_autonomous_loop.py fresh-eval` vérifie
d'abord qu'aucune ne partage de séquence de 12 jetons avec les textes du monde, puis compare
l'initialisation au champion par exécution sandbox et bootstrap apparié.

## 5. Exécution réelle et jugement hors sandbox

Pour chaque tâche : le modèle classe tous les candidats (NLL moyenne par jeton), les K = 3 premiers sont
exécutés, chacun dans un processus enfant (`python -s -B`, délai, réseau coupé). Le journal consigne pour
CHAQUE tâche et CHAQUE tentative : candidat (empreinte), exécuté ou non, statut (`pass`, `wrong_output`,
`exception`, `timeout`, `invalid`), tests exécutés / réussis, premier échec (entrée, attendu vs obtenu ou
exception) — `capability_details` du journal, et `task_changes` du Judge (tâches gagnées / perdues).

Anti-fraude du harness :
- les résultats sortent par un descripteur dédié, préfixés d'un **nonce** tiré par l'hôte : ce que le
  candidat écrit sur stdout ne peut pas se faire passer pour un résultat ;
- nombre de lignes et de résultats vérifiés : sortie partielle, dupliquée ou forgée -> `ExecutionInvalid`
  (jamais un succès par défaut) ;
- comparaison aux sorties attendues faite par l'hôte (`judge_run`) ;
- regroupement des lots sous la limite noyau `MAX_ARG_STRLEN`.

## 6. Mémorisation / généralisation / transfert

`classify_learning` (`contract.py`) est calculé par le Judge à partir de SES comparaisons appariées, jamais
déclaré par l'exécuteur :

| étiquette | preuve exigée |
|---|---|
| GENERALIZATION | gain significatif (IC 95 % > 0) sur la suite d'acceptation held-out (fonctions jamais vues, dont la famille parsing) |
| TRANSFER | gain significatif sur la suite de transfert (famille sécurité jamais vue) |
| MEMORIZATION | gain significatif sur les tâches TRAIN sans gain held-out |
| NONE | aucun gain significatif ; les régressions significatives sont listées à part |

Le résultat est consigné dans le jugement (`learning`) et relu depuis le journal lors d'une reprise.

**Expériences dirigées** (`LoopController.run_directed`, CLI `directed`) : l'opérateur pose UNE question
(une modification de la recette de référence) ; plan validé, entraînement, Judge, analyse d'échec, journal
et registre sont ceux de la boucle ; la stratégie est consignée `directed`. Refusée sans champion attesté,
sans faiblesse mesurée sur la cible, ou avec plus d'une modification.

## 7. Cycle 2 — attaques contre la capacité (`tests/test_capability_adversarial.py`, sandbox réel)

| attaque | défense | test |
|---|---|---|
| solution frauduleuse qui imprime un faux résultat, quitte 0, écrit sur stdout | nonce + descripteur dédié + jugement hôte | `test_a_candidate_cannot_forge_its_own_success` |
| lire les sorties attendues depuis le sandbox | jamais transmises (espion sur toutes les charges envoyées) | `test_expected_outputs_never_enter_the_sandbox` |
| exécution partielle / sortie forgée / exécution revendiquée | lignes et résultats comptés -> `ExecutionInvalid` | `test_partial_or_forged_sandbox_output_is_invalid` |
| métrique forgée (scores de réparation déclarés) | le Judge re-mesure PAR EXÉCUTION ; écart -> rejet | `test_forged_repair_scores_are_rejected` |
| held-out utilisé à l'entraînement (fuite de solutions) | rejeu bit à bit de l'entraînement déclaré -> `UNDECLARED_TRAINING` | `test_training_on_heldout_solutions_is_unmasked` |
| choisir soi-même la suite qui décide | registre et Judge : suite principale = cible pré-enregistrée seulement si suite d'acceptation | `test_registry_never_lets_a_proposal_choose_a_non_acceptance_primary` |
| modifier le benchmark / le critère de succès | empreinte du monde (benchmark + contrat) | `test_changing_the_success_criteria_changes_the_world` + `test_world_freezes_the_capability_and_decontaminates_training` |
| évaluer sans isolation | `IsolationUnavailable` | `test_capabilities_are_never_evaluated_without_a_real_sandbox` |
| fuite de la réponse dans la tâche | `RepairTaskView` sans référence, édition ni sorties | `test_task_view_carries_no_answer` |
| candidat identique au champion / plan sans modification | plan refusé (`InvalidPlanError`), consigné, jamais un plantage | `test_policy_adapts_when_even_the_trained_domain_regresses` + contrôleur |
| résultat mémorisé présenté comme généralisation | `classify_learning` sur les comparaisons du Judge | `test_learning_is_classified_from_significant_paired_gains_only` |
| test contourné / modifié | tests cachés côté hôte, bug gardé seulement s'il est détecté | `test_every_kept_bug_is_really_detected_by_the_tests` |

## 8. Raisonnement et cybersécurité : ce qui existe, ce qui n'existe pas

**Rien de décoratif n'a été ajouté.** Aucun module « reasoning » ou « security » vide n'existe dans le code.

- **Cybersécurité défensive** : présente AUJOURD'HUI comme suite de TRANSFERT de `code_repair`
  (16 fonctions, 48 bugs) : la chaîne vulnérabilité (bug injecté qui ouvre la faille : traversée de chemin,
  rôle mal comparé, borne décalée, liste blanche contournée, échappement manquant, comparaison non
  constante, redirection ouverte, dépassement de quota) -> détection (tests d'entrées d'attaque) ->
  remédiation (correctif) -> test -> validation (exécution sandbox, jugement hôte) est exécutée pour de vrai.
  « Explication » n'est PAS produite par le modèle : l'explication consignée est le premier test en échec
  (entrée d'attaque, attendu, obtenu), c'est-à-dire un fait mesuré, pas un texte généré.
- **Raisonnement vérifiable** : dans cette capacité, le « raisonnement » est le classement des correctifs,
  vérifié par exécution ; aucune chaîne de pensée n'est produite ni notée. Une capacité de raisonnement
  autonome (ex. arithmétique multi-étapes vérifiée par un vérificateur exact) peut se brancher sur la même
  interface `Capability` (contrat, benchmark gelé, `evaluate` par vérification, suites de mémorisation /
  acceptation / transfert) : NON implémentée.
- **Génération libre de code** : hors de portée d'un modèle de 0,7 M paramètres entraîné sur CPU (mesuré au
  §1) ; l'espace de sortie est fermé (éditions d'un jeton) et c'est une limite déclarée du contrat.
