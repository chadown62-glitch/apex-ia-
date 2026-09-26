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
