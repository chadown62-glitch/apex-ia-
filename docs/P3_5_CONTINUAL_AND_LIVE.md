# P3.5 — apprentissage continu robuste · P3.6 — APEX Live

Suite de `docs/CONTINUAL_LEARNING.md` (commit `56db9bb`). Statuts : **OBSERVED** (constaté dans le
code ou les fichiers), **TESTED** (test automatisé), **MEASURED** (mesure chiffrée), **JUDGED**
(verdict du Judge indépendant), **INFERRED** (déduit, non mesuré), **UNVERIFIED**, **BLOCKED**.
Tout a été exécuté dans le bac à sable de développement (Linux, 4 cœurs, sans GPU) : rien ici ne
valide la machine Windows/WSL de l'utilisateur.

## 1. État initial

- Branche `claude/apex-official-development-4kao8t` à `56db9bb`. Le conteneur a été réinitialisé :
  le clone local pointait sur `origin/main` (`ca3479a`, 11 commits de lanceur et de sandbox absents
  de la branche) ; la branche a été restaurée à `56db9bb` (arbre propre, rien de local perdu).
- Environnement recréé (Python 3.12.3, torch 2.14 CPU depuis PyPI ; `download.pytorch.org` est
  bloqué par la politique réseau). Sandbox NAMESPACED disponible.
- **Tous les mondes, registres et checkpoints des champions P2/P3 ont disparu** avec le conteneur :
  ils vivaient dans des répertoires de travail hors dépôt.

## 2. Audit

| question | réponse | statut |
|---|---|---|
| où est l'état du champion ? | répertoire de travail de la boucle : `registry.jsonl` (chaîne de hachage, entrées promues), `artifacts/` (checkpoints adressés par contenu), attestation par `journal.jsonl` ; **rien dans le dépôt, rien de durable** : perdu au redémarrage du conteneur | OBSERVED |
| le champion peut-il être reconstruit ? | le benchmark `code_repair` se reconstruit à l'identique (`sha256:61e18a19a307…`, 1 144 mutants) ; le MONDE non (`872103c5…` au lieu de `cb9313a0…` : corpus texte récolté dans la bibliothèque standard et la doc de l'hôte, 292 docs `stdlib` / 3 726 `code` ici) ; reproduction bit à bit des champions : impossible | OBSERVED ; **BLOCKED** |
| représentation des capacités | contrat `Capability` (`apex/capabilities/contract.py`) ; données gelées dans `world.json` avec empreintes par suite ; `code_repair`, `program_trace`, `dependency_order` | OBSERVED, TESTED |
| enregistrement des benchmarks | gelés dans le monde (empreintes, relecture revérifiée) ; suites texte tirées des splits | OBSERVED, TESTED |
| mesure des régressions par le Judge | (1) gardes contre le champion : bootstrap apparié par élément + tolérance par métrique ; (2) protections : oubli contre l'ANCRE (meilleur niveau promu, re-mesurée depuis son checkpoint) | OBSERVED, TESTED, mutation 28/28 |
| communication des résultats | journal (`executed` : mesures déclarées ; `judged` : mesures du Judge, oubli, raisons ; `analyzed` ; `replicated` ; `promotion`) ; registre (propositions) | OBSERVED |
| choix des stratégies | `StrategyPolicy` : cause établie du dernier échec + effet mesuré du levier précédent + mémoire de la chaîne | OBSERVED, TESTED |
| événements existants | boucle : started, cycle_started/completed, target_exhausted, plan_refused, adopted, rollback, stopped ; expérience : planned (stratégie + justification), started, executed, judged, analyzed, replicated, promotion, aborted, interrupted, recovered | OBSERVED |
| événements manquants | rien PENDANT l'entraînement (progression, perte), ni jeu de données prêt, ni début du jugement (≈ 5 min) ou de la réplique ; le journal ne peut pas les accueillir tel quel : la reprise après interruption repart du NOM du dernier événement | OBSERVED |
| interface existante | React/Vite, 19 pages, `usePolling` seul (aucun WebSocket/SSE) ; 21 routeurs d'API ; **aucun n'importait `apex.neural`** : le cerveau natif était invisible. « Experiment Lab » et « Evolution Timeline » pilotent un AUTRE moteur (`apex.metalearner`, agents LLM) | OBSERVED |
| mocks / chemins morts | pages `planned` affichées comme telles (`PlannedSpecialtyPage`) ; aucune page ne simulait le cerveau natif — elle n'existait pas | OBSERVED |
| une UI peut-elle refléter le moteur réel ? | oui : journal, registre, verrou de boucle et checkpoints sont des fichiers ; il manquait la télémétrie du travail en cours et une vue en lecture seule | INFERRED puis TESTED (§4) |

### Faiblesses non signalées par les rapports précédents

1. **Champion éphémère** (ci-dessus). Le dépôt ne contient ni le champion ni de quoi le reproduire
   bit à bit ; seule la procédure est reproductible. OBSERVED.
2. **Monde dépendant de l'hôte** : le corpus texte vient de `/usr/lib/python3.12` et de pydoc ;
   « le même monde » n'existe pas sur une autre machine. OBSERVED.
3. **Ancre = une seule mesure, sélectionnée** : l'ancre d'un groupe est la version promue au
   meilleur niveau ; pour `code_repair:validation` (18 fonctions) le champion P3 valait 0,469 et
   SA PROPRE réplique indépendante 0,586. Comparer tout candidat à un tirage favorable (« winner's
   curse ») marquerait de l'« oubli » systématiquement — cohérent avec « `code_repair` validation
   oubliée dans presque tous les bras ». INFERRED, à MESURER (expérience A/A, §5).
4. **Comparaisons multiples** : ≈ 15 suites × (garde + ancre) testées à 95 % sans correction ; même
   sans aucun changement réel, la probabilité qu'une suite paraisse « significativement dégradée »
   n'est pas négligeable. INFERRED, à MESURER.
5. **Variance d'entraînement non modélisée** : le bootstrap apparié couvre l'échantillonnage des
   éléments, pas la graine d'entraînement ; la réplique ne protège que les acceptations, pas les
   rejets. INFERRED.

## 3. P3.5 — quelle architecture contre l'oubli ? (analyse des preuves existantes)

| piste | preuve disponible dans ce dépôt | coût / risque | décision |
|---|---|---|---|
| **rejeu des données des capacités anciennes** (répétition) | l'E2E de la boucle : 25 % de `reasoning_tasks` ramène `program_trace` de 1,0 à 0,32 (IC du gain [0,63 ; 0,73]) ; sans rejeu, apprendre `dependency_order` détruit `program_trace` (0,28 -> 0,95, deux graines) | les données existent déjà (réservoirs gelés de chaque capacité, générateurs) ; aucun paramètre nouveau | **retenue** comme mécanisme principal |
| volume de données (moins de relectures) | bras E/F : 4x plus de jetons tirés supprime l'oubli du texte, à taux égal | nul (budget identique) | **retenue** (condition nécessaire) |
| taux d'apprentissage réduit | bras B/D/E : oubli réduit mais apprentissage supprimé | ralentit tout | écartée comme levier principal (la boucle la garde en levier secondaire mesuré) |
| distillation (LwF) depuis le champion | aucune ; utile quand les étiquettes anciennes manquent — ici elles existent (le rejeu est strictement plus informatif) | second modèle en mémoire, un hyperparamètre | non retenue (pas de consommateur tant que le rejeu suffit) |
| EWC (pénalité de Fisher) | aucune | un hyperparamètre sensible, estimation de Fisher | non retenue sans échec du rejeu |
| gel de couches / adaptateurs / LoRA | aucune ; isolerait par construction mais bloquerait le transfert (mesuré faible mais réel) et exigerait un routage | nouvelle architecture de modèle | non retenue à ce stade |
| modèle plus grand | P4 : x4 paramètres -> profondeur 3 à 0,997 (capacité utile en distribution) | nouvelle lignée complète (architecture gelée par monde) | hors P3.5 ; candidat pour P4 selon la mesure du goulot |

Le rapport précédent n'a pas montré l'échec du rejeu : il a montré (a) que le rejeu corrige l'oubli
d'une capacité quand ses données sont rejouées, (b) qu'AUCUNE promotion n'a eu lieu parce qu'une
suite (`code_repair:validation`, 18 fonctions) était déclarée oubliée dans presque tous les bras.
Avant de chercher un autre mécanisme, il faut savoir si ce blocage est un oubli réel ou un artefact
de mesure (faiblesses 3–5 de l'audit). D'où l'ordre : lignée reconstruite -> A/A -> cycles.

## 4. P3.6 — APEX Live (IMPLEMENTED, TESTED, REAL E2E)

Architecture (aucun second moteur, aucun état simulé) :

```
boucle (LoopController) ──► journal.jsonl      décisions (chaîne de hachage) — inchangé
          │               ──► registry.jsonl     versions / champion (chaîne de hachage)
          └─ execute ─────► runs/<exp>/telemetry.jsonl   travail EN COURS (nouveau)
                                   │
apex.neural.live (lecture seule) ◄─┘  + world.json (résumé) + /proc/locks (verrou de boucle)
          │
GET /api/v1/brain, /brain/events?after=<seq>   (répertoire fixé côté serveur : APEX_BRAIN_WORKDIR)
          │
BrainLivePage (/brain) : usePolling 2 s (état) + useEventStream (journal incrémental)
```

- **Télémétrie** : `training_started`, `dataset_ready` (jetons tirés, documents), `training_progress`
  (pas, perte, lr : les points mêmes de la courbe consignée), `training_completed`, `judge_started`,
  `replication_started` puis la progression de la réplique marquée `run: "replica"`. Émise par le
  contrôleur existant via UN crochet de `execute` ; n'influe sur aucun calcul.
- **Pourquoi pas dans le journal** : la reprise après interruption repart du nom du dernier événement
  du journal ; y écrire la progression casserait la reprise.
- **Étiquettes** (du journal, le nom d'origine reste visible) : `LOOP_STARTED`, `CYCLE_STARTED`,
  `STRATEGY_SELECTED` (stratégie + justification ESTABLISHED/INFERRED), `EXPERIMENT_STARTED`,
  `TRAINING_COMPLETED` / `EXECUTION_FAILED`, `JUDGE_ACCEPT|REJECT|INCONCLUSIVE` (+ régressions : suite,
  avant, après, IC, tolérance), `CAUSE_ANALYSIS`, `REPLICATION_*`, `NEW_CHAMPION` / `NOT_PROMOTED`,
  `CHAMPION_ADOPTED`, `ROLLBACK`, `LOOP_STOPPED`.
- **Honnêteté** : champion affiché ATTESTÉ seulement si le journal justifie sa promotion (sinon la
  raison) ; boucle « en cours » seulement si un processus DÉTIENT le verrou `flock` (pas le pid
  resté dans le fichier) ; chaîne de hachage rompue -> `unknown` + « intégrité rompue », jamais
  masquée ; toute source absente -> `not_available` + raison.
- **Rejeu du Judge** : sa progression est publiée, marquée `run: "replay"` (`replica_replay` pour la
  réplique) ; le test vérifie qu'elle reproduit la courbe consignée pas à pas.
- **N'existe pas (non simulé)** : re-mesures internes du Judge (seul son début est publié) ; pas de canal poussé (WebSocket/SSE) : relecture HTTP toutes les 2 s d'un journal
  append-only avec curseur ; pas d'état de la sandbox par exécution.

Vérifications :
- tests (boucle RÉELLE réduite) : état = fichiers du moteur ; événements dans l'ordre du journal
  (défaut trouvé et corrigé : le curseur par défaut perdait l'événement n° 0) ; télémétrie = courbe
  réelle consignée ; réplique marquée ; champion non attesté si la promotion manque au journal ;
  journal falsifié -> API `unknown` ; verrou réel tenu / libre / verrou d'un autre fichier ;
  2 tests d'interface (jsdom) dont l'état « non disponible » ;
- **E2E réel** : API (uvicorn) + interface (Vite) + Chromium sur la boucle P2 EN COURS : pid, mémoire
  (1,1 Go), CPU, expérience courante et sa perte, ACCEPT du Judge avec son IC, chronologie — tout lu
  dans les fichiers du moteur. L'E2E a révélé un manque : la réplique ne publiait aucune
  progression (l'écran restait à 400/400) ; corrigé.

## 5. Expériences PRÉ-ENREGISTRÉES (texte commité avant exécution)

Lignée : les champions P2/P3 perdus sont RECONSTRUITS avec le code actuel (P2 : `init-world
--capability code_repair --vocab 1024 --context 256` puis `run --bootstrap stdlib=1.0
--max-total-experiments 10` ; P3 : `derive-world --add-capability program_trace`, adoption, puis
l'expérience dirigée « 6 000 pas, 40 % de traces + mélange du champion »). Ce ne sont pas les mêmes
modèles (monde non reproductible bit à bit) ; leurs mesures seront comparées à celles documentées.

### 5.1 A/A — le Judge distingue-t-il deux modèles équivalents ?

Deux expériences dirigées IDENTIQUES depuis le champion P3 reconstruit, ne différant que par la
graine (`--seed` : graine du monde, puis 1000003) : mélange du champion, 1 500 pas, 2,4 M jetons,
lr 1e-3 (régime F, sans oubli du texte mesuré). Mesures : (i) verdicts du Judge et suites marquées
« oubliées » contre les ancres ; (ii) comparaison appariée CANDIDAT contre CANDIDAT, suite par suite.
Règle d'interprétation fixée ici : si au moins 2 suites (sur ≈ 15) diffèrent significativement entre
ces deux modèles de même recette, l'intervalle par élément sous-estime la variance d'entraînement
(MEASURED). Cette expérience ne modifie AUCUNE règle du Judge ; un changement éventuel serait
pré-enregistré à part, appliqué seulement aux expériences suivantes, et devrait garder verts tous
les tests adversariaux et mutants existants.

*Écart de procédure (consigné AVANT tout résultat A/A)* : le validateur de plan a refusé la première
tentative — une recette identique à la référence est « une expérience sans objet » (protection
voulue, conservée) ; la deuxième aussi (pas ET jetons changés : « 2 modifications, attribution
impossible »). La référence déclarée est donc 1 400 pas / 2,4 M jetons ; la recette des DEUX
candidats reste exactement celle ci-dessus (1 500 pas, 2,4 M jetons). La mesure A/A (candidat contre
candidat) n'en dépend pas. Conséquence d'ordre : cycles 1 et 3 s'exécutent avant l'A/A et C2.

### 5.2 Cycle 1 — apprendre `dependency_order` en conservant le reste

Monde enfant du monde P3 reconstruit (+ `dependency_order`), champion P3 adopté. Deux bras, même
budget (6 000 pas, lots 16 × 256, 9,6 M jetons tirés, lr 1e-3), une seule modification chacun (le
mélange) :
- **NR (sans rejeu)** : `order_tasks 0,52 / code 0,36 / code_tasks 0,06 / stdlib 0,06` ;
- **R (rejeu)** : `order_tasks 0,40 / reasoning_tasks 0,20 / code 0,28 / code_tasks 0,04 / stdlib 0,08`.

**Critère de succès (P3.5)** : R est ACCEPTÉ par le Judge avec ses règles actuelles (baisse
significative de l'erreur `dependency_order:heldout`, aucune suite oubliée contre les ancres), sa
réplique (graine indépendante) aussi, donc PROMU ; NR sert de témoin de l'oubli. Toute autre issue
est rapportée telle quelle (dont : R apprend mais est refusé pour oubli ; R n'apprend pas).

### 5.3 Cycle 2 — attaques (pré-enregistrées avant tout résultat du cycle 1)

Ce qui est déjà couvert par des tests adversariaux (non refait ici) : capacité oubliée, dérive
cumulée, capacité rare (dégradation agrégée au-delà de la tolérance sans significativité),
contamination / fuite du benchmark, métriques falsifiées, promotion sans Judge, champion corrompu,
reprise après arrêt (petite échelle), journal d'un autre monde. Attaques ajoutées, à ÉCHELLE RÉELLE :

- **C2.1 Interruption puis reprise** : le bras A de l'A/A rejoué dans une copie neuve du monde P3,
  arrêt (`STOP`) pendant l'entraînement, reprise par `run`. Prédiction : même identifiant
  d'expérience, candidat identique bit à bit à celui de l'A/A, même décision, mêmes comparaisons.
  Toute différence = échec.
- **C2.2 Registre réécrit avec chaîne de hachage recalculée** (attaquant qui connaît le format) :
  mesures du champion modifiées dans une copie, chaîne recalculée, puis une courte expérience
  dirigée. Prédiction : la chaîne ne détecte RIEN (pas de clé) ; la re-mesure du champion par le
  Judge (`baseline_unchanged`) ou l'observation d'intégrité doit refuser. Si rien ne refuse = faille.
- **C2.3 Réfutation d'un succès du cycle 1** (s'il y en a un) : réplique à graine indépendante
  (déjà dans la boucle) ; gain de `dependency_order:heldout` comparé à l'écart A/A entre deux modèles
  de même recette ; gains sur les suites non vues de la capacité (transfert / profondeur) ; aucune
  suite de garde hors tolérance. Un succès qui ne dépasse pas l'écart A/A n'est pas un succès.

## 6. Résultats

### 6.1 Lignée reconstruite (MEASURED, JUDGED)

Mondes et champions perdus avec l'ancien conteneur : reconstruits avec le code actuel. Le benchmark
se reproduit bit à bit, pas le monde (corpus tiré de la bibliothèque standard de l'hôte) : ce sont
d'AUTRES modèles, comparés aux mesures documentées.

- **P2** (monde `sha256:872103c5…`, boucle autonome, 5 expériences) : amorçage promu (BPB 3,879 ->
  3,258) ; `target_only` REJETÉ (oubli de `heldout:stdlib`) ; `protect_domains` non concluant ;
  `more_steps` REJETÉ (gain significatif sur `code_repair` MAIS oubli de `heldout:stdlib`) ;
  `more_data` ACCEPTÉ, répliqué, promu (`code_repair:heldout` 0,971 -> 0,942 ; BPB 2,302). Le Judge
  refuse encore « gain + destruction ». Champion plus faible que le P2 documenté (BPB 2,151,
  `code_repair:heldout` 0,749).
- **P3** (monde `sha256:8370944b…`, + `program_trace`, champion P2 adopté, même expérience dirigée
  de 6 000 pas) : ACCEPTÉ, répliqué, promu.

| suite | champion P2 adopté | candidat (promu) | réplique (graine 1 000 003) | P3 documenté (cand. / répl.) |
|---|---|---|---|---|
| `program_trace:heldout` | 1,000 | **0,480** | **0,317** | 0,283 / 0,253 |
| `program_trace:deeper` (prof. 4) | 1,000 | 1,000 | 1,000 | 1,0 / 1,0 |
| `code_repair:heldout` | 0,942 | 0,840 (sign.) | 0,827 (sign.) | n.s. / n.s. |
| texte `heldout` (BPB) | 2,302 | 1,863 | 1,849 | 1,801 / 1,807 |

Reproduit (qualitatif) : la capacité s'acquiert et généralise (GENERALIZATION établie par le Judge),
aucun oubli, échec total en profondeur 4. Non reproduit : l'ampleur (0,48 contre 0,28) — parent plus
faible (INFERRED). **Écart entre deux graines d'une même recette : 0,480 contre 0,317** sur la même
suite de 300 problèmes : la variance d'entraînement est grande devant l'IC par élément (voir 6.2).

### 5.4 Cycle 3 — amélioration : la boucle choisit (pré-enregistré avant le verdict du bras R)

Constat déjà mesuré (bras NR, 6.3) : REJETÉ pour oubli de `program_trace` ; cause établie
`protected_regression`, modification impliquée : le mélange. La règle EXISTANTE de la politique
(`protect_domains`, palier 25 %) proposerait `reasoning_tasks` à 25 % — presque le mélange R écrit à
la main : la stratégie de la boucle et celle de l'opérateur coïncident (MEASURED sur le code, pas un
résultat d'expérience).

Protocole : la BOUCLE AUTONOME (`run`, politique inchangée) repart d'une copie du répertoire du bras
R (journal compris : rejet, cause, promotion éventuelle), échelle de référence 6 000 pas / 9,6 M
jetons, au plus 2 expériences, 1 cycle. Elle choisit seule faiblesse, stratégie et recette ; rien
n'est choisi à la main. Variantes comparées : NR, R, choix de la boucle — sur qualité (cible),
généralisation (classification du Judge), oubli (lignes d'oubli contre les ancres), coût (pas,
durée), stabilité (réplique) et reproductibilité (rejeu bit à bit). Une variante n'est dite
meilleure qu'une autre que si l'écart dépasse l'écart A/A de même recette (5.1). Aucun changement
de règle du Judge.

### 6.3 Cycle 1 — apprendre `dependency_order` (monde `sha256:1bb2b62d…`, champion P3 adopté)

Référence : champion P3 adopté, erreur 1,000 sur les trois suites `dependency_order` (n = 300 en
held-out). Même budget pour les deux bras : 6 000 pas, 9,6 M jetons tirés (24,6 M vus, 2,6 passages),
≈ 1 470 s d'entraînement ; rejeu du Judge identique bit à bit dans les deux cas (provenance prouvée).

**Bras NR (sans rejeu des traces) — REJETÉ** (JUDGED) :

| suite | champion P3 | NR | IC 95 % du gain | statut |
|---|---|---|---|---|
| `dependency_order:heldout` (cible) | 1,000 | 0,970 | [0,013 ; 0,050] | gain significatif mais minime |
| `program_trace:heldout` | 0,480 | **0,943** | [−0,523 ; −0,403] | **OUBLIÉ** (ancre #0) |
| `program_trace:validation` / `train` | 0,525 / 0,470 | 0,945 / 0,950 | exclut 0 | OUBLIÉS |
| `code_repair:heldout` | 0,840 | 0,794 | [−0,082 ; 0,185] | bruit |
| `code_repair:train` | 0,765 | **0,003** | [0,676 ; 0,848] | MEMORIZATION (Judge) : solutions vues, rien en held-out |
| texte `heldout` (BPB) | 1,863 | 1,790 | [0,070 ; 0,082] | amélioré |

Analyse de cause (moteur) : `protected_regression`, attribution `single_modification` (le mélange).
Décision correcte du Judge : gain réel sur la cible + destruction d'une capacité = REJET.

**Bras R (20 % de rejeu des traces) — REJETÉ** (JUDGED) :

| suite | champion P3 | R | statut contre l'ancre |
|---|---|---|---|
| `dependency_order:heldout` (cible) | 1,000 | 0,980 | gain significatif, minime (IC [0,007 ; 0,037]) |
| `program_trace:heldout` | 0,480 | **0,743** | **OUBLIÉ** (IC [−0,333 ; −0,193]) |
| `heldout:stdlib` (BPB) | 2,672 | 2,690 | **OUBLIÉ** (IC [−0,043 ; −0,008], tolérance 0,01) |
| `code_repair:train` | 0,765 | 0,003 | MEMORIZATION |
| texte `heldout` (BPB) | 1,863 | 1,841 | amélioré |

**Critère de succès P3.5 (5.2) : NON ATTEINT.** R est refusé ; aucune promotion ; le champion P3
reste en service. Rien n'a été ajusté pour obtenir un autre verdict.

Comparaison appariée NR contre R, candidat contre candidat (mesures par élément consignées par le
Judge, aucune re-mesure) — le rejeu est un COMPROMIS, pas un gain gratuit :

| suite | NR | R | IC 95 % (NR − R) |
|---|---|---|---|
| `program_trace:heldout` | 0,943 | 0,743 | [0,153 ; 0,250] : R protège |
| `heldout:stdlib` (BPB) | 2,528 | 2,690 | [−0,181 ; −0,153] : R coûte |
| texte `heldout` (BPB) | 1,790 | 1,841 | [−0,066 ; −0,053] : R coûte |
| `code_repair:validation` / `transfer` | 0,543 / 0,743 | 0,611 / 0,806 | significatif : R coûte |
| `dependency_order:heldout` | 0,970 | 0,980 | [−0,03 ; 0,01] : indistinguable |

Lecture (INFERRED jusqu'à l'A/A) : dans un budget fixe, chaque part de rejeu est prise aux autres
domaines ; 20 % de traces réduisent l'oubli des traces d'environ 40 % sans l'annuler, et la nouvelle
capacité n'apprend presque rien dans les deux bras (0,97–0,98) : le goulot principal n'est pas
l'oubli mais l'ACQUISITION de `dependency_order` à cette échelle.

### 5.5 P4 — chercher le goulot réel (pré-enregistré, avant exécution ; pas de passage à l'échelle)

Deux goulots observés, deux laboratoires à modèle SPÉCIALISTE neuf (architecture du cerveau,
6 000 pas, lots 32 × 256), critères écrits dans les scripts avant exécution :

1. **`dependency_order` ne s'acquiert pas** (`scripts/p4_order_lab.py`, monde du cycle 1). Détails
   du Judge sur le bras R : sorties bien formées 100 %, ordre juste 50 %, ordre textuel 1 %, valeurs
   justes rares (2 % résolus ; la plupart n'ont que l'étape 1). Bras `base` (format standard) et
   `explicit` (l'instruction exécutée est recopiée avant sa valeur). Acquis si IC 95 % bas de
   l'exactitude held-out > 0,10. Lecture fixée : `base` acquis -> l'architecture suffit, l'échec du
   cycle 1 vient de l'entraînement depuis le champion (part, budget, interférence) ; `base` non acquis
   et `explicit` acquis avec écart apparié excluant 0 -> la liaison non locale implicite est le goulot
   (représentation / format), pas les paramètres ; aucun des deux -> ni l'un ni l'autre, hypothèse
   suivante : capacité.
2. **Profondeur 4 de `program_trace` à 1,0** (`scripts/p4_depth_lab.py`, monde P3 reconstruit) :
   bras `base` et `interp` (25 % de profondeur 5, la 4 jamais vue). `interp` réussit la profondeur 4
   (IC bas > 0,10) -> interpolation possible, l'échec est une EXTRAPOLATION au-delà de la longueur
   entraînée ; `interp` échoue en 4 alors qu'il réussit 5 -> gabarits par longueur, pas même
   d'interpolation. Profondeur 6 : extrapolation depuis 5.
