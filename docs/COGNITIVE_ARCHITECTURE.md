# APEX — Architecture cognitive et apprentissage autonome (conception, non implémentée)

**Rien de ce document n'est implémenté**, à l'exception des éléments explicitement
marqués REAL. Il fixe l'emplacement architectural de chaque composant afin que
son introduction ultérieure ne demande pas de refonte, dans le même esprit que
`docs/EXTERNAL_INTELLIGENCE.md` et `docs/VISION_INTEGRATION.md`, dont il
poursuit la méthode : confronter chaque besoin aux abstractions existantes
avant d'en créer une nouvelle (ADR-0007).

---

## 0. Méthode et verdict global

Confrontation aux 34 abstractions existantes avant ce document (36 après —
voir §9 et ADR-0011). **Résultat : 2 nouvelles abstractions (`AgentRuntime`,
`CodeIntelligence`), une généralisation de schéma (`LanguageToolchain` →
`Domain`), zéro doublon introduit.** Tout le reste demandé (Learning Manager,
Knowledge Graph, Experiment Engine, Benchmark Engine, boucle
d'auto-amélioration) se compose à partir de `TaskBank`, `EvalRunner`,
`StatTest`, `OracleProvider`, `WeaknessDetector`, `CurriculumStrategy`,
`MemoryProvider`, `HypothesisGenerator`, `PromotionGovernor`,
`CostGainEstimator` — tous déjà déclarés, tous encore `PLANNED`.

Aucune capacité de ce document n'est déclarée `EXISTING`. Le vocabulaire du
Gate (`PASS`/`FAIL`/`DEGRADED`/`UNKNOWN`/`SIMULATED`) et celui d'`apex.obs.health`
(`OK`/`DEGRADED`/`FAILED`) restent les deux seuls vocabulaires d'état reconnus
dans APEX — ce document n'en introduit pas un troisième, il les applique à un
domaine nouveau (§12).

---

## 1. Architecture cognitive globale

```
┌─────────────────────────────────────────────────────────────────────┐
│ CORE (Python, Git, humain seul — ADR-0001)                          │
│  errors · obs · config · doctor · gate · db · interfaces.registry    │
│  PolicyEngine · SecretBroker · Resource Manager (ComputeBudget)      │
│  PromotionGovernor (hors de portée de toute mutation)                │
└─────────────────────────────────────────────────────────────────────┘
              │ lit/écrit                         │ autorise (spawn)
              ▼                                    ▼
┌───────────────────────────┐        ┌───────────────────────────────┐
│ GENOME (données versionnées│        │ AgentRuntime → Clones          │
│ mutables — jamais de code) │◄──────►│ Research/Learning/Developer/   │
│ prompts, curriculum, seuils│ lisent │ Experiment/Critic/Security/    │
│                             │        │ Benchmark/Teacher/Integrator   │
└───────────────────────────┘        └───────────────────────────────┘
              │                                    │ écrivent (scellé, scopé)
              ▼                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ MÉMOIRE & CONNAISSANCE : MemoryProvider (+ schéma Knowledge Graph)   │
│ travail · épisodique (`events`, EXISTS) · sémantique · procédurale ·│
│ expérimentale                                                        │
└─────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌───────────────────────────┐        ┌───────────────────────────────┐
│ ExperimentEngine            │───────►│ Benchmark (mode d'EvalRunner)  │
│ = TaskBank + OracleProvider │        │ vN vs vN-1, empreinte identique│
│   + EvalRunner + StatTest   │        └───────────────────────────────┘
│   + HypothesisGenerator      │
└───────────────────────────┘
              │ preuve
              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ GATE — Hardware/Environment (EXISTS) + Code/Security/Learning (§10)  │
│ PASS/FAIL/DEGRADED/UNKNOWN/SIMULATED — jamais contourné               │
└─────────────────────────────────────────────────────────────────────┘
              │
              ▼
      PromotionGovernor (HUMAIN par défaut, §11) → GenomeStore (phase 17)
```

Invariant non négociable (ADR-0001, confirmé par ce document) : **aucun clone,
aucune boucle d'apprentissage, aucun `ExperimentEngine` ne modifie le Core.**
Toute évolution réelle du comportement d'APEX passe par une mutation du
Genome, votée par un `PromotionGovernor` qui n'est lui-même jamais soumis à
mutation.

---

## 2. `AgentRuntime` et le système de clones

### 2.1 Décision

Nouvelle abstraction `AgentRuntime` (voir ADR-0011) : instanciation, cycle de
vie et isolation d'un clone spécialisé. Un clone n'est pas une primitive
d'exécution nouvelle — c'est la composition de `ComputePool`+`trust_level`
(EXISTS, phase 2), d'un rôle SQL scopé (motif déjà validé contre PostgreSQL
réel, phase 2), de `SandboxDefaults` (EXISTS, phase 1) et de `PolicyEngine`
(PLANNED, phase 7).

### 2.2 Identité

```
Clone {
  clone_id: uuid
  type: enum(research|learning|developer|experiment|critic|
             security|benchmark|teacher|integrator)
  mission_id: fk → Mission (nullable)
  genome_ref: hash              # version du Genome épinglée AU SPAWN
  spawned_by: clone_id | "core" | "human"
  pool_id: fk → compute_pools    # EXISTS déjà
  isolation_level: str           # jamais > ce que GATE-19 a mesuré RÉELLEMENT
  created_at, terminated_at
}
```

`genome_ref` épinglé au spawn est obligatoire : sans lui, deux clones
travaillant pendant une mutation du Genome produiraient des résultats
incomparables sans qu'on le sache — même défaut que l'absence d'empreinte
matérielle aurait produit pour le Gate.

### 2.3 État — machine à états

```
SPAWNING → READY → WORKING → REPORTING → TERMINATING → TERMINATED
              │         │
              │         └──(heartbeat expiré)──→ CRASHED ──→ RECOVERING ─┐
              │                                                           │
              └───────────────────(retry, budget et max_retries restants)┘
                                          │
                                (max_retries atteint)
                                          ▼
                                    FAILED (terminal)
```

### 2.4 Création / destruction

**Création** refusée si : le `ComputePool` n'a pas la `Capability` requise
(EXISTS), l'isolation mesurée par GATE-19 est insuffisante pour le type de
clone (ex. Developer Clone exige `code_execution_allowed: true` mesuré, pas
supposé), ou le budget demandé dépasse `ComputeBudget.reserve_fraction`
restant.

**Destruction** : `terminate()` explicite, budget épuisé (`timeout_s`,
principe 13), crash détecté (heartbeat absent), ou mission annulée en
cascade. Toujours une écriture `audit_log` finale — jamais silencieuse.

### 2.5 Mémoire propre au clone

Espace nommé dans `MemoryProvider`, clé = `clone_id`. Écriture : uniquement
son espace. Lecture : définie par type (table §2.7). Rien ne survit à la
terminaison par défaut ; la promotion vers la mémoire partagée est un acte
explicite et audité, jamais automatique.

### 2.6 Budget CPU/GPU/RAM et accès réseau

Réutilisation directe de `SandboxDefaults` (EXISTS, phase 1) :
`cpu_limit`, `memory_mb`, `pids_limit`, `timeout_s`. `network` reste limité à
`none`/`proxy` (déjà appliqué par `ApexConfig.validate()`, principe 4). Par
défaut `none` pour tous les types sauf **Research Clone** (`proxy`, contextes
déjà en liste blanche fermée — `ExternalPolicy.allowed_contexts`, aucun
nouveau contexte ajouté par ce document).

### 2.7 Permissions par type

| Type | Lecture | Écriture | Jamais |
|---|---|---|---|
| Research | graphe de connaissances, sources externes autorisées | hypothèses, mémoire propre | Genome, code |
| Learning | mission assignée, curriculum | progression, résultats | autres missions |
| Developer | instantané figé du dépôt | worktree Git isolé | dépôt principal, `main` |
| Experiment | dataset, config | résultats bruts, métriques | conclusions non vérifiées par `StatTest` |
| Critic | ce qu'il évalue | critique structurée | le résultat qu'il critique |
| Security | ce qu'il audite | rapport, `audit_log` | correction directe |
| Benchmark | historique de versions | scores comparatifs | tout le reste |
| Teacher | mémoire de l'élève (lecture seule) | traces d'enseignement | Genome de l'élève directement |
| Integrator | proposition validée par GATE | soumission à `PromotionGovernor` | tout commit/push/merge |

Motif technique de mise en œuvre (le jour venu) : un rôle SQL par type,
GRANT/REVOKE explicites — exactement le motif `apex_core`/`apex_agent`/
`apex_eval`/`apex_audit`/`apex_readonly` déjà prouvé contre PostgreSQL réel
en Phase 2 (ADR-0010), étendu, pas réinventé.

### 2.8 Communication

Aucun canal direct entre clones. Tout passe par `events` (EXISTS, partitionnée
mensuellement) avec une taxonomie déclarée dans le Genome (donnée, pas code —
ADR-0001) : `clone.spawned` · `clone.heartbeat` · `clone.result` ·
`clone.error` · `clone.terminated` · `clone.message` (relayé par le Core,
jamais pair-à-pair).

### 2.9 Supervision et traçabilité

Un superviseur (Core) poll les `clone.heartbeat` — même famille structurelle
défensive que `apex/gate/runner.py`/`apex/doctor/runner.py` : une boucle qui
n'explose jamais sur l'échec d'un item individuel. Chaque action significative
→ `audit_log` (gouvernance) ou `events` (routine).

### 2.10 Récupération après crash

Heartbeat expiré → `CRASHED` → décision du superviseur : **retry** (nouveau
clone, budget décrémenté, jusqu'à `max_retries` — champ nouveau, à ajouter au
Genome) ou **escalade** (`FAILED`, notification). **Le travail partiel d'un
clone crashé n'est jamais promu automatiquement** — il retraverse Critic +
Security + Gate comme toute proposition normale ; les écritures DB restent
protégées par `session_scope` (EXISTS, transactionnel depuis la Phase 2),
donc jamais d'état intermédiaire visible en base.

### 2.11 Limites absolues de sécurité (clones)

1. Aucun clone ne possède d'identifiants Git pour `origin`.
2. Aucun clone n'écrit hors de son espace mémoire propre (et, pour Developer,
   hors de son worktree isolé).
3. Aucun clone ne modifie `apex/gate/`, `apex/interfaces/registry.py`
   (logique d'application), `apex/errors.py`, `gate.exceptions.yaml`,
   `audit_log`, ou un ADR.
4. Rôle SQL fixé au spawn, non modifiable à l'exécution — aucune
   auto-élévation possible.
5. Isolation toujours ≤ ce que GATE-19 a **mesuré** sur la machine réelle.
6. Nombre de clones concurrents plafonné (champ nouveau, Genome).
7. Integrator Clone ne merge jamais — il soumet, point final.

---

## 3. `Domain` — généralisation de `LanguageToolchain`

### 3.1 Décision (arbitrage tranché)

`LanguageToolchain` (`apex.config.schema`, `DEFINED` depuis la phase 1) est
généralisée en `Domain` ; `LanguageToolchain` devient sa **spécialisation
dédiée au code**. Objectif : apprentissage non-code (Maths, Physique, Anglais,
Cybersécurité...) sans abstraction parallèle.

### 3.2 Schéma proposé (conception — non implémentée)

```python
class DomainKind(str, Enum):
    CODE = "code"
    KNOWLEDGE = "knowledge"

@dataclass(frozen=True)
class Domain:
    """Base commune à tout domaine d'apprentissage, code ou non."""
    name: str
    kind: DomainKind
    wave: int
    oracle_strength: str          # very_strong|strong|medium|weak|unknown — INCHANGÉ
    separate_metrics: bool

@dataclass(frozen=True)
class LanguageToolchain(Domain):
    """Spécialisation code : image de conteneur + commandes d'outillage.
    Champs et validations INCHANGÉS par rapport à la phase 1."""
    image: str
    commands: dict[str, str]
```

### 3.3 Invariants de compatibilité — obligatoires

- **`languages:` dans `config.yaml` n'est pas touché.** Même clé, même
  structure, même validation (`image` obligatoire avec tag/digest,
  `oracle_strength` dans le même ensemble fermé de 5 valeurs). Toute
  configuration valide aujourd'hui reste valide sans modification.
- `ApexConfig.languages: dict[str, LanguageToolchain]` reste tel quel.
- **Nouveau champ additif** `ApexConfig.domains: dict[str, Domain]`, alimenté
  par une nouvelle clé `domains:` (optionnelle, défaut `{}`) — aucune
  configuration existante n'est affectée par son absence.
- `LanguageToolchain` reste substituable partout où `Domain` est attendu
  (relation is-a réelle, pas un renommage cosmétique) : le futur code du
  Learning Manager (`CurriculumStrategy`, `WeaknessDetector`, `TaskBank`,
  tous encore `PLANNED`) s'écrit contre `Domain` et fonctionne pour le code
  et le non-code sans branche spéciale.
- Un domaine `KNOWLEDGE` n'exige ni `image` ni `commands` — une leçon de
  Maths ne tourne pas dans un conteneur par défaut.

### 3.4 Migration

Aucune migration de données requise : c'est un ajout de schéma pur (nouvelle
classe base + nouveau champ optionnel), pas une modification de forme
existante. L'ensemble de `tests/test_config.py` (21 tests actuels) doit
continuer à passer sans modification le jour de l'implémentation — c'est le
critère de non-régression à vérifier avant tout commit sur ce point.

---

## 4. Learning Manager multi-domaines

Aucune nouvelle abstraction technique : orchestration de `Domain` (§3),
`Placement` (PLANNED, phase 6), `TaskBank`/`OracleProvider`/`EvalRunner`
(PLANNED, phase 5), `CurriculumStrategy`/`WeaknessDetector` (PLANNED, phase
12), `ComputeBudget` (EXISTS).

```
Mission {
  mission_id, domain: Domain,
  assigned_clones: [clone_id],
  priority: float,               # arbitré par Placement × CostGainEstimator
  status: active|paused|completed|abandoned,
  budget_allocated, created_at
}
```

| Capacité demandée | Mécanisme | Dépendance |
|---|---|---|
| Détecter les lacunes | `WeaknessDetector` sur scores `EvalRunner` + traversée Knowledge Graph (§6) | phase 12 |
| Missions simultanées | Plusieurs `Mission` actives, `Placement` arbitre `ComputeBudget` | extension `Placement` |
| Affecter des clones | `AgentRuntime.spawn` par mission | §2 |
| Adapter la difficulté | `CurriculumStrategy` sur sortie `WeaknessDetector` | phase 12/15 |
| Interrompre un domaine | `mission.status = paused` : clones terminés proprement, budget restitué, mémoire/graphe conservés | comportement, pas de nouvelle table |
| Prioriser | Changement de `priority` → ré-arbitration `Placement` (événementiel, pas cron) | extension |
| Transférer entre domaines | Arêtes cross-domaines du Knowledge Graph, **vérifié par une expérience**, jamais supposé | §6, §7 |
| Mesurer la progression | Historique `EvalRunner` par domaine | table `learnings` (§9) |
| Détecter la stagnation | `StatTest` sur pente de progression, non significativement différente de zéro sur N évaluations | extension `StatTest` |
| Changer de méthode | Déclenche §5 (comparaison de méthodes) pour le domaine en stagnation | dépendance directe |

---

## 5. Apprentissage adaptatif — collecte tôt, comparaison tard

`MetaLearner` reste `PLANNED`, phase 22 (registre inchangé par ce document).
Ce qui démarre dès `EvalRunner` (phase 5) : chaque run est tagué par un champ
fermé `method`, sans comparaison :

```
method: enum(practice | spaced_repetition | problem_generation | research |
             imitation | self_explanation | teaching | experimentation | combination)
```

enregistré dans `learnings` (§9) avec `{domain, method, temps, coût,
taux_erreur, rétention, généralisation, transfert}`. **Collecte pure.** La
comparaison réelle (`MetaLearner`) ne s'active pas à une phase fixe mais
**une fois assez d'essais accumulés par couple (domaine, méthode)** pour
qu'un `StatTest` produise une décision non bruitée — seuil de taille
d'échantillon à fixer au moment de l'implémentation, pas ici. Tant que ce
seuil n'est pas atteint, toute affirmation de type « la méthode X est
meilleure » reste `UNKNOWN`, jamais présentée comme conclusion.

---

## 6. Knowledge Graph

Pas de nouvelle abstraction top-niveau — extension de schéma de
`MemoryProvider` (PLANNED, phase 8), pour éviter le doublon avec
l'absorption déjà décidée de « Memory + RAG » (`docs/VISION_INTEGRATION.md`
§2.1).

```
Node {id, label, domain, type: concept|skill|fact,
      confidence: float, validated_at,
      provenance{source, external_provenance, created_by}}
Edge {source, target, type: prerequisite|related|contradicts|supports,
      evidence: [event_id|experiment_id], confidence}
```

Exemple : `dérivées --prerequisite--> fonctions --prerequisite--> algèbre`,
et `mécanique --prerequisite--> dérivées` (arête cross-domaine
Maths→Physique).

**« Pour apprendre X, il me manque probablement Y »** : pour chaque arête
`prerequisite` entrant sur X, si Y n'a pas de score de maîtrise validé
au-dessus d'un seuil dans `learnings`, c'est une **hypothèse de lacune**
— jamais une certitude, le graphe pouvant être incomplet ou faux.

**Contradictions** : une arête `contradicts` entre deux nœuds tous deux
au-dessus du seuil de confiance est **signalée pour revue** (Critic Clone ou
humain), jamais résolue automatiquement — même principe que « `UNKNOWN` ne
devient jamais `PASS` », appliqué ici au savoir.

`external_provenance`/`external_source` (déjà spécifiés,
`docs/EXTERNAL_INTELLIGENCE.md` §3) s'appliquent tels quels à chaque nœud —
aucun mécanisme de traçabilité nouveau.

---

## 7. `ExperimentEngine`

Nom formalisé pour une composition déjà spécifiée en prose
(`docs/VISION_INTEGRATION.md` §2.2, §3.3) mais jamais déclarée au registre.
**Aucune nouvelle mécanique** :

```
ExperimentEngine = TaskBank (tâches) + OracleProvider (vérité terrain)
                  + EvalRunner (exécution appariée, seeds, budgets)
                  + StatTest (décision statistique)
                  + HypothesisGenerator (proposition d'hypothèse, phase 16)
```

```
Experiment {
  hypothesis, method, variables, dataset_ref: hash,   # principe 5
  clones_participants: [clone_id],
  results: [...], metrics: {...},
  comparison: baseline + empreinte matérielle,
  conclusion, confidence: StatTest_output
}
```

Cinq comparaisons types, toutes la même structure, seule la variable change :

| Comparaison | Variable | Dépendance |
|---|---|---|
| Méthode A vs B | `method` | §5 |
| Clone A vs B | configuration/`genome_ref` | §2 |
| Genome v1 vs v2 | `genome_ref` | `GenomeStore`, **phase 17** — impossible avant |
| Prompt A vs B | donnée du Genome | aucune nouvelle |
| Curriculum A vs B | paramètres `CurriculumStrategy` | phase 12 |

Trois préconditions obligatoires, aucune optionnelle : `seed` fixée,
`dataset_ref` (hash), empreinte matérielle identique entre les deux bras
(mécanisme du Gate, `fingerprint()`, EXISTS — réutilisé, pas réinventé).
Sans les trois : résultat `DEGRADED` au mieux, jamais concluant.

**Benchmark Engine** = mode d'`EvalRunner` comparant `APEX vN` vs `vN-1`
plutôt que deux méthodes — même moteur, même exigence d'empreinte identique,
troisième mode d'ablation après `LOCAL_ONLY`/`EXTERNAL_ASSISTED` déjà
spécifiés.

---

## 8. `CodeIntelligence`

Nouvelle abstraction (voir ADR-0011). Périmètre strict : **lecture et
proposition seulement.** Elle ne modifie jamais le dépôt — c'est le Developer
Clone (§2, sous supervision complète §10) qui matérialise une proposition,
jamais `CodeIntelligence` elle-même.

### 8.1 Représentation — construite sur l'existant, pas inventée

| Couche | Source | Disponibilité |
|---|---|---|
| Modules/classes/fonctions | AST + `apex.interfaces.registry` (déjà une carte partielle honnête) | à construire |
| Dépendances | imports, analysable statiquement | à construire |
| Code ↔ tests | convention déjà cohérente dans tout le dépôt (`test_gate.py`↔`gate`, `test_db_*.py`↔`db`...) | exploitable immédiatement |
| Code ↔ documentation | docstrings de module déjà systématiquement riches (« pourquoi », pas « quoi ») | exploitable immédiatement |
| Historique Git | `git log` par fichier | exploitable immédiatement |
| Décisions ADR | déjà structurées, jamais réécrites | exploitable immédiatement |
| Migrations/config | déjà structurées (Alembic, schéma `config.yaml` strict) | exploitable immédiatement |

### 8.2 Détection

- **Incohérences** : généralisation du test déjà existant
  `test_registry_cannot_overstate_reality` à d'autres paires doc/code.
- **Bugs potentiels** : `ruff`/`mypy` (déjà dans `Makefile`) + reconnaissance
  de motifs historiques (K1/K2 sont un précédent réel). Capacité limitée,
  volontairement pas sur-promise.
- **Code mort** : accessibilité depuis `apex/cli/main.py` + absence de
  couverture de test.
- **Tests manquants** : module sans `test_<module>.py` correspondant.
- **Régressions** : comparaison à l'historique du Benchmark Engine (§7).

### 8.3 Workflow strictement borné

Developer Clone → worktree Git isolé (pas un fork de l'environnement),
checkout au commit exact de la proposition. Écrit uniquement sur sa branche
locale. **Aucun clone, y compris Integrator, ne possède d'identifiants pour
`origin`.** Interdits absolus, en plus de §2.11 : jamais `apex/gate/`,
jamais `apex/interfaces/registry.py` (application des règles), jamais un
ADR, jamais `main`, jamais les mécanismes de sécurité ou de gouvernance.

---

## 9. Table DB — ce qui est réutilisable, ce qui est nouveau (aucune création maintenant)

| Table | Statut | Abstraction propriétaire |
|---|---|---|
| `compute_pools`, `events`, `audit_log` | **EXISTS** (Phase 2) | réutilisées telles quelles |
| `agents` (identité, cycle de vie des clones) | nouvelle, non créée | `AgentRuntime` |
| `knowledge_nodes`, `knowledge_edges` | nouvelle, non créée | `MemoryProvider` (§6) |
| `experiments`, `experiment_results` | nouvelle, non créée | `ExperimentEngine` (§7) |
| `benchmarks`, `benchmark_runs` | nouvelle, non créée | Benchmark Engine (§7) |
| `learnings` (méthode, domaine, métriques) | nouvelle, non créée | §5 |
| `skills` | nouvelle, non créée | `SkillRegistry`, phase 11 |
| `genome_versions` | nouvelle, non créée | `GenomeStore`, phase 17 |
| `code_intelligence_findings` | nouvelle, non créée | `CodeIntelligence` (§8) |

Aucune de ces tables n'est créée par ce document. Chacune est introduite avec
son abstraction propriétaire, jamais avant (ADR-0007), avec partitionnement
mensuel day-one pour les tables à forte cardinalité (`experiments`,
`benchmark_runs`) — même motif que `docs/adr/0010-persistance-postgresql.md`.

---

## 10. Auto-amélioration contrôlée — gouvernance AUTO / PROPOSE-ONLY / HUMAIN

```
OBSERVE → IDENTIFY → HYPOTHESIZE → EXPERIMENT → IMPLEMENT → TEST →
BENCHMARK → CRITIC → SECURITY → GATE → PROMOTION
```

| Étape | Décision | Détail |
|---|---|---|
| OBSERVE | **AUTO** | lecture seule |
| IDENTIFY | **AUTO** | lecture seule |
| HYPOTHESIZE | **AUTO** | marqué `external_provenance` si `HypothesisGenerator` a consulté un modèle externe |
| EXPERIMENT | **AUTO**, plafonné par `ComputeBudget` | conception revue par Critic si code sensible à la sécurité |
| IMPLEMENT | **AUTO**, strictement dans le worktree isolé du Developer Clone | jamais ailleurs |
| TEST | **AUTO** | suite existante + nouveaux tests exigés, aucun test supprimé (règle déjà en vigueur, Phase 2) |
| BENCHMARK | **AUTO** | vs version précédente, empreinte identique exigée |
| CRITIC | **AUTO** (Critic Clone) | verdict **consultatif**, ne bloque pas seul |
| SECURITY | **AUTO** (scan) → **PROPOSE-ONLY** si finding bloquant | jamais auto-écarté |
| GATE | **AUTO** (évaluation déterministe, vocabulaire déjà verrouillé) | `FAIL`/`UNKNOWN` arrête toujours, jamais retenté silencieusement |
| PROMOTION | **HUMAIN** | voir §11 |

---

## 11. Gouvernance de la promotion — HUMAINE aujourd'hui, prospective documentée

**Décision actuelle, non négociable tant qu'elle n'est pas explicitement
révisée par vous : `PROMOTION` reste HUMAINE par défaut. Aucune promotion
automatique n'est mise en place par ce document ni par son implémentation
future immédiate.**

Gouvernance prospective — **documentée pour mémoire, statut `PROPOSED/FUTURE`,
non activée** — sous réserve que TOUS les critères suivants soient
simultanément vrais, aucune exception :

1. Critères stricts et **déterministes** (pas de jugement flou).
2. `GATE = PASS` obligatoire (jamais `DEGRADED` ni excusé par une exception
   pour une promotion automatique).
3. `SECURITY` : aucun finding bloquant, aucune exception active.
4. Tests et benchmarks satisfaisants au seuil défini par le Genome.
5. Reproductibilité démontrée (seed, `dataset_ref`, empreinte matérielle
   identique — §7).
6. Provenance complète (`external_provenance` tracée jusqu'au bout, principe
   déjà posé par `docs/EXTERNAL_INTELLIGENCE.md`).
7. **Aucune modification des composants protégés** — Core, Gate, registre,
   ADR, `PromotionGovernor` lui-même, mécanismes de sécurité : une promotion
   qui toucherait l'un de ces éléments reste HUMAINE quoi qu'il arrive, sans
   exception possible, même sous ce régime prospectif.
8. Budget plafonné (ne peut pas dépasser une enveloppe fixée à l'avance).
9. Audit complet (`audit_log`, INSERT-only, déjà prouvé contre PostgreSQL
   réel).

Ce régime reste une **possibilité documentée pour discussion future**, pas
une feuille de route engagée. Son activation éventuelle est une décision de
gouvernance qui vous reviendra explicitement, séparément de ce document.

---

## 12. Distinctions REAL / SIMULATED / UNKNOWN

Règle héritée du Gate (`docs/PHASE_1_GATE.md` §1bis), étendue ici aux
composants cognitifs : **une capacité non mesurée/non implémentée est
`UNKNOWN`, jamais `false` ni une valeur plausible.** Un panneau graphique qui
afficherait une donnée absente comme si elle existait commettrait exactement
l'erreur que le Gate interdit déjà pour le matériel.

| Panneau | REAL aujourd'hui | UNKNOWN/SIMULATED tant que | Dépendance bloquante |
|---|---|---|---|
| Dashboard | version, statut Gate | — | aucune |
| Learning Center | rien | `WeaknessDetector`/`learnings` absents | phase 5/12 |
| Clone Center | rien | `AgentRuntime` non implémenté | §2, phase 3 (sandbox) |
| Knowledge Graph | rien | schéma non étendu | §6, phase 8 |
| Experiment Lab | rien | `ExperimentEngine` non formalisé | §7, phase 5 |
| Benchmark Center | rien | — sauf `audit_log` (gouvernance) déjà exploitable | phase 5 |
| Self-Code Center | rien | `CodeIntelligence` non implémentée | §8 |
| Memory Center | rien | `MemoryProvider` absent | phase 8 |
| Knowledge Gap Center | rien | §6 dépendante | phase 12 |
| **Evolution Timeline** | **`audit_log` EXISTS** | — | **réalisable maintenant** |
| Resource Monitor | `ComputeBudget` (config) EXISTS | consommation par clone SIMULATED tant que §2 n'existe pas | §2 pour la partie clones |
| **Security/GATE Center** | **`apex.gate` EXISTS** | extensions Code/Learning Gate absentes | réalisable maintenant pour le Gate matériel |

---

## 13. Impact précis sur le plan de phases

Dans l'esprit de `docs/VISION_INTEGRATION.md` §4 : ajustements à des phases
**existantes**, pas d'insertion de nouvelles phases numérotées (décision de
renumérotation réservée à l'utilisateur si elle est un jour souhaitée).

| Phase | Ajustement proposé | Motif |
|---|---|---|
| 3 (Sandbox) | Devient aussi le prérequis dur d'`AgentRuntime` | un clone ne peut exister sans isolation mesurée par GATE-19 |
| 5 (`EvalRunner`) | Champ `method` collecté dès cette phase (§5) | collecte tôt sans comparaison prématurée |
| 6 (`Placement`, Model Capability Gate) | `Domain` généralise `LanguageToolchain` ici (§3) | ouvre le non-code sans nouvelle abstraction |
| 7 (`PolicyEngine`) | `AgentRuntime` implémentée ici (phase_implemented proposée) | dépend de l'autorisation par politique |
| 12 (`WeaknessDetector`/`CurriculumStrategy`) | `CodeIntelligence` implémentée ici (phase_implemented proposée) | même principe de détection de lacunes, appliqué au code |
| 16 (`PromotionGovernor`/`HypothesisGenerator`) | Gouvernance prospective de promotion documentée ici (§11), jamais activée | cohérent avec l'introduction du Governor |
| 17 (`GenomeStore`) | Seule phase où « Genome v1 vs v2 » (§7) devient possible | dépendance dure |

---

## 14. Risques et questions ouvertes

| Risque/question | Nature |
|---|---|
| `PromotionGovernor` tourne sur le même hôte que ce qu'il juge — limite déjà posée honnêtement par `docs/VISION_INTEGRATION.md` §3.3, non résolue ici | structurelle, assumée |
| Seuil de taille d'échantillon pour activer la comparaison `MetaLearner` (§5) | à fixer à l'implémentation |
| `max_retries` et plafond de clones concurrents (§2.10, §2.11) | champs à ajouter au Genome, valeurs à définir |
| Numéro de phase exact pour `AgentRuntime`/`CodeIntelligence` (proposé §13) | ouvert à arbitrage final au moment de l'implémentation |
| Knowledge Graph qui deviendrait un doublon de `MemoryProvider` si mal implémenté | dette évitable, à surveiller à l'implémentation |
| Gouvernance prospective de promotion automatique (§11) : à activer un jour ou jamais ? | décision réservée, aucune urgence |

---

## 15. Ce qu'APEX pourra réellement faire, par regroupement

- **Isolation (phase 3)** : exécuter du code généré sans risque pour l'hôte.
- **Clones (3–7)** : instancier des clones spécialisés isolés, incapables de
  toucher au Core.
- **Apprentissage (5, 8, 9, 12, 15)** : suivre sa progression multi-domaines,
  détecter ses lacunes, adapter sa difficulté.
- **Expérimentation & Benchmark (5, 16)** : comparer des hypothèses avec
  preuve statistique, se comparer à sa version précédente.
- **Gouvernance (16–18)** : proposer une promotion sans être seul juge de
  lui-même — toujours validée par un humain à ce stade (§11).
- **Auto-évolution du Genome (17)** : muter sa configuration de façon
  traçable et réversible — jamais son code.
- **Méta-apprentissage (22, ou collecte anticipée dès la phase 5)** : choisir
  sa méthode selon le type de connaissance, une fois assez de preuves
  accumulées.
- **Code Intelligence (proposé 7/12)** : comprendre son dépôt, proposer des
  améliorations via un Developer Clone, jamais en écriture directe.

À aucun stade APEX n'obtient de droit d'écriture direct sur `main`, le Core,
ou ses propres garanties de sécurité.
