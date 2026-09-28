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

### 5.6 Cycle 3b — la politique corrigée rejoue sa décision (pré-enregistré avant exécution)

Défaut trouvé par le cycle 3 réel (voir 6.4) : la politique relevait la part d'un domaine dégradé
sans regarder ses données UNIQUES (`stdlib` : 75 966 jetons ; 25 % de 9,6 M = 32 relectures).
Correction (même politique, même seuil `max_passes` = 4 appliqué par domaine) : un domaine déjà tiré
plus de 4 fois n'est jamais relevé ; un palier qui le ferait dépasser est écarté ; si le rejeu ne peut
plus protéger, pas d'apprentissage divisé par deux. Décision de la politique corrigée sur le journal
RÉEL du bras R (à blanc, sans entraînement) : `protect_domains`, `reasoning_tasks` 25 %,
`stdlib` inchangé à 7,5 %, avec le fait consigné. Protocole : `run` depuis une copie du bras R, une
expérience, échelle 6 000 pas / 9,6 M jetons, Judge et critères inchangés. Prédiction écrite
d'avance : l'oubli de `program_trace` diminue sans doute encore un peu mais l'acquisition de
`dependency_order` reste minime ; un REJET reste l'issue la plus probable.

### 6.4 Cycle 3 — la boucle choisit seule (politique d'origine)

Expérience 1 (`exp-88e71328c2af`, choisie par la boucle depuis le rejet de R, cause établie
`protected_regression`) : `protect_domains`, `reasoning_tasks` + `stdlib` portés à 50 %
(25 % chacun), `order_tasks` 27,8 %. **REJETÉE** (JUDGED) :

| suite | champion P3 | candidat | statut |
|---|---|---|---|
| `dependency_order:heldout` (cible) | 1,000 | 0,990 | non significatif |
| `heldout:stdlib` (BPB) | 2,672 | **3,439** | **OUBLIÉ** (IC [−0,797 ; −0,734]) |
| texte `heldout` (BPB) | 1,863 | 2,036 | OUBLIÉ |
| `program_trace:heldout` | 0,480 | 0,710 | OUBLIÉ |

Cause découverte (MEASURED, tokenisation des données du monde) : `stdlib` n'a que **75 966 jetons
uniques** ; 25 % d'un tirage de 9,6 M en fait **32 relectures** (81 en comptant les 2,56 passages de
l'entraînement), contre 7,6 (NR) et 10,1 (R). Relever la part d'un domaine épuisé ne le « protège »
pas : il est sur-appris et son held-out s'effondre. `code_tasks` (2 531 jetons, 270 à 580 relectures)
explique de même la MEMORIZATION de `code_repair:train` relevée par le Judge dans tous les bras.
La règle de sur-apprentissage de la politique (`max_passes`) ne regardait que le flux global
(≈ 2,6 passages) : aveugle à ce cas. Correction : 5.6 (test ajouté).

Expérience 2 (politique d'origine, déjà lancée) : le levier précédent jugé sans effet est annulé,
puis `code` + `reasoning_tasks` + `stdlib` à 75 % — de nouveau `stdlib` à 25 %. Prédiction écrite
avant son verdict : nouvel effondrement de `heldout:stdlib`.

**Verdict (`exp-ab8d938c516f`) : REJETÉE ; prédiction CONFIRMÉE** — `heldout:stdlib` 2,672 -> **3,325**
(IC [−0,691 ; −0,631]), texte 1,863 -> 2,001, `program_trace:heldout` 0,48 -> 0,64 (oubliés) ;
`dependency_order` : 0,99, aucun gain (NONE) ; `code_repair:transfer` 0,806 -> 0,722 (TRANSFER
significatif, sans valeur ici puisque le reste est détruit). **Fin du cycle 3 : 2 expériences
choisies par la boucle, 2 rejets, aucune promotion.** Le défaut de la politique s'est reproduit
deux fois de suite à l'échelle réelle : c'est lui, pas le hasard, qui produit l'effondrement.

### 6.2 A/A — deux modèles de même recette (MEASURED, JUDGED)

Champion P3, sa recette (`code` 0,45 / `reasoning_tasks` 0,40 / `stdlib` 0,15), 1 500 pas, 2,4 M
jetons ; graines : celle du monde (A, `exp-8d3086d8d8dc`) et 1 000 003 (B, `exp-810720deb49e`).

| suite | champion | A | B | A contre B (IC 95 %) |
|---|---|---|---|---|
| `program_trace:heldout` | 0,480 | **0,403** (gain sign., GENERALIZATION) | **0,507** (n.s.) | **[−0,153 ; −0,057] : différents** |
| `program_trace:train` | 0,470 | 0,425 | 0,500 | [−0,130 ; −0,025] : différents |
| `code_repair:train` | 0,765 | 0,762 | 0,718 | [0,003 ; 0,092] : différents |
| `code_repair:validation` (n = 18) | 0,735 | 0,796 (« oublié ») | 0,735 | n.s. |
| `heldout:stdlib` (BPB) | 2,672 | 2,708 (« oublié ») | 2,690 (« oublié ») | n.s. |
| texte `heldout` (BPB) | 1,8632 | 1,8631 (« oublié » : IC par document [−0,007 ; −0,0002]) | 1,8592 | n.s. |

Règle pré-enregistrée (≥ 2 suites significativement différentes entre deux modèles de même recette)
: **3 suites sur 12 -> l'IC par élément sous-estime la variance d'entraînement (MEASURED).**
Conséquences, sans aucune modification du Judge (conformément à 5.1) :
- un gain « significatif » d'un seul entraînement n'est pas fiable : le gain de A sur
  `program_trace` (0,48 -> 0,40, étiqueté GENERALIZATION) disparaît avec l'autre graine ; la
  RÉPLIQUE à graine indépendante, déjà exigée avant promotion, est la protection qui compte ;
- **faux rejets** : les deux continuations de la recette du champion sont REJETÉES pour « oubli »
  (`heldout:stdlib` +0,017 et +0,036 BPB pour une tolérance de 0,01 ; A aussi pour le texte, alors
  que la valeur agrégée s'AMÉLIORE de 0,0001, et pour `code_repair:validation` sur 18 éléments). Avec
  ces règles, depuis ce champion, AUCUN affinage qui n'améliore pas `stdlib` ne peut être promu :
  l'objectif P3.5 est structurellement bloqué ici, quel que soit le mélange ;
- ce n'est pas une raison d'affaiblir le Judge après coup. Proposition (à pré-enregistrer à part,
  NON appliquée) : une tolérance par suite fondée sur l'écart A/A mesuré, et un oubli déclaré
  seulement s'il se reproduit sur la réplique.

### 6.5 Cycle 2 — attaques à échelle réelle

**C2.2 Registre réécrit, chaîne recalculée** (copie du monde P3 ; `heldout` du champion présenté
PIRE de 0,5 BPB pour qu'un candidat paraisse meilleur) :
- la chaîne de hachage ne détecte RIEN (elle n'a pas de clé) : `status` accepte le registre — prédit ;
- le Judge REFUSE : contrôle `baseline_unchanged` en échec (re-mesure du checkpoint ≠ registre),
  cause `baseline_changed`, « expérience invalide, pas une réfutation » — prédit ;
- faille trouvée à côté : l'interface affichait ce champion « ATTESTÉ » avec les mesures forgées
  (l'attestation vérifiait la promotion au journal, pas les mesures). **Corrigé** dans
  `journal.attest` (partagé par la boucle et APEX Live) : les mesures du registre doivent être
  exactement celles que le Judge (ou l'adoption) a consignées au journal. Vérifié sur les registres
  réels honnêtes (P3, cycle 1 : identiques) et forgé (différent) ; test ajouté (chaîne recalculée).
  Limite restante (INFERRED) : un attaquant qui réécrit AUSSI le journal et recalcule ses deux
  chaînes reste indétectable par les fichiers seuls ; seule la re-mesure du Judge l'arrête. Une
  signature à clé hors du répertoire serait nécessaire.

**C2.1 Interruption puis reprise** : première tentative INVALIDE (défaut du script d'attaque : le
déclencheur « pas 700 » lisait la télémétrie de l'expérience P3 copiée, l'arrêt a eu lieu avant le
début ; la boucle a d'ailleurs refusé proprement l'expérience, « arrêt demandé »). Relancée
corrigée : résultat en 6.5 bis.

### 6.6 P4 — le goulot de `dependency_order` (MEASURED, critère pré-enregistré en 5.5)

Modèle spécialiste neuf (724 864 paramètres, architecture du cerveau), réservoir d'entraînement de
la capacité seul (30 000 problèmes, 52 passages), 6 000 pas × 32 × 256 ; évaluation sur les 300
problèmes held-out PROTÉGÉS du monde (jamais entraînés). Graine 0 :

| bras | exact held-out (IC 95 %) | acquis | ordre trouvé | 4 étapes justes |
|---|---|---|---|---|
| `base` (`a3,b7,c4=4`) | 0,107 [0,073 ; 0,143] | non | **95 %** | 32 / 300 |
| `explicit` (`a=3>3,b=a+4>7,…`) | **0,763** [0,713 ; 0,807] | **oui** | 99 % | 229 / 300 |

Écart apparié explicit − base : **+0,657, IC [0,600 ; 0,713]** (229 contre 32 résolus). Critère
pré-enregistré ATTEINT : **la liaison non locale implicite est le goulot** — pas les paramètres (les
mêmes), pas les données (les mêmes problèmes), pas l'oubli (modèle neuf), pas la planification
(l'ordre est trouvé à 95 % même en `base`). Ce qui manque en `base` : calculer une étape dont
l'instruction est AILLEURS dans le texte mélangé (ex. `a=d-9` avec d = 15 -> le modèle écrit 23).
Recopier l'instruction avant sa valeur rend la lecture locale et la tâche s'apprend.

Lectures liées : dans la lignée (cycle 1), l'ordre n'était trouvé qu'à 50–60 % et rien ne
s'acquérait : même en format explicite, rien ne garantit qu'un affinage DEPUIS le champion y
parvienne (UNVERIFIED). Réplication à graine indépendante des deux bras : 6.6 bis.
Temps d'entraînement non comparables entre bras (le bras `base` a partagé le CPU avec des tests).

### 6.7 P4 — profondeur de `program_trace` : interpolation (MEASURED, critère 5.5)

Monde P3 reconstruit, spécialiste neuf, graine 0 ; `interp` = 25 % du réservoir remplacé par des
problèmes NEUFS de profondeur 5 (la 4 reste jamais vue).

| bras | prof. 3 | prof. 4 (jamais vue, DANS la plage) | prof. 5 | prof. 6 (au-delà) | valeurs écrites en prof. 4 |
|---|---|---|---|---|---|
| `base` (1-3) | 0,803 [0,760 ; 0,847] | 0,0 | 0,0 | 0,0 | 4 valeurs : 199/200 |
| `interp` (1-3 + 5) | 0,847 (+0,043, IC [0,003 ; 0,087]) | **0,0** | **0,435** [0,370 ; 0,505] (entraînée) | 0,0 (6 valeurs : 197/200) | 3 ou 6 valeurs, JAMAIS les 5 attendues |

Lecture pré-enregistrée : `interp` réussit la 5 et échoue en 4 -> **gabarits par longueur, pas
même d'interpolation** (MEASURED). Le modèle choisit le nombre d'étapes parmi les longueurs vues,
indépendamment du programme ; au-delà, il réutilise le plus long gabarit. Avec les résultats P4
précédents (`big` 0,997 en prof. 3 et 0,0 au-delà ; `explicit` et `curriculum` en échec), les
paramètres, la copie de l'instruction et le curriculum sont écartés pour la longueur. Le goulot est
la REPRÉSENTATION de la fin de trace (positionnelle, non liée au contenu). Piste suivante à
pré-enregistrer : fin de trace explicitement dictée par le programme (instructions restantes).

**Synthèse P4 (MEASURED, deux goulots, aucun n'est la taille)** : le modèle apprend des procédures
LOCALES et POSITIONNELLES. Rendre la dépendance locale dans le texte écrit (`explicit`) débloque
`dependency_order` (0,11 -> 0,76) ; la longueur, elle, reste un gabarit. Passer à l'échelle
(paramètres) n'est justifié par aucune de ces mesures.
