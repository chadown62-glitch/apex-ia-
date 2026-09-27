# APEX — Audit P2 -> P3 et roadmap recommandée

Point de départ : `25244e5` (fin de Priority 2). Chaque affirmation porte son statut :
**OBSERVED** (vu une fois), **TESTED** (test automatisé, souvent synthétique), **MEASURED** (mesure
réelle répétable), **JUDGED** (décision du Judge indépendant sur mesures réelles), **INFERRED**
(interprétation plausible, non prouvée), **UNVERIFIED**, **BLOCKED** (impossible dans cet environnement).

## 1. Ce qui est réellement acquis après P2

| affirmation | statut | preuve |
|---|---|---|
| le cerveau natif (0,72 M paramètres) apprend la réparation de code à partir de code source réel | JUDGED | coût held-out 0,9465 -> 0,749, 6 -> 24/81 bugs, deux promotions (`docs/CAPABILITIES.md` §10) |
| la compétence généralise à des fonctions jamais vues | JUDGED | held-out (dont la famille parsing) + 13 fonctions fraîches écrites après le gel (IC [0,026 ; 0,291]) |
| la première promotion est de la généralisation pure | JUDGED | aucune solution de tâche dans l'entraînement vérifié par rejeu |
| mémorisation et généralisation sont distinguées | JUDGED | `classify_learning` sur les comparaisons du Judge et les documents rejoués |
| la boucle REJECT -> diagnostic -> stratégie -> ACCEPT fonctionne sans humain | JUDGED | E2E final, 10 expériences choisies par la boucle |
| le diagnostic « 13,7 passages = sur-apprentissage » est la cause | INFERRED | `more_data` a ensuite été accepté ; aucune expérience contrôlée n'isole la cause |
| reproductibilité | OBSERVED | une expérience identique dans deux mondes construits séparément (une seule paire) |
| les garanties anti-fraude résistent | TESTED | 12 attaques synthétiques sur le vrai harness / Judge ; 42/42 mutants tués |
| transfert vers la sécurité par le champion promu | non démontré | 0,882 -> 0,826, non significatif |
| raisonnement | non démontré | aucune capacité de raisonnement n'existe |
| fonctionnement sur la machine de l'utilisateur (Windows / WSL / Docker / GPU) | UNVERIFIED / BLOCKED | pas de GPU ni de Docker ici |

## 2. Problèmes réels trouvés dans le code

1. **Le cerveau n'a jamais produit de solution évaluée.** `code_repair` mesure un CLASSEMENT de
   candidats énumérés : l'espace de sortie est fermé par le harness. `ApexLM.generate` existe mais
   aucune capacité ni la boucle ne l'utilise (OBSERVED, `apex/neural/model.py`).
2. **La génération est inutilisable pour l'évaluation** : décodage séquence par séquence, sans cache,
   sans arrêt anticipé. MEASURED : 1 000 problèmes courts ≈ 60 s par évaluation ; avec un décodage par
   lots et arrêt quand toutes les séquences ont terminé : quelques secondes.
3. **L'expérience n'atteint jamais le modèle comme donnée.** La boucle apprend des HYPER-PARAMÈTRES
   (mélange, jetons, lr) ; les succès / échecs par tâche (`capability_details`, `task_changes`) sont
   journalisés mais **aucune décision ne les lit** (OBSERVED : ni `strategy` ni `observe` n'y
   accèdent). « Apprendre de ses erreurs » n'existe qu'au niveau méta.
4. **Ajouter une capacité remet le cerveau à zéro.** Chaque monde entraîne SON tokenizer sur ses
   données, et un checkpoint ne se charge que si l'empreinte du dataset et le tokenizer sont ceux du
   monde (OBSERVED, `world.py:171`, `execute.py:104`). Une deuxième capacité = nouveau monde = toutes
   les promotions précédentes perdues. C'est l'obstacle principal à P3 (dans le même cerveau) et à P5.
5. **Interférence entre compétences dans un modèle minuscule** : 7 des 10 rejets de l'E2E final sont
   des régressions de la modélisation du texte (MEASURED). Toute nouvelle capacité sera en compétition
   de capacité avec les anciennes.
6. **Objectif d'entraînement non adapté aux tâches** : fenêtres aléatoires sur un flux concaténé, perte
   sur tous les jetons ; un exemple question -> réponse peut être coupé, et l'essentiel du gradient sert
   à prédire l'énoncé (OBSERVED ; coût réel : INFERRED).
7. **Sélection de la faiblesse** : l'observation prend les capacités dans l'ordre de déclaration ;
   aucune priorisation par gain attendu entre plusieurs capacités (lacune P5).
8. **Système parallèle hérité** : ≈ 17 000 lignes (`metalearner` 8 350, `improvement`, `taskgen`,
   `taskloop`, `evalrunner`…) dont « l'apprentissage » règle des prompts / génomes autour d'un modèle
   EXTERNE (Ollama via `ModelProvider`). Aucun import depuis le cerveau natif (OBSERVED). Il ne fait
   pas partie des preuves d'apprentissage d'APEX ; à traiter dans la mission de nettoyage.
9. **Statistique** : 27 fonctions held-out, 13 fraîches -> intervalles larges ; objectif du contrat
   (0,25) loin.
10. **Tokenizer** : chiffres = jetons isolés (bon pour l'arithmétique), mais « 1 » et « ␣1 » sont des
    jetons différents : un format compact sans espaces est nécessaire pour le calcul.
11. Registre des interfaces : cohérent dans le dépôt de développement (0 « mensonge ») ; le contrôle
    `doctor` échoue seulement dans l'artefact Runtime où les modules Research sont exclus (déjà couvert
    par les 4 échecs pré-existants de packaging).

## 3. Expérience de faisabilité P3 (avant toute architecture)

Question : le cerveau natif, tel quel (chemin de production : `ApexLM`, `ByteBPETokenizer`,
`TokenBatcher`, `TrainingEngine`), peut-il APPRENDRE un raisonnement multi-étapes dont la réponse est
GÉNÉRÉE, et généraliser à des problèmes jamais vus ? Tâche : programmes d'affectations dépendantes
(`c=9;f=c+8;k=f-c` -> valeur de `k`), profondeur = nombre d'opérations enchaînées ; vérification
exacte par un évaluateur indépendant ; 60 000 problèmes d'entraînement (profondeurs 1 à 3) ; 200
problèmes FRAIS par profondeur (36 collisions avec l'entraînement rejetées). 631 168 paramètres,
6 000 pas, ≈ 17 min sur 2 cœurs. Scripts : hors dépôt (brouillon de faisabilité).

| problèmes frais | prof. 1 | prof. 2 | prof. 3 | prof. 4 (jamais entraînée) | prof. 5 (jamais entraînée) |
|---|---|---|---|---|---|
| réponse directe | 0,74 | 0,665 | 0,48 | 0,07 | 0,035 |
| trace des étapes | **1,00** | **1,00** | **0,725** | 0,065 | 0,105 |

MEASURED :
- le cerveau APPREND un raisonnement multi-étapes génératif et le généralise à des problèmes frais
  de même profondeur (0 -> 1,00 / 1,00 / 0,725) ;
- écrire les étapes intermédiaires améliore fortement la fiabilité (profondeur 3 : 0,48 -> 0,725) ;
- AUCUNE généralisation en longueur : profondeurs 4-5 au niveau du hasard ;
- **trace partiellement décorative détectée** : les VALEURS intermédiaires sont justes mais les NOMS
  de variables écrits dans la trace sont faux (`s6,g12` au lieu de `j6,v11`) : le modèle calcule sans
  recopier. Un contrôle « réponse finale seulement » l'aurait masqué ; la vérification doit porter
  sur chaque étape, et la fidélité (l'étape est-elle réellement utilisée ?) doit être mesurée par
  intervention.

### 3b. Apprendre de sa propre expérience vérifiée (cœur de P5), mesuré

Protocole : base entraînée sur les profondeurs 1-3 ; deux tours où le modèle tente 3 000 problèmes de
profondeur 4 puis 5 ; ne deviennent données que les tentatives dont CHAQUE valeur intermédiaire est
vérifiée exacte. Contrôles causaux à budget égal : « more » (données d'origine seules) et
« unverified » (toutes ses tentatives, non filtrées).

| après 2 tours (problèmes frais) | prof. 3 | prof. 4 | prof. 5 | prof. 6 |
|---|---|---|---|---|
| base | 0,90 | 0,115 | 0,09 | 0,04 |
| auto-entraînement VÉRIFIÉ | 0,97 | 0,105 | 0,10 | 0,055 |
| contrôle « more » | 0,97 | 0,105 | 0,10 | 0,055 |
| contrôle « unverified » | **0,89** | 0,095 | 0,065 | 0,045 |

MEASURED :
1. **L'auto-amélioration n'a pas pu démarrer** : 0 tentative entièrement correcte sur 3 000 à la
   profondeur 4 (les ≈ 10 % de réponses finales justes sont de la chance). Le bras vérifié est
   identique au contrôle, au chiffre près (ce qui confirme au passage le déterminisme).
2. **Cause structurelle** : en profondeur 4, le modèle écrit TOUJOURS exactement 4 étapes (le maximum
   vu) : il saute une étape au lieu de la calculer. Même 16 tirages échantillonnés par problème
   (1 600 tentatives) ne donnent aucune trace correcte en profondeur 4 ou 5. L'exploration n'atteint
   jamais un succès vérifiable : la difficulté suivante doit être ATTEIGNABLE.
3. **Apprendre de ses tentatives non vérifiées nuit** (profondeur 3 : 0,97 -> 0,89) : le
   vérificateur-filtre est nécessaire, pas décoratif.
4. **Variance entre exécutions** : même configuration, profondeur 3 = 0,725 dans un run, 0,90 dans
   l'autre (seul le nombre de threads diffère). Une promotion jugée sur UN entraînement peut être la
   chance de la graine ; le Judge actuel ne le mesure pas (P2 inclus).

## 4. Définition du raisonnement : ce que je recommande

« Raisonnement » = un calcul en plusieurs étapes dont :
- la solution est **GÉNÉRÉE** (pas choisie dans une liste) ;
- **chaque étape** est vérifiable par un vérificateur exact indépendant (pas seulement la réponse) ;
- les problèmes de test sont **frais par construction** (générateur procédural, espace combinatoire,
  collisions avec l'entraînement rejetées) : la mémorisation est impossible ;
- la généralisation est mesurée le long d'axes STRUCTURELS nommés à l'avance (profondeur, nouvelles
  combinaisons d'opérations, distracteurs, noms jamais vus) ;
- la **fidélité** est mesurée par intervention : corrompre une étape écrite doit changer la réponse,
  sinon la trace est décorative.

Une chaîne de prompts, un score de réponse finale seule ou un benchmark fixe réutilisé ne satisfont
aucun de ces points.

## 5. Faut-il faire P3 maintenant ? Ordre recommandé

Oui pour P3, mais **pas comme une capacité isolée de plus** et **pas avant deux fondations** :

| étape | contenu | pourquoi maintenant |
|---|---|---|
| **F1 — cerveau continu** | tokenizer partagé et figé, filiation de mondes (monde enfant = parent + nouvelles capacités), adoption auditée du champion parent comme point de départ re-mesuré | sans elle, chaque capacité remet le cerveau à zéro (problème 4) : P3 puis P5 produiraient une succession de capacités isolées |
| **F2 — outillage génératif et statistique** | décodage par lots avec arrêt anticipé ; calibration du bruit entre graines (le Judge exige un gain au-delà de la variance mesurée entre deux entraînements) | évaluer une capacité générative est sinon trop coûteux (problème 2) ; sans calibration, une promotion peut être la chance de la graine (§3b.4) |
| **P3 — raisonnement vérifiable** | deuxième capacité dans le MÊME cerveau que `code_repair` : exécution de programmes générée et vérifiée étape par étape, tests frais, axes de généralisation structurels, fidélité par intervention | le monde commun oblige le Judge à garder `code_repair` et le texte : première preuve de coexistence multi-capacités |
| **P5-noyau (avancé)** | apprentissage à partir de SES tentatives vérifiées, avec une difficulté suivante atteignable (nouvelles combinaisons, distracteurs, noms jamais vus avant la longueur) et les contrôles causaux ci-dessus | c'est le mécanisme manquant (problème 3) ; il doit être construit sur une capacité où le vérificateur est exact, donc P3 |
| **P4 — mesurer puis dimensionner** | sonde de dimensionnement (3 tailles × 2 graines) sur les deux capacités, et levier d'architecture (encodage de position, format) pour la généralisation en longueur | §3b.2 montre une limite de FORMAT et d'architecture, pas seulement de taille ; P4 doit comparer « plus gros » à « mieux conçu » |
| P6 | cybersécurité défensive | après P5-noyau ; le transfert depuis `code_repair` n'est pas une preuve |
| P7 -> P10 | inchangés | |

Différences avec la roadmap proposée : deux fondations avant P3 ; le noyau de P5 avancé et construit
AVEC P3 ; P4 réduit d'abord à une MESURE des goulots (taille contre format / architecture).

## 6. Architecture recommandée contre les capacités isolées

- UN monde et UN cerveau porteurs de toutes les capacités ; chaque nouvelle capacité arrive par un
  monde enfant qui hérite du tokenizer et du champion (adoption re-mesurée, jamais une promotion
  héritée sans mesure) ; le Judge garde TOUTES les suites de toutes les capacités.
- Interface `Capability` existante conservée (contrat, benchmark, `evaluate`) ; deux familles
  d'évaluateurs réellement utilisées : classement exécuté (`code_repair`) et génération vérifiée
  (P3). Pas de framework plus abstrait tant qu'une troisième capacité n'en a pas besoin.
- Source de données « expérience vérifiée » : les tentatives réussies d'une capacité deviennent des
  documents d'entraînement journalisés et rejouables par le Judge (même provenance que le reste).

## 7. Plan d'expérimentation et critères de réussite

- **F1** : un champion `code_repair` adopté par un monde enfant garde sa valeur re-mesurée (même
  empreinte de benchmark) ; une adoption non attestée ou un tokenizer différent est refusé.
- **F2** : l'évaluation générative est au moins 10 fois plus rapide ; le seuil de promotion intègre la
  variance entre graines mesurée.
- **P3** : de la ligne de base (0) à un gain significatif sur des problèmes frais ; aucune régression
  de `code_repair` ni du texte ; exactitude PAR ÉTAPE rapportée ; fidélité par intervention ; au moins
  un axe structurel hors distribution mesuré honnêtement (réussite ou échec).
- **P5-noyau** : le bras « vérifié » bat les deux contrôles à budget égal, sur des problèmes frais du
  niveau suivant.
