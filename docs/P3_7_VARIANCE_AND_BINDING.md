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
