# APEX — Handoff Operator (N+16)

Ce document décrit **comment piloter APEX sans terminal**, via l'Operator
introduit en N+16. Il complète `docs/DEVELOPMENT_HANDOFF.md` (état général
du dépôt) — source de vérité en cas de doute :
`apex/interfaces/registry.py`, entrée `Operator`.

## 1. Ce que N+16 change

Avant N+16, faire progresser une tâche exigeait un appel HTTP ponctuel
(`POST /missions/{id}/launch`) qui déclenchait un seul `tick()` du
Scheduler — rien ne faisait tourner APEX en continu, et rien ne contrôlait
son lifecycle (démarrer/arrêter/mettre en pause). `apex.interfaces.registry`
documentait explicitement cette absence comme un arbitrage humain (« aucune
boucle de fond autonome ... pourra être ajoutée sans modifier ce module »).

**`apex.operator.service.OperatorService`** ajoute exactement cette pièce
manquante — rien d'autre :

1. Un **lifecycle explicite** (Start/Stop/Pause/Resume/Status) avec une
   machine à états fermée.
2. Une **façade unique** pour les tâches (Submit/Cancel/GetStatus/GetResult/
   GetRecentOperations), consommable identiquement par une future UI, une
   API locale, ou un client — sans qu'aucun appelant n'ait besoin de
   connaître `MissionService`/`SchedulerService` directement.

Aucun second Task System, aucun second orchestrateur, aucun second Gate :
tout délègue à `apex.missions`/`apex.scheduler`, inchangés.

## 2. Architecture

```
Client (future UI, script, curl)
    │  HTTP + jeton (Authorization: Bearer ...)
    ▼
apex/api/routes/operator.py            (10 routes, /api/v1/operator/*)
    │  DataEnvelope[T] (status: real|simulated|unknown, jamais un 500)
    ▼
apex.operator.service.OperatorService  (singleton applicatif, N+16)
    │  1. lifecycle (thread de supervision : tick() périodique)
    │  2. façade tâches (délégation pure)
    ▼
apex.scheduler.service.SchedulerService  (inchangé, N+8/N+11)
    │
    ▼
apex.missions.service.MissionService + apex.missions.execution.launch_mission
    │
    ▼
AgentRuntime → SandboxProvider (namespace Linux réel) → résultat
```

Un **« Task »** au sens Operator EST une `apex.missions.model.Mission` —
jamais un second concept. `GetTaskStatus`/`GetTaskResult` lisent directement
l'état/le résultat persistés de la Mission ; `GetRecentOperations` projette
`events` (`EventRepository.recent`), déjà la source de vérité existante.

## 3. Machine à états (`apex.operator.states.OperatorState`)

| État | Signification | Atteint par |
|---|---|---|
| `STOPPED` | rien ne tourne | initial, ou après `stop()` réussi |
| `STARTING` | vérification Runtime/DB en cours | `start()` appelé |
| `READY` | supervision active, aucune tâche en cours | `start()` réussi, ou dernière tâche terminée |
| `RUNNING` | supervision active, ≥1 tâche en cours | dérivé automatiquement (jamais une commande) |
| `PAUSED` | supervision suspendue, tâches en cours non affectées | `pause()` |
| `STOPPING` | arrêt en cours (attend les tâches actives) | `stop()` appelé |
| `FAULTED` | `start()`/`stop()` a échoué (Runtime/DB indisponible) | échec constaté, jamais fabriqué |

`READY <-> RUNNING` sont **dérivés automatiquement** de
`SchedulerService.registry` (le thread de supervision les observe après
chaque `tick()`) — jamais une décision de l'appelant. Toutes les autres
transitions sont **commandées** et vérifiées par une table de transitions
fermée (`apex.operator.states.assert_transition_allowed`, même patron que
`apex.missions.states`) : une transition absente de la table lève
`InvalidOperatorTransition`, jamais un raccourci silencieux.

**Récupération après erreur** : un `start()` qui échoue (Scheduler/DB
indisponible) transitionne vers `FAULTED` avec l'erreur observable via
`status().last_error` — jamais un `READY` optimiste. Un `start()` ultérieur,
une fois le Runtime de nouveau sain, ramène normalement à `READY`.

**Pause ≠ annulation** : `pause()` suspend uniquement le déclenchement de
*nouvelles* tâches (le thread de supervision cesse d'appeler `tick()`) — une
tâche déjà en cours continue jusqu'à son terme naturel. Pour arrêter une
tâche en cours, utiliser `CancelTask` (annulation réelle, vérifiée).

## 4. API HTTP (`/api/v1/operator/*`)

Même authentification que le reste de l'API (`Authorization: Bearer
<jeton>`, voir `apex/api/auth.py`) — même hypothèse simplificatrice que
`/missions`/`/agents` : tout appelant authentifié agit comme `Subject.HUMAN`
implicite (aucun mapping appelant → identité fine n'existe encore ailleurs
dans ce dépôt).

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/operator/start` | démarre le lifecycle |
| `POST` | `/operator/stop` | arrête proprement (attend les tâches actives) |
| `POST` | `/operator/pause` | suspend le déclenchement de nouvelles tâches |
| `POST` | `/operator/resume` | reprend |
| `GET` | `/operator/status` | état observable complet |
| `POST` | `/operator/tasks` | soumet une tâche (corps = `MissionCreateIn`, identique à `POST /missions`) |
| `POST` | `/operator/tasks/{id}/cancel` | annule (réel, vérifié) |
| `GET` | `/operator/tasks/{id}` | état de la tâche |
| `GET` | `/operator/tasks/{id}/result` | résultat (`ready`, `result`, `error`) |
| `GET` | `/operator/operations?limit=100` | événements récents (`events`, réutilisé, jamais un second journal) |

Chaque réponse suit `DataEnvelope[T]` (`status`: `real`/`unknown`, `data`,
`detail`) — **jamais un HTTP 500** : Runtime/DB indisponible, transition
invalide, ou tâche inconnue dégradent tous en `status: "unknown"` avec un
`detail` humain.

## 5. Permissions

Réutilise entièrement `apex.policy` — jamais un second système
d'autorisation :

- **Lifecycle** (`Start`/`Stop`/`Pause`/`Resume`) : quatre actions
  nouvelles (`START_OPERATOR`/`STOP_OPERATOR`/`PAUSE_OPERATOR`/
  `RESUME_OPERATOR`, `apex.policy.model.Action`), réservées à
  **`Subject.HUMAN` seul** — ni `Subject.CORE`, ni aucun des 9 types de
  clone. Démarrer/arrêter APEX reste une décision humaine explicite.
- **Tâches** (`SubmitTask`/`CancelTask`) : **aucune action nouvelle** —
  délèguent entièrement à `MissionService.create`/`SchedulerService.
  cancel_mission`, qui exigent déjà `CREATE_MISSION`/`CANCEL_MISSION`
  (HUMAN/CORE selon le cas, inchangé depuis N+6/N+8). Un clone ne peut donc
  jamais soumettre ou annuler une tâche via Operator, exactement comme il
  ne le pouvait pas déjà via `/missions`.

## 6. Limites honnêtes

- **Pas d'état partagé entre plusieurs instances/processus** : le thread
  de supervision et le lifecycle Operator sont locaux à CE processus (même
  nature que `SchedulerService.ExecutionRegistry`) — redémarrer le
  processus API repart de `STOPPED`, jamais un état fantôme retrouvé. Ce
  n'est pas un oubli : l'état d'installation/Runtime/Gate réel (au sens
  Windows/WSL2) reste entièrement celui déjà persisté par Setup, voir
  `setup/docs/LAUNCHER_ARCHITECTURE.md` (N+15) — Operator ne le
  réinvente pas.
- **Aucun mapping appelant → identité précise** (même limite que
  `/missions`/`/agents`) : tout appelant authentifié agit comme
  `Subject.HUMAN`.
- **`GetRecentOperations` est un flux d'événements ordonné, pas un
  historique de commandes distinct** : chaque appel Operator écrit dans
  `events` (`apex.operator` comme `logger`), lisible avec les événements
  Mission/Scheduler existants — jamais un second journal séparé à
  corréler manuellement.

## 7. Tests

- `tests/test_operator_service.py` (33 tests) — lifecycle, permissions,
  concurrence réelle (plusieurs tâches simultanées, exécution sandboxée
  réelle), FAULTED + récupération, résultat/échec réels. SQLite fichier
  (jamais `:memory:`+`StaticPool` — voir la docstring de la fixture pour
  la limitation de concurrence découverte et contournée).
- `tests/test_api_operator.py` (11 tests) — authentification, dégradation
  propre sans DB, et **un parcours HTTP complet contre PostgreSQL réel**
  (Stopped → Start → Submit → Running/Queued → Completed → GetResult →
  Stop) quand `tests/db_test_support.postgres_testable()` est vrai.
- `tests/test_policy.py` (19 tests, inchangés) — toujours verts après
  l'ajout des 4 actions Operator.

Voir le rapport de mission N+16 pour les résultats exacts (PASS/SKIP) de
cette exécution.

---

# APEX — Handoff Design Engine (N+17)

Ce chapitre décrit le **pipeline de conception assistée** introduit en
N+17 : d'une demande en langage naturel à un `DesignPackage` traçable
(brief, recherche, concepts, prompts visuels, résultats visuels,
validation, artefacts), pilotable sans terminal via l'Operator (N+16).

## 1. Ce que N+17 change

Avant N+17, rien dans ce dépôt ne transformait une demande de conception
en un résultat structuré : `apex.artifacts` (ArtifactStore, phase 10)
existait mais n'était câblé nulle part ; aucune recherche web, aucune
génération visuelle, aucun moteur de synthèse n'existaient, même à l'état
de stub.

**`apex.design`** ajoute cette pièce manquante — un pipeline réel en 7
étapes, la première utilisation réelle d'`apex.artifacts`, et deux
nouvelles abstractions honnêtement documentées (`ResearchProvider` côté
Design Engine, `VisualGenerationProvider`) — sans dupliquer ni l'Operator,
ni le Task System, ni `apex.policy`.

## 2. Architecture

```
Client (Operator, script, curl)
    │  POST /api/v1/operator/designs  {"type": "design", "request": "..."}
    ▼
apex/api/routes/operator.py            (4 routes /api/v1/operator/designs*)
    │  DataEnvelope[DesignPackageOut]
    ▼
apex.operator.service.OperatorService  (N+16, inchangé dans sa nature)
    │  submit_design/resume_design/get_design_status/get_design_result
    │  même garde `accepts_submissions`, même journal `events`
    ▼
apex.design.service.DesignService      (façade, N+17, singleton applicatif)
    │  exécution SYNCHRONE — aucune étape n'attend un backend lent réel
    ▼
apex.design.pipeline.DesignPipeline    (orchestrateur, 7 étapes)
    │  ANALYZE -> RESEARCH -> SYNTHESIZE -> DESIGN -> VISUALIZE -> VALIDATE -> PACKAGE
    ├─ apex.design.synthesis    (ANALYZE/SYNTHESIZE/DESIGN, déterministe)
    ├─ apex.design.research     (RESEARCH — aucun backend réel connecté)
    ├─ apex.design.prompt_engine (construction des VisualPrompt)
    ├─ apex.design.visual_provider (VISUALIZE — aucun backend réel connecté)
    └─ apex.design.validation   (VALIDATE — QA structurelle)
    ▼
apex.artifacts (LocalFilesystemArtifactStore)  — chaque résultat durable
    persisté, adressé par contenu (sha256:...), PREMIÈRE utilisation réelle
    de ce magasin dans ce dépôt (phase 10, jusqu'ici non câblé)
```

Un `DesignPackage` référence tout : `request`, `steps` (un `StepRecord`
par étape, avec état/entrée-sortie/erreur/logs/durée/provenance),
`brief`, `research`, `concepts`, `visual_prompts`, `visual_results`,
`validation`, `artifacts` (références `sha256:...` vers `apex.artifacts`).

## 3. Machine à états (`apex.design.states.DesignPipelineState`)

| État | Atteint par |
|---|---|
| `REQUEST` | création du `DesignPackage`, avant toute étape |
| `ANALYZING` / `RESEARCHING` / `SYNTHESIZING` / `DESIGNING` / `VISUALIZING` / `VALIDATING` / `PACKAGING` | une étape en cours, une par nom d'étape |
| `COMPLETED` | les 7 étapes ont réussi |
| `FAILED` | une étape a réellement échoué (jamais RESEARCH/VISUALIZE indisponibles — voir §4) |

Table de transitions fermée (`assert_transition_allowed`, même patron que
`apex.missions.states`/`apex.operator.states`) : une transition absente
lève `InvalidDesignTransition`. `FAILED` peut reprendre vers N'IMPORTE
quelle étape de traitement — `DesignPipeline.resume()` choisit la cible
exacte selon la DERNIÈRE étape réellement en échec, ne ré-exécute JAMAIS
les étapes déjà réussies (leurs artefacts ne sont pas dupliqués).

**Reprise après échec** : `resume(design_id)` (via `DesignService`/
`OperatorService.resume_design`) rejoue le pipeline à partir de l'étape en
échec — testé (`tests/test_design_pipeline.py::TestPipelineEdgeCases::
test_interrupted_pipeline_resumes_from_the_failed_step_only`) avec une
panne transitoire réelle (exception levée, pas une simulation).

## 4. Honnêteté RESEARCH/VISUALIZE — jamais une fabrication

Aucun backend de recherche web réel, aucun backend de génération visuelle
réel n'est connecté dans ce dépôt (confirmé par audit N+17 : aucune
dépendance, aucune clé API, `apex.routing.model.Capability.VISION` jamais
dérivée — même vide que `SearchProvider`, `apex.interfaces.registry`,
PLANNED depuis la Phase 13, non comblé ici, seulement consommé
honnêtement) :

- **RESEARCH** (`apex.design.research.NullResearchProvider`) : `detect()`
  répond `UNAVAILABLE_ON_DEV`, `search()` retourne un tuple VIDE. L'étape
  est marquée `StepRecord.state = SKIPPED`, jamais `FAILED` — le pipeline
  continue, `DesignConcept.based_on_research` reste vide plutôt que
  fabriqué.
- **VISUALIZE** (`apex.design.visual_provider.NullVisualGenerationProvider`) :
  un `VisualPrompt` structuré RÉEL est toujours construit
  (`apex.design.prompt_engine`, aucun backend requis pour ça), mais
  `generate()` lève `apex.errors.VisualBackendUnavailable` — capturé par
  le pipeline et traduit en `VisualResult(state="unavailable",
  artifact_content_hash=None)`, jamais un octet d'image inventé. Le
  scénario E2E (§7) rapporte explicitement ce résultat plutôt que de
  prétendre une génération.

Un futur backend réel (recherche web via une API configurée, génération
d'image via un service réel) se brancherait dans le SEUL point de
construction de chaque contrat (`get_default_provider()` de
`apex.design.research`/`apex.design.visual_provider`) — aucun consommateur
(`apex.design.pipeline`) n'a besoin de changer.

Un double de test (`_MockVisualGenerationProvider`, sous-classe RÉELLE du
contrat `VisualGenerationProvider`) existe UNIQUEMENT dans
`tests/test_design_pipeline.py` — jamais dans `apex/design/`, même
discipline que `_FakeProvider(ModelProvider)` dans
`tests/test_routing_generation.py`. Il exerce le chemin `state="completed"`
sans jamais être présenté comme une preuve de génération réelle en
production.

## 5. API HTTP (`/api/v1/operator/designs*`)

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/operator/designs` | soumet `{"type": "design", "request": "..."}`, exécute le pipeline, retourne le `DesignPackage` complet |
| `POST` | `/operator/designs/{id}/resume` | reprend une conception en échec |
| `GET` | `/operator/designs/{id}` | statut/résultat courant (même objet que POST) |
| `GET` | `/operator/designs/{id}/result` | alias explicite, même sémantique `ready`/ `result`/`error` que `GetTaskResult` |

Même `DataEnvelope[T]`/authentification que le reste de l'Operator —
jamais un HTTP 500 : Operator non démarré, conception inconnue, ou requête
vide dégradent en `status: "unknown"` avec un `detail` humain.

## 6. Permissions

Une seule action nouvelle, `Action.SUBMIT_DESIGN`
(`apex.policy.model.Action`), réservée à **`Subject.HUMAN` seul** — même
motif que `CREATE_MISSION`/`CREATE_TASK` : un clone ne peut jamais
soumettre de conception (absente de toutes les allow-lists de clone et de
CORE). Les étapes internes du pipeline ne re-vérifient jamais cette
autorisation individuellement — elles s'exécutent sous la demande déjà
autorisée, comme `LAUNCH_MISSION` ne re-vérifie pas `CREATE_MISSION`.

## 7. Scénario E2E validé

`tests/test_api_operator.py::TestOperatorRoutesWithRealDatabase::
test_e2e_create_a_modern_urban_skatepark_concept` — le scénario imposé par
la mission N+17 (« Créer le concept d'un skatepark urbain moderne »), de
bout en bout via HTTP réel (PostgreSQL) : Start -> Submit -> 7 étapes
COMPLETED/SKIPPED (jamais FAILED) -> `ready=True` -> chaque
`VisualResult.detail` contient littéralement `VISUAL_BACKEND_UNAVAILABLE`
-> GetResult -> visible dans `GetRecentOperations` (`design.submitted`) ->
Stop. Aucune image n'est fabriquée ; le rapport reste honnête du début à
la fin.

## 8. Intégration Operator (N+16)

`OperatorService` (N+16) compose désormais, en plus du Scheduler/
MissionService, un `DesignService | None` optionnel
(`apex.api.dependencies.get_design_service`, même discipline « singleton
paresseux » que `get_scheduler_service`/`get_operator_service`) :

- `submit_design`/`resume_design` suivent la MÊME garde
  `accepts_submissions` (READY/RUNNING/PAUSED) que `submit_task` — jamais
  une seconde machine à états.
- `_observe_activity` additionne `DesignService.active_count()` au nombre
  de Missions actives du Scheduler pour dériver READY↔RUNNING — une
  exécution de pipeline de conception compte comme une activité Operator,
  jamais un second compteur.
- Chaque soumission/reprise de conception écrit dans `events`
  (`design.submitted`/`design.resumed`), lisible via
  `GET /operator/operations` — jamais un second journal.
- Un `OperatorService` construit SANS `DesignService` (ex. un test N+16
  non modifié) reste entièrement fonctionnel pour Start/Stop/Pause/Resume/
  SubmitTask ; seules les méthodes `submit_design`/`get_design_*`/
  `resume_design` lèvent alors `OperatorNotReady`.

## 9. Artefacts

Chaque résultat durable produit par le pipeline (brief, brief finalisé,
chaque concept, chaque prompt visuel, chaque résultat visuel réellement
généré, la validation, le paquet final) est écrit dans
`apex.artifacts.LocalFilesystemArtifactStore` — content-addressed
(`sha256:<64 hex>`), intégrité re-vérifiée à la lecture. C'est la PREMIÈRE
utilisation réelle de ce magasin dans ce dépôt (phase 10, jusqu'ici
délibérément non câblé — voir sa propre entrée de registre). Chaque
`DesignArtifactRef` porte `content_hash`/`kind`/`created_at` ; aucune
table SQL dédiée — la durabilité vient entièrement du magasin de
contenu, jamais réinventée.

## 10. Limites honnêtes

- **Recherche et génération visuelle réellement indisponibles** (§4) —
  documenté, jamais contourné par une fabrication.
- **État de suivi en mémoire, PAR PROCESSUS** (`DesignService._runs`) —
  même limite déjà documentée pour `OperatorService`/`ExecutionRegistry` :
  redémarrer le processus API perd le SUIVI d'une conception déjà
  terminée (le `DesignPackage` final reste lui-même durable, car déjà
  écrit dans `apex.artifacts`).
- **Décision volontaire de NE PAS intégrer `apex.memory`** en v1 : câbler
  une écriture `MemoryService.write()` depuis le Design Engine exigerait
  d'étendre l'allow-list HUMAN/CORE avec `WRITE_OWN_MEMORY` — un
  changement de portée de sécurité que cette étape ne prend jamais sans
  décision explicite. L'architecture (dataclasses immuables, provenance
  déjà portée par chaque enregistrement) reste prête pour un futur
  câblage sans réécriture.
- **Decision Trace absente de ce dépôt** — confirmé par audit N+17
  (aucune occurrence, aucune entrée de registre) : rien à intégrer, ce
  n'est pas un oubli de cette étape.
- **ANALYZE/SYNTHESIZE/DESIGN sont déterministes** (`apex.design.
  synthesis`), jamais un appel `ModelProvider` — un enrichissement futur
  par modèle réellement invoqué reste une extension documentée, jamais
  une simulation aujourd'hui.
- **VALIDATE est une QA structurelle**, jamais une validation par modèle
  de vision (aucun modèle de vision connecté dans ce dépôt) — l'abstraction
  reste prête pour une future validation réelle, jamais simulée.

## 11. Tests

- `tests/test_design_pipeline.py` (29 tests) — états, modèle, recherche/
  génération visuelle honnêtes, synthèse déterministe, moteur de prompts,
  validation, pipeline complet (y compris un backend visuel simulé pour le
  chemin `completed`, un échec de backend visuel, une reprise après panne
  transitoire réelle).
- `tests/test_design_service.py` (10 tests) — façade seule : soumission,
  permissions, suivi en mémoire, `active_count()` observé PENDANT une
  exécution réellement bloquée (pas une valeur devinée).
- `tests/test_operator_service.py`, classe `TestDesignIntegration` (8
  tests) — intégration RÉELLE avec `OperatorService` : garde
  `accepts_submissions`, permissions, `GetRecentOperations`, Operator sans
  `DesignService`.
- `tests/test_api_operator.py` (4 tests dédiés + le scénario E2E du §7) —
  authentification, dégradation propre sans DB, scénario complet contre
  PostgreSQL réel.

Voir le rapport de mission N+17 pour les résultats exacts (PASS/SKIP) de
cette exécution, et ce que N+18 (APEX 3D Engine) trouvera prêt à l'emploi
sans devoir être réarchitecturé.
