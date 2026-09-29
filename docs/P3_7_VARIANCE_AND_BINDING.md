# P3.7 — Apprentissage continu, contrôle de la variance, liaison explicite

Suite de `docs/P3_5_CONTINUAL_AND_LIVE.md` (P3.5/P3.6). Étiquettes : OBSERVED (lu dans le code ou
les fichiers), TESTED (test automatisé), MEASURED (mesure réelle), JUDGED (Judge indépendant),
REPLICATED, REAL E2E, INFERRED, HYPOTHESIS, NOT_AVAILABLE, BLOCKED. Ce document est écrit dans
l'ordre chronologique : chaque protocole est commité avant ses résultats.

## 1. Audit initial (HEAD `5890304`, branche `claude/apex-official-development-4kao8t`, arbre propre)

| élément | état réel | niveau |
|---|---|---|
| Modèle | `ApexLM` 724 864 paramètres (RoPE, vocab 1024, contexte 256, 3 couches) | IMPLEMENTED, TESTED |
| Monde | `world.json` gelé : splits, tokenizer, benchmarks de capacité, empreinte ; mondes enfants (`derive-world`) | IMPLEMENTED, TESTED, REAL E2E |
| Registre | `registry.jsonl` chaîné (sha256, sans clé) ; `propose` REJUGE lui-même la proposition : `NeuralJudge` contre le champion + oubli contre les ancres, règles MONO-GRAINE | IMPLEMENTED, TESTED |
| Journal | `journal.jsonl` chaîné ; la reprise repart du dernier événement | IMPLEMENTED, TESTED, REAL E2E (arrêt et arrêt brutal, P3.5) |
| Attestation | promotion justifiée au journal + mesures du registre = mesures du Judge au journal | IMPLEMENTED, TESTED, MUTATION (P3.5) |
| Judge (`IndependentJudge`) | re-mesure le candidat deux fois, contamination, provenance par rejeu bit à bit, référence re-mesurée, oubli contre les ancres, bootstrap apparié par élément (2 000 tirages, IC 95 %) | IMPLEMENTED, TESTED, ADVERSARIALLY VERIFIED |
| Règle de régression | une suite est « oubliée » si l'IC apparié par élément exclut 0 du côté dégradé OU si la dégradation agrégée dépasse la tolérance (0,01 BPB texte ; 0,05 capacités) — UNE graine suffit | IMPLEMENTED, TESTED ; **insuffisante** (§2) |
| Réplication | seulement si le premier candidat est ACCEPTÉ ; graine +1 000 003 ; la réplique consigne ses COMPARAISONS agrégées, **pas ses mesures par élément** | IMPLEMENTED, TESTED, REAL E2E |
| A/A | AUCUN mécanisme permanent : l'A/A de P3.5 a été fait à la main (scripts hors dépôt) | NOT_AVAILABLE dans le moteur |
| Variance par suite | non modélisée ; aucune distinction bruit / régression | NOT_AVAILABLE |
| Ancres (`protections`) | pour chaque groupe : version PROMUE et attestée qui a le MEILLEUR niveau sur la suite-clé, benchmarks identiques ; ses mesures sont celles d'un artefact réel | IMPLEMENTED, TESTED |
| StrategyPolicy | cause établie -> une modification ; mémoire des leviers ; `max_passes` par domaine (P3.5) | IMPLEMENTED, TESTED, REAL E2E |
| Rejeu (replay) | part des domaines dégradés relevée par paliers (25/50/75 %) | IMPLEMENTED, MEASURED (réduit l'oubli, ne le supprime pas) |
| Télémétrie | `runs/<exp>/telemetry.jsonl`, non décisionnelle | IMPLEMENTED, TESTED, REAL E2E |
| API `/brain` + UI `/brain` | lecture seule des fichiers du moteur ; états EN COURS / À L'ARRÊT / INTERROMPUE ; ATTESTÉ seulement si attesté | IMPLEMENTED, TESTED, MUTATION, REAL E2E |
| États RECOVERING / COMPLETED | non distingués (une reprise apparaît comme « en cours ») | NOT_AVAILABLE |
| Persistance | monde, registre, journal, artefacts (`artifacts/`, adressés par contenu) et checkpoints de reprise vivent dans UN répertoire de travail choisi par l'opérateur ; aucun outil de vérification d'intégrité d'un répertoire complet | OBSERVED ; outil de vérification NOT_AVAILABLE |
| `dependency_order` | capacité réelle, format implicite `a3,b7,c4=4` ; jamais acquise depuis le champion (0,97–0,99) | MEASURED, JUDGED |

Provenance des mesures (OBSERVED) : toute mesure qui décide est calculée par le Judge sur
l'artefact relu depuis le store (hash vérifié) ; les mesures déclarées par l'exécuteur ne servent
qu'au contrôle `claimed_metrics_match` ; le registre stocke les mesures du Judge.

### 1.1 Variance entre graines : ce que les données existantes montrent (MEASURED)

Paires de MÊME recette ne différant que par la graine, déjà présentes dans les journaux réels
(affinages depuis un champion ; l'amorçage depuis l'initialisation, autre régime, est exclu) :
P2 `exp-a10e1cff3488` et sa réplique, P3 `exp-f0f4ed9a92d6` et sa réplique, A/A de P3.5.
Écart-type entre graines estimé par suite : σ = √(moyenne(écart²)/2).

| suite | écarts observés entre deux graines | σ graine | tolérance actuelle |
|---|---|---|---|
| `program_trace:heldout` | 0,163 ; 0,103 | **0,097** | 0,05 |
| `program_trace:validation` | 0,165 ; 0,050 | 0,086 | 0,05 |
| `program_trace:train` | 0,125 ; 0,075 | 0,073 | 0,05 |
| `code_repair:validation` (n = 18) | 0,049 ; 0,068 ; 0,062 | 0,043 | 0,05 |
| `code_repair:heldout` | 0,041 ; 0,012 ; 0,049 | 0,027 | 0,05 |
| `code_repair:train` / `transfer` | — | 0,021 / 0,016 | 0,05 |
| `heldout:stdlib` (BPB) | 0,003 ; 0,035 ; 0,018 | **0,016** | 0,01 |
| `heldout` / `heldout:code` / `validation` (BPB) | ≤ 0,014 | 0,007 / 0,005 / 0,006 | 0,01 |

Constat : sur `program_trace` et `heldout:stdlib`, la variance d'une simple re-graine DÉPASSE la
tolérance ; les règles mono-graine y confondent bruit et oubli (c'est ce qui a rejeté les deux
continuations A/A de P3.5). Estimation fondée sur 2 à 3 paires seulement : elle-même incertaine.

### 1.2 Ancres et « winner's curse » (MEASURED sur les registres existants)

L'ancre d'un groupe est le MAXIMUM sélectionné parmi les versions promues (meilleure valeur de la
suite-clé), pas une moyenne ni une distribution ; ses mesures sont exactes pour cet artefact.
Registres existants : P2 (2 versions promues), P3 (adoption + `exp-f0f4ed9a92d6`), mondes du
cycle 1 et suivants (une seule version) — **dans tous, l'ancre de chaque groupe EST le champion en
service** ; aucune sélection parmi des versions bruitées n'a encore eu lieu. De plus, la version
promue est toujours le candidat de la graine planifiée (jamais « la meilleure des deux graines ») :
le champion P3 (0,480) est même MOINS bon que sa réplique (0,317). La malédiction du gagnant par
sélection d'ancre est donc NON DÉMONTRÉE sur les données existantes : conformément à la consigne,
le mécanisme d'ancrage n'est PAS modifié. Le risque réel mesuré est autre : l'ancre est UN tirage
d'une recette bruitée — la variance est traitée par la procédure de §2 (marge et incertitude),
jamais en déplaçant l'ancre.

### 1.3 Bilan de l'audit

- Existe et fonctionne : monde gelé, Judge indépendant avec rejeu, attestation, chaînes, reprise,
  réplique à graine indépendante, APEX Live, politique avec `max_passes`.
- Insuffisant : (1) une seule graine décide de l'oubli ; (2) la réplique n'est faite qu'après un
  ACCEPT et sans mesures par élément ; (3) aucune estimation de variance, aucun statut « bruit » ;
  (4) le registre rejuge avec les mêmes règles mono-graine (il annulerait toute règle plus fine
  appliquée ailleurs) ; (5) pas de format de liaison explicite ; (6) pas de vérification
  d'intégrité d'un répertoire complet ; (7) états RECOVERING / COMPLETED absents de l'interface.
- À modifier : décision de promotion multi-graines, dans le contrôleur ET recalculée par le
  registre ; réplications consignées avec leurs mesures par élément ; capacité à liaison explicite ;
  vérification d'intégrité ; états de l'interface.
- À NE PAS modifier : le Judge par artefact (intégrité, contamination, rejeu, re-mesure) ; le
  choix des ancres (§1.2) ; les tolérances du monde ; `max_passes` ; aucun second moteur.

## 2. Règles statistiques PRÉ-ENREGISTRÉES (commitées avant leur implémentation et avant toute expérience P3.7)

Unité expérimentale : une RECETTE entraînée avec k graines indépendantes (k = 2 au départ ; 3 au
plus). Chaque graine produit un artefact réel, jugé par le Judge existant (intégrité, contamination,
rejeu bit à bit, re-mesure) ; seules les mesures par élément du Judge entrent dans les calculs.

Pour chaque suite s d'un groupe protégé (`world.protected_groups`), référence R = l'ancre du groupe
(mécanisme existant, inchangé : un artefact réel promu et attesté, benchmarks gelés) :
- D_i = valeur(C_i) − valeur(R) pour la graine i (métriques « plus bas = mieux » ; > 0 = pire), avec
  l'agrégation existante d'`EvalReport` (pondérée par les octets pour le BPB) ; D̄ = moyenne des D_i.
- Incertitude d'échantillonnage des éléments : bootstrap apparié sur les éléments (2 000 tirages,
  graine 0) de D̄, recalculé avec la même agrégation ; SE_item = écart-type bootstrap.
- Variance entre graines : σ_s = max(σ_prior,s ; s_obs,s), où s_obs est l'écart-type (ddof = 1) des
  D_i de l'expérience et σ_prior l'estimation groupée (Σ carrés des écarts à la moyenne des graines /
  Σ (k_j − 1)) sur l'HISTORIQUE des réplications disponibles AU MOMENT DE LA PLANIFICATION : paires
  ou triplets de même recette (même plan, graines différentes), affinages depuis un champion
  (`init = champion`), benchmarks de même empreinte. L'historique importé pour P3.7 est celui de §1.1
  (P2 `exp-a10e1cff3488`, P3 `exp-f0f4ed9a92d6`, A/A de P3.5), consigné au journal avec sa provenance.
- Incertitude totale : U = 1,96 × √(SE_item² + σ_s² / k).
- Marge de régression ACCEPTABLE (définie ici, avant les résultats) : m_s = max(tolérance du monde
  pour la métrique ; σ_prior,s). Justification : une dégradation plus petite que l'écart produit par
  une simple re-graine de la recette du champion n'est pas attribuable à la nouvelle capacité ; les
  tolérances du monde restent le plancher (jamais abaissées, jamais relevées au-delà de σ mesuré).

Statut par suite :
- **REGRESSION** : D̄ − U > m_s ET D_i > 0 pour toutes les graines (dégradation au-delà de la marge,
  reproduite) ;
- **IMPROVEMENT** : D̄ + U < 0 ;
- **NO_REGRESSION** : D̄ + U ≤ m_s (toute dégradation éventuelle reste dans la marge, avec confiance) ;
- **VARIANCE_INCONCLUSIVE** : sinon (l'intervalle chevauche la marge).
Statut de la capacité visée : **MEMORIZATION** si sa suite train est IMPROVEMENT sans que sa suite
held-out le soit. **INVALID_EXPERIMENT** si un contrôle d'intégrité du Judge échoue pour UNE graine
(`candidate_integrity`, `no_contamination`, `candidate_reproducible`, `claimed_metrics_match`,
`baseline_integrity`, `baseline_unchanged`, `candidate_differs`, `declared_data_matches_recipe`,
`training_replayed`, contrôles d'ancre).

Décision de l'expérience (dans cet ordre) :
1. INVALID_EXPERIMENT si une graine est invalide (aucune graine supplémentaire) ;
2. REJECT si une suite protégée est REGRESSION ou si la capacité visée est MEMORIZATION ;
3. REJECT si la cible n'est pas IMPROVEMENT alors que l'intervalle l'exclut (D̄ − U ≥ 0 : aucun gain) ;
4. ACCEPT si la cible est IMPROVEMENT, si toutes les suites protégées sont NO_REGRESSION ou
   IMPROVEMENT, et si l'ARTEFACT promu (celui de la graine planifiée, jamais « la meilleure graine »)
   n'est lui-même démontrablement au-delà d'aucune marge (D_0 − 1,96·SE_item,0 ≤ m_s) ;
5. sinon (au moins un VARIANCE_INCONCLUSIVE) : une graine de plus si k < 3, puis on recalcule ;
   à k = 3, **INCONCLUSIVE** — aucune promotion.
Seuil d'ACQUISITION d'une nouvelle capacité (ajouté à la condition 4 quand le plan vise une
capacité) : borne haute de l'erreur moyenne held-out (moyenne + U) < 0,90, soit une exactitude
démontrée > 0,10 (même seuil que les laboratoires P4).

Garanties voulues : « une graine gagne » ne promeut jamais (k ≥ 2, cible et gardes sur toutes les
graines) ; « une graine perd » ne déclare jamais un oubli (REGRESSION exige la reproduction du signe et
un intervalle au-delà de la marge). Le registre RECALCULE lui-même ce verdict depuis les mesures de
toutes les graines avant d'inscrire une promotion. Limites connues : σ_prior repose sur 2–3 paires ;
ajouter une graine après avoir vu les deux premières est une procédure séquentielle (prévue ici,
bornée à 3). Les règles seront aussi appliquées, à titre d'ILLUSTRATION seulement, aux expériences
P3.5 déjà faites : ce résultat ne servira à régler aucun seuil.

### 2.1 Précision d'interprétation (écrite avant toute expérience P3.7)

Cas non couvert littéralement par l'ordre des décisions : si aucune suite n'est
VARIANCE_INCONCLUSIVE mais que le seuil d'acquisition (ou la condition sur l'artefact promu) n'est
pas atteint, la décision est REJECT (une graine de plus ne changerait pas une condition déjà
tranchée sans ambiguïté). Implémentation : `apex/neural/variance.py` (fonction pure), consignée par
le contrôleur (`seed_verdict` au journal, historique de variance figé dans l'événement `planned`),
RECALCULÉE par le registre avant toute promotion.

### 2.2 Illustration sur l'A/A de P3.5 (ne règle aucun seuil)

Les deux continuations A/A (même recette, graines 0 et 1 000 003), jugées à l'origine REJECT toutes
deux pour « oubli » par la règle mono-graine, donnent avec les règles de §2 : **INCONCLUSIVE**
(`heldout:stdlib` +0,027 ± 0,024 pour une marge de 0,016 ; `program_trace:heldout` −0,025 ± 0,142 ;
`code_repair:validation` +0,031 ± 0,076) — le protocole demanderait une troisième graine. Ni oubli
confirmé, ni le gain « GENERALIZATION » de la seule graine A. Circularité connue : la paire A/A fait
partie de l'historique σ utilisé ici ; c'est pourquoi ce n'est qu'une illustration.

## 3. Expérience principale PRÉ-ENREGISTRÉE (commitée avant l'exécution du premier bras)

**Monde** `sha256:6cd85c74…` : enfant du monde P3 (+ `dependency_order_explicit`, 30 500 problèmes
vérifiés par exécution sandbox ; held-out 300 problèmes neufs, jamais entraînés ; 744 candidats
rejetés pour recouvrement de 12-grammes avec l'entraînement `program_trace`, 3 documents retirés
par la décontamination du monde). Champion P3 ATTESTÉ adopté (`sha256:da8bf910…`), re-mesuré :
erreur 1,000 sur les trois suites de la nouvelle capacité, autres mesures identiques à P3.
Historique de variance importé (événement `replication_history`) : exactement §1.1.

**Hypothèses** : H1 (acquisition) au moins un bras qui entraîne la nouvelle capacité atteint le
seuil d'acquisition sur ≥ 2 graines ; H2 (protection) les bras avec rejeu (B, C) ont moins de suites
en REGRESSION que A ; H3 (adaptatif) C protège `program_trace` mieux que B (comparaison appariée
candidat contre candidat, graine par graine, secondaire) ; H4 (témoin) D ne présente aucune
REGRESSION.

**Bras** — même modèle de départ (champion adopté), même budget : 6 000 pas, lots 16 × 256,
9,6 M jetons tirés, lr 1e-3 ; une seule modification par rapport à la recette de référence
(le mélange) ; graines appariées entre bras (graine du monde 0, puis 1 000 003, puis 2 000 006) :

| bras | mélange | relectures tirées par domaine (jetons tirés / jetons uniques) |
|---|---|---|
| A — nouvelle capacité seule | `order_explicit_tasks` 1,0 | explicite 7,2 |
| B — rejeu fixe (proportions du champion) | explicite 0,40 ; code 0,27 ; `reasoning_tasks` 0,24 ; `stdlib` 0,09 | 2,9 ; 2,0 ; 1,2 ; **11,4** |
| C — rejeu adaptatif (règle ci-dessous) | explicite 0,40 ; `reasoning_tasks` 0,35 ; code 0,22 ; `stdlib` 0,03 | 2,9 ; 1,8 ; 1,6 ; 3,8 |
| D — témoin, allocation standard du champion, SANS nouvelle capacité | code 0,45 ; `reasoning_tasks` 0,40 ; `stdlib` 0,15 | 3,3 ; 2,0 ; **19,0** |

Règle de C (dérivée des MESURES antérieures, pas des résultats de ce protocole) : même part de
nouvelle capacité que B (0,40) ; `reasoning_tasks` (domaine de `program_trace`, oublié à 20 % et 25 %
de rejeu en P3.5) porté au-delà de ces niveaux (0,35) ; `stdlib` plafonné par `max_passes` (≤ 4
relectures : 0,03) ; le reste au code. B et C ne diffèrent que par la répartition du rejeu.

**Exécution** : chaque bras via `directed` (boucle réelle, Judge indépendant avec rejeu bit à bit,
protocole multi-graines de §2 : 2 graines, une 3e si VARIANCE_INCONCLUSIVE), chacun dans sa propre
copie du répertoire (aucune promotion d'un bras n'affecte un autre), dans l'ordre D, A, B, C,
l'un après l'autre. Métriques primaires : décision multi-graines par bras ; erreur held-out de la
nouvelle capacité (moyenne ± U). Secondaires : lignes par suite (REGRESSION / NO_REGRESSION /
IMPROVEMENT / VARIANCE_INCONCLUSIVE), mémorisation (train contre held-out), transfert
(`code_repair:transfer`, `program_trace`), coût (pas, durée), stabilité (écart entre graines),
reproductibilité (rejeu bit à bit de chaque graine).

**Promotion** : uniquement par le verdict de §2, recalculé par le registre. Si plusieurs bras sont
ACCEPTÉS, la lignée continue avec le premier dans l'ordre fixé B, C, A (jamais « le meilleur »).
**Arrêt** : 3 graines au plus par bras ; aucun bras ajouté à ce protocole après les résultats ; toute
variante nouvelle (cycle 3) fera l'objet d'un pré-enregistrement séparé. Si aucun bras n'est accepté :
AUCUNE promotion, le champion P3 reste en service.

## 4. Cycle 2 sur les mécanismes (pendant l'exécution des bras)

**Mutation testing** (worktree isolé, PYTHONPATH vérifié, sources restaurées et vérifiées par
empreinte ; un mutant n'est tué que si un test ÉCHOUE avec lui) : **17 mutants, 16 tués**. Tués :
marge calculée sur les graines du candidat, variance entre graines retirée de U, pas de graine
supplémentaire en cas de doute, seuil d'acquisition retiré, mémorisation ignorée, graines invalides
ignorées, doute traité comme acceptation, décision sur UNE graine, mesures de la réplique non
consignées, intégrité de la réplique ignorée, recalcul du registre contourné, réponse explicite au
format implicite, instructions recopiées non vérifiées, format du benchmark non vérifié au chargement,
états RECOVERING / INTERRUPTED jamais affichés. **Survivant : ÉQUIVALENT** (« condition sur l'artefact
promu » retirée) — pour k ≥ 2, U contient l'écart observé entre graines (U ≳ 0,98·|d₀ − d₁|), donc
`moyenne + U ≤ marge` implique d₁ > d₀, incompatible avec d₀ au-delà de la marge : aucune entrée ne
peut distinguer le mutant. La condition reste (défense en profondeur, pré-enregistrée).

**Attaques couvertes par des tests** : réplique invalide (rejeu en échec injecté) -> expérience
INVALIDE, cause `undeclared_training`, jamais promue ; contrôleur qui annonce ACCEPT avec des
graines montrant une régression reproduite -> le REGISTRE, qui recalcule, refuse ; benchmark
explicite forgé (format retiré d'un problème) ou chargé par la mauvaise capacité -> refusé ;
réponse au mauvais format ou instruction mal recopiée -> coût 1.

**Persistance** (audit §1) : tout ce qui doit survivre tient dans le répertoire de travail —
`world.json` (monde, benchmarks, empreintes), `journal.jsonl`, `registry.jsonl`, `artifacts/`
(checkpoints adressés par contenu : champion, candidats, graines), `runs/*/resume.pt` (reprise d'un
entraînement en cours). Après adoption, un monde enfant est autonome (le checkpoint du parent est
recopié dans son store, l'adoption est consignée au journal). Ajout : `verify` (lecture seule) —
monde relu avec ses empreintes, chaînes, journal produit sur ce monde, attestation de chaque version
promue, CHAQUE artefact référencé re-haché, expériences à reprendre. Testé (copie propre -> OK ; octet
inversé dans le checkpoint du champion -> « altéré » ; fichier supprimé -> « absent ») et essayé sur
les répertoires réels de P3.5 (P3 et 3b : OK ; le registre forgé de C2.2 : non attesté). Survivre à
la perte du CONTENEUR exige que ce répertoire soit sur un disque persistant : dans ce bac à sable,
seul git persiste et les artefacts (≈ 9 Mo par checkpoint, monde ≈ 32 Mo) n'y ont pas leur place —
BLOCKED ici, à configurer par l'opérateur (répertoire de travail sur disque persistant).

## 5. Boucle autonome : scénario « mauvaise stratégie » (PRÉ-ENREGISTRÉ, avant exécution)

**Constat préalable (MEASURED, passage à blanc, aucun entraînement)** : sur le monde explicite
vierge, le premier choix de la politique est `target_only` (`order_explicit_tasks` 1,0) — plan
IDENTIQUE à celui du bras A (même recette, même graine, même référence ; comparaison des recettes
consignées). L'entraînement étant déterministe (reprise et rejeu bit à bit démontrés en P3.5 et par le
Judge à chaque graine), le résultat réel du bras A EST le résultat de ce premier choix : REJETÉ, 8 suites
en REGRESSION reproduite sur 2 graines. Le scénario reprend donc la boucle depuis ce journal.

**Défaut trouvé par ce passage à blanc, corrigé avant exécution** (`4018d64`) : après ce rejet, la
politique renonçait au rejeu (pas d'apprentissage divisé par deux) parce qu'UN domaine épuisé
(`stdlib`) aurait dépassé `max_passes` ; désormais le domaine épuisé est écarté et les autres domaines
dégradés sont rejoués. Choix de la politique corrigée (à blanc) : `protect_domains`,
`order_explicit_tasks` 0,75, `code` 0,125, `reasoning_tasks` 0,125 (`stdlib` écarté : > 4 relectures).

**Protocole** : copie du répertoire du bras A ; `run` (politique inchangée depuis `4018d64`, protocole
multi-graines : 2 graines, 3 au plus), 1 cycle, 1 expérience, échelle 6 000 pas / 9,6 M jetons /
lr 1e-3. **Critères** : (a) la boucle ne répète pas sa stratégie rejetée et en choisit une justifiée par
la cause établie (vérifié par le journal) ; (b) moins de suites en REGRESSION reproduite que le bras A
(8). Promotion uniquement par le verdict de §2. **Prédiction écrite avant** : acquisition probable de
la nouvelle capacité (75 % du mélange) ; `stdlib`, non rejoué, sans doute encore en REGRESSION ;
`program_trace` partiellement protégé ; issue la plus probable : REJECT.

## 6. Résultats de l'expérience principale (JUDGED, protocole multi-graines)

Chaque graine : entraînement réel de 6 000 pas, Judge indépendant (re-mesure double, contamination,
provenance par REJEU bit à bit : réussi pour chaque graine de chaque bras), puis verdict de §2 consigné
et recalculable. Valeurs : erreur (capacités) ou BPB (texte), plus bas = mieux ; Δ moyen ± U ; m = marge.

**Bras D — témoin (recette du champion, sans nouvelle capacité) : REJECT, 2 graines.**
`heldout:stdlib` 2,672 -> 2,863 / 2,858 : Δ +0,188 ± 0,032 (m 0,016) **REGRESSION reproduite** ;
`program_trace:heldout` 0,480 -> 0,343 / 0,343 : Δ −0,137 ± 0,142 (NO_REGRESSION : l'amélioration
n'est pas DÉMONTRÉE à cette variance, malgré deux graines identiques) ; nouvelle capacité : 1,000 (rien
à apprendre ici). H4 (« le témoin ne régresse pas ») **REJETÉE** : continuer la recette du champion
(`stdlib` relu 19 fois) dégrade `stdlib` de façon reproductible — l'allocation « standard » est
elle-même destructrice.

**Bras A — nouvelle capacité seule : REJECT, 2 graines.**
Nouvelle capacité : held-out 0,810 / 0,847, Δ −0,172 ± 0,049 (IMPROVEMENT ; borne 0,877 < 0,90 :
**seuil d'acquisition atteint, répliqué**) ; train 0,535 / 0,570 (écart train/held-out : part de
mémorisation, étiquetée par le Judge). Mais **8 suites en REGRESSION reproduite** : texte +3,9 à +4,4
BPB (1,86 -> 5,73 / 6,00), `program_trace` 0,48 -> 1,00 sur les deux graines, `code_repair:validation`
+0,20. « Amélioration + destruction » : REJETÉ.

**Bras B — rejeu fixe (proportions du champion) : INCONCLUSIVE, 3 graines** (la 3e demandée par le
protocole après deux graines non concluantes).
Nouvelle capacité : 0,927 / 0,930 / 0,930, Δ −0,071 ± 0,021 (IMPROVEMENT, mais borne 0,95 > 0,90 :
**non acquise**) ; AUCUNE régression reproduite ; texte `heldout` et `heldout:code` IMPROVEMENT
(−0,023 ; −0,032) ; `program_trace:heldout` 0,52 / 0,46 / 0,44, Δ −0,004 ± 0,121 (préservé en moyenne,
non concluant) ; `heldout:stdlib` +0,030 ± 0,024 (m 0,016) et `code_repair:validation` +0,056 ± 0,075 :
VARIANCE_INCONCLUSIVE. Avec les règles mono-graines, la graine 0 seule aurait conclu à l'« oubli » de
`stdlib` et de `code_repair:validation`.

**Bras C — rejeu adaptatif (`stdlib` plafonné par `max_passes`, `reasoning_tasks` relevé) :
INCONCLUSIVE, 3 graines.** Les deux premières graines sont chacune ACCEPTÉES par le Judge mono-artefact
(aucun oubli) ; le verdict multi-graines demande une 3e graine puis reste non concluant sur deux suites
`code_repair` (`heldout` Δ −0,003 ± 0,066 ; `validation` Δ −0,008 ± 0,112 ; marge 0,05). Tout le reste :
texte IMPROVEMENT reproduite (`heldout` 1,863 -> 1,754 / 1,749 / 1,742 ; `heldout:stdlib` 2,672 ->
2,407 / 2,404 / 2,403) ; `program_trace:heldout` 0,313 / 0,410 / 0,280, Δ −0,146 ± 0,122 IMPROVEMENT
(train et validation aussi) ; nouvelle capacité 0,923 / 0,850 / 0,857, Δ −0,123 ± 0,052 IMPROVEMENT
mais borne 0,929 > 0,90 : **non acquise**. Même sans les deux suites douteuses, C serait REJETÉ pour
acquisition insuffisante : aucune promotion n'était possible.

**Hypothèses** : H1 (acquisition sur ≥ 2 graines) — atteinte par le SEUL bras A, au prix de 8
régressions reproduites ; aucun bras n'acquiert la capacité SANS destruction -> **critère P3.7 non
atteint**. H2 (le rejeu réduit les régressions reproduites) — **CONFIRMÉE** : A 8, B 0, C 0.
H3 (C protège `program_trace` mieux que B) — **CONFIRMÉE**, graine par graine (mêmes graines dans les
deux bras, bootstrap apparié candidat contre candidat) : `program_trace:heldout` meilleur avec C sur
les 3 graines, significatif sur 2 ; `heldout:stdlib` et `heldout` meilleurs sur les 3 graines,
significatifs sur les 3 ; nouvelle capacité meilleure sur 2 graines sur 3 ; `code_repair` sans
différence démontrée. H4 (le témoin ne régresse pas) — **REJETÉE** (`stdlib`, 19 relectures).

### 6.1 Cycle 3 — comparaison des variantes (même budget, mêmes graines)

| | A (seule) | B (rejeu fixe) | C (rejeu adaptatif) | D (témoin) |
|---|---|---|---|---|
| nouvelle capacité, erreur held-out (moyenne ± U) | **0,828 ± 0,049 acquise** | 0,929 ± 0,021 | 0,877 ± 0,052 | 1,000 |
| mémorisation (train − held-out) | forte (0,55 contre 0,83) | faible | faible | — |
| régressions reproduites | **8** | 0 | 0 | 1 (`stdlib`) |
| suites améliorées de façon démontrée | cible seule | texte ×2, `code_repair:train` | texte ×4, `program_trace` ×3 | `heldout:code`, `program_trace:train` |
| suites non concluantes | 3 | 7 | 2 | 5 |
| graines nécessaires | 2 | 3 | 3 | 2 |
| entraînement par graine | 1 071 s | 1 052 s | 1 154 s | 1 615 s (CPU partagé) |
| coût total (graines + jugements, rejeux compris) | 1,3 h | 2,0 h | 2,1 h | 1,4 h |
| rejeu bit à bit (provenance) | 2/2 | 3/3 | 3/3 | 2/2 |

Lecture (INFERRED à partir de ces mesures) : la variable qui sépare protection et destruction ici est
la RELECTURE des petits domaines (`stdlib` : 19 relectures -> régression reproduite, 11 -> douteux,
3,8 -> amélioration reproduite) et la présence de rejeu ; le prix de la protection est l'acquisition de
la nouvelle capacité (A 0,83 ; C 0,88 ; B 0,93) : à 40 % du budget, elle reste sous le seuil.

## 7. Résultats du scénario « mauvaise stratégie » (§5, JUDGED, 2 graines)

**Choix de la boucle (OBSERVED, journal)** : après le rejet consigné du plan `target_only` (bras A),
la boucle ne le répète pas ; elle planifie `exp-1d2d5875d253`, `protect_domains` : mélange
`order_explicit_tasks` 0,75, `code` 0,125, `reasoning_tasks` 0,125 (`stdlib` écarté du rejeu : > 4
relectures) — exactement le choix annoncé à blanc et justifié par la cause établie
(`protected_regression` sur les domaines `code` / `reasoning_tasks` / texte). **Critère (a) atteint.**

**Verdict : REJECT, 2 graines** (rejeu bit à bit réussi pour les deux graines).
- Nouvelle capacité : held-out 0,830 / 0,853, Δ −0,158 ± 0,048, borne 0,89 < 0,90 : **acquise,
  répliquée** ; train 0,770 / 0,765 (écart train / held-out bien plus faible qu'en A : 0,55 contre 0,83).
- Texte : aucune régression (`heldout:stdlib` IMPROVEMENT, 2,672 -> 2,433 / 2,444, alors que `stdlib`
  n'est PAS rejoué) ; `code_repair` : 3 suites VARIANCE_INCONCLUSIVE, `transfer` NO_REGRESSION.
- **`program_trace` : REGRESSION reproduite** — held-out 0,48 -> 0,703 / 0,833 (Δ +0,288 ± 0,143),
  train idem ; validation VARIANCE_INCONCLUSIVE. Cause consignée : `protected_regression`.
- **Critère (b) atteint** : 2 suites en REGRESSION reproduite contre 8 pour le bras A. La prédiction
  écrite avant (« `stdlib` sans doute encore en REGRESSION ») est **réfutée** : le texte s'améliore ; la
  destruction se déplace sur `program_trace` (12,5 % de `reasoning_tasks` ne suffisent pas à le
  protéger face à 75 % de nouvelle capacité).

Lecture : la boucle DÉTECTE sa mauvaise stratégie depuis des résultats réels (le rejet de A), change de
stratégie pour une raison consignée, réduit les dégâts, et le verdict multi-graines refuse encore la
promotion parce qu'une capacité protégée est oubliée de façon reproduite. Aucune promotion.

## 8. Crash / reprise en conditions réelles (REAL E2E, boucle L)

Deux interruptions pendant la même expérience, toutes deux sur le vrai répertoire, sans simulation :
1. **Perte du conteneur** (redémarrage de l'environnement, non provoqué) juste après `executed` de la
   graine 0 : `verify` OK (1 expérience inachevée signalée) ; relance de la MÊME commande -> événement
   `recovered` (`from_event: executed`), Judge exécuté (rejeu bit à bit de la graine 0 réussi).
2. **`kill -9` contrôlé** au pas 1 500 de l'entraînement de la graine 1 (déclencheur sur la télémétrie
   de cette expérience seulement) : `verify` OK ; relance -> `recovered` (`from_event: judged`) ;
   l'entraînement de la réplique reprend de son checkpoint (`resume.pt`) au pas 1 525, puis le Judge
   REJOUE la graine 1 depuis zéro : bit à bit identique (`invalid = []`). La reprise ne laisse donc
   aucune trace dans le résultat.
`verify` final : OK — chaînes du journal (seq 11–26) et du registre intactes, chaque artefact re-haché,
champion inchangé (P3 adopté), lignée identique. Le registre n'a reçu aucune promotion.

## 9. Vulnérabilité démontrée dans le calcul de la marge, et correction

**Constat (MEASURED, boucle L)** : la marge du texte y valait 0,097 (`heldout`), contre 0,010 dans les
bras. Cause : `σ_prior` regroupait AUSSI les expériences multi-graines du journal lui-même ; or la
boucle L reprend le journal du bras A, dont les graines DÉTRUITES divergent fortement (texte 5,73
contre 6,00 BPB). Une expérience destructrice élargissait donc la marge — et l'incertitude, puisque
σ = max(σ_prior, s_obs) — des expériences suivantes : dans la boucle L (U ≈ 0,13), une régression de
texte reproduite ne pouvait être déclarée REGRESSION qu'au-delà d'environ +0,23 BPB (D̄ − U > m) ;
en deçà, au mieux VARIANCE_INCONCLUSIVE. Dans l'autre sens, la même mise en commun RÉDUISAIT la marge de
`program_trace` (0,079 contre 0,097). La règle §2 (« historique des réplications ») n'excluait pas
explicitement ce cas : c'est une faille de la règle, pas de son implémentation.

**Correction (règle modifiée, applicable aux seules expériences PLANIFIÉES après ce commit ; aucun
verdict consigné n'est réécrit — chacun reste recalculable avec le `variance_prior` figé dans son
`planned`)** : σ_prior provient UNIQUEMENT de l'historique de réplications IMPORTÉ explicitement
(`import-replications`, consigné avec sa provenance). La dispersion des graines d'une expérience ne
compte que pour ELLE (s_obs dans U), jamais dans la marge des suivantes. Tests : l'a priori est
exactement `pooled_sigma` de l'historique importé ; adversarial — une expérience destructrice aux
graines très dispersées ajoutée au journal ne change pas l'a priori. Mutant « réintroduire la mise en
commun du journal » : **tué** (les deux tests échouent).

**Recalcul ILLUSTRATIF de la boucle L avec la règle corrigée** (ne remplace pas le verdict consigné) :
toujours **REJECT** (`program_trace:heldout` et `:train` en REGRESSION, marges 0,097 et 0,073) ; le
texte passe de NO_REGRESSION à **IMPROVEMENT** (`heldout` Δ −0,104 ± 0,012, marge 0,010) — la faille
masquait ici des améliorations, pas une régression. Bras A : inchangé (son a priori ne contenait que
l'historique importé). Limite qui demeure : l'import est une action d'opérateur ; importer un
répertoire destructeur élargirait la marge. Il est consigné et vérifiable, mais pas filtré.

## 10. Profondeur (`program_trace:deeper`) — hors périmètre, constat seulement

Aucun passage à l'échelle ni aucune modification ici. MEASURED dans les 5 expériences P3.7 (D, A, B,
C, L ; 12 graines au total) : `program_trace:deeper` (profondeur 4, jamais entraînée) reste à 1,000
sur chaque graine, NO_REGRESSION (rien à perdre). Documenté en P3.5 §6.7 (`docs/P3_5_CONTINUAL_AND_LIVE.md`) :
le modèle apprend des gabarits PAR LONGUEUR (profondeur 5 entraînée réussie, 4 non vue échouée) —
ni extrapolation, ni même interpolation. Investigation séparée, non commencée.

## 11. Synthèse et décision

**Ce que le moteur sait maintenant faire (IMPLEMENTED, TESTED, MUTATION, REAL E2E)** : décider sur
k ≥ 2 graines indépendantes, chacune jugée (rejeu bit à bit compris) ; distinguer par suite
REGRESSION / IMPROVEMENT / NO_REGRESSION / VARIANCE_INCONCLUSIVE, et MEMORIZATION /
INVALID_EXPERIMENT ; demander une graine de plus en cas de doute (3 au plus) puis conclure
INCONCLUSIVE ; faire RECALCULER le verdict par le registre avant toute promotion ; consigner
l'historique de réplications avec sa provenance ; vérifier un répertoire complet (`verify`) ; afficher
le cycle de vie réel et le verdict par graine dans `/brain`.

**Ce que les expériences montrent (JUDGED, REPLICATED)** : la liaison explicite est apprenable
(acquise, répliquée, dans A et dans L), mais dans ce modèle et à ce budget, elle ne l'a été qu'au prix
d'une régression reproduite d'une capacité protégée (A : 8 suites ; L : `program_trace`). Avec rejeu
(B, C), aucune régression reproduite, mais acquisition sous le seuil (bornes 0,95 et 0,93 > 0,90). Le
rejeu protège (H2) ; l'allocation adaptative protège mieux (H3) ; la recette « standard » du champion
est elle-même destructrice pour `stdlib` (H4 rejetée).

**Décision : AUCUNE PROMOTION.** Aucune expérience ne satisfait ensemble les critères 1 à 10
(acquisition sans régression reproduite). Le champion P3 (`adopted:exp-f0f4ed9a92d6`, artefact
`sha256:da8bf910…`) reste en service ; les ancres sont inchangées.

**Prochaine étape justifiée par ces mesures (HYPOTHESIS, non testée)** : le compromis
acquisition / protection est une question de budget à part égale de rejeu — protocole pré-enregistré
avec la répartition de C (le meilleur protecteur mesuré) et un budget de nouvelle capacité doublé
(12 000 pas, même part de rejeu), sur 2 à 3 graines ; critère : borne < 0,90 SANS REGRESSION.
