# P3 — Raisonnement vérifiable dans le même cerveau que `code_repair`

Suite de `docs/P3_AUDIT.md` (audit, faisabilité, ordre recommandé). Statuts : **OBSERVED**,
**TESTED**, **MEASURED**, **JUDGED**, **INFERRED**, **UNVERIFIED**, **BLOCKED** (définis dans l'audit).
Tout ce qui suit a été exécuté dans le bac à sable de développement (Linux, 4 cœurs, sans GPU) :
rien ici ne valide la machine de l'utilisateur.

## 1. Ce qui a été construit (dans l'ordre de l'audit)

| étape | contenu | preuve |
|---|---|---|
| F1 cerveau continu | monde ENFANT (splits, tokenizer, architecture, capacités hérités) ; adoption du champion ATTESTÉ du parent, re-mesuré | JUDGED sur le monde P2 réel : valeurs re-mesurées identiques ; 7 tests réels |
| F2a décodage par lots | `generate_batch` : mêmes sorties que `generate`, arrêt anticipé | MEASURED ×6 ; tests d'égalité |
| F2b réplique | une promotion exige qu'un second entraînement (graine indépendante) soit AUSSI accepté | TESTED (branche forcée) ; JUDGED sur l'expérience P3 ci-dessous |
| capacité `program_trace` | générateur, vérité terrain exécutée dans le sandbox, fraîcheur canonique, évaluation générative vérifiée valeur par valeur, fidélité par intervention | 30+ tests ; exécution réelle de 60 700 programmes à la construction |
| P5-noyau (préparation) | tentatives du modèle sur des problèmes neufs, vérifiées par le RÉSULTAT seul, documents « train seulement » d'un monde petit-enfant | tests réels (modèle entraîné, sandbox) |

### Contrat de la capacité (résumé ; texte complet dans `program_trace/capability.py`)

- **Entrée** : `a=3;b=a+4;c=b-a?c:` (programme en ligne droite, chaque étape lit la précédente).
- **Sortie GÉNÉRÉE** : `a3,b7,c4=4` (valeur de chaque variable, puis la réponse).
- **Succès** : la sortie ENTIÈRE a la forme d'une trace ; chaque valeur et la réponse sont exactes
  (erreur 0) ; les noms ne comptent pas (consignés à part).
- **Suites** : `train` (sonde de mémorisation), `validation`, `heldout` (acceptation, profondeur 3),
  `deeper` (profondeur 4, jamais entraînée).
- **Fraîcheur** : forme canonique (variables renommées) jamais vue à l'entraînement ; aucun
  12-gramme de code partagé ; programme ou trace cité tel quel dans un autre document -> retiré.

## 2. Cycle 1 — fonctionnalité (monde enfant du monde P2, champion P2 adopté)

| expérience | décidée par | recette | verdict | preuve |
|---|---|---|---|---|
| ligne de base | — | champion P2 adopté | erreur 1,0 sur toutes les suites `program_trace` ; `code_repair` et texte identiques à P2 | MEASURED |
| `exp-4e492a02428c` | boucle autonome | 200 pas, 100 % traces | REJECT : aucun gain ; oubli massif (BPB texte 2,15 -> 3,86 ; `code_repair` train 0,03 -> 0,82) | JUDGED |
| `exp-1e4ef7dac326` | boucle autonome (stratégie `protect_domains`) | 200 pas, 75 % traces | REJECT : aucun gain ; BPB 2,15 -> 2,66 | JUDGED |
| `exp-d30526ee3140` | dirigée (une modification : le mélange) | 6 000 pas, 40 % traces + mélange du champion | **ACCEPT, répliqué, promu** | JUDGED |

Expérience promue (Judge, rejeu, bootstrap apparié ; n = 300 problèmes frais en held-out) :

| suite | champion P2 | candidat | réplique (graine 1 000 003) |
|---|---|---|---|
| `program_trace:heldout` | 1,0 | **0,283** (gain IC 95 % [0,667 ; 0,770]) | **0,253** ([0,693 ; 0,797]) |
| `program_trace:validation` | 1,0 | 0,325 | 0,26 |
| `program_trace:train` | 1,0 | 0,32 | 0,27 |
| `program_trace:deeper` (prof. 4) | 1,0 | 1,0 | 1,0 |
| held-out texte (BPB) | 2,151 | 1,801 | 1,807 |
| `code_repair:heldout` | 0,749 | 0,770 (n.s.) | 0,679 (n.s.) |
| `code_repair:validation` | 0,735 | 0,469 (sign.) | 0,586 (sign.) |

- **Généralisation** (JUDGED) : 215 problèmes held-out nouvellement résolus, 0 perdu ; train ≈ held-out
  (aucun écart de mémorisation).
- **Aucune régression** d'une capacité existante (JUDGED, deux entraînements indépendants).
- **Attribution** : le champion P2 n'avait été entraîné que 200 pas ; les gains texte / `code_repair`
  peuvent venir de l'échelle seule. Un transfert raisonnement -> code est **UNVERIFIED** (la réplique
  ne reproduit que le gain de validation ; pas de bras témoin à même échelle sans traces).
- **Généralisation en longueur : échec** (MEASURED) — en profondeur 4, le modèle écrit exactement
  4 étapes dans 197 cas sur 200 (il saute une variable pour tenir le gabarit appris).

### Goulots mesurés (préalable à P4)

1. **Budget par expérience** : 200 pas ne font rien émerger ; 6 000 pas oui. La boucle autonome,
   avec ses plafonds par défaut (800 pas), ne pouvait pas atteindre ce régime (MEASURED).
2. **Interférence** : un mélange sans les domaines anciens détruit le texte et `code_repair` en
   200 pas ; à 6 000 pas avec 60 % de mélange ancien, rien ne régresse (JUDGED).
3. **Contention CPU** : sur 4 cœurs, un entraînement concurrent de tests (threads torch
   sur-souscrits) passe de ≈ 600 à ≈ 30 pas/min (MEASURED) : les travaux lourds sont sérialisés.
4. La taille du modèle n'a PAS été le facteur limitant de la profondeur 3 (0,72 M paramètres
   suffisent) ; elle n'est pas testée comme cause de l'échec en profondeur 4 (voir §5).

## 3. Cycle 2 — robustesse adverse (le Judge et l'évaluation comme cibles)

Défauts RÉELS trouvés et corrigés (chacun avec un test de régression) :

| défaut | trouvé par | correction |
|---|---|---|
| le Judge lisait les détails de tâche au format de `code_repair` : plantage à la première expérience d'un monde à deux capacités | run réel P3 | format `instance`/`solved` dans le contrat de capacité ; ligne mal formée refusée |
| une trace d'évaluation citée dans un commentaire ou dans de la prose échappait à la décontamination (12-grammes de jetons de code) | test adverse | présence telle quelle (aux espaces près) d'un texte protégé -> retrait ; programmes évalués protégés eux aussi |
| domaine « non vérifié » du P5 vidé par la déduplication (1 147 / 3 992 documents) | construction réelle du monde P5 | domaines disjoints `self_verified` / `self_rejected` |

Attaques couvertes par des tests : sortie avec texte en trop, étape manquante ou en trop, réponse
juste par chance ; benchmark falsifié (empreinte, puis valeurs avec empreintes recalculées) ;
problème d'évaluation renommé dans l'entraînement ; générateur faux démasqué par l'exécution
sandbox ; tokenizer fusionnant invite et réponse ; document « train seulement » hors domaine déclaré.

**Fidélité par intervention** (MEASURED ; on impose une valeur intermédiaire fausse et on regarde si
la suite est recalculée à partir d'elle) :

| modèle | interventions | suit (trace causale) | ignore (trace décorative) | autre |
|---|---|---|---|---|
| modèle qui a appris la suite train par cœur | 14 | **0** | 5 | 9 |
| champion promu, held-out | 215 | **177 (82 %)** | 5 (2 %) | 33 |
| champion promu, validation | 135 | 107 (79 %) | 1 | 27 |

**Mutation** (TESTED ; worktree git isolé, `PYTHONPATH` vérifié pointant sur le worktree, sources
restaurées et vérifiées par empreinte) : 42 mutants, chacun affaiblissant UNE protection de P3, F1 ou
F2 (générateur, vérificateur, benchmark, décontamination, tokenizer, Judge, fidélité, P5-noyau,
lignée, réplique, décodage par lots). Premier passage : 38 tués, 4 survivants — chacun révélait un
test trop faible (réponse chanceuse jamais testée, monde frère au même tokenizer, fin de séquence
jamais émise, générateur qui propose un problème évalué), et un défaut réel (`fresh_problems`
bouclait sans fin quand l'espace était épuisé). Après renforcement : **42 / 42 tués**, chacun par le
test visé (un mutant n'est compté tué que si un test ÉCHOUE, jamais sur une erreur de collecte).

La trace du champion est majoritairement CAUSALE (il calcule à partir de ce qu'il écrit) ; celle du
mémoriseur ne l'est pas.

**Analyse d'échec** (MEASURED, held-out) : quand la dernière étape lit une variable NON adjacente
(`d=c+a`), l'exactitude tombe à 0,38 contre 0,88 pour un littéral ; 61 des 85 erreurs portent sur la
dernière étape. Le goulot est la recherche d'une valeur lointaine dans le contexte, pas l'arithmétique.

## 4. Cycle 3 — amélioration (P5-noyau : apprendre de ses propres tentatives vérifiées)

Protocole (réel, de bout en bout) :

1. le champion promu tente 4 000 problèmes NEUFS de profondeur 3 (forme canonique ni évaluée ni
   dans le réservoir : il n'en a jamais vu la solution) — difficulté ATTEIGNABLE (il en résout ≈ 72 %) ;
2. le vérificateur ne connaît que le RÉSULTAT final, exécuté dans le sandbox (comme un test unitaire)
   — pour cette tâche, vérifier chaque étape reviendrait à fournir la solution ;
3. ses tentatives deviennent les documents « train seulement » d'un monde PETIT-ENFANT (F1), sous les
   mêmes contrôles de contamination (4 retirées : 12-grammes partagés avec l'évaluation) ;
4. trois bras partent du MÊME champion adopté, même budget (1 500 pas, 52 % de traces), une seule
   modification (le mélange) ; jugés par le Judge, puis comparés entre eux par bootstrap apparié.

Tentatives (MEASURED) : 3 992 / 4 000 de forme valide ; 2 844 vérifiées par le résultat, dont **4**
seulement ont une étape fausse (réponse juste malgré une erreur) ; 1 148 refusées.

| bras | donnée ajoutée (20 %) | erreur held-out | BPB texte | verdict |
|---|---|---|---|---|
| vérifié | tentatives dont le résultat est vérifié | 0,240 | 2,106 | REJECT (régression texte) |
| non vérifié | toutes les tentatives (proportion naturelle) | 0,290 | 2,109 | REJECT (régression texte) |
| plus d'étiqueté | davantage du réservoir étiqueté d'origine | 0,233 | 2,108 | REJECT (régression texte) |
| champion de départ | — | 0,283 | 1,801 | — |

Comparaisons appariées entre bras (held-out, n = 300 ; MEASURED, un entraînement par bras) :

- vérifié **meilleur** que non vérifié : IC 95 % de l'écart [0,013 ; 0,087] — le filtre est causal ;
- vérifié **≈** plus d'étiqueté : [-0,047 ; 0,033], non significatif ;
- non vérifié **pire** que plus d'étiqueté : [-0,097 ; -0,020].

Conclusions :

- Les tentatives du cerveau, vérifiées par le seul résultat, valent autant que des données
  étiquetées à budget égal (MEASURED) ; sans vérificateur, elles nuisent (MEASURED, cohérent avec la
  faisabilité). **Le critère P5 « battre les deux contrôles » n'est PAS atteint** : l'auto-amélioration
  n'apporte rien de PLUS que des étiquettes quand des étiquettes existent.
- Les trois bras régressent IDENTIQUEMENT sur le texte (1,80 -> 2,11) : la cause est le régime de
  poursuite d'entraînement (taux d'apprentissage remis au maximum sur un modèle en fin de décroissance,
  part de texte réduite), pas la donnée (MEASURED : aucun écart texte entre bras). Aucun bras n'est
  promu : la garde du Judge a fonctionné.
- Où le P5 aurait une valeur réelle : quand aucune étiquette n'existe (code évalué par des tests) et
  quand la difficulté suivante est atteignable ; en profondeur 4, le champion obtient 7 % de réponses
  finales justes et 0 trace exacte : un vérificateur de résultat n'y garderait que des traces fausses
  (INFERRED des mesures du §2 et de la faisabilité).

**Suite complète** (bac à sable, fin d'étape) : 2 492 réussis, 175 ignorés (PostgreSQL, Docker, GPU
absents), 4 échecs — les 4 tests d'empaquetage `test_runtime_packaging.py`, qui échouent à
l'identique sur le commit de départ `25244e5` (pré-existants, hors périmètre).

## 5. Limites (ce qui n'est PAS démontré)

- Profondeur 4 et au-delà : échec complet ; aucune expérience n'a testé si la taille du modèle, le
  format ou un curriculum le corrigerait (UNVERIFIED).
- `program_trace` est un monde fermé et synthétique ; aucun transfert vers un raisonnement ouvert.
- Pour cette tâche, exécuter le programme DONNE la réponse : un vérificateur par étape serait un
  étiqueteur. Le P5-noyau n'utilise donc que le résultat final (comme un test unitaire).
- Les chiffres viennent d'un seul monde et d'une paire d'entraînements (original + réplique).

## 6. Étape suivante recommandée

1. **Poursuite d'entraînement sans oubli** (préalable à tout P5 et P4) : les trois bras P5 montrent que
   reprendre l'entraînement d'un champion au taux maximal détruit le texte. Mesurer un régime de
   poursuite (taux réduit, échauffement, part de rejeu) avant toute nouvelle expérience.
2. **P4 comme mesure, pas comme mise à l'échelle** : l'échec en profondeur 4 est-il dû à la taille, au
   format (le modèle écrit un nombre FIXE d'étapes) ou aux données (aucune profondeur 4) ? Trois bras à
   budget égal : modèle plus grand ; format avec compteur d'étapes explicite ; curriculum (quelques
   exemples de profondeur 4). Le critère : profondeur 5 jamais vue > 0.
3. **P5 là où il a un sens** : `code_repair` génératif (le modèle écrit le correctif ; les tests
   vérifient sans donner la solution).
