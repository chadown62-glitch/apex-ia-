# Apprentissage continu, oubli, P4, P5, transfert

Suite de `docs/P3_REASONING.md`. Statuts : IMPLEMENTED, TESTED, MEASURED, REAL E2E, GENERALIZED,
ADVERSARIAL, PROMOTED, et **HYPOTHESIS REJECTED** quand une hypothèse est réfutée. Tout a été
exécuté dans le bac à sable de développement (Linux, 4 cœurs, sans GPU) ; rien ne valide la
machine de l'utilisateur.

## 1. Audit de départ (commit `9560cdf`)

- Les gardes du Judge comparent le candidat au SEUL champion courant : une suite de promotions dont
  chacune perd un peu, dans la tolérance ou sous le seuil de signification, peut faire dériver une
  capacité sans limite (aucune ancre du niveau démontré).
- Aucune mesure formelle de l'oubli : les régressions apparaissent comme `protected_regression`
  (suites dégradées contre le champion), sans « avant / après » par capacité.
- La boucle sait déjà réagir à une régression mesurée (`protect_domains` : plus de rejeu des domaines
  dégradés ; `lower_lr`) : réutilisé, aucun moteur parallèle créé.
- Une expérience dirigée n'admet qu'UNE modification par rapport à sa recette de référence : les bras
  ci-dessous déclarent leur référence (taux d'apprentissage) et ne changent que le mélange.

## 2. Oubli mesuré : reprise du champion P3 (MEASURED)

Protocole : même monde, même champion adopté (P3 promu), même cible, même budget (1 500 pas,
600 000 jetons tirés, lots de 16 × 256), un entraînement par bras, jugé par le Judge (rejeu compris).
Oubli d'une suite = valeur après − valeur avant (métriques « plus bas = mieux »).

| bras | lr | données anciennes | `program_trace` held-out | BPB texte held-out (oubli) | pire oubli | verdict |
|---|---|---|---|---|---|---|
| A régime actuel | 1e-3 | 48 % | 0,283 -> **0,233** | 1,801 -> 2,108 (+0,307) | stdlib +0,445 | REJECT |
| B lr réduit | 1e-4 | 48 % | 0,283 -> 0,293 (n.s.) | 1,801 -> 1,865 (+0,064) | stdlib +0,100 | REJECT |
| C rejeu | 1e-3 | 70 % | 0,283 -> 0,327 (pire) | 1,801 -> 2,031 (+0,230) | stdlib +0,332 | REJECT |
| D lr réduit + rejeu | 1e-4 | 70 % | 0,283 -> 0,313 (n.s.) | 1,801 -> 1,848 (+0,048) | stdlib +0,076 | REJECT |
| E = D + 4x données tirées (2,4 M) | 1e-4 | 70 % | 0,283 -> 0,303 (n.s.) | 1,801 -> **1,766 (−0,035)** | aucun | INCONCLUSIVE |
| F = A + 4x données tirées (2,4 M) | 1e-3 | 48 % | 0,283 -> **0,233** | 1,801 -> **1,784 (−0,017)** | `code_repair` validation +0,031 | ACCEPT, réplique REJECT |

Comparaisons appariées entre bras (IC 95 % de l'écart) :

- le taux d'apprentissage est le facteur DOMINANT de l'oubli : A -> B divise l'oubli du texte par ≈ 5
  (écart [0,239 ; 0,256] BPB) ; C -> D aussi ([0,181 ; 0,196]) ;
- le rejeu réduit l'oubli, moins : A -> C −0,077 BPB, B -> D −0,017 BPB (significatifs) ;
- seul le bras A APPREND (−0,05 d'erreur, significatif contre B, C, D) — et c'est lui qui oublie le
  plus ; `code_repair` : aucune différence significative entre bras ;
- coût : ≈ 215–240 s d'entraînement, ≈ 575–610 s de bout en bout par bras (Judge et rejeu compris).

Bras E (une modification par rapport à D : 2,4 M jetons tirés au lieu de 600 000, soit ≈ 2,5
passages sur l'échantillon au lieu de ≈ 10) : **plus aucun oubli** — le texte S'AMÉLIORE
significativement (IC [0,034 ; 0,037]), `code_repair` est stable ; la cible n'est pas améliorée
(IC [−0,057 ; 0,010]). L'oubli résiduel de D venait de la RELECTURE d'un petit échantillon.

Bras F (une modification par rapport à A : 2,4 M jetons tirés) : la cible est apprise autant que
dans A (0,233 ; IC du gain [0,013 ; 0,090]) et le texte S'AMÉLIORE (1,784, IC [0,015 ; 0,019]) au
lieu d'oublier (+0,307 pour A ; écart A -> F [0,320 ; 0,344]). Premier jugement : ACCEPT
(`code_repair` held-out même significativement meilleur, 0,803 -> 0,671). **Réplique (graine
1000003) : REJECT** — cible confirmée (IC [0,01 ; 0,09]), texte confirmé (1,753), mais
`code_repair` validation régresse significativement (0,469 -> 0,568, IC [−0,198 ; −0,006], n = 18).
Non promu. L'effet de F sur `code_repair` dépend de la graine : ni amélioration ni oubli établis.

Conclusions (MEASURED, un entraînement par bras + une réplique pour F) :

- la cause principale de l'oubli du TEXTE à ce budget est la **relecture** d'un petit échantillon
  (≈ 10 passages) : à taux égal, 4x plus de données tirées supprime l'oubli (A -> F, D -> E) ;
- réduire le taux (B, D, E) réduit l'oubli mais **supprime l'apprentissage** de la cible (écart
  E -> F sur la cible [0,03 ; 0,11] en faveur de F) : l'hypothèse « le taux remis au maximum est la
  cause principale de l'oubli » est **HYPOTHESIS REJECTED** comme explication principale — c'est un
  facteur, pas la cause (F oublie moins que D avec un taux 10x plus élevé) ;
- le rejeu (C, D) aide peu et coûte de l'apprentissage ;
- F est la meilleure stratégie mesurée (apprend, n'oublie pas le texte) mais **n'est pas promue** :
  sa réplique oublie `code_repair` validation. Le Judge (réplique comprise) a refusé les six bras
  qu'il devait refuser ; aucune capacité acquise n'a été détruite dans le cerveau promu.

## 3. Protection des capacités acquises (IMPLEMENTED, TESTED, ADVERSARIAL)

`apex/neural/protection.py`, branché dans le Judge (`judge(..., protections=)`), le registre
(`ModelRegistry(protected_groups=)`) et l'analyse d'échec de la boucle. Groupes protégés
(`World.protected_groups`) : le texte (suite principale + benchmarks texte) et chaque capacité
(suite d'acceptation + suites du contrat).

Pour chaque groupe, une **ancre** : l'entrée PROMUE et attestée du registre qui a démontré le meilleur
niveau sur la suite-clé, parmi les versions mesurées sur les MÊMES benchmarks (empreintes égales).
L'ancre conserve : benchmark (empreinte), version du modèle (index, nom, artefact), mesures brutes
par élément, métrique, tolérance, historique de toutes les versions comparables.

- **Oubli** d'une suite = après − avant (contre l'ancre, pas contre le seul champion) ; statut
  FORGOTTEN (dégradation significative au bootstrap apparié ou au-delà de la tolérance), IMPROVED
  (amélioration significative), NOISE ; mesures brutes, IC et n conservés dans le jugement.
- **Vérification par le Judge** : il remesure lui-même l'ancre depuis son checkpoint ; si les
  mesures enregistrées ne sont plus celles du checkpoint (registre falsifié), le jugement est REJECT
  (`anchor_<groupe>`) et aucun oubli n'est calculé contre une ancre invérifiable.
- **Refus d'une promotion destructrice** : le Judge ET le registre rejettent tout candidat avec une
  suite FORGOTTEN. Test de dérive cumulée : quatre versions, chacune perd 1 tâche sur 40 (dans la
  tolérance) contre la précédente ; sans ancre la 4e est promue, avec ancre elle est refusée
  (0,575 contre 0,50 démontré).
- **Boucle** : une réplique qui gagne sur la cible mais dégrade une suite protégée est analysée comme
  `protected_regression` (-> stratégie `protect_domains`), et non plus comme « non répliqué »
  (-> plus de pas), ce qui aurait aggravé l'oubli. Défaut trouvé par le bras F, corrigé et testé.

Cycle 2 (adversarial + mutation, worktree isolé) : 28 mutants sur l'ancre (meilleur niveau, versions
rejetées, autre benchmark), la règle d'oubli (tolérance, signification, signe, éléments), le Judge
(oubli sans rejet, ancre crue sur parole, ancre non champion mesurée sur le champion), le registre,
la boucle (versions non attestées, groupes non transmis) et les bras P5 : **28/28 tués**, après deux
corrections de tests révélées par les survivants (test circulaire sur `protected_groups` ; aucun test
ne vérifiait la distinction des problèmes du bras `mixed`). Tests adversariaux sur modèles réels :
un modèle meilleur que son champion mais sous le niveau démontré est refusé (ancre = autre checkpoint,
re-mesurée) ; une entrée de registre forgée non attestée ne devient jamais ancre ; une ancre dont les
mesures enregistrées sont fausses invalide le jugement.

## 4. P4 — profondeur : protocole PRÉ-ENREGISTRÉ (écrit et commité avant toute exécution)

Question : l'échec en profondeur 4 (le modèle écrit toujours 4 valeurs) vient-il de la TAILLE, de la
REPRÉSENTATION ou des DONNÉES ? Outil : `scripts/p4_depth_lab.py` (composants de production :
`ApexLM`, `TrainingEngine`, `TokenBatcher`, `generate_batch`, générateur et évaluateur de
`program_trace`). Un bras = un facteur changé par rapport à `base`, à budget égal (6 000 pas, lots de
32 × 256 jetons : ≈ 49 M jetons vus), même tokenizer (celui du monde), mêmes problèmes d'évaluation.

| bras | facteur changé | profondeurs entraînées |
|---|---|---|
| `base` | — (architecture du cerveau, 0,72 M paramètres, format `a3,b7,c4=4`) | 1–3 |
| `big` | modèle ≈ 4x (2,87 M paramètres : 6 couches, largeur 192) | 1–3 |
| `explicit` | chaque étape recopie l'instruction exécutée : `a=3>3,b=a+4>7,c=b-a>4=4` | 1–3 |
| `curriculum` | 25 % des problèmes d'entraînement en profondeur 4 | 1–4 |

Évaluation : profondeur 3 = suite held-out protégée (300), profondeur 4 = suite `deeper` protégée
(200), profondeur 5 = 200 problèmes neufs (forme canonique absente de tout entraînement et de toute
évaluation, noms tirés au hasard) ; exactitude de TOUTE la trace, longueur de trace écrite.

**Critère de réussite (fixé ici, avant les résultats)** : un bras réussit la profondeur d si la borne
inférieure de l'IC bootstrap à 95 % de son exactitude en profondeur d est > 0,10. La généralisation en
longueur n'est établie qu'à une profondeur JAMAIS entraînée par le bras (5 pour tous ; 4 aussi sauf
`curriculum`). La profondeur 3 ne doit pas régresser par rapport à `base`. Un bras qui réussit est
REJOUÉ avec une seconde graine avant toute conclusion ; seul un bras répliqué peut être intégré au
cerveau (par une expérience jugée par le Judge, avec les protections).

### Résultats P4 (MEASURED, graine 0, 6 000 pas × 32 × 256 jetons par bras, CPU)

| bras | paramètres | temps | prof. 3 exact [IC 95 %] | prof. 4 | prof. 5 | valeurs écrites (prof. 4 / 5) | critère |
|---|---|---|---|---|---|---|---|
| base | 0,72 M | 20 min | 0,790 [0,743 ; 0,833] | 0,0 | 0,0 | 4 valeurs : 161/200, 188/200 | échec |
| explicit | 0,72 M | 19 min | 0,793 (= base, IC de l'écart [−0,03 ; 0,03]) | 0,0 | 0,0 | 4 valeurs : 199/200, 141/200 | échec |
| curriculum (25 % prof. 4) | 0,72 M | 20 min | **0,170** (régression, [−0,68 ; −0,56]) | 0,590 (profondeur ENTRAÎNÉE) | 0,0 | 5 valeurs : 199/200, 190/200 | échec |
| big | 2,87 M | 60 min | **0,997** [0,99 ; 1,0] (+0,16 à +0,25) | 0,0 | 0,0 | 4 valeurs : 176/200, 198/200 | échec |

**HYPOTHESIS REJECTED** (les trois leviers, à ce budget) : aucun bras ne dépasse le critère
pré-enregistré à une profondeur jamais entraînée. Diagnostic mesuré, identique dans les quatre bras :
le modèle écrit le NOMBRE d'étapes vu à l'entraînement (4 ; 5 après le curriculum), pas le nombre
d'instructions du programme — il a appris un gabarit de longueur, pas « suivre les dépendances
jusqu'à la variable demandée ». Plus de capacité perfectionne la profondeur entraînée (0,997) sans
rien transférer ; recopier l'instruction exécutée (`explicit`) ne suffit pas (le modèle saute la
première instruction et garde 4 étapes) ; le curriculum déplace le gabarit (5 étapes partout) et
détruit la profondeur 3. Aucun bras n'ayant réussi, rien à répliquer ni à intégrer au cerveau.
La profondeur 5 reste NON DÉMONTRÉE. Piste non testée ici : un format où la fin de trace dépend
explicitement du programme (compteur d'instructions restantes), à pré-enregistrer.

## 5. P5 — données vérifiées : protocole PRÉ-ENREGISTRÉ (commité avant exécution)

Question : à budget STRICTEMENT égal, des traces écrites par le modèle et VÉRIFIÉES par exécution
enseignent-elles mieux `program_trace` que des traces étiquetées (référence) ou non vérifiées ?

- Quatre mondes enfants du monde P3 (`self-world`, même graine, mêmes 3 000 problèmes neufs tentés
  par le champion P3 attesté) ; seul change le contenu des 2 000 documents du domaine `self_data` :
  A `labeled` (trace de référence), B `unverified` (trace du modèle, forme valide),
  C `verified` (trace du modèle dont le résultat est exact à l'exécution), D `mixed` (moitié A,
  moitié C, problèmes distincts).
- Même champion adopté, même expérience dirigée : cible `cap:program_trace:heldout`, mélange
  `code 0,36 / code_tasks 0,06 / stdlib 0,06 / self_data 0,52` (le réservoir `reasoning_tasks`
  exclu : seule la source du bras apporte des traces), 1 500 pas, 2,4 M jetons tirés, lr 1e-3
  (régime F, sans oubli du texte mesuré en §2). Même nombre de documents, même nombre de pas,
  mêmes jetons vus ; nombre de jetons par bras consigné (les traces fausses peuvent différer en
  longueur).
- Mesures : Judge complet (cible, texte, `code_repair`, oubli contre les ancres) + comparaisons
  appariées ENTRE bras sur les mesures du Judge (mêmes 300 problèmes held-out).

**Critère (fixé avant les résultats)** : l'hypothèse « la vérification apporte plus que les
étiquettes » n'est retenue que si C bat A ET B sur `program_trace:heldout` (IC 95 % du bootstrap
apparié de l'écart d'erreur excluant 0, en faveur de C) sans oubli (verdict du Judge). Sinon :
**HYPOTHESIS REJECTED**, sans changer de métrique ni de budget après coup.

### Résultats P5 (MEASURED, un entraînement par bras, budget identique)

Même champion, mêmes 3 000 problèmes neufs tentés (le champion en résout 72 %), 2 000 documents par
bras (≈ 78 000 jetons chacun : 78 151 / 78 204 / 77 949 / 78 032), 1 500 pas, 6,1 M jetons vus,
même mélange. Erreur `program_trace` held-out (avant 0,283) :

| bras | held-out | validation | texte (BPB) | `code_repair` validation | oubli (ancre) | Judge |
|---|---|---|---|---|---|---|
| A labeled | 0,270 (n.s.) | 0,275 | 1,801 -> 1,788 | 0,469 -> 0,593 | `code_repair` validation | REJECT |
| B unverified | **0,327 (pire, significatif)** | 0,360 | 1,789 | 0,531 | `program_trace` held-out/train ; `code_repair` validation | REJECT |
| C verified | 0,283 (inchangé) | 0,310 | 1,787 | 0,543 | `code_repair` validation | REJECT |
| D mixed | 0,283 (inchangé) | 0,280 | 1,788 | 0,512 (n.s.) | aucun | INCONCLUSIVE |

Comparaisons appariées entre bras (held-out, 300 problèmes) : C bat B (écart [0,003 ; 0,083]) ;
C contre A : non significatif ([−0,03 ; 0,06], A légèrement meilleur) ; D contre A et C : non
significatif ; D bat B sur la validation.

**HYPOTHESIS REJECTED** (critère pré-enregistré : C doit battre A ET B) : la vérification empêche
le mal que font les traces non vérifiées (B dégrade la capacité), mais n'apporte rien de mesurable
par rapport au même nombre de traces étiquetées. Explication compatible avec les mesures : les
traces vérifiées portent sur les problèmes que le modèle résout déjà (72 %), alors que les
étiquettes couvrent aussi ceux qu'il rate. Aucun bras n'améliore significativement la cible à ce
budget ; seul D n'oublie rien. Non répliqué (un entraînement par bras).

## 6. Transfert : protocole PRÉ-ENREGISTRÉ (commité avant exécution)

Nouvelle capacité `dependency_order` (instructions mélangées, trace dans l'ordre d'exécution :
planification de l'ordre imposé par les dépendances), benchmark neuf, protégé, disjoint de
`program_trace` (voir `apex/capabilities/dependency_order`).

Échelle (`scripts/transfer_ladder.py`, champions P2 et P3, mêmes problèmes) : 1 domaine entraîné
(`program_trace:train`), 2 même forme held-out, 3 forme nouvelle (question sur une variable
intermédiaire), 4 tâche apparentée (`dependency_order:heldout`, sans entraînement), 5 tâche
lointaine (`code_repair:heldout`).

Apprentissage de la tâche apparentée : bras T = champion P3 (a appris `program_trace`), bras C =
champion P2 (jamais de traces), dans deux mondes frères aux benchmarks IDENTIQUES (empreintes
vérifiées) ; même expérience dirigée : cible `cap:dependency_order:heldout`, mélange
`order_tasks 0,52 / code 0,36 / code_tasks 0,06 / stdlib 0,06` (aucune trace `program_trace`),
1 500 pas, 2,4 M jetons, lr 1e-3.

**Critère** : transfert établi si l'erreur de T après entraînement est inférieure à celle de C
(IC 95 % apparié sur les 300 problèmes excluant 0). Aucun barreau n'autorise « raisonnement
général » : au mieux, transfert mesuré vers UNE tâche apparentée.

### Résultats du transfert (MEASURED, REAL E2E, répliqué)

Mondes frères T (parent P3) et C (parent P2) : benchmarks `program_trace`, `dependency_order` et
`code_repair` d'empreintes IDENTIQUES (vérifié). Défaut trouvé en construisant T : le contrôle de
filiation du monde a refusé le benchmark (5 documents d'entraînement de `program_trace` partageaient
une séquence de 12 jetons de code avec des problèmes évalués : même préfixe, mêmes noms) ; le
constructeur applique désormais la règle du monde (47 problèmes retirés), test ajouté.

Échelle, sans entraînement (champions P3 et P2, mêmes problèmes) :

| barreau | P3 | P2 | écart (IC 95 %) |
|---|---|---|---|
| 1 domaine entraîné (`program_trace:train`) | 0,68 | 0,0 | [0,62 ; 0,75] |
| 2 même forme held-out | 0,717 | 0,0 | [0,67 ; 0,76] |
| 3 forme nouvelle (question intermédiaire) | **0,0** (écrit tout le programme : 100 %) | 0,0 | 0 |
| 4 tâche apparentée (`dependency_order`) | **0,0** (lit de gauche à droite : 68 %) | 0,0 | 0 |
| 5 tâche lointaine (`code_repair` held-out, résolues) | 0,309 | 0,296 | [−0,10 ; 0,12] |

Apprentissage de la tâche apparentée (même recette, 1 500 pas, 2,4 M jetons, sans traces
`program_trace`), erreur `dependency_order` held-out (avant : 1,0 pour les deux) :

| graine | T (depuis P3) | C (depuis P2) | écart T − C (IC 95 %) | `program_trace` après T | Judge T / C |
|---|---|---|---|---|---|
| monde | 0,970 | 0,997 | [0,007 ; 0,047] | 0,283 -> 0,953 (oubli) | REJECT / INCONCLUSIVE |
| 1000003 (réplique) | 0,957 | 1,000 | [0,023 ; 0,070] | 0,283 -> 0,930 (oubli) | REJECT / INCONCLUSIVE |

Conclusions : **transfert établi et répliqué selon le critère pré-enregistré, mais FAIBLE** (9 à 13
problèmes de plus sur 300 ; la tâche reste échouée à ≈ 96 %) ; sans entraînement, aucun transfert
aux barreaux 3 et 4 — l'acquis P3 reste lié à sa forme (mémorisation de la procédure dans son
format, pas une compétence de dépendance réutilisable). Apprendre la tâche apparentée sans rejeu
DÉTRUIT `program_trace` dans les deux graines : le Judge le refuse. Aucune promotion. Ce n'est en
aucun cas du « raisonnement général ».

## 7. Boucle autonome : « X s'améliore mais Y se dégrade » (REAL E2E, deux défauts corrigés)

Protocole : monde P3 (texte + `code_repair` + `program_trace`), champion P3 adopté, `run` avec
1 500 pas / 2,4 M jetons par expérience, protections actives (Judge, registre, réplique).

**Premier passage (défaut 1)** : faiblesse choisie `code_repair` (écart le plus grand) ;
1. `target_only` (code seul) : texte amélioré, mais `program_trace` entièrement oublié
   (0,283 -> 1,0) et `code_repair` validation/train oubliés -> REJECT (oubli contre l'ancre) ;
2. `lower_lr` (0,001 -> 0,0005) : oubli de `code_repair` réduit (train 0,143 -> 0,032, significatif),
   `program_trace` toujours à 1,0 -> REJECT ;
3. la politique divisait ENCORE le taux : elle n'utilisait pas l'effet mesuré de sa modification.

Correction : `lever_effect` compare, par élément (bootstrap apparié), le candidat de l'expérience
à celui de l'expérience précédente sur chaque suite que la précédente dégradait :
levier utile partout -> répétable ; utile à X seulement -> gardé, AUTRE levier pour Y (le même
réglage scalaire n'est pas répété) ; utile nulle part -> annulé (on repart de la recette
précédente). Le rejeu vise les domaines dégradés qui ne sont pas déjà entraînés.

**Deuxième passage (défaut 2)** : 3. `protect_domains` (25 % `reasoning_tasks`) CORRIGE
`program_trace` (1,0 -> 0,32, IC du gain [0,63 ; 0,73]) sans aider `code_repair` ; la règle ne
comptait que les suites ENCORE dégradées : l'effet sur `program_trace` était ignoré, le levier
jugé inutile et annulé. Correction : une suite corrigée compte comme un effet ; et une MÉMOIRE le
long de la chaîne d'expériences (`helpful_levers`) propose d'abord le levier qui a déjà réduit
l'oubli des suites encore dégradées. Rejouée sur le journal réel, la décision suivante devient :
garder le rejeu (utile à `program_trace`) ET diviser le taux (utile à `code_repair` en 1 -> 2),
chaque étape citant sa mesure (ESTABLISHED) et son inférence (INFERRED).

Cycle 2 : 9 mutants de la stratégie (effet ignoré, levier partiel répété, levier inutile gardé,
signification ignorée, signe inversé, rejeu du domaine entraîné, suite corrigée non comptée,
mémoire ignorée, mémoire limitée au dernier maillon) : **9/9 tués**.

## 8. Capacité `dependency_order` (IMPLEMENTED, TESTED, ADVERSARIAL)

Choisie pour le meilleur test de transfert : même arithmétique et même format de sortie que
`program_trace` (transfert possible), mais une compétence nouvelle — les instructions sont
présentées dans le désordre (`c=b-a;a=3;b=a+4?c:`) et la trace doit suivre l'ordre imposé par les
dépendances. Lire de gauche à droite échoue (la notation l'identifie : `textual_order`) ; les
noms comptent dans la métrique (l'ordre est l'objet du raisonnement).

Garanties : vérité terrain exécutée dans le sandbox (programme ordonné) ; résolveur hôte
indépendant qui retrouve l'ordre depuis le seul texte mélangé ; présentation jamais dans l'ordre
d'exécution ; fraîcheur par forme canonique du programme ORDONNÉ (mélanger ou renommer ne rend pas
un problème nouveau) ; exclusions croisées avec `program_trace` (jamais évalué ici un programme
entraîné là-bas ; jamais entraîné ici un programme évalué là-bas) ; formes mélangée ET ordonnée des
problèmes évalués protégées contre toute citation extérieure. 14 tests ; cycle 2 : 13 mutants
(résolveur, mélange, notation, exclusions à la construction et à la relecture, vérité terrain,
protections) **13/13 tués** après l'ajout d'un test (l'exclusion des évaluations de
`program_trace` n'était jamais exercée par le hasard du tirage) et la suppression de deux gardes
redondantes (mutants équivalents : la couverture de la chaîne les subsumait).

**Passage final (code corrigé, cycle 3, REAL E2E, 6 expériences, 9 000 pas, ≈ 60 min)** :

| # | stratégie (choisie par la boucle) | modification | cible (avant -> après) | oubli contre l'ancre | Judge |
|---|---|---|---|---|---|
| 1 | target_only | mix `code` 1,0 | code_repair 0,770 -> 0,761 (n.s.) | program_trace 0,283 -> 1,0 ; code_repair train/validation | REJECT |
| 2 | lower_lr | lr 0,0005 | 0,770 -> 0,728 (n.s.) | program_trace -> 1,0 ; code_repair validation 0,469 -> 0,574 | REJECT |
| 3 | protect_domains (lr gardé, aide code_repair) | + 25 % `reasoning_tasks` | 0,770 -> 0,712 (n.s.) | **program_trace corrigé** ; code_repair validation 0,568 | REJECT |
| 4 | lower_lr (mémoire : a aidé code_repair) | lr 0,00025, rejeu gardé | **0,770 -> 0,700 (significatif)** | code_repair validation 0,469 -> 0,556 (n = 18) | REJECT |
| 5 | protect_domains (cible suivante : program_trace) | 75 % `reasoning_tasks` | **0,283 -> 0,233 (significatif)** | texte (+0,17 à +0,21 BPB) ; code_repair validation | REJECT |
| 6 | protect_domains | rejeu du texte relevé | 0,283 -> 0,250 (n.s.) | texte toujours oublié ; code_repair validation | REJECT |

Constats (MEASURED) : la boucle observe « X s'améliore mais Y se dégrade » et change de levier à
partir des mesures (1 -> 2 -> 3 -> 4) ; chaque changement cite l'effet mesuré du précédent. Les
protections ont refusé les six candidats, dont deux qui amélioraient significativement leur cible :
aucune capacité acquise n'a été détruite, le champion P3 reste en service. **Aucune promotion** : en
6 expériences, la boucle n'a pas trouvé de recette qui apprend sans oublier (`code_repair`
validation, 18 tâches, reste dégradée dans tous les bras). La meilleure recette connue pour
`program_trace` (bras F, §2 : 2,4 M jetons, 48 % d'anciennes données) n'est pas celle que la
politique construit (elle part du seul domaine cible puis relève le rejeu par paliers).
