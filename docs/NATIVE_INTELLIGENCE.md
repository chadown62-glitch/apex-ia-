# APEX Native Intelligence — audit et architecture

**Principe.** Le cerveau d'APEX est un réseau de neurones appartenant à APEX :
ses poids, son tokenizer, son entraînement, ses checkpoints. Un modèle externe
(Ollama, GPT, Claude, Gemini…) n'est jamais ce cerveau ; s'il est utilisé,
c'est comme outil auxiliaire marqué `EXTERNAL_MODEL_DEPENDENCY`.

    EXTERNAL MODEL ≠ APEX MODEL

## 1. Audit du dépôt (avant code)

Classement : A réel et utilisable, B partiel réutilisable, C dormant,
D façade/stub honnête, E comportement incorrect, F manquant.

| Composant | Classe | Constat | Décision |
|---|---|---|---|
| `ModelProvider` / `ModelEndpoint` (`apex.models`) | A | clients de modèles EXTERNES (Ollama, OpenAI-compatible) | reste l'accès aux modèles externes ; jamais le cerveau |
| `ModelRouter` (`apex.routing`) | B | choisit un modèle externe ; apprend de l'historique pour `apex solve` | inchangé ; le modèle natif n'est pas routé ici |
| `ResearchProvider` (`apex.design.research`) | D | `NullResearchProvider` : aucun backend web | la découverte du Knowledge Agent réutilise ce contrat de recherche |
| `SearchProvider`, `FileScanner`, `EmbeddingProvider` (registre) | F (PLANNED) | aucun code | FileScanner partiellement comblé par les filtres du Knowledge Agent (texte) |
| `MemoryService` / `MemoryRecord` (`apex.memory`) | B | provenance, `VerificationStatus`, relations SUPPORTS/CONTRADICTS ; pas de RAG ni d'embeddings | destination « RAG / mémoire » des connaissances curées — distincte des données d'entraînement |
| `ArtifactStore` local (`apex.artifacts`) | A | stockage adressé par contenu (SHA-256) | stockage des sources BRUTES acquises |
| `TrustLevel` (`apex.config.schema`) | A | TRUSTED / UNTRUSTED | tout contenu externe est UNTRUSTED |
| `docs/UNTRUSTED_DATA_SECURITY.md` | D | pipeline prévu, non implémenté | implémenté pour le texte par le Knowledge Agent |
| `TaskBank` + `TaskSplit` TRAIN/HELDOUT | A | séparation HELDOUT garantie par les droits SQL | même discipline pour les splits du dataset d'entraînement |
| `Experience` + `SuccessEvidence` (`apex.metalearner`) | A | verdict jugé, succès CONFIRMED/OBSERVED/REFUTED | source d'expériences candidates à l'entraînement (Partie E) |
| `StatTest` (bootstrap apparié) | A | significativité | preuve d'apprentissage V0 -> V1 |
| `GauntletRunner`, juges, oracles, sandbox, EvalRunner | A | agrégation stricte, véto, jamais PASS par défaut | Judge d'acceptation d'une nouvelle version du modèle |
| Continuous Improvement Engine (`apex.improvement`) | A | baseline, mesure, juge épinglé, ledger chaîné | réutilisé pour les nouvelles capacités |
| Tokenizer, modèle neuronal, entraînement, checkpoints, datasets | **F** | rien n'existait ; ni torch ni numpy installés | construits ici |
| Dépendance tensor/autograd | **F** | aucune | PyTorch 2.14 installé depuis PyPI (l'index pytorch.org est refusé par le proxy) ; ajouté comme extra optionnel `neural` |

### Flux réels constatés

- **Entrée des données** : aujourd'hui seulement des tâches écrites à la main
  (`TaskBank`) et les sorties de modèles externes (`EvalRunner`). Aucune
  ingestion de documents, aucune recherche web réelle (proxy : seuls PyPI et
  quelques registres sont joignables).
- **Stockage** : PostgreSQL/SQLite (`tasks`, `eval_runs`, `experiences`,
  `memory_records`), `ArtifactStore` sur disque, ledgers JSONL.
- **Évaluation** : oracles + juges + `GauntletRunner` ; `StatTest`.
- **Expériences** : `experiences` (verdict, preuve de succès).
- **Vers l'entraînement** : aucun chemin n'existait. Il est créé ainsi :

```
Knowledge Agent (UNTRUSTED)                 Experience (jugée)
      │                                           │
RAW SOURCE -> NORMALIZED -> CURATED KNOWLEDGE      │ seulement CONFIRMED
      │                        │                  │
      │                 ┌──────┴──────┐            │
      │                 ▼             ▼            ▼
      │           RAG / MEMORY   TRAINING CANDIDATE ◄┘
      │           (MemoryService)      │
      │                                ▼
      │                        TRAINING DATASET (train / validation / held-out test)
      │                                │
      │                         APEX TRAINING ENGINE (forward, loss, backward, optimizer)
      │                                │
      │                          APEX MODEL vN+1 (checkpoint)
      │                                │
      │                     held-out evaluation -> Judge -> ACCEPT / REJECT
```

## 2. Architecture retenue

Deux paquets de recherche (jamais dans l'artefact Runtime,
`runtime_build/selection.py`) plutôt qu'une dizaine de paquets de premier
niveau :

- `apex/neural/` — le cerveau : `config` (taille du modèle, jamais codée en
  dur), `tokenizer` (BPE au niveau octet, entraîné par APEX), `model`
  (transformeur décodeur), `training` (moteur : forward, loss, backward,
  optimizer.step, zero_grad ; optimiseurs, scheduler, clipping, accumulation,
  précision mixte), `checkpoint`, `dataset` (splits, empreintes, contrôle de
  fuite), `evaluation` (held-out, V0 vs V1, Judge).
- `apex/knowledge/` — l'agent d'acquisition : modèle de données et
  provenance, droits/licences, acquisition, extraction, filtres (qualité,
  pertinence, injection, doublons, contradictions), multimodal (interfaces),
  curation, store, export vers les candidats d'entraînement et vers la mémoire.

Le moteur d'entraînement ne dépend de PyTorch que derrière ses propres
abstractions (`TrainingEngine`, `OptimizerSpec`, `SchedulerSpec`).

## 3. Knowledge Acquisition Agent (`apex/knowledge/`)

Pipeline réel, une trace `StageRecord` par étape (`KnowledgeItem.history`) :

    discovery -> acquisition -> license -> extraction -> normalization -> language
    -> relevance -> quality -> evidence -> dedup -> contradiction -> injection
    -> structuring -> provenance -> curation -> KnowledgeStore

| Élément | Implémentation | Limite honnête |
|---|---|---|
| Découverte | `LocalDirectoryDiscovery`, `PythonDocsDiscovery` (docstrings stdlib, PSF-2.0), `StaticDiscovery`, `ResearchDiscovery` (réutilise `ResearchProvider`) | `NullResearchProvider` est le seul backend : aucune découverte web réelle |
| Acquisition | `FileAcquirer`, `PythonDocsAcquirer`, `HttpAcquirer` (robots.txt respecté, 401/402/403/451 refusés sans relance ni identifiant, taille bornée) | Politique réseau du bac à sable : Internet général inaccessible ; HTTP testé sur un serveur local réel |
| Licences | `classify_license` : FREE / COMPATIBLE / UNKNOWN / RESTRICTED ; RESTRICTED testé en premier | Heuristique sur l'indication fournie, pas une analyse juridique ; en doute : UNKNOWN |
| Métadonnées seules | licence UNKNOWN (REVIEW) ou RESTRICTED (REJECT) : `text=None`, empreinte + provenance conservées | — |
| Qualité | score explicite (longueur, lettres, lignes uniques, diversité lexicale, longueur de mot) | Heuristique ; ne mesure ni l'exactitude ni la profondeur |
| Injection | 16 motifs EN/FR ; texte HTML masqué (CSS, `hidden`) écarté ; QUARANTINE / UNSAFE prioritaire sur tout | Motifs connus seulement ; faux positif réel : `docs/DEVELOPMENT_HANDOFF.md` est mis en quarantaine pour une commande `curl … \| sh` légitime (conservateur, voulu) |
| Doublons | exact (texte normalisé) + MinHash 64 permutations, seuil 0.8 | — |
| Contradictions | même sujet/objet à négation opposée, ou même sujet numérique à valeur différente -> REVIEW | Volontairement conservateur : rate les contradictions paraphrasées |
| Multimodal | métadonnées PNG/GIF/JPEG réelles ; OCR, description, diagrammes, vidéo : `BLOCKED` | Aucun moteur OCR/vision/transcription dans l'environnement ; rien n'est inventé |
| Store | JSONL en ajout seul, chaîné par empreintes ; modification/suppression détectées | — |
| Exports | `to_training_candidates` (ACCEPT + HIGH/MEDIUM + licence OK + provenance complète, fragments par paragraphes) ; `to_memory_records` (RAG : toujours `external_provenance`, jamais VERIFIED) | — |

Règle de curation (`curate`), par priorité : injection -> QUARANTINE ; accès
refusé, licence restrictive, pas de texte, provenance incomplète -> REJECT ;
doublon -> REJECT/DUPLICATE ; hors sujet, langue, qualité -> REJECT/LOW_VALUE ;
licence inconnue, contradiction, intégrité non établie -> REVIEW ; sinon
ACCEPT. Seul un élément ACCEPT alimente les index de doublons et de
contradictions : un contenu rejeté ne peut ni masquer un doublon ni fabriquer
une contradiction.

Mesure réelle sur les sources locales (12 modules stdlib + 16 documents
`docs/*.md`, licence `apex-own`) : 28 sources -> 26 ACCEPT, 1 REJECT
(`bisect` : docstrings trop peu de mots-outils pour identifier la langue),
1 QUARANTINE (faux positif ci-dessus) ; 241 fragments d'entraînement.

## 4. Dataset (`apex/neural/dataset.py`)

TRAINING CANDIDATE -> TRAINING DATASET. Un candidat invalide (provenance,
empreinte incohérente, licence, qualité) fait ÉCHOUER la construction
(`ProvenanceError`) : jamais d'exclusion silencieuse. Dédoublonnage avant
découpage ; découpage déterministe PAR SOURCE (tous les fragments d'un
document dans le même split) ; contrôle de fuite PAR DOCUMENT de test
(8-grammes) et par source (`LeakageError`) ; empreinte SHA-256 du jeu.

Défauts trouvés par les tests de cette étape, et corrigés :

- contrôle de fuite initialement GLOBAL : quelques documents de test copiés
  intégralement dans le train étaient dilués sous le seuil par les documents
  sains. Désormais par document (test de régression dédié) ;
- extraction HTML : la fermeture d'un bloc masqué n'était jamais détectée
  (tout le texte suivant disparaissait) et l'attribut `hidden` sans valeur
  était ignoré. Réécrit avec une pile de balises ;
- qualité : un texte de 4 mots obtenait exactement le seuil (0.5) et un texte
  entièrement répétitif 0.6 : tous deux acceptés. Pénalités rendues
  décisives + diversité lexicale.

Contrôles de mutation (script hors dépôt, sources restaurées après chaque
mutant) : 17 mutants sur les garanties ci-dessus (porte de provenance,
dédoublonnage, fuite, source partagée, injection, empoisonnement des index,
doublon, contradiction, texte conservé malgré la licence, licence inconnue
promue, chaîne du store, porte d'export, mémoire marquée VERIFIED,
robots.txt, paywall, texte masqué, texte court) : 17/17 détectés.

Campagne finale (après les cycles CI et le Judge) : 34 mutants — les 17
ci-dessus, plus le Judge (empreinte du benchmark, régression significative,
tolérance de garde, ACCEPT sans significativité, mesure non finie), le pont
des expériences (succès non confirmé, empreinte de solution, cause d'erreur),
la décontamination, la vue canonique (injection, identité, homoglyphes), les
clauses de licence tokenisées, la clé du dataset, l'unicité des documents
d'un benchmark et la garde de dataset des checkpoints. Premier passage :
32/34 détectés ; les deux survivants (tolérance de garde, ACCEPT sans
significativité) révélaient des TROUS DE TEST — le code était correct mais
aucun test n'isolait ces règles ; deux tests ajoutés -> 34/34.

## 5. Évaluation et Judge (`apex/neural/evaluation.py`)

- **Mesure** : bits par octet (BPB) par document — nll totale / ln 2 / octets
  prédits (`<bos>` jamais prédit, `<eos>` compte 0 octet). Indépendant du
  tokenizer, donc comparable entre tokenizers différents. Précision top-1 du
  prochain token en complément. Vérifié contre un calcul manuel (test).
- **Benchmark** : nom + documents ; empreinte SHA-256 du contenu ; refuse
  identifiants et textes répétés (vue canonique) — un document répété
  gonflerait l'échantillon apparié.
- **NeuralJudge** (réutilise `apex.stattest.StatTest`, bootstrap apparié) :
  REJECT si benchmark modifié (empreinte ou documents), suites différentes,
  mesure non finie, régression significative sur une suite quelconque, ou
  régression de garde au-delà de la tolérance ; ACCEPT seulement si le gain
  sur la suite principale est significatif (IC 95 % strictement positif) ;
  sinon INCONCLUSIVE — jamais ACCEPT par défaut.
- **Une baisse de loss d'entraînement n'est pas une preuve** : le Judge ne
  voit que des suites séparées ; l'écart train/held-out est mesuré et publié
  (tranche `train_sample`), pas utilisé comme argument.

## 6. Expériences d'entraînement réelles (`docs/neural/experiment-00N.json`)

Commande unique (`scripts/apex_neural_experiment.py`) : Knowledge Agent sur des
sources réelles -> candidats -> dataset -> tokenizer APEX (train seulement) ->
V0 -> entraînement -> V1 -> Judge ; V2 = V1 ré-entraîné sur un flux mélangé
(régression voulue) ; tentative de benchmark allégé. Modèle : 4 couches,
largeur 192, 6 têtes, contexte 128, **1 918 656 paramètres**, vocabulaire 768.
CPU 4 cœurs. Sources : docstrings de 107 modules de la bibliothèque standard
(PSF-2.0) et, selon l'expérience, les 16 documents `docs/*.md` du dépôt
(licence `apex-own`). Chaque rapport contient configurations, empreintes
(dataset, tokenizer, benchmarks, poids), courbes, scores par document et
verdicts. Les checkpoints eux-mêmes ne sont pas versionnés (SHA-256 consignés).

**Reproductibilité MESURÉE** : 001 et 002 relancées après les correctifs des
cycles CI donnent exactement les mêmes BPB qu'à la première exécution
(2.6134 et 2.5384) — identifiants adressés par le contenu, graines fixées.
Les durées dépendent de la charge de la machine (d'autres calculs tournaient
en parallèle pour 003/004) : indicatives seulement.

| | 001 | 002 | 003 | 004 |
|---|---|---|---|---|
| Changement | docs APEX = garde hors domaine | docs APEX dans les données | 002 + dropout 0.1 | 002 + 1200 pas |
| Dataset (fragments train/val/test) | 432 / 62 / 70 | 597 / 68 / 140 | idem 002 | idem 002 |
| Sources (train/val/test) | 72 / 10 / 15 | 82 / 11 / 17 | idem | idem |
| Fragments décontaminés | 2 | 3 | 3 | 3 |
| Held-out BPB V0 -> V1 | 4.435 -> 2.613 | 4.725 -> **2.538** | 4.725 -> 2.647 | 4.725 -> 2.719 |
| Échantillon train BPB V1 | 1.040 | 1.817 | 2.064 | 0.648 |
| Précision top-1 held-out V1 | 0.251 | 0.248 | 0.206 | 0.299 |
| Judge V1 vs V0 | **REJECT** (garde) | **ACCEPT** | ACCEPT | ACCEPT |
| Judge V2 corrompue vs V1 | REJECT | REJECT | REJECT | REJECT |
| Benchmark allégé | refusé | refusé | refusé | refusé |
| Registre (vs version en service) | — | **promue** | rejetée | rejetée |

Lecture :

- **001** : V1 apprend (held-out 4.43 -> 2.61, significatif) mais régresse
  significativement sur la garde hors domaine (docs APEX en français :
  6.31 -> 7.42, IC 95 % [-1.18, -0.42]) : le Judge REJETTE. Une moyenne ou
  la seule suite principale aurait masqué cette régression. L'écart train
  (1.04) / held-out (2.61) montre aussi une forte mémorisation.
- **002** (cycle 1 côté données : diversité) : même modèle, données
  diversifiées découpées par source ; toutes les tranches progressent
  (stdlib 4.35 -> 2.40, docs APEX 5.00 -> 2.64), IC du gain held-out
  [2.15, 2.26] sur 140 documents : ACCEPT, puis promue dans le registre.
- **003** (cycle 2 : régularisation) : le dropout réduit l'écart train/held-out
  (0.58 contre 0.72) mais dégrade le held-out (+0.11 bpb, IC entièrement
  négatif sur les trois tranches) : REJETÉE face à 002. Réduire l'écart n'est
  pas s'améliorer.
- **004** (cycle 3 : budget) : doubler l'entraînement abaisse la loss
  d'entraînement (1.15) et le BPB train (0.65, précision 77 %) mais DÉGRADE
  le held-out (2.54 -> 2.72) : sur-apprentissage, REJETÉE. Nuance : la
  précision top-1 held-out monte (0.25 -> 0.30) pendant que le BPB se
  dégrade — le modèle devient trop sûr de lui quand il se trompe ; le Judge
  décide sur le BPB (métrique principale déclarée avant la mesure).
- Le texte généré (`samples_illustration_only`) est une illustration, pas une
  preuve : V1 produit une syntaxe de docstrings plausible et beaucoup de
  mots inventés.

Ce que ces mesures NE prouvent PAS : une capacité de raisonnement, de
compréhension ou d'utilité ; seulement que le modèle APEX prédit mieux, sur
des documents jamais vus, le texte d'un domaine donné. Aucune comparaison
avec un modèle externe n'a été faite (aucune n'aurait de sens à cette taille).

## 7. Expériences -> entraînement (`apex/knowledge/experience.py`)

- Succès : uniquement `SuccessEvidence.CONFIRMED` (même verdict à la
  ré-exécution) ET texte dont le SHA-256 égale l'empreinte enregistrée par
  l'expérience. OBSERVED / REFUTED : refusés.
- Erreurs : uniquement causes établies et propres à la solution (solution
  incorrecte, échec d'exécution, délai, candidat invalide, refus de
  sécurité) avec une cause décrite ; pannes d'infrastructure, de ressources,
  de génération ou d'oracle : refusées.
- Source = la tâche : toutes les tentatives d'une tâche restent dans le même
  split. Licence : fournie explicitement par l'appelant, jamais supposée.
- Un code produit par un modèle externe porte
  `|EXTERNAL_MODEL_DEPENDENCY:<modèle>` dans son origine.
- Limite : aucune expérience réelle CONFIRMÉE n'existe dans cet
  environnement (modèle externe inaccessible, voir mission précédente) ; le
  pont est testé sur des `Experience` construites, pas alimenté par la base.

## 8. Registre des versions (`apex/neural/registry.py`)

Journal en ajout seul, chaîné par empreintes. Première version promue
seulement si son expérience a démontré un gain sur V0 ; ensuite, promotion
uniquement sur ACCEPT du `NeuralJudge` contre la version en service, sur les
mêmes benchmarks. Chaque rejet est consigné avec ses raisons ; une réécriture
de l'historique est détectée. CLI : `scripts/apex_neural_promote.py`.

## 9. Continuous Improvement Engine : deux nouvelles capacités

Protocole identique aux capacités précédentes : adaptateur et sondes commités
AVANT tout correctif, baseline mesurée deux fois (reproductibilité), juge
exécuté dans un processus séparé et épinglé à la révision de la baseline
(`--judge-ref`), ledger chaîné (`docs/improvement/*.ledger.jsonl`,
`verify` : intact).

### knowledge_curation (15 scénarios adverses, juge épinglé à 9319314)

| Version | Correctif | Scénarios à 1.0 | Juge | Verdict |
|---|---|---|---|---|
| v0 | — | 9/15 | — | baseline |
| v1 | vue de détection canonique (NFKC, caractères de format) | 12/15 | reject | FAILED (véto : défauts restants) |
| v2 | clauses de licence sur forme tokenisée | 14/15 | reject | FAILED (véto : homoglyphes) |
| v3 | repli des homoglyphes dans les mots de script mixte | 15/15 | pass | IMPROVED |

Défaut le plus grave trouvé à la baseline : « CC BY NC 4.0 » classée
COMPATIBLE — du contenu non commercial aurait été accepté pour l'entraînement.

### native_learning (13 scénarios sur la vraie chaîne, juge épinglé à 4589089)

| Version | Correctif | Scénarios à 1.0 | Juge | Verdict |
|---|---|---|---|---|
| v0 | — | 10/13 | — | baseline |
| v1 | clé de dédoublonnage du dataset sur la vue canonique | 11/13 | reject | FAILED (véto) |
| v2 | benchmark : documents répétés refusés | 12/13 | reject | FAILED (véto) |
| v3 | `load_checkpoint(dataset_fingerprint=...)` refuse un autre dataset | 13/13 | pass | IMPROVED |

Un gain mesuré ne rachète jamais un véto : les cycles 1 et 2 sont
consignés FAILED alors que leurs métriques progressent, parce que le juge
(qui couvre tous les scénarios) réfutait encore la capacité.

## 10. Architecture d'apprentissage (partie D) — état réel

| Mode | État | Preuve |
|---|---|---|
| Pré-entraînement (prochain token) | IMPLÉMENTÉ, MESURÉ | expériences 001–003 |
| Détection de régression | IMPLÉMENTÉ, MESURÉ | V2 corrompue rejetée ; garde hors domaine (exp. 001) |
| Apprentissage continu (reprise) | IMPLÉMENTÉ, TESTÉ | reprise bit-exacte, garde de dataset |
| Données d'expérience (succès confirmés, leçons) | IMPLÉMENTÉ, TESTÉ | aucune expérience réelle confirmée disponible |
| SFT / instruction tuning | NON IMPLÉMENTÉ | pas de jeu instruction/réponse validé |
| Préférences (DPO/RLHF), RL | NON IMPLÉMENTÉ | aucun signal de préférence ni environnement de récompense |
| Données synthétiques | NON IMPLÉMENTÉ pour l'entraînement | (corpus synthétique utilisé seulement pour les sondes CI) |
| Curriculum, replay | NON IMPLÉMENTÉ | — |

Chaque mode non implémenté est une absence déclarée, pas une interface vide
présentée comme disponible.

## 11. Échelle (CPU / CUDA)

Tout le code est paramétré (`ModelConfig`, `TrainingConfig`) : taille,
contexte, dtype, device ; précision mixte bf16 sur CPU et fp16 + GradScaler
sur CUDA ; accumulation de gradients. MESURÉ ici : CPU seulement (4 cœurs,
`cuda.is_available()` = False). Le chemin CUDA est écrit mais NON VÉRIFIÉ
sur GPU dans cet environnement.

## 12. Modèles externes

Aucun modèle externe n'intervient dans le modèle APEX, son tokenizer, son
entraînement ou son évaluation. Là où un modèle externe produirait une
donnée (code d'une expérience, future OCR/vision), l'origine porte
`EXTERNAL_MODEL_DEPENDENCY`. Ollama n'a pas pu être exercé (politique réseau :
ollama.com, registry.ollama.ai et huggingface.co répondent 403) — cela ne
bloque en rien le modèle natif, entraîné et évalué sans lui.

## 13. Réponses expérimentales aux 14 questions

Étiquettes : TESTED (test automatisé), MEASURED (mesure consignée),
JUDGED (décision du Judge / juge CI épinglé), INFERRED, UNVERIFIED, BLOCKED.

| # | Question | Réponse | Preuve |
|---|---|---|---|
| 1 | APEX possède-t-il un modèle neuronal propre ? | **Oui** — transformeur décodeur (`apex.neural.model.ApexLM`), tokenizer BPE entraîné par APEX, aucun poids externe. Petit (1,9 M paramètres). | TESTED, MEASURED |
| 2 | Peut-on calculer ses gradients ? | **Oui** — gradients finis et non nuls sur tous les paramètres ; nuls là où ils doivent l'être (lignes d'embedding absentes). | TESTED |
| 3 | La backpropagation modifie-t-elle ses paramètres ? | **Oui** — tous les tenseurs changent ; `optimizer.step()` neutralisé -> paramètres inchangés (test discriminant) ; pas sans gradient -> refusé. | TESTED, MEASURED (CI native_learning) |
| 4 | Peut-on l'entraîner ? | **Oui** — held-out 4.73 -> 2.54 bpb (IC 95 % du gain [2.15, 2.26], 140 documents jamais vus). | MEASURED, JUDGED |
| 5 | Sauvegarder / recharger son état ? | **Oui** — modèle, optimiseur, scheduler, RNG, échantillonneur, empreinte et provenance du dataset ; reprise bit-exacte ; reprise sur un autre dataset refusée. | TESTED |
| 6 | Construire un dataset propre à partir du Knowledge Agent ? | **Oui** — 810 fragments de 110 sources acceptées, découpage par source, fuite ≤ 8,3 % par document de test (décontamination tracée : cas réel gzip/lzma). | TESTED, MEASURED |
| 7 | Le Knowledge Agent distingue-t-il utile et bruit ? | **Partiellement** — 15/15 scénarios adverses (injections déguisées, doublons maquillés, licences mal écrites, contradictions, texte trop court ou répétitif). Heuristiques : ne juge ni l'exactitude ni la profondeur ; faux positif réel connu (commande `curl … \| sh` légitime mise en quarantaine). | JUDGED (CI), TESTED |
| 8 | La provenance de chaque connaissance est-elle conservée ? | **Oui** — source, licence, empreintes, méthode d'extraction, historique de transformation, trace par étape, store chaîné ; chaque fragment d'entraînement remonte à un élément ACCEPT (validation croisée). | TESTED |
| 9 | Les contenus externes restent-ils séparés des instructions ? | **Oui dans ce pipeline** — le texte n'est jamais interprété ; une instruction (y compris invisible, pleine chasse, homoglyphes, masquée en CSS) -> QUARANTINE, jamais dans le dataset ni la mémoire. Limite : motifs connus ; un mot entièrement en homoglyphes n'est pas replié. | TESTED, JUDGED |
| 10 | Transformer des connaissances validées en données d'entraînement ? | **Oui** — ACCEPT + qualité + licence + provenance uniquement ; expériences : seulement succès CONFIRMÉS à empreinte vérifiée et erreurs à cause établie. Aucune expérience réelle confirmée disponible ici. | TESTED ; UNVERIFIED sur données d'expérience réelles |
| 11 | Entraîner une nouvelle version ? | **Oui** — V1 de 002 acceptée puis promue dans le registre des versions. | MEASURED, JUDGED |
| 12 | Démontrer qu'elle est meilleure sur des capacités données ? | **Oui pour la capacité mesurée** (prédiction de texte technique jamais vu, par tranche de domaine), bootstrap apparié. **Non démontré** pour raisonnement, code, compréhension : aucun benchmark de ces capacités n'est encore branché au modèle natif. | MEASURED, JUDGED ; UNVERIFIED au-delà |
| 13 | Rejeter une version qui régresse ? | **Oui** — V2 corrompue rejetée (4 expériences) ; 001 rejetée pour régression de garde malgré le gain principal ; 003 et 004 rejetées face à la version en service ; benchmark allégé refusé ; comparaison entre datasets différents refusée. | JUDGED, TESTED |
| 14 | Recommencer tout le processus automatiquement ? | **Partiellement** — une commande relance toute la chaîne de façon déterministe (résultats identiques d'une exécution à l'autre) et le registre ne promeut que sur ACCEPT. Pas encore de boucle planifiée qui acquiert de nouvelles sources, relance et promeut sans intervention ; et un changement de corpus change le benchmark, donc la comparaison est refusée (voulu) : il faudra un benchmark de référence figé pour comparer des versions entraînées sur des corpus différents. | MEASURED ; INFERRED pour la boucle complète |

**BLOCKED** dans cet environnement : acquisition web réelle (politique
réseau), modèles externes (Ollama, Hugging Face : 403), GPU (aucun),
OCR / vision / transcription (outils absents).

## 14. Validation de la suite complète (compteurs exacts)

`scripts/apex_test_report.py` sur tout `tests/` (conteneur relancé, CPU,
PyTorch 2.14.0 installé, ni PostgreSQL ni Docker démarrés) :

**2538 collectés — 2357 réussis, 6 échoués, 175 ignorés, 665 sous-tests
réussis, code de sortie 1.**

Ignorés : 154 PostgreSQL réel absent (le cluster jetable de la mission
précédente n'a pas survécu au redémarrage du conteneur — non recréé), 13
Docker, 5 Ollama / endpoint OpenAI-compatible, 1 root, 2 autres raisons
d'environnement. Aucun ne couvre du code modifié par cette mission.

Échecs, tous hors du périmètre de cette mission et analysés :

- `tests/test_runtime_packaging.py` (4) — préexistants depuis 92daa7a
  (58 violations anti-fuite, contrat JSON du Doctor absent d'un build depuis
  HEAD) ; nombre de violations inchangé (58) avec les nouveaux paquets
  `neural` et `knowledge`, tous deux classés Research.
- `tests/test_learning_orchestrator.py::TestIdempotencyAndConcurrency` (2) —
  intermittents : 0 échec sur 8 exécutions isolées à la révision d'avant
  mission (90f12d3) comme à HEAD. Causes lues dans la trace : (a) la session
  se TERMINE avant l'annulation envoyée après un `sleep(0.05)` sous charge
  (hypothèse temporelle du test) ; (b) SQLite partage une seule connexion
  entre le test et le thread de fond et lève un `DatabaseError` générique
  au lieu d'`IntegrityError`, donc la traduction en
  `LearningSessionAlreadyRunning` ne s'applique pas. Correctif proposé (non
  appliqué, autre sous-système) : bloquer la génération dans le test (a)
  comme le fait déjà son voisin, et traduire aussi `DatabaseError` portant
  une violation d'unicité dans `LearningSessionService.start`.

Tests ajoutés par cette mission (tous réussis) :
`test_neural_model.py`, `test_neural_training.py`, `test_neural_dataset.py`,
`test_neural_evaluation.py`, `test_neural_registry.py`,
`test_knowledge_agent.py`, `test_knowledge_experience.py`,
`test_improvement_native.py`, `test_native_cross_system.py`.
Exécutés seuls : **143 collectés, 143 réussis, 59 sous-tests réussis, code 0.**

## 15. Le Cerveau APEX dans l'application (0.4.1)

Demande de l'utilisateur (09/10/2026) : pouvoir choisir, dans l'assistant, entre Ollama et « le modèle qu'on
développe nous-même ». Ce modèle est ApexLM ; il est désormais servi par l'application Windows, comme moteur
**expérimental**, à côté d'Ollama et du moteur intégré (Qwen2.5-Coder 1.5B exécuté par APEX).

| Élément | Où | Garantie |
|---|---|---|
| Architecture, tokenizer, configuration du modèle | `apex/brain/` (paquet Runtime) | une seule définition : `apex/neural` (recherche) la réexporte |
| Poids servis | `brain/apex-brain-v1/` (safetensors, 7,7 Mo) | jamais un checkpoint d'entraînement (pickle) ; SHA-256 du fichier ET empreinte des poids chargés comparés à `provenance.json` à chaque chargement — des poids modifiés sont refusés |
| Version | `experiment-002:v1` | la version **promue** du registre (`docs/neural/registry.jsonl`, index 0, décision ACCEPT) |
| Export | `scripts/apex_brain_export.py` | refuse une version non promue, ou des poids dont l'empreinte diffère de celle mesurée par le registre |
| Backend | `apex/models/apex_brain.py` (`runtime: apex_brain`) | reçoit le dernier message seulement (contexte de 128 jetons), continue le texte ; sortie rendue telle quelle |
| Choix | `apex/assistant/engines.py`, Paramètres → Modèles, liste « Moteur » | jamais candidat d'un rôle de la configuration : ni « Automatique », ni tâches, missions ou `apex solve` ne le choisissent |

**Reproduction mesurée.** Les checkpoints n'étant pas versionnés, `experiment-002` a été relancée le 09/10/2026
avec le code et les documents du commit `59cdbd1` (état du 26/09), lus à leur chemin d'origine
(`/home/user/apex-ia-/docs` : les identifiants des documents, donc le découpage train/test, en dépendent — une
première relance depuis un autre chemin donnait un autre découpage et d'autres poids). Résultat : empreintes des
poids (`sha256:be46e658…`), du dataset (`sha256:0de29c88…`) et du tokenizer (`d04294c7…`) **identiques** au
registre ; BPB held-out 2,53844, validation 2,89142, mêmes verdicts du Judge (V1 ACCEPT contre V0, V2 corrompue
REJECT). Seul le fichier de checkpoint diffère (il contient aussi l'horodatage et l'état de l'optimiseur).

**Ce qu'il fait dans l'assistant, mesuré.** 1 918 656 paramètres, CPU : ~190 jetons/s, chargement < 0,1 s.
« Return the » → « current module processes. ## itertools.crement Return the codecstical keywords… » ;
« Bonjour, qui es-tu ? » (décodage glouton) → une suite de chiffres et de ponctuation. Il ne converse pas : ce
n'est pas un défaut de l'intégration, c'est l'état du modèle (§10 : ni SFT, ni préférences, ni RL). L'interface
le dit avant l'envoi. Prochaine étape pour qu'il réponde : un jeu instruction/réponse validé, puis un
entraînement supervisé jugé comme les versions précédentes (ACCEPT seulement sur gain significatif, sans
régression de garde).
