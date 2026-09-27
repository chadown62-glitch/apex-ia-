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
| MEMORIZATION | gain significatif sur les tâches TRAIN, sans gain held-out OU nettement supérieur au gain held-out (intervalles disjoints) : une part du gain ne se transfère pas |
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

## 9. Résultats réels — run 1 (boucle autonome, monde `sha256:a6aff027f183…`, code d'avant le cycle 3)

Monde réel : 259 documents acceptés par le Knowledge Agent (docstrings stdlib + 40 fichiers source Python de
la stdlib), 4 210 fragments, modèle de 724 864 paramètres (vocabulaire 1 024, contexte 256), benchmark de
288 bugs ; chaque mesure de capacité = 288 tâches classées par le modèle puis exécutées dans le sandbox.
Ce run a été lancé AVANT l'ajout de `more_data`, de `classify_learning` et du contrat dans l'empreinte :
ses décisions sont celles de l'ancienne politique (c'est ce qui a motivé ces ajouts).

Coût de réparation (plus bas = mieux ; IC 95 % du gain par bootstrap apparié par fonction) :

| expérience | stratégie (choisie par la boucle) | held-out BPB | coût held-out | coût transfert | coût train | décision |
|---|---|---|---|---|---|---|
| init | — | 3,8727 | 0,9465 (6/81 résolus) | 0,9236 | 0,9746 | — |
| exp-db2476cb9e95 | bootstrap stdlib | 3,3209 ✓ | 0,9547 (n.s.) | 0,9931 (n.s., au-delà de la tolérance) | 0,9683 | **REJECT** (régression protégée : transfert) |
| exp-a2b0ce565c29 | protect_domains code 25 % | 2,4268 ✓ | 0,9177 (n.s.) | 0,9167 | 0,8952 ✓ | **ACCEPT, promu** |
| exp-7963d440f401 | target_only code, cible `cap:code_repair:heldout` | 2,4425 ✗ | **0,8189 ✓ [0,037 ; 0,165]**, 10 -> 18 résolus | **0,8194 ✓** | 0,8095 ✓ | **REJECT** (BPB held-out / stdlib / validation régressent significativement) |
| exp-3c4f314d3bc0 | protect_domains stdlib 25 % | 2,5073 ✗ | 0,8765 (n.s.) | 0,8542 | 0,8444 | **REJECT** (tous les domaines, même entraînés, régressent) -> cible épuisée |
| exp-d73943865e0d | protect_domains, cible `heldout:stdlib`, rejeu 12,5 % code + **12,5 % solutions train** | 2,7028 ✗ | **0,8025 ✓ [0,037 ; 0,202]**, 10 -> 20 résolus | 0,9028 (n.s.) | **0,0222 ✓ [0,797 ; 0,940]** | **REJECT** (cible stdlib dégradée) |
| exp-997941c0e6cd | lower_lr (1e-3 -> 5e-4) | 2,5758 ✗ | **0,7778 ✓**, 10 -> 21 résolus | 0,8750 (n.s.) | 0,0698 ✓ | **REJECT** (cible stdlib dégradée) |
| exp-8d46d0a58a55 | lower_lr (5e-4 -> 2,5e-4) | 2,4975 ✗ | **0,7654 ✓ [0,086 ; 0,222]**, 10 -> 22 résolus | 0,8542 (n.s.) | 0,2159 ✓ | **REJECT** (cible stdlib dégradée) ; arrêt : plafond de 7 expériences |

Coût mesuré (run 1, 4 cœurs CPU) : entraînement de 200 pas + mesure déclarée ≈ 100 s ; Judge (rejeu bit à
bit + re-mesure des deux modèles, 864 exécutions sandbox de candidats par modèle) ≈ 300 s ; le Judge coûte
environ 3 fois l'entraînement.

Lecture (REAL EVIDENCE, mesures du Judge) :
- **l'apprentissage de la réparation est réel et généralise** : entraîné sur du code source (jamais sur les
  tâches d'évaluation), le coût held-out baisse significativement (0,918 -> 0,819) et le TRANSFERT vers la
  famille sécurité jamais vue aussi (0,917 -> 0,819) ;
- **mémorisation mesurée** : avec les solutions train dans le mélange, le coût train s'effondre
  (0,895 -> 0,022 : quasi toutes les tâches vues sont réparées) alors que le gain held-out reste modéré
  (0,918 -> 0,803) et que le transfert ne bouge pas : l'écart train / held-out est la signature de la
  mémorisation ; le gain held-out restant est de la généralisation (fonctions jamais vues) ;
- **aucune de ces améliorations n'a été promue** : chacune dégradait significativement la modélisation du
  texte (suites protégées). Le Judge refuse d'échanger une compétence contre une autre ; c'est le
  comportement voulu, et c'est aussi la limite actuelle d'un modèle de 0,7 M paramètres.

**Défaut réel trouvé par ce run (cycle 3) et corrigé** : les affinages tiraient 60 000 jetons mais en lisaient
819 200 (200 pas) : **13,7 passages** sur le même échantillon, d'où la régression des domaines ENTRAÎNÉS
eux-mêmes. La politique ne proposait pourtant que de baisser le taux d'apprentissage, pour deux raisons :
`more_data` n'était envisagé que pour une régression PROTÉGÉE (pas pour une régression de la cible), et il
exigeait que tous les domaines entraînés régressent, ce qui est impossible quand le mélange contient
`code_tasks` (domaine sans suite d'évaluation). Correction (`strategy.py`) : le nombre de passages
(jetons vus / jetons tirés, fait ÉTABLI du journal) au-delà de `max_passes` = 4 avec un domaine entraîné
dégradé déclenche d'abord `more_data` ; les domaines sans suite ne comptent plus. Test :
`test_policy_adds_data_first_when_the_sample_was_reread_many_times` ; deux mutants dédiés.

## 10. E2E final indépendant (monde reconstruit `sha256:cb9313a0e09d…`, code final)

Monde reconstruit de zéro avec le code final (le contrat est dans l'empreinte) : **même benchmark, même
empreinte `sha256:61e18a19a307…`** (1 144 mutants, 288 bugs) — la construction est reproductible. Boucle
autonome : `apex_autonomous_loop.py run --bootstrap stdlib=1.0 --max-total-experiments 10` ; aucune
expérience choisie à la main.

| # | expérience | stratégie (choisie par la boucle) | coût held-out (résolus /81) | BPB held-out | apprentissage (Judge) | décision |
|---|---|---|---|---|---|---|
| 1 | exp-d97d44598669 | bootstrap stdlib | 0,9465 -> 0,9547 (6 -> 5) | 3,873 -> 3,321 ✓ | NONE | REJECT (transfert au-delà de la tolérance) |
| 2 | exp-2f6ed7cd8e6a | protect_domains code 25 % | 0,9465 -> 0,9177 n.s. (6 -> 10) | -> 2,427 ✓ | NONE ¹ | **ACCEPT, promu** |
| 3 | exp-85f21fc0f858 | target_only code (cible `cap:code_repair:heldout`) | 0,9177 -> **0,8189 ✓** (10 -> 18) | 2,427 -> 2,443 ✗ | GENERALIZATION + TRANSFER | **REJECT** : régression protégée |
| 4 | exp-552a21663a76 | protect_domains stdlib 25 % | 0,9177 -> 0,8765 n.s. | -> 2,507 ✗ | NONE | **REJECT** : tous les domaines ENTRAÎNÉS régressent |
| 5 | exp-17f26b37ea36 | **more_data** 60 k -> 120 k jetons ² | 0,9177 -> **0,8518 ✓ [0,008 ; 0,132]** (10 -> 18) | 2,427 -> **2,272 ✓** | **GENERALIZATION** (aucune solution de tâche vue) | **ACCEPT, promu** |
| 6 | exp-f46576ddfb7b | protect_domains (rejeu avec solutions train) | 0,8518 -> 0,7695 n.s. | -> 2,572 ✗ | MEMORIZATION (train 0,838 -> 0,019) | REJECT |
| 7 | exp-d3be6116e7f4 | more_data 60 k -> 120 k | 0,8518 -> **0,7284 ✓** (18 -> 26) | -> 2,353 ✗ | GENERALIZATION + MEMORIZATION ¹ | REJECT : régression protégée |
| 8 | exp-e4f2b01f8d4f | **more_data** 120 k -> 240 k | 0,8518 -> **0,7490 ✓ [0,025 ; 0,185]** (18 -> 24) | 2,272 -> **2,151 ✓** | GENERALIZATION + MEMORIZATION ¹ | **ACCEPT, promu (champion final)** |
| 9 | exp-ae29866c326e | more_steps 200 -> 400 pas | 0,7490 -> 0,7613 n.s. | 2,151 -> 2,901 ✗ | NONE ; transfert régresse significativement | REJECT (27 passages : sur-apprentissage) |
| 10 | exp-4c6d35505f66 | more_data | 0,7490 -> 0,6996 n.s. (24 -> 30) | -> 2,480 ✗ | MEMORIZATION (train seul) | REJECT ; arrêt : plafond de 10 expériences |
| D | exp-4fa6477d2929 | **dirigée** : `code_tasks` seul (question de l'opérateur) | 0,7490 -> 0,7942 n.s. (24 -> 19) | 2,151 -> 3,503 ✗ | NONE | REJECT : mémoriser les solutions ne généralise pas |

¹ Étiquettes recalculées avec la règle finale (la boucle tournait avec la version précédente de
`classify_learning`) ; fonction déterministe des comparaisons et des documents d'entraînement journalisés.
² Justification consignée par la boucle : « ESTABLISHED : 819200 jetons vus pour 60000 tirés (13.7 passages,
seuil 4.0) ; domaines entraînés dégradés ['code', 'stdlib'] » puis « INFERRED : sur-apprentissage d'un
échantillon trop petit ».

**Chaîne démontrée (REAL EVIDENCE)** : baseline (init 0,9465, 6/81) -> faiblesse (coût held-out > objectif
0,25) -> hypothèse pré-enregistrée sur `cap:code_repair:heldout` -> REJECT (#3) -> analyse ÉTABLIE
(régression protégée) -> nouvelle stratégie (#4) -> REJECT -> analyse ÉTABLIE (13,7 passages) -> nouvelle
stratégie `more_data` (#5) -> **ACCEPT -> PROMOTION** sur la capacité, avec une amélioration de la
modélisation du texte au lieu d'un échange compétence contre compétence ; puis une seconde promotion (#8).

**Reproductibilité** : l'expérience #3 donne exactement les mêmes valeurs que exp-7963d440f401 du run 1
(0,9177 -> 0,8189, mêmes intervalles, mêmes tâches gagnées) sur un monde reconstruit séparément.

### Tâches fraîches (écrites après le gel, jamais dans aucun monde)

`fresh-eval` : 14 fonctions, 42 bugs construits par exécution ; contrôle de contamination : `selection_sort`
partage une séquence de 12 jetons avec la solution train `insertion_sort` (en-tête
`(values): items = list(values) for i …`) -> **exclue automatiquement**. Sur les 13 fonctions / 39 bugs
restants (similarité maximale au corpus 0,571) :

| modèle | coût | bugs réparés |
|---|---|---|
| initialisation | 0,889 | 15,4 % |
| champion exp-e4f2b01f8d4f | **0,744** | **30,8 %** |
| gain (bootstrap apparié par fonction) | IC 95 % [0,026 ; 0,291], **significatif** | |

### Mesures de performance (4 cœurs CPU, pas de GPU)

| mesure | valeur |
|---|---|
| modèle | 724 864 paramètres, vocabulaire 1 024, contexte 256 |
| mémoire de pointe du processus de boucle | 1,05 Go RSS (échantillonnée toutes les 2 s) |
| durée totale de l'E2E (10 expériences) | 71 min (21:53 -> 23:04 UTC) ; 2 800 pas d'entraînement |
| entraînement + mesure déclarée, par expérience | ≈ 100 s (200 pas) ; ≈ 139 s (400 pas) |
| Judge, par expérience | ≈ 290–330 s (rejeu bit à bit + re-mesure des deux modèles) |
| exécutions sandbox de candidats par mesure de capacité | 864 (288 tâches × 3 tentatives au plus) |
| évaluation des tâches fraîches (2 modèles, construction comprise) | 35 s |
| amélioration vs baseline | coût held-out 0,9465 -> 0,7490 (−21 %), tâches résolues 6 -> 24 / 81 ; tâches fraîches 15 % -> 31 % |

## 11. Limites

**Démontré (REAL EVIDENCE)** : apprentissage réel de la réparation de code par un modèle natif entraîné sur
du code source réel, jugé par exécution sandbox ; généralisation à des fonctions jamais vues (held-out,
famille parsing incluse) et à des tâches écrites après le gel ; transfert vers la famille sécurité
(expérience #3, non promue) ; mémorisation mesurée et distinguée ; boucle autonome REJECT -> analyse ->
nouvelle stratégie -> ACCEPT -> promotion ; expérience dirigée ; reproductibilité inter-mondes.

**Testé synthétiquement seulement (SYNTHETICALLY TESTED)** : les attaques du §7 (fraude de candidat, sortie
forgée, métrique forgée, entraînement non déclaré sur le held-out) sont des tests d'attaque sur le vrai
harness et le vrai Judge, pas des incidents observés en production.

**Implémenté, non démontré** : aucun composant P2 n'est dans cet état (les classes non exercées par
l'E2E ont des tests réels : refus d'une expérience dirigée sans faiblesse, plafonds).

**Limites connues** :
- espace de sortie fermé (éditions d'un jeton) ; pas de génération libre, pas de bugs multi-lignes ;
- le transfert vers la sécurité n'est PAS acquis par le champion promu (#8 : 0,882 -> 0,826 n.s.) ;
- coût held-out final 0,749 : loin de l'objectif 0,25 du contrat ; 24/81 bugs réparés ;
- modèle de 0,7 M paramètres : chaque gain de compétence menace la modélisation du texte (la plupart des
  rejets) ;
- 27 fonctions held-out, 13 fraîches : les intervalles restent larges ;
- `explication` d'une vulnérabilité = premier test en échec (fait mesuré), jamais un texte généré.

**Bloqué par l'environnement** : aucun GPU ; les résultats sont ceux d'un sandbox Linux (namespaces) de ce
conteneur, PAS une validation de la machine de l'utilisateur.

**Hors périmètre** : calcul distribué, business, workers, grands modèles, cloud, UE5, interface commerciale.

## 12. Mutation testing (cycle 2)

`42` mutants, chacun affaiblissant UNE garantie (harness, opérateurs, benchmark, solveur, agrégation,
monde, Judge, registre, observation, analyse d'échec, stratégie, Knowledge Agent, classification
mémorisation / généralisation / transfert, tâches fraîches) ; exécutés dans une copie de travail isolée
(`git worktree`), sources restaurées et vérifiées par hash, chaque mutant tué par un ÉCHEC d'assertion
(jamais une erreur d'import ou de collecte).

| passage | total | valides | compilent | tués | survivants |
|---|---|---|---|---|---|
| 1er passage | 42 | 42 | 42 | 38 | 4 |
| après correction des tests | 4 | 4 | 4 | 4 | 0 |
| **bilan** | **42** | **42** | **42** | **42** | **0** |

Les 4 survivants étaient des tests qui passaient pour une MAUVAISE raison (défauts réels des tests, pas du
code) :

| survivant | pourquoi le test passait quand même | correction |
|---|---|---|
| nonce non vérifié | le faux nonce était 4 caractères plus long : c'est l'analyse JSON qui échouait | faux nonce de même longueur, charge utile intacte |
| pas de découpage en lots | la charge (≈ 80 Ko) restait sous `MAX_ARG_STRLEN` (131 072) | charge au-delà de la limite noyau + espion : plusieurs lancements, chaque argument sous la limite |
| valeur de capacité pondérée par les exécutions | le modèle non entraîné exécutait presque toujours 3 candidats : pondérations égales | test direct avec des nombres d'exécutions inégaux |
| benchmark non gelé dans `world.json` | l'empreinte du MONDE rattrapait la falsification (couche redondante, réécrivable par un attaquant) | le test exige que la couche benchmark elle-même détecte |

## 13. Tests et non-régression

Suite complète (`pytest`, JUnit XML) : **2 447 réussis, 4 échecs, 175 ignorés, 0 erreur** (18 min 46 s).

- Les 4 échecs sont tous dans `tests/test_runtime_packaging.py` et **identiques sur le commit de départ
  `51b77d2`** (vérifié en exécutant ce fichier sur une copie de travail de ce commit : 31 réussis,
  les mêmes 4 échecs) : imports `apex.metalearner` dans l'API, une migration qui mentionne
  `apex.metalearner`, champ `schema_version` absent du contrat `doctor`. Pré-existants, hors périmètre.
- **Régression introduite puis corrigée** : le nouveau sous-paquet `apex.capabilities` n'était classé
  ni Runtime ni Research dans `runtime_build/selection.py` ; la garde de construction refusait alors
  de construire (29 tests de packaging en échec supplémentaires). Classé Research (il importe
  `apex.neural` et n'est importé que par `apex.neural.loop`, aucun paquet Runtime ne l'importe).
- Tests de la priorité 2 : `test_capability_code_repair.py` (23), `test_capability_adversarial.py` (12),
  `test_capability_loop.py` (7, boucle réelle sur un monde réduit réel), `test_loop_components.py`
  (21) : 63 réussis. Boucle P1 : `test_loop_*` inclus dans la suite complète, sans régression.
- Lint (`ruff check`, `ruff format`) et types (`mypy`) propres sur tous les fichiers de la mission
  (seul `runtime_build/selection.py` était déjà non formaté au départ ; laissé tel quel, une ligne
  ajoutée).
