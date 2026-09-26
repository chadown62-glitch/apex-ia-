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
