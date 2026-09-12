# APEX — Handoff de développement

Ce document est le point de reprise pour la prochaine session. Il résume
l'état réel du dépôt à ce checkpoint — jamais un état espéré ou visé.
Source de vérité en cas de doute : `apex/interfaces/registry.py`, qui porte
le statut réel (`PLANNED`/`STUB`/`IMPLEMENTED`) de chaque abstraction avec
sa justification exacte — ce document en est un résumé, jamais un
remplacement.

## 1. État actuel

Tous les composants suivants sont **réellement implémentés** (STUB honnête —
voir `apex.interfaces.registry` pour le détail exact de ce qui est prouvé et
ce qui ne l'est pas encore, composant par composant) :

- `TaskBank` / `OracleProvider` / `EvalRunner` — exécution de tâches sous
  vraie sandbox namespaced, jugement PASS/FAIL/ERROR/TIMEOUT réel.
- `JudgeTier` — Tier0 réel, délègue à `OracleProvider`.
- `WeaknessDetector` / `CurriculumStrategy` / `ConfidenceCalibrator` —
  détection de faiblesse, progression de niveau, intervalle de Wilson.
- `SkillRegistry` — proposition/promotion/rejet HUMAIN, et désormais
  `propose_from_evidence()` : une compétence proposée automatiquement à
  partir d'`EvalRun` réels, jamais sur simple affirmation d'un clone (seuil
  = borne inférieure calibrée à 70 %).
- `TaskGenerator` — génération déterministe (NORMAL, TARGETED_WEAKNESS,
  CURRICULUM_INTERMEDIATE, REGRESSION, ...).
- `StatTest` / `HypothesisGenerator` — bootstrap apparié réel.
- `GenomeStore` / `MutationOperator` / `SelectionStrategy` — versions
  versionnées avec provenance, mutation/crossover déterministes,
  successive halving / front de Pareto.
- `CostGainEstimator` — coût réel (`elapsed_s`, `memory_peak_mb` désormais
  réellement mesuré via cgroups v1), gain via `StatTest`, jamais de faux 0.
- `MetaLearner` (v1, politique statique) + boucle expérimentale réelle
  `apex.metalearner.cycle.run_experiment_cycle` (Weakness → décision →
  génération → exécution → mesure, bout en bout, sandbox réelle).
- `apex.selfmodel` — vue composée READ-ONLY (« que sait APEX ? ») et
  traçabilité complète d'une compétence jusqu'à ses `EvalRun` d'origine.
- **`MissionEngine → ModelProvider → OllamaProvider`** — raccordement réel,
  HORS sandbox (voir §6) : `apex.missions.execution.generate_mission_response`.
- **`AgentRegistry`** (passe « Agents V1 ») — `Agent` (config réutilisable,
  table `configured_agents`) → Mission (`Mission.agent_id`) →
  `AgentRegistryService.run_execution` → Model Router
  (`select_model`/`generate_with_fallback`) → provider réel. Bout en bout
  réel, prouvé par test d'intégration réel (`tests/test_api_agent_registry.
  py::test_full_chain_mission_run_via_agent_with_a_fake_provider`, double
  fidèle du seul point réseau — `OllamaProvider.generate`/`.detect` — jamais
  du reste de la chaîne).
- **`LearningOrchestrator`** (passe « Autonomous Multi-Generation Learning
  Orchestrator ») — boucle MULTI-génération réelle au-dessus de
  `PopulationEvolutionService.evolve_generation` (table `learning_sessions`,
  API `/learning-sessions`, CLI `apex learning`) : combien de générations,
  où s'arrêter, comment reprendre après une panne — jamais une seconde
  implémentation de l'évolution elle-même. Voir la sous-section dédiée
  ci-dessous.
- **`GauntletEvaluation`** (passe « Strict Judge System + Gauntlet
  Learning Loop ») — évaluation adversariale stricte, multi-juges
  (`apex.metalearner.judges`/`gauntlet`, table `gauntlet_results`, API
  `/gauntlets`), intégrée à `PopulationEvolutionService` : un véto
  (finding CRITICAL, ex. tentative de contournement sandbox, échec
  HELDOUT, non-déterminisme) bloque la promotion CHAMPION quel que soit
  le score moyen. Voir la sous-section dédiée ci-dessous.

Rien n'est prétendu `IMPLEMENTED` sans preuve empirique. Tout ce qui reste
`PLANNED`/déféré est documenté avec sa raison exacte dans le registre
(`apex/interfaces/registry.py`) — notamment `network="proxy"` (egress
sandboxé, §6) et `PromotionGovernor` (réévalué, toujours non justifié par
le test en 4 points).

## 2. État détaillé par système

Statut au sens `apex.interfaces.registry.ImplStatus` : `PLANNED` (rien de
réel), `STUB` (réel mais partiel — jamais présenté comme complet),
`IMPLEMENTED` (jamais utilisé dans ce dépôt à ce jour, réservé à une preuve
opérationnelle totale).

- **MissionEngine / ModelProvider / OllamaProvider** — `ModelProvider` :
  STUB. `OllamaProvider` (`apex.models.ollama`) est réel : `detect()` sonde
  `GET /api/tags` en HTTP réel, `generate()` appelle `POST /api/generate`
  réellement, timeout/annulation coopérative réels, aucune fuite de prompt
  dans les erreurs. `apex.missions.execution.generate_mission_response`
  raccorde MissionEngine à ce `ModelProvider`, HORS sandbox, au niveau
  orchestrateur (jamais depuis un clone). Non prouvé sur un vrai Ollama
  DANS CE dépôt de développement (aucun accès réseau vers `ollama.com` —
  voir §7) ; le test d'intégration réel existe et s'auto-exécute dès qu'un
  Ollama devient joignable (`tests/test_missions_model_generation.py::
  TestRealOllamaMissionIntegration`).
- **ModelRouter / SecretBroker / OpenAICompatibleProvider** (passe
  « Multi-Model/Provider Fabric ») — tous STUB réels, zéro provider payant
  prouvé. `apex.models.registry` dérive des `ModelCandidate` depuis
  `ApexConfig` (roles + endpoints déjà réels, aucune duplication).
  `apex.routing.select_model` filtre/classe par capacité/contexte/
  localité/`ExternalPolicy` (score transparent, historique de
  qualité/coût/latence explicitement PAS câblé — aucune donnée réelle
  encore). `apex.routing.generate_with_fallback`/`generate_all_candidates`
  composent `ModelProvider.generate()` sur plusieurs candidats, jamais de
  sélection auto de « meilleure réponse » (lacune documentée : comparer du
  texte libre exigerait un jugement que `Tier0Judge` ne couvre pas).
  `apex.secrets.EnvSecretBroker` résout `ModelEndpoint.secret_key`
  (`APEX_SECRET_<CLÉ>`, jamais une clé en dur) à chaque appel.
  `OpenAICompatibleProvider` (`apex.models.openai_compatible`) : UN adapter
  HTTP générique pour tout service `/v1/chat/completions` (OpenAI,
  OpenRouter, Together, Groq, Fireworks, DeepSeek, Mistral, xAI, Qwen
  compatible) — `get_default_provider()` dispatche réellement sur
  `ModelEndpoint.runtime`. Testé contre un serveur HTTP de test fidèle,
  **jamais contre un vrai service payant** — le test réel existe
  (`tests/test_models_openai_compatible.py::
  TestRealOpenAICompatibleIntegration`), sauté sans
  `APEX_OPENAI_COMPATIBLE_URL`/clé réels.
- **Model/Provider Catalog** (`apex.models.catalog`, passe « Model/Provider
  Catalog ») — extension de la couche de préparation du Router, PAS une
  nouvelle abstraction ni une seconde registry : `build_model_catalog()`/
  `build_provider_catalog()` sont des vues DÉRIVÉES et STATIQUES d'
  `ApexConfig` (via `resolve_candidates`/`derive_capabilities` déjà réels),
  recalculées à chaque appel, jamais stockées. `Availability` (AVAILABLE/
  UNAVAILABLE/UNKNOWN/DISABLED) reste `UNKNOWN` pour tout candidat déclaré
  tant que `discover_live()` (seul point réseau du module, jamais appelé
  implicitement) n'a pas exécuté un vrai `ModelProvider.detect()` —
  `UNKNOWN` n'est jamais promu à `AVAILABLE` sans preuve. `DiscoverySource`
  distingue `CONFIGURED`/`DISCOVERED` (réels) de `VERIFIED`, qui reste
  **PLANNED** : aucune Mission ne persiste aujourd'hui quel `ModelEndpoint`
  l'a réellement servie (`Mission.result` ne porte que le texte généré),
  donc rien à interroger pour prouver un usage réel — câbler `VERIFIED`
  exigerait d'abord cette traçabilité, hors périmètre de cette passe.
  `Capability` (`apex.routing.model`) étendu de `TEXT`/`AUDIO`/`VOICE`/
  `VIDEO`/`SCREEN` (vocabulaire fermé, prêt pour de futurs endpoints
  multimodaux réels — vision d'images, captures d'écran, messages/réponses
  vocales, compréhension puis génération vidéo) ; seule `TEXT` est dérivée
  aujourd'hui (chaque backend réel est une API de génération de texte,
  vérifié par construction), les 4 autres ne sont **JAMAIS** dérivées faute
  de tout signal réel dans le schéma actuel — aucune capacité multimodale
  n'est ni implémentée ni simulée. `PerformanceHistory` toujours vide par
  construction (`usage_count=0`) — emplacement prêt pour un futur
  agrégat par modèle, jamais une statistique inventée.
  `ModelEndpoint.enabled` (nouveau champ, défaut `True`) distingue une
  désactivation explicite en configuration (`DISABLED`, jamais sondé) d'une
  indisponibilité constatée. 32 tests dédiés
  (`tests/test_models_catalog.py`), y compris une preuve de bout en bout
  `RoutingRequest` -> Model Catalog -> `select_model` -> dispatch réel
  `get_default_provider()` -> vrai `OpenAICompatibleProvider` contre un
  serveur HTTP de test local (jamais un service payant réel).
- **AgentRegistry** (`apex.agent_registry`, passe « Agents V1 ») — STUB.
  `Agent` (dataclass, table `configured_agents` — délibérément PAS `agents`,
  déjà pris par `Clone`/`AgentRuntime`, ni `AgentOut`, déjà pris par les
  routes `/agents` existantes) : configuration réutilisable et réellement
  exécutable, distincte à la fois du `Clone`/`AgentRuntime` (instance
  OS-level spawnée) et de l'`AgentTemplate` (blueprint jamais exécutable).
  Aucun outil (`ToolName`, 8 valeurs) ni permission (`Permission`, 6
  valeurs : `READ_ONLY`/`WORKSPACE_WRITE`/`EXECUTE`/`NETWORK`/`GIT_READ`/
  `GIT_WRITE`) n'est jamais accordé par défaut — validé à la création.
  `AgentStatus` (7 valeurs, dont `WAITING`) n'est **jamais stocké** :
  `derive_agent_status()` le dérive à chaque lecture de `enabled` + la
  dernière `AgentExecution` réelle — `DISABLED` si désactivé, `IDLE` si
  aucune exécution, sinon calque `AgentExecutionStatus` ; `WAITING` reste
  déclaré, jamais produit (aucun signal réel ne le justifie encore).
  `AgentExecution` (table `agent_executions`) trace agent/mission/modèle/
  provider/résultat/erreur/`FallbackReason`, **sans aucun champ token/coût/
  latence** (pas seulement `NULL` — le champ n'existe pas : `GenerationResult`
  ne fournit pas ces données, donc rien à stocker sans les inventer).
  `AgentRegistryService.run_execution()` compose les primitives déjà
  réelles du Model Router (`select_model`, `generate_with_fallback`) et de
  `MissionService` (`mark_running`/`mark_review`/`mark_failed`) — une
  troisième composition, jamais un second moteur d'orchestration parallèle
  à `generate_mission_response`/`generate_mission_response_via_router`.
  `classify_outcome()` (`apex.agent_registry.fallback`) ne produit
  aujourd'hui que `SUCCESS`/`TIMEOUT`/`CAPABILITY_MISSING`/
  `PROVIDER_ERROR` parmi les 9 valeurs de `FallbackReason` — les 5 autres
  (`QUOTA_EXCEEDED`/`RATE_LIMITED`/`OFFLINE`/`AUTH_ERROR`/
  `CONTEXT_TOO_LONG`) restent déclarées mais jamais fabriquées, faute d'un
  code d'erreur HTTP structuré remonté par `ModelProviderError`.
  `AgentTeam` (table `agent_teams`) reste minimal V1 : nom/description/
  membres, aucune communication autonome entre agents. Routes API :
  `/configured-agents` (CRUD + instantiation depuis un `AgentTemplate` via
  `template_id`), `/configured-agents/{id}/executions`, `/agent-teams`,
  et sur `/missions` : `POST /{id}/run` (exécute via l'agent assigné) et
  `GET /{id}/executions`. `PolicyEngine` : HUMAIN seul crée/modifie/
  supprime un `Agent` et crée une `AgentTeam` ; CORE seul déclenche
  `RUN_AGENT_EXECUTION` (même chaîne « HUMAIN assigne, CORE lance » que
  `LAUNCH_MISSION`). 42 tests dédiés (`tests/test_agent_registry_service.py`,
  `tests/test_api_agent_registry.py`), dont un chemin complet Mission → Agent
  → Model Router → provider (double fidèle) → résultat, et une preuve que le
  Model Router refuse honnêtement (`FAILED`, jamais un succès simulé) quand
  aucun provider n'est joignable.
- **ArtifactStore** — STUB. `LocalFilesystemArtifactStore`
  (`apex.artifacts.local_filesystem`) réel : identité par hash SHA-256,
  écriture atomique, vérification d'intégrité à la lecture, path traversal
  structurellement impossible. `MinioArtifactStore` architecturé (même
  contrat) mais ses opérations lèvent `NotImplementedYet` — MinIO non
  joignable sur les machines de développement à ce jour.
- **SandboxProvider** — STUB. `LinuxNamespaceSandbox`
  (`apex.sandbox.linux_namespace`) réel : namespaces Linux (mount/pid/net/
  ipc/uts), cgroups v1 (cpu/memory/pids), abaissement de privilège réel
  (`setpriv`), racine en lecture seule, réseau coupé — vérifié par
  intégration réelle sur cette machine, jamais supposé. `network="none"`
  est le SEUL mode implémenté ; tout autre lève `NotImplementedYet` (voir
  §7, bloque l'accès réseau depuis un clone sandboxé). `ContainerSandbox`
  reste `PLANNED`.
- **Scheduler** — STUB. `apex.scheduler.service` réel : priorité/FIFO,
  dépendances entre missions (détection de cycle), capacité/backpressure,
  annulation réelle. `POST /missions/{id}/launch` est le SEUL déclencheur
  réel de l'application et passe RÉELLEMENT par ce Scheduler. AUCUNE boucle
  de fond autonome — `tick()` seulement à la demande, jamais un thread
  périodique (arbitrage humain explicite).
- **Evaluation / EvalRunner** — STUB. `apex.evalrunner.service` réel :
  compose `TaskBank`/`AgentRuntime`/`SandboxProvider`/`OracleProvider`,
  exécution sous vraie sandbox, `EvalRun.facts` porte désormais
  `memory_peak_mb` (réel, cgroups v1) et, si fourni, un contexte de
  stratégie (`strategy_facts`, ajout minimal pour `MetaLearner` v2).
  `task.command` reste TOUJOURS déterministe — aucun raccordement à
  `ModelProvider` dans ce chemin à ce jour (voir §8, prochaine priorité).
- **Oracle / Judge** — STUB. `ExitCodeOracle` réel (code de sortie
  0 = PASS). `Tier0Judge` (`apex.judge.tier0`) réel, délègue entièrement à
  `OracleProvider` — aucune seconde logique de jugement. Judge/Oracle
  restent structurellement indépendants du clone/modèle évalué.
- **Weakness Engine** — STUB. `apex.weakness.detector`/`service` réels :
  classification pure PASS/FAIL agrégée par spécialité, seuils heuristiques
  documentés (`MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS=5`,
  `CREDIBLE_FAILURE_RATE_THRESHOLD=0.5`), jamais confondu avec un
  `StatTest` réel. Catégories : `INSUFFICIENT_DATA`/`NO_WEAKNESS`/
  `ISOLATED_FAILURE`/`REPEATED_WEAKNESS`/`STATISTICALLY_CREDIBLE_WEAKNESS`.
- **Curriculum Engine** — STUB. `apex.curriculum.strategy` réel : décide
  HOLD/ADVANCE/REINFORCE à partir d'un `WeaknessReport`, composé (pas
  réimplémenté) par `MetaLearner`.
- **Learning loop** — STUB, bout en bout réel :
  `apex.metalearner.cycle.run_experiment_cycle` compose Weakness →
  MetaLearner → TaskGenerator → TaskBank → EvalRunner → Weakness (relecture),
  gouvernance HUMAIN préservée (`Action.CREATE_TASK`), aucune promotion
  automatique. Boucle actuellement séparée du chemin `ModelProvider` (voir
  §8).
- **Skill System** — STUB. `SkillRegistryService` réel : cycle
  proposition (CORE/HUMAIN) → promotion/rejet (HUMAIN seul, décision
  unique) → révision (chaîne linéaire), plus `propose_from_evidence()`
  (dérive la preuve de vrais `EvalRun`, jamais d'une auto-déclaration).

### Model Router — Adaptive Model Router V1

Voir `apex/interfaces/registry.py` (entrée `ModelRouter`) pour le détail
complet et la justification de chaque poids — résumé ici :

- **IMPLEMENTED** : `agent_executions` persiste maintenant `elapsed_s`/
  `tokens_prompt`/`tokens_completion` (mesurés par `GenerationResult`,
  jusqu'ici jetés — migration `c3e5f7a9b1d2`) ; `apex.agent_registry.
  performance.aggregate_model_performance` (fonction pure) construit un
  `apex.routing.model.ModelPerformanceStats` par `model_id` depuis ces
  exécutions réelles ; `select_model(..., performance=, scoring_policy=)`
  intègre `historical_reliability`/`historical_latency` — deux paramètres
  strictement optionnels, compatibilité stricte prouvée par test
  (`tests/test_routing_router_scoring.py::TestStrictBackwardCompatibility`)
  ; le score est un `ScoreBreakdown` structuré et auditable (jamais un
  chain-of-thought) ; `apex.config.schema.RouterScoringPolicy` (`routing:`
  dans `config.yaml`) rend les poids explicites/configurables/testés,
  avec des défauts qui reproduisent exactement le score déjà en
  production avant cette passe. Poids durcis (passe « Hardening ») :
  `RouterScoringPolicy.parse` rejette explicitement `NaN`/`+inf`/`-inf`
  (`math.isfinite`), pas seulement les négatifs — un poids `NaN` passait
  silencieusement `value < 0` (toute comparaison avec `NaN` vaut `False`
  en Python) avant ce durcissement.
- **DORMANT / NOT WIRED — À LIRE avant toute conclusion sur le
  comportement réel en production** : aucun des 3 sites d'appel réels de
  `select_model` ne fournit `performance=` — ni `AgentRegistryService.
  run_execution`, ni `generate_mission_response_via_router`, ni
  `ConversationService`. Le Router **N'APPREND PAS** des performances des
  modèles en production aujourd'hui ; le mécanisme est câblé, testé,
  prêt, mais inactif tant qu'un futur passage ne branche pas
  explicitement `list_by_model` → `aggregate_model_performance` →
  `select_model(performance=)` dans ces 3 sites — voir « PLANNED »
  ci-dessous pour la condition préalable à ce câblage.
- **PLANNED, honnêtement non simulé** : `eval_quality` (`EvalRunner`
  n'invoque toujours aucun `ModelProvider` — voir §8, c'est le même
  changement de périmètre déjà identifié comme prochaine priorité avant
  cette passe) ; `cost` (aucun prix déclaré nulle part dans le schéma) ;
  mapping `Mission.specialty` → `Capability` (vérifié absent, pas
  fabriqué) ; `difficulty` (aucun champ de ce nom sur `Task`/`Mission`) ;
  activation du feedback historique en production (câblage ci-dessus) ;
  une stratégie d'exploration/exploitation (epsilon-greedy/UCB/Thompson
  sampling) **avant** ce câblage — sans elle, une sélection gloutonne
  top-1 peut durablement favoriser le premier candidat ayant accumulé un
  historique (starvation des alternatives jamais réessayées), design
  documenté ci-dessous, non implémenté.
- Index `agent_executions_model_id_idx` (migration `d4f6a8c0e2b3`, passe
  « Hardening ») — `list_by_model` filtrait sur `model_id` sans index
  dédié ; testé UP/DOWN/UP sur PostgreSQL réel avec une ligne réelle,
  aucune perte de donnée.
- Tests : `tests/test_routing_router_scoring.py`,
  `tests/test_agent_registry_performance.py`,
  `tests/test_db_agent_execution_performance_columns.py` (PostgreSQL
  réel), `tests/test_adaptive_router_integration.py` (bout-en-bout),
  `tests/test_config.py::TestRouterScoringPolicy` (incluant les 5 tests
  de durcissement `NaN`/`+inf`/`-inf`/`0.0`/défauts inchangés).

**Design PLANNED — stratégie d'exploration/exploitation** (analyse
documentée, aucun code, passe « Hardening ») : avant tout câblage de
`performance=` dans un site d'appel réel avec plus d'un candidat
véritablement comparable, un mécanisme d'exploration explicite doit
exister pour éviter la starvation identifiée par la review R1. Doit
considérer, sans qu'aucune valeur ne soit choisie ici : un seuil minimal
d'observations avant qu'un `success_rate` soit considéré statistiquement
exploitable (`usage_count` déjà disponible, pas encore utilisé comme
garde de confiance) ; une part d'exploration contrôlée et bornée (ex.
epsilon-greedy — un candidat `UNKNOWN`/sous-échantillonné choisi avec une
probabilité non nulle même si son score actuel est inférieur) plutôt
qu'un choix strictement glouton ; une fenêtre temporelle glissante sur
`AgentExecution` (`aggregate_model_performance` traite aujourd'hui toute
la fenêtre uniformément, aucune décroissance temporelle) pour que la
performance d'un modèle retiré/mis à jour ne pèse pas indéfiniment ;
un traitement explicite du cas « nouveau modèle/provider ajouté à
`config.yaml` » (doit démarrer à `UNKNOWN`, jamais hériter du score d'un
autre candidat) ; un traitement explicite d'une indisponibilité
temporaire (`Availability.UNAVAILABLE` réel, distinct d'un historique de
mauvaise performance) ; l'intégration future, sans conversion implicite,
de `eval_quality`/`cost` une fois mesurables. Ce mécanisme reste
PLANNED — ne pas l'improviser au moment du câblage.

### Frontend — UI Foundation V1

Passe purement frontend (aucun fichier backend touché) : fondation visuelle
réutilisable par toutes les pages, sans réécrire ce qui fonctionnait déjà.

- **AppShell** (`components/layout/`) — `Sidebar` regroupe désormais les 27
  entrées de `SPECIALTY_DEFINITIONS` (20 déjà réelles + 7 nouvelles entrées
  honnêtement `PLANNED`/`concept` — Multi-IA/Teams, Skills, Weaknesses,
  Images, Media, Audio, RAG, Datasets, toutes rendues par la page générique
  `PlannedSpecialtyPage`, aucun code de simulation) en 6 catégories
  (`SpecialtyCategory` : Workspace/Intelligence/Cognition/Creative/Data/
  System, définies dans `specialties/data.ts`). Réductible (`SidebarContext`,
  persisté `localStorage`), hors-écran sur mobile (<900px, `SidebarContext.
  mobileOpen`), navigation clavier native (`NavLink`). `TopBar` intègre
  désormais `ApexStatusBadge` (déjà réel, seulement déplacé du Dashboard
  vers un emplacement global) + `ThemeToggle`, garde `GlobalSearch` inchangé.
- **Design system** (`components/ui/`) — primitives réellement
  interactives, aucune bibliothèque externe ajoutée : `Button`/`IconButton`,
  `Input`/`Textarea`/`Select`/`Checkbox`/`Switch`, `Card`/`Skeleton`,
  `Tabs` (navigation clavier flèches/Home/End, roving tabindex), `Tooltip`
  (survol ET focus clavier), `Dropdown` (ferme sur clic extérieur/Échap),
  `Modal` (portail, focus initial, restitution du focus, Échap/backdrop),
  `Toast`/`ToastProvider` (auto-disparition + fermeture manuelle). CSS
  entièrement pilotée par les jetons de `theme/theme.css` (aucune valeur
  arbitraire). `DataTable` volontairement PAS créé — `kv-table` existant
  suffit, pas de besoin réel identifié.
- **Thème** (`theme/useTheme.ts`) — persisté (`localStorage` clé
  `apex-theme`), priorité choix explicite > préférence système
  (`prefers-color-scheme`, suivie en direct tant qu'aucun choix n'est fait)
  > `dark` par défaut. Un script bloquant dans `index.html` pose
  `data-theme` avant le premier paint (même logique que le hook, dupliquée
  intentionnellement côté HTML pur — aucun flash visuel).
- **Motion** — jetons `--duration-*`/`--ease-*` (theme.css), appliqués au
  collapse de sidebar, au scrim mobile, aux primitives du design system, au
  pulse d'`ApexStatusBadge` (RÉSERVÉ à `using_tool`, le seul état qui
  prouve une activité backend réellement en cours — jamais sur
  `analyzing`/`waiting`/`learning`). `prefers-reduced-motion: reduce` est
  respecté GLOBALEMENT (une seule règle dans theme.css, jamais contournée
  composant par composant).
- **Dashboard** — réorganisé en sections nommées (System status / Hardware
  / Active agents / Recent missions / Models-Providers / Recent activity),
  toutes des vues sur des hooks déjà réels (`useHealth`/`useGate`/
  `useAgents`/`useMissions`/`useCatalogModels`/`useProviders`/`useEvents`/
  `useAuditLog`) — aucune nouvelle mesure, aucun chiffre inventé. « Active
  agents » filtre désormais réellement par état (spawning/ready/working/
  reporting), au lieu d'afficher les 6 derniers clones sans distinction.
  « Recent activity » utilise `Tabs` pour Événements/Gouvernance au lieu de
  deux panneaux empilés. Le badge d'état APEX en tête de page a été retiré
  (doublon avec celui, désormais global, de la TopBar).
- **Non fait dans cette passe** (scope délibérément limité à la fondation,
  pas une réécriture de chaque page) : Chat/Agents/Mission Center héritent
  des nouveaux jetons via la cascade CSS mais leur logique/markup interne
  n'a pas été réécrite — aucune régression (71 tests existants toujours
  verts), mais pas encore de refonte visuelle profonde de ces trois pages.
  `AgentTeam` (réel côté backend, `apex.agent_registry`) reste sans
  interface dédiée — l'entrée Sidebar "Multi-IA / Teams" le documente
  honnêtement comme `PLANNED` plutôt que de le cacher ou de le simuler.

### Frontend — API Integration (Skills / Weaknesses / Experiment Lab)

Raccorde le frontend à l'API d'évaluation/apprentissage déjà réelle côté
backend (`apex/api/routes/evals.py` : `GET /tasks`, `GET /tasks/{id}`,
`GET /eval-runs`, `GET /eval-runs/{id}`, `GET /weakness/{specialty}`,
`GET /self-model/{specialty}`, `POST /experiment-cycles`).

- **Authentification centralisée** (`src/api/client.ts`) — `fetchEnvelope`
  exige désormais un jeton (`VITE_APEX_API_TOKEN`, voir
  `frontend/.env.example`) envoyé comme `Authorization: Bearer <jeton>` sur
  CHAQUE requête ; jamais lu ni exposé ailleurs dans le code (seul un
  booléen, `isApiTokenConfigured()`, sort de ce module). Jeton absent →
  échec clair (`DataEnvelope(status:"unknown")`), jamais un appel réseau
  silencieusement non authentifié. `fetchEnvelope` distingue maintenant
  deux formes de réponse HTTP : l'enveloppe `DataEnvelope` existante
  (routes historiques, dégradation 500/DB indisponible) et la forme brute
  `{"detail": ...}` de `HTTPException` FastAPI (401/404/422 des nouvelles
  routes) — cette dernière est levée comme `ApiError` typée (`status`,
  `message`), jamais mélangée à l'enveloppe.
- **Client API étendu** (`src/api/{types,endpoints,hooks}.ts`) :
  `listTasks`/`getTask`/`listEvalRuns`/`getEvalRun`/`getWeakness`/
  `getSelfModel`/`createExperimentCycle`, chacun un miroir champ-à-champ
  des schémas Pydantic du backend — aucune logique métier dupliquée côté
  client.
- **Skills** (`pages/SkillsPage.tsx`) — reste volontairement LECTURE SEULE :
  aucun endpoint `/skills` dédié n'existe, seul `GET /self-model/{specialty}`
  porte `acquired_skills`/`proposed_skills`. Aucune action
  proposer/promouvoir/rejeter n'est simulée.
- **Weaknesses** (`pages/WeaknessesPage.tsx`) — `GET /weakness/{specialty}`
  pour le rapport de faiblesse, `GET /self-model/{specialty}` pour le
  contexte calibration/coût ; état `insufficient_data` affiché honnêtement,
  jamais une faiblesse fabriquée.
- **Experiment Lab** (`pages/ExperimentLabPage.tsx`) — liste/détail des
  `EvalRun` réels + déclenchement réel d'un cycle
  (`apex.metalearner.cycle.run_experiment_cycle` via
  `POST /experiment-cycles`). **Aucun champ d'identité/sujet n'est envoyé
  par le frontend** — le backend force déjà `Subject.HUMAN` — propriété
  vérifiée par un test dédié (corps JSON de la requête comparé
  strictement, `not.toHaveProperty("subject"/"requested_by")`). Le
  sélecteur de clone réutilise `useAgents()` existant (aucun mécanisme de
  spawn ajouté).
- **Registre** (`specialties/data.ts`/`registry.tsx`) — `skills`,
  `weaknesses`, `experiment-lab` passent de `planned`/`concept` à
  `real`/`connected` (les 3 seules entrées touchées ; `evals` reste
  volontairement non connecté cette passe).
- **Non fait dans cette passe** : aucune fonctionnalité de gestion de
  tâches (Task view reste hors périmètre, lecture/investigation
  uniquement) ; aucun travail sur les launchers (architecture « ONE
  CLICK » explicitement différée).

### Autonomous Learning Trigger + Scheduler/CLI Execution Path

Donne à `apex.metalearner.cycle.run_experiment_cycle` (déjà réel depuis la
passe « Real Model Evaluation ») un point de déclenchement production —
CLI, API, Scheduler optionnel — sans jamais réimplémenter le moteur
d'apprentissage lui-même.

- **`apex.metalearner.autonomy.AutonomousLearningService`** — service
  autoritaire unique. `start_cycle(specialty, current_level, method, split,
  clone_id=None)` démarre le cycle sur un thread démon (même patron qu'
  `apex.scheduler.heartbeat.HeartbeatService`) et rend la main
  IMMÉDIATEMENT (`RUNNING`) ; `status()` (snapshot en mémoire, par
  processus — même limite honnête qu'`ExecutionRegistry`) ;
  `history(limit)` (reconstruit depuis `EventRepository`, déjà réel et
  utilisé par `apex.scheduler.service` pour le même usage — aucune
  nouvelle table) ; `cancel()` (annulation réelle via `CancellationToken`,
  même mécanisme que `SchedulerService.cancel_mission`). **`requested_by`
  n'est PAS un paramètre** — toujours `Subject.HUMAN` en dur, exactement
  comme `POST /experiment-cycles` : aucun appelant (CLI/API/Scheduler) ne
  peut jamais fournir une autre identité. Si `clone_id` est omis, un clone
  jetable `Subject.LEARNING` (nouveau, vocabulaire déjà défini dans
  `apex.policy.model.Subject`) est auto-provisionné (premier `ComputePool`
  ACTIVE capable de `sandbox`, `GenomeVersion` actuellement PROMOTED —
  échoue proprement si aucun des deux n'existe, jamais une valeur
  fabriquée). Protection anti-doublon : `CycleAlreadyRunning` (nouvelle
  erreur, `apex/errors.py`) si un cycle est déjà `RUNNING` dans CE
  processus.
- **`apex.metalearner.cycle.run_experiment_cycle`** — étendu de façon
  strictement additive (`experiment_ref`/`cancel_token`, défaut `None`,
  aucun appelant existant affecté) : simple relais vers
  `EvalRunnerService.run`, qui acceptait déjà les deux depuis la phase 5.
- **`apex.metalearner.autonomous_scheduler.AutonomousLearningScheduler`** —
  déclenche `start_cycle` à intervalle (`AutonomousLearningPolicy.
  interval_s`), même patron `start()`/`stop()`/`tick_once()` qu'
  `HeartbeatService`. **Désactivé par défaut** (`enabled: false`) —
  installer/mettre à jour APEX ne démarre jamais l'apprentissage autonome
  par surprise. Un tick ignoré (cycle déjà `RUNNING`) est journalisé,
  jamais une erreur.
- **Configuration** (`config.yaml` `autonomous_learning:`, `apex.config.
  schema.AutonomousLearningPolicy`) : `enabled` (défaut `false`,
  substituable via `APEX_AUTONOMOUS_LEARNING_ENABLED`), `interval_s`,
  `max_concurrent_cycles` (doit valoir `1` — documente honnêtement la
  limite réelle du code plutôt que d'en tolérer une autre en silence),
  `specialty`/`current_level`/`method`/`split` (paramètres par défaut d'un
  cycle SCHEDULED, jamais utilisés par un déclenchement CLI/API explicite).
- **CLI** (`apex learn start|status|history|stop`, `apex/cli/main.py`) —
  appelle directement `apex.api.dependencies.get_learning_service()` (même
  service que l'API, jamais dupliqué). `apex learn start` ATTEND la fin
  réelle du cycle avant de rendre la main (contrairement à `start_cycle`
  lui-même, non bloquant) : un processus CLI qui rendrait la main
  immédiatement tuerait le thread démon avant la fin du cycle. Ctrl+C
  pendant `start` annule réellement le cycle (`CancellationToken`) — la
  façon effective d'annuler depuis la CLI, puisque `apex learn stop` lancé
  depuis un AUTRE processus ne voit jamais l'état d'un processus différent
  (mémoire par processus, documenté, pas résolu silencieusement).
- **API** (`apex/api/routes/learning.py`, `/learning/cycles` [POST],
  `/learning/cycles/cancel` [POST], `/learning/status` [GET],
  `/learning/history` [GET]) — même authentification uniforme
  (`require_api_token`) que toutes les routes existantes. `POST
  /learning/cycles` (`LearningCycleStartIn`) ne déclare AUCUN champ
  d'identité — un champ additionnel envoyé par un client est silencieusement
  ignoré par Pydantic, jamais transmis au service (qui ne l'accepte de
  toute façon pas). Cycle déjà `RUNNING` -> 409, jamais un doublon.
  L'import d'`apex.metalearner` reste différé dans le corps des fonctions
  (jamais au niveau module) — même discipline que `apex.api.routes.evals`,
  pour qu'`apex.api.app` reste importable sans que `apex.metalearner` fasse
  partie de l'environnement packagé.
- **`POST /experiment-cycles` délibérément NON refactoré** pour réutiliser
  `AutonomousLearningService` : c'est un appel humain direct, SYNCHRONE,
  qui peut légitimement s'exécuter en parallèle d'un autre (aucun verrou
  partagé) — le forcer à travers le service à cycle-unique changerait son
  comportement existant (régression silencieuse). Contrat public inchangé,
  tests existants toujours verts.
- **Lifespan FastAPI** (`apex/api/app.py`) — démarre/arrête
  `AutonomousLearningScheduler` (no-op si désactivé), jamais bloquant même
  si DB/config indisponible (`try/except` large autour de la construction —
  aucun code de ce dépôt ne lance aujourd'hui réellement ce processus via
  un serveur ASGI, voir `apex/api/auth.py` ; ce lifespan ne s'exécute donc
  concrètement que sous `TestClient` utilisé comme gestionnaire de
  contexte, ou un futur déploiement réel).
- **Frontend** (`ExperimentLabPage.tsx`) — panneau « Apprentissage
  autonome » minimal : état Scheduler (activé/désactivé, actif), état du
  cycle courant, dernier verdict, action « Démarrer un cycle autonome »/
  « Annuler ». Réutilise les champs déjà saisis dans le formulaire
  « Nouveau cycle expérimental » (spécialité/niveau/méthode/split/clone) —
  aucun second formulaire. La planification (intervalle, activation) reste
  une opération d'administration, jamais exposée dans cette interface —
  seulement via `config.yaml`/`apex learn`.
- **Non fait dans cette passe** : aucune implémentation de launcher
  (§21 de la mission, différée) ; aucune interface d'administration
  séparée pour la configuration du Scheduler.

**Démarrer un cycle manuellement** (l'apprentissage autonome reste
`enabled: false` tant que `config.yaml`/`APEX_AUTONOMOUS_LEARNING_ENABLED`
ne l'active pas explicitement — ce qui suit fonctionne indépendamment de
cette configuration, à la demande) :

```bash
# CLI — bloque jusqu'à la fin réelle du cycle, imprime le résultat
python -m apex.cli.main learn start --specialty research --current-level 1
python -m apex.cli.main learn status --json
python -m apex.cli.main learn history --json

# API — rend la main immédiatement (RUNNING), interroger /learning/status ensuite
curl -X POST http://127.0.0.1:8000/api/v1/learning/cycles \
  -H "Authorization: Bearer $APEX_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"specialty": "research", "current_level": 1}'
curl http://127.0.0.1:8000/api/v1/learning/status -H "Authorization: Bearer $APEX_API_TOKEN"
```

### Skill + Genome Integration into the Autonomous Learning Loop

Connecte le cycle d'apprentissage autonome (`AutonomousLearningService`,
ci-dessus) aux systèmes Skill (`apex.skills`) et Genome
(`apex.genome`) déjà réels — ils cessent d'être des sous-systèmes isolés
sans devenir une seconde implémentation de leur logique métier.

- **`apex.metalearner.feedback.LearningFeedbackService`** — nouveau,
  petit, appelé UNIQUEMENT depuis `AutonomousLearningService._run_cycle`
  après un `ExperimentCycleResult` réussi, juste avant de passer l'état à
  `COMPLETED`. `process_cycle_result(result, experiment_ref=...)` ne
  réimplémente ni l'évaluation, ni la détection de faiblesse, ni la
  calibration : il appelle `WeaknessDetectorService.detect_for_specialty`
  + `ConfidenceCalibrator.calibrate_from_weakness_report` (Skills, via
  `SkillRegistryService.propose_from_evidence`, déjà réel et inchangé) et
  la même paire pour Genome (nouveau, mais même mécanisme de preuve —
  aucun second calcul de confiance).
- **Skill** — `SkillRegistryService.propose_from_evidence(...,
  created_by=Subject.CORE)` est appelé tel quel ; refuse déjà lui-même
  (`ValueError`) si la preuve calibrée est insuffisante
  (`CalibrationStatus.INSUFFICIENT_DATA` ou borne inférieure calibrée
  sous `MIN_CALIBRATED_LOWER_BOUND_FOR_SKILL_EVIDENCE = 0.7`) — ce garde-
  fou n'est jamais dupliqué ni contourné. Le candidat reste `PROPOSED` ;
  `PROMOTE_SKILL`/`REJECT_SKILL` restent `Subject.HUMAN`-only, inchangés.
- **Genome** — plus petite frontière d'intégration sûre possible, comme
  demandé explicitement par la mission tant que le contenu d'une
  `GenomeVersion` n'est câblé à aucun comportement réel de clone
  (limite déjà documentée dans `GenomeStoreService`) : seule une
  **mutation** (`apex.genome.mutation.mutate`, réutilisée telle quelle,
  jamais réimplémentée) de la `GenomeVersion` actuellement `PROMOTED` est
  proposée (`GenomeStoreService.propose(..., created_by=Subject.CORE,
  origin=GenomeVersionOrigin.MUTATION)`), avec la même preuve calibrée que
  Skill. Jamais de `crossover`, jamais de sélection multi-génération,
  jamais de promotion automatique — délibérément différé (voir
  ci-dessous). Seed déterministe
  (`sha256(experiment_ref)[:16]` en entier) : rejouer le même
  `experiment_ref` produit la même mutation, jamais un résultat différent
  à chaque relecture.
- **Sécurité contre l'accumulation non contrôlée** (garde-fou symétrique,
  Skill ET Genome — aucune notion de `Population` n'existe dans le code,
  voir `apex/genome/selection.py` ; ce garde-fou en tient lieu sans
  inventer un second framework d'évolution) : si une compétence
  `PROPOSED` existe déjà pour la spécialité (`list_by_specialty`), ou une
  `GenomeVersion` `PROPOSED` existe déjà (`list_all`, toute spécialité
  confondue — une seule lignée de Genome), aucun nouveau candidat n'est
  créé ; la raison exacte est renvoyée dans `LearningFeedbackOutcome`.
- **Idempotence (obligatoire)** — aucune nouvelle table : chaque appel
  réussi écrit un événement `learning.feedback_processed`
  (`EventRepository`, déjà réel, même patron que
  `AutonomousLearningService.history()`) avec `experiment_ref` dans son
  contexte JSON. `process_cycle_result` cherche d'abord cet événement
  avant de créer quoi que ce soit — un `experiment_ref` déjà traité
  renvoie le résultat déjà enregistré (mêmes `skill_candidate_id`/
  `genome_candidate_id`), jamais un doublon.
- **Isolation des pannes** — le traitement Skill et Genome sont chacun
  dans leur propre `try/except Exception` : un échec de l'un
  (`learning.skill_feedback_failed`/`learning.genome_feedback_failed`,
  journalisé, jamais avalé silencieusement) ne bloque jamais l'autre, et
  aucun des deux ne peut faire échouer le cycle d'apprentissage —
  `AutonomousLearningService._process_feedback` capture toute exception
  (`cycle.feedback_failed`) et renvoie `(None, None)`, le cycle atteint
  toujours `COMPLETED`. Le résultat d'expérience lui-même
  (`ExperimentCycleResult`, `EvalRun`) reste durable et inchangé quoi
  qu'il arrive côté Skill/Genome.
- **Observabilité** — `skill_candidate_id`/`genome_candidate_id` exposés
  sur `CycleStatus`/`CycleHistoryEntry` (donc `GET /learning/status` et
  `GET /learning/history`, et le panneau « Apprentissage autonome » du
  frontend) ; traçable de bout en bout via `experiment_ref` dans les
  événements `learning.*` déjà cités.
- **API** — aucune nouvelle route. Les schémas `LearningCycleStatusOut`/
  `LearningCycleHistoryEntryOut` existants portent simplement les deux
  nouveaux champs, décision volontairement conservatrice (§9 de la
  mission : pas de surface d'API spéculative) — `GET /learning/skills`,
  `/genomes`, `/population` non ajoutées faute de besoin réel non couvert
  autrement.
- **Sécurité/Policy inchangée** — `Subject.CORE` en dur pour toute
  proposition automatique (jamais négociable depuis l'extérieur, aucun
  paramètre d'identité sur `process_cycle_result`) ; aucune promotion
  automatique, jamais ; `PolicyEngine` inchangé.
- **Non fait dans cette passe (différé explicitement)** : aucun domaine
  `Population`/`Experiment` réel (n'existe pas dans le code, non inventé
  ici) ; aucune promotion automatique de Skill ou de Genome ; le contenu
  d'une `GenomeVersion` reste sans effet sur le comportement réel d'un
  clone/`AgentRuntime` ; `crossover` jamais utilisé ; aucun orchestrateur
  de sélection/évolution multi-génération ; aucune nouvelle migration de
  base de données (schémas `SkillRow`/`GenomeVersionRow` réutilisés tels
  quels, idempotence/provenance entièrement portée par
  `EventRepository`).

### Real Clone Population + Evolution Loop

Construit la première boucle évolutive RÉELLE (§ mission N+6) :

```
Population -> Selection -> Clone/Fork -> Genome application
  -> Experiment assignment -> Execution -> Evaluation -> Fitness
  -> Selection -> Skill/Genome feedback -> Mutation -> nouvelle génération
```

Composée EXCLUSIVEMENT d'abstractions déjà réelles — aucune n'est
réimplémentée : `AgentRuntime.spawn` (Phase 8), `GenomeStoreService.
propose`/`apex.genome.mutation.mutate`/`crossover` (Phase 17),
`apex.genome.selection.successive_halving` (Phase 17),
`apex.metalearner.cycle.run_experiment_cycle` (Phase 5+),
`apex.metalearner.feedback.LearningFeedbackService` (N+5).

- **`apex.metalearner.population_model`/`population_repository`/
  `population_service`/`population_fitness`** (nouveau, petit — vit dans
  `apex.metalearner`, déjà classé Research par `runtime_build/selection.py`
  [fichier RÉSERVÉ, jamais modifié], plutôt qu'un nouveau paquet
  `apex.population` séparé : ajouter un sous-paquet de premier niveau
  casserait la partition Runtime/Research que ce fichier réservé impose,
  sans pouvoir la corriger) — bookkeeping PUR, aucun spawn, aucune
  proposition de Genome, aucune exécution. `PopulationMember`
  (`population_members`, nouvelle table — métadonnées seules : `clone_id`
  RÉFÉRENCE un `Clone` réel, `genome_version_id` une `GenomeVersion`
  réelle, jamais une troisième représentation dupliquée). Pas de classe
  `Population` séparée : une population EST l'ensemble des membres qui
  partagent `specialty` — ADR-0007 (aucune abstraction sans ≥2
  consommateurs/aucun composant existant ne peut porter la responsabilité)
  appliqué explicitement pour refuser cette classe. `MemberStatus`
  (`experimental`/`elite`/`champion`/`hibernating`/`retired`) reste une
  ÉTIQUETTE DE BOOKKEEPING INTERNE, jamais une décision `PolicyEngine` —
  ne change aucun droit, ne promeut aucun Genome/Skill (voir §8/§19 de la
  mission, anti-auto-illusion). `retired` est terminal (même discipline
  que `CloneState.is_terminal`).
- **`apex.genome.clone_config.resolve_resource_limits`** — la PREMIÈRE
  influence réelle du contenu d'un Genome sur l'exécution d'un clone
  (limite exacte documentée par N+5 : « `GenomeVersion` content still has
  zero effect on real clone/AgentRuntime behavior »). Frontière minimale
  et sûre : seul `ResourceLimits` (`apex.sandbox.model`, dont la docstring
  invitait déjà explicitement un futur appelant à le faire) peut être
  surchargé par la clé optionnelle `execution` du contenu JSON d'un
  Genome (`timeout_s`/`memory_mb`/`cpu_cores`) — jamais `network` (choix
  administrateur seul). Chaque champ n'est appliqué que borné par la
  config administrateur (jamais une élévation, même si le Genome vient
  d'une mutation `Subject.CORE` jamais promue par un humain). Câblé dans
  `EvalRunnerService.run(genome_content=...)` (additif, défaut `None`,
  aucun appelant existant affecté) et relayé par `run_experiment_cycle`.
- **`apex.metalearner.evolution.PopulationEvolutionService.evolve_generation`**
  — l'orchestrateur, déclenché HUMAIN (même modèle N+4 :
  `evolve_generation` ne prend AUCUN paramètre d'identité, `Subject.HUMAN`
  fourni en dur à `run_experiment_cycle`). Génération 0 : mute le Genome
  PROMOTED courant. Générations suivantes : parents = élite + champion de
  la génération précédente ; mutation sur un seul parent, ou `crossover`
  entre deux élites (une génération sur deux, uniquement si
  `PopulationPolicy.enable_crossover` et ≥2 élites existent — jamais à la
  génération 0). Sélection par `successive_halving` (réutilisé tel quel,
  jamais une seconde logique de sélection) : `elite_size` survivants
  gardés, jamais un seul (§9 : préserver la diversité). **Contrôle
  HELDOUT réel avant promotion `CHAMPION`** (§13) : un candidat meilleur
  que le champion actuel doit d'abord réussir un `run_experiment_cycle`
  supplémentaire sur `TaskSplit.HELDOUT` — échec -> reste `ELITE`, le
  champion existant n'est jamais remplacé par un individu qui n'a pas
  prouvé sa généralisation.
- **Idempotence (§17, obligatoire)** — `population_members.
  experiment_ref` est `UNIQUE`, vérifiée AVANT toute création
  (`PopulationService.add_member`) : rejouer le même `experiment_ref`
  renvoie le membre déjà créé, jamais un doublon, jamais un second spawn.
- **Isolation des pannes (§18)** — chaque membre d'une génération est créé
  dans son propre `try/except` (`PopulationEvolutionService.
  evolve_generation`) : l'échec d'UN membre (spawn, exécution, Genome) est
  journalisé (`evolution.member_failed`) et n'empêche jamais les autres
  membres de la même génération d'être créés et évalués.
- **Plafonds (§15, `apex.config.schema.PopulationPolicy`,
  `config.yaml` `population:`)** — `max_population_size` (membres NON
  `retired` simultanés, par spécialité), `generation_size` (membres créés
  par appel), `elite_size`, `enable_crossover`. Valeurs prudentes par
  défaut (8/3/2/true). Un plafond atteint arrête la génération proprement
  (`PopulationCapExceeded`, nouvel `ApexError`), jamais une erreur pour
  l'appelant — les membres déjà créés restent valides.
- **Skills + Genome (§12)** — chaque membre appelle
  `LearningFeedbackService.process_cycle_result` (N+5, INCHANGÉ) après
  son expérience ; `skill_candidate_id`/`genome_candidate_id` sont
  enregistrés SUR le membre (observabilité), jamais une seconde logique
  de proposition. Toujours `PROPOSED`, jamais promu par ce chemin — les
  garde-fous N+5 (accumulation contrôlée, provenance) s'appliquent
  identiquement, réutilisés tels quels.
- **API** (`apex/api/routes/population.py`) — seulement `GET /population/
  {specialty}` (lecture agrégée : membres, élite, champion, génération) et
  `POST /population/{specialty}/evolve` (mutatif, synchrone/bloquant,
  aucun corps de requête — donc structurellement aucun champ d'identité
  possible, contrairement à `POST /learning/cycles` qui doit explicitement
  ignorer un corps). `GET /population/{id}/lineage`, `GET /clones/{id}/
  genome`, `GET /clones/{id}/fitness` volontairement PAS ajoutés — le
  lignage simple (`parent_member_id`) est déjà porté par chaque membre,
  aucune route dédiée n'est nécessaire (§20 : pas de surface d'API
  spéculative).
- **Frontend** (`ExperimentLabPage.tsx`, panneau « Population / Évolution »)
  — génération courante, nombre de membres/élites, champion (clone +
  fitness), table des membres avec statut/fitness, bouton « Lancer une
  génération ». Aucun contrôle de promotion Genome/Skill exposé.
- **Non fait dans cette passe (différé explicitement)** : le contenu d'un
  Genome n'influence toujours PAS le choix de modèle/stratégie/skills
  activés (uniquement `ResourceLimits`) ; aucun orchestrateur
  multi-génération automatique (l'appelant décide de déclencher chaque
  génération, pas de boucle infinie cachée) ; `crossover` seulement entre
  deux élites déjà existantes, jamais à la génération 0 ; aucune évolution
  distribuée/concurrente (séquentiel, §16) ; aucun ordonnancement GPU
  avancé ; `apex.genome.selection.pareto_front` existe déjà mais n'est pas
  branché ici (sélection mono-objectif `successive_halving` suffisante
  pour cette passe) ; pas d'héritage de Skills déjà validés par un
  descendant (mentionné §12 de la mission, non implémenté — aucun
  mécanisme réel ne relie encore un `Skill` ACQUIRED à la configuration
  d'un nouveau clone).

### Autonomous Multi-Generation Learning Orchestrator

Construit la première boucle MULTI-génération réelle (§ mission N+7) :

```
specialty -> generation 0 -> fitness -> selection -> evolution
          -> generation 1 -> fitness -> selection -> evolution
          -> ...
          -> arrêt selon une politique explicite (max_generations / échec /
             annulation / convergence optionnelle)
```

Compose EXCLUSIVEMENT `apex.metalearner.evolution.PopulationEvolutionService.
evolve_generation` (passe précédente, INCHANGÉE) — ce module n'ajoute QUE la
politique de boucle, jamais une seconde implémentation de l'évolution
elle-même (sélection de parents, spawn, mutation/croisement, expérience,
fitness restent exactement ceux de N+6).

- **`apex.metalearner.learning_session_model`/`learning_session_repository`/
  `learning_session_service`** — bookkeeping PUR d'une « session
  d'apprentissage » (table `learning_sessions`, migration
  `dfd9571d6020`) : `specialty`, `status` (`running`/`cancel_requested`/
  `completed`/`failed`/`cancelled`), `max_generations`,
  `generations_completed`, `convergence_patience`/
  `generations_since_improvement`, `best_member_id`/`best_fitness`/
  `best_genome_version_id` (reflètent simplement le CHAMPION courant de la
  population — jamais un second calcul), `stop_reason`, `error`,
  `created_by`, `created_at`/`completed_at`. Vit dans `apex.metalearner`,
  même raison que N+6 (`runtime_build/selection.py`, fichier RÉSERVÉ,
  classe déjà ce paquet Research — un nouveau paquet top-level casserait la
  partition Runtime/Research qu'il impose).
- **`apex.metalearner.learning_orchestrator.LearningOrchestrator`** —
  l'orchestrateur : `start_session`/`resume_session`/`cancel_session`/
  `get_session`/`list_sessions`. Chaque `start_session`/`resume_session`
  lance un thread démon borné (même patron qu'`AutonomousLearningService`)
  qui appelle `evolve_generation` en boucle jusqu'à l'arrêt — jamais un
  scheduler/planificateur : ce module exécute la session explicitement
  demandée, il n'en démarre jamais une lui-même.
- **Reprise sûre par construction** — `evolve_generation` dérive TOUJOURS
  son numéro de génération de `population_members` déjà persisté
  (`PopulationService.latest_generation`), jamais d'un paramètre fourni par
  l'orchestrateur : reprendre une session après une panne revient à
  rappeler `evolve_generation`, qui reprend naturellement à la bonne
  génération — jamais un rejeu de 0, jamais un doublon de membre/lignage/
  `experiment_ref` (idempotence N+6, `PopulationService.add_member`,
  intégralement réutilisée). `resume_session` refuse une session `running`/
  `completed` (`ValueError`) — seules `failed`/`cancelled` sont reprenables.
- **Exclusion mutuelle RÉELLEMENT inter-processus** — au plus une session
  `running` par spécialité, imposée par un index UNIQUE PARTIEL en base
  (`learning_sessions_one_running_per_specialty_idx`, `specialty` WHERE
  `status = 'running'`), déclaré à la fois côté ORM
  (`apex.db.models.LearningSessionRow.__table_args__`, pour que les tests
  SQLite en mémoire l'appliquent aussi) et dans la migration — JAMAIS un
  `threading.Lock`/booléen mémoire (contrairement à `CycleAlreadyRunning`,
  `apex.metalearner.autonomy`, explicitement documenté comme valide PAR
  PROCESSUS seulement) : cette garantie tient avec plusieurs
  processus/workers API. Une tentative concurrente échoue au niveau du
  SGBD (`IntegrityError`), traduite en `LearningSessionAlreadyRunning`
  (`apex/errors.py`, `exit_code = 13`) par le repository.
- **Annulation ENTRE générations, jamais en cours de génération** — `_run_loop`
  relit le statut de la session (DB, jamais un drapeau mémoire) AVANT de
  démarrer chaque nouvelle génération ; une génération déjà commencée va
  toujours à son terme (elle a sa propre valeur scientifique). `record_
  generation` enregistre le résultat réel même si une annulation est
  arrivée pendant l'exécution — seule la décision de CONTINUER la boucle,
  jamais celle d'enregistrer, respecte l'annulation en attente
  (`generations_completed` reste toujours cohérent avec `population_members`
  réellement persisté).
- **Taxonomie précise des raisons d'arrêt** (`stop_reason`) —
  `max_generations_reached` (COMPLETED), `population_at_capacity`
  (COMPLETED — plafond de population déjà atteint, un état attendu, jamais
  une panne), `generation_failed_all_members` (FAILED — panne
  d'infrastructure réelle, ex. aucun Genome PROMOTED), `generation_raised`
  (FAILED — `evolve_generation` a levé), `cancelled` (CANCELLED),
  `converged` (COMPLETED, optionnel — voir ci-dessous).
- **Convergence déterministe, optionnelle** (`convergence_patience`, défaut
  `0` = désactivée) — arrêt anticipé après N générations consécutives sans
  amélioration réelle du meilleur fitness (marge fixe `1e-9`, une garde
  numérique contre l'égalité flottante fragile, jamais un seuil ML
  arbitraire) : jamais activée sans que l'appelant le demande explicitement.
- **Deux plafonds indépendants sur `max_generations` (§ jamais une boucle
  non bornée)** — la valeur demandée par l'appelant (`1` à
  `PopulationPolicy.max_session_generations`, `config.yaml` `population:`,
  défaut `20`) : dépasser ce plafond ADMINISTRATEUR lève `ValueError`,
  jamais un plafonnement silencieux.
- **Déclenchement HUMAIN, modèle N+4/N+6 inchangé** — `start_session` ne
  prend AUCUN paramètre d'identité ; `Subject.HUMAN` reste fourni en dur
  par `evolve_generation` lui-même à chaque génération, jamais négociable
  depuis l'extérieur. Aucune extension de `PolicyEngine` nécessaire :
  toutes les opérations privilégiées réelles (spawn, proposition de
  Genome) restent gouvernées transitivement par `PopulationEvolutionService`
  déjà composé.
- **API** (`apex/api/routes/learning_sessions.py`) — `POST
  /learning-sessions` (aucun corps d'identité possible), `GET
  /learning-sessions`, `GET /learning-sessions/{id}`, `POST
  /learning-sessions/{id}/resume`, `POST /learning-sessions/{id}/cancel` ;
  `start`/`resume` rendent la main IMMÉDIATEMENT (`running`), la boucle
  continue en tâche de fond. `LearningSessionAlreadyRunning` -> 409 ;
  spécialité inconnue/`max_generations` hors bornes -> 422 (vérifié AVANT
  toute connexion DB) ; session introuvable/non reprenable -> 404/422 ;
  DB injoignable -> `DataEnvelope(status="unknown")`, jamais une 500.
- **CLI** (`apex/cli/main.py`, groupe `apex learning`, DISTINCT du groupe
  `apex learn` déjà existant à un seul cycle) — `start --specialty ... --
  generations N [--convergence-patience N]`, `status <id>`, `list
  [--specialty ...]`, `resume <id>`, `cancel <id>`. Appelle exactement
  `apex.api.dependencies.get_learning_orchestrator()`, LE MÊME service que
  l'API — aucune seconde implémentation de la boucle. Contrairement à
  `apex learn start` (qui BLOQUE jusqu'à la fin d'un cycle unique),
  `apex learning start` rend la main immédiatement (`running`) : une
  session multi-génération peut durer largement plus longtemps qu'un
  processus CLI ne devrait bloquer — `apex learning status <id>` interroge
  ensuite l'état durable (DB), depuis n'importe quel processus.
- **Non fait dans cette passe (différé explicitement)** : AUCUN
  scheduler/démon qui démarre automatiquement des sessions (l'appelant
  décide toujours quand démarrer/reprendre — même discipline que N+6 pour
  une génération unique) ; aucun ordonnancement GPU/parallélisme spéculatif
  (séquentiel, déterministe — le contrat public n'empêche pas une future
  exécution parallèle, mais n'en construit aucune ici) ; frontend NON
  câblé (le contrat backend/CLI vient de se stabiliser cette passe ;
  câbler l'Experiment/Learning Lab existant sans fabriquer de progression
  inventée est un chantier distinct, volontairement différé plutôt que
  bâclé) ; aucun nouvel `Action` `PolicyEngine` (aucune opération
  privilégiée nouvelle ne le justifiait).

### Strict Judge System + Gauntlet Learning Loop

Construit le premier système de juges MULTI-DIMENSIONS, résistant au
reward hacking (§ mission N+8) :

```
candidate -> juges CHEAP (correctness/efficiency/security/regression)
          -> [si aucun véto] juges EXPENSIVE (robustness/generalization)
          -> agrégation déterministe -> PASS / REJECT / INCONCLUSIVE
          -> promotion CHAMPION uniquement si PASS
```

Principe central : « A solution does not pass because it looks good. A
solution passes because it survives hostile evaluation. » — AUCUNE moyenne
ne masque jamais un véto ; AUCUNE preuve manquante ne devient jamais un
`PASS` silencieux.

- **`apex.metalearner.judge_model`** (nouveau, pur, aucune DB) —
  `JudgeDimension` (9 valeurs nommées, 6 réellement instanciées :
  `CORRECTNESS`/`ROBUSTNESS`/`EFFICIENCY`/`SECURITY`/`GENERALIZATION`/
  `REGRESSION` ; `EDGE_CASES`/`QUALITY`/`ADVERSARIAL` restent PLANNED,
  aucun juge n'y est jamais associé — même discipline que `JudgeTierLevel.
  TIER_1`/`TIER_2`, Phase 9), `JudgeSeverity` (`LOW`/`MEDIUM`/`HIGH`/
  `CRITICAL` — `CRITICAL` = véto), `JudgeDecision` (`PASS`/`REJECT`/
  `INCONCLUSIVE`, vocabulaire PARTAGÉ entre un juge individuel et la
  décision agrégée), `JudgeEvidence`/`JudgeFinding`/`JudgeResult`
  (`score`/`confidence`/`rationale` toujours SÉPARÉS — un score élevé à
  faible confiance n'est jamais une preuve forte), `GauntletResult`
  (décision agrégée) et `GAUNTLET_PROTOCOL_VERSION` (constante de CODE,
  jamais un réglage admin — un protocole d'agrégation qui change EST un
  changement de comportement).
- **`apex.metalearner.judges`** — 6 juges réels, UNE responsabilité
  chacun (§5 : jamais un « God Judge »), TOUS objectifs/déterministes
  (§6 : aucun LLM consulté) :
  - `CorrectnessJudge` — enveloppe `OracleVerdict` (`apex.oracles`), zéro
    logique dupliquée.
  - `EfficiencyJudge` — `cost_penalty` = `elapsed_s`/`timeout_s`, MÊME
    formule qu'`apex.metalearner.population_fitness.compute_fitness` ;
    rejette aussi un dépassement `RESOURCE_LIMIT_EXCEEDED`/`OOM_KILLED`.
  - `SecurityJudge` — `ExecutionStatus.NETWORK_DENIED`/`PERMISSION_DENIED`
    (signal RÉEL déjà produit par le sandbox, Phase 7) = véto CRITICAL.
  - `RegressionJudge` — compare le candidat au CHAMPION courant sur les
    composantes de fitness DÉJÀ PERSISTÉES (aucune exécution
    supplémentaire) : une régression de `quality` est un véto ; une
    régression majeure de coût (`major_regression_factor`, défaut `2.0`)
    est un `REJECT` non-véto.
  - `RobustnessJudge` — cohérence entre DEUX exécutions RÉELLES du MÊME
    (tâche, Genome) : un verdict qui change entre les deux est un véto
    (candidat non-déterministe). `None` (pas de ré-exécution effectuée)
    -> `INCONCLUSIVE`, jamais un `PASS` déduit.
  - `GeneralizationJudge` — enveloppe le contrôle HELDOUT déjà réel de
    N+6 (aucune seconde implémentation) : un échec HELDOUT est un véto
    (surapprentissage du TRAIN).
- **`apex.metalearner.gauntlet.GauntletRunner`** — `run_cheap` (les 4
  juges CHEAP, calculé pour CHAQUE membre, zéro coût supplémentaire) et
  `run_full` (ajoute les 2 juges EXPENSIVE, réservé au candidat à la
  promotion CHAMPION — s'arrête tôt, sans même les exécuter, si le stage
  CHEAP a déjà produit un véto, §13/§24). `_aggregate` est une fonction
  PURE : véto -> `REJECT` (peu importe le reste) ; sinon un `REJECT` non-
  véto bloque déjà ; sinon un `INCONCLUSIVE` bloque déjà (jamais
  silencieusement un `PASS`) ; sinon `PASS` SEULEMENT si la confiance
  globale (le MINIMUM des confidences, jamais une moyenne optimiste)
  dépasse `GauntletPolicy.min_confidence_threshold` (défaut `0.6`),
  sinon `INCONCLUSIVE`. Un juge qui LÈVE devient un `JudgeResult`
  `INCONCLUSIVE` synthétique (jamais un `PASS` par défaut, §21 Case I).
  Déterministe et indépendant de l'ORDRE des juges (§21 Case G/H).
- **Intégration dans `PopulationEvolutionService` (N+6), AUCUN second
  pipeline** — `_create_member` calcule le gauntlet CHEAP juste après
  `compute_fitness` (même `EvalRun`, aucune exécution supplémentaire) ;
  `_promote_champion_if_earned` REMPLACE l'ancien contrôle HELDOUT
  booléen SEUL par le gauntlet FULL : ré-exécute RÉELLEMENT la même tâche
  avec le même Genome (`EvalRunnerService.run` directement — JAMAIS
  `run_experiment_cycle`, qui générerait une tâche DIFFÉRENTE à chaque
  appel) pour `RobustnessJudge`, ET relance le contrôle HELDOUT existant
  pour `GeneralizationJudge` — SEULEMENT si le stage CHEAP n'a pas déjà
  vétoé (même profil de coût que le contrôle HELDOUT seul existant en
  N+6 : un candidat déjà rejeté par les juges CHEAP ne paie jamais le
  coût réel d'une ré-exécution/HELDOUT). L'`EvalRun` original du candidat
  est retrouvé via la preuve CITABLE déjà persistée dans le gauntlet CHEAP
  (`JudgeEvidence(kind="eval_run", reference=...)`) — AUCUNE nouvelle
  colonne sur `population_members`.
- **`GauntletPolicy`** (`apex.config.schema`, `config.yaml` `gauntlet:`) —
  `min_confidence_threshold` (0.6), `efficiency_reject_threshold` (0.9),
  `major_regression_factor` (2.0), `enable_expensive_judges` (`true` —
  désactivable par un administrateur ; dans ce cas les dimensions
  EXPENSIVE sont honnêtement ABSENTES du résultat, jamais un `PASS`/
  `INCONCLUSIVE` fabriqué pour combler leur absence).
- **Persistence** — `gauntlet_results` (migration `9cafd4f90c50`, INSERT
  seul, même garantie qu'`eval_runs`) : `member_id`/`stage`
  (`cheap`/`full`)/`protocol_version`/`decision`/`overall_score`/
  `overall_confidence`/`veto`/`veto_dimension`/`disagreement`/
  `stopped_after_critical_failure`/`judge_results` (JSON, la liste
  STRUCTURÉE de chaque `JudgeResult`, jamais seulement un texte
  narratif)/`compared_champion_id`. `apex.metalearner.gauntlet_repository.
  GauntletRepository` traduit vers/depuis `GauntletResult`.
- **API** (`apex/api/routes/gauntlets.py`, lecture seule) — `GET
  /gauntlets/{id}`, `GET /population/{specialty}/gauntlets` (filtrable par
  `member_id`) — observabilité seule (§19/§25) : « pourquoi cette
  candidate a-t-elle été acceptée/rejetée, quel juge a trouvé le problème,
  quelle preuve, quelle différence avec le champion ». Aucune route
  mutative : le Judge System s'exécute exclusivement à l'intérieur de
  `PopulationEvolutionService`, jamais déclenché depuis l'API/CLI.
- **Sécurité** — aucun nouvel `Action` `PolicyEngine` (aucune opération
  privilégiée nouvelle : le gauntlet ne fait que LIRE des preuves déjà
  produites par des primitives déjà gouvernées). Un candidat ne peut
  jamais choisir ses propres juges/seuils : `GauntletPolicy` vient
  exclusivement de la configuration administrateur (`config.yaml`),
  jamais du contenu d'un Genome.
- **Non fait dans cette passe (différé explicitement)** : `EDGE_CASES`/
  `QUALITY`/`ADVERSARIAL` restent des dimensions PLANNED (aucun juge
  réel) ; aucun juge LLM (§26, architecture évolutive — s'ajouterait dans
  `apex.metalearner.judges` sans toucher l'agrégateur) ; aucune
  calibration de juge réelle (faux positifs/négatifs, réputation) — seul
  le modèle de données (`judge_results` structuré, `protocol_version`) le
  permet pour une phase future ; **aucun raccordement automatique au
  Weakness/Curriculum Engine** au-delà de l'événement `evolution.
  gauntlet_rejected_champion_candidacy` (`EventRepository`, observabilité
  humaine réelle) — le rejet d'une candidature est visible/traçable, mais
  aucun composant ne le relit encore comme signal d'apprentissage
  automatique (ne pas prétendre un wiring qui n'existe pas, §15/§23 de la
  mission) ; frontend NON câblé (observabilité API suffisante pour cette
  passe ; une visualisation minimale reste un chantier distinct) ; aucun
  ordonnancement GPU/parallélisme spéculatif (séquentiel, déterministe).

### Judge Feedback -> Weakness -> Curriculum -> Adaptive Evolution

Referme la limitation explicitement laissée ouverte par N+8 (« le rejet est
visible via `EventRepository`, pas encore un signal d'apprentissage
automatique ») — § mission N+9 :

```
Gauntlet (CHEAP puis FULL, N+8, INCHANGÉ)
  -> JudgeLearningSignal (1 par JudgeResult, classification pure)
  -> judge_learning_signals (INSERT seul)
  -> WeaknessEngineService.aggregate (calculé à la demande, jamais persisté
     comme état mutable — même discipline qu'apex.weakness.model)
  -> CurriculumEngineService.build_targets (tâches ciblées PAR dimension,
     jamais un score modifié directement)
  -> EvolutionContext (chemins Genome à biaiser)
  -> PopulationEvolutionService.evolve_generation (SEUL moteur, N+6/N+7/N+8
     inchangé) -> mutation biaisée -> nouvelle expérience -> nouveau Gauntlet
```

- **`apex.metalearner.judge_feedback_model`** (nouveau, pur) —
  `SignalCategory` (`SUCCESS`/`WEAKNESS`/`FAILURE`/`CRITICAL_FAILURE`/
  `INCONCLUSIVE`, répond à « que faire de ce signal ») et `FailureCategory`
  (`CANDIDATE_FAILURE`/`JUDGE_FAILURE`/`INSUFFICIENT_EVIDENCE`/
  `INFRASTRUCTURE_FAILURE`/`POLICY_REJECTION`/`GENUINE_CAPABILITY_WEAKNESS`,
  répond à « pourquoi ») sont deux axes ORTHOGONAUX — jamais confondus
  (§11 de la mission : un juge qui plante ou une preuve absente ne devient
  JAMAIS « le modèle est mauvais »). `JudgeLearningSignal` (1 par
  `JudgeResult`, jamais agrégé à l'écriture), `GauntletWeakness` (calculée,
  même patron qu'`apex.weakness.model.WeaknessReport`), `CurriculumTarget`,
  `EvolutionContext` (chemins Genome + génération recommandée, jamais un
  second moteur).
- **`apex.metalearner.judge_feedback_service.classify`** — fonction PURE
  `(JudgeResult, EvalRun | None) -> (SignalCategory, FailureCategory |
  None)` : un `PASS` -> `SUCCESS` ; un `REJECT` non-véto -> `WEAKNESS` ; un
  véto (`JudgeSeverity.CRITICAL`) -> `CRITICAL_FAILURE`, JAMAIS rétrogradé ;
  un `INCONCLUSIVE` re-vérifie le VRAI `EvalRun` référencé (preuve
  `JudgeEvidence`, jamais le texte de la justification) : `oracle_verdict
  is ERROR` -> `INFRASTRUCTURE_FAILURE`, sinon `INSUFFICIENT_EVIDENCE` ; un
  marqueur structurel `JudgeEvidence(kind="judge_error")` (ajout additif à
  `GauntletRunner._judge_error_result`, N+8) -> `JUDGE_FAILURE`, jamais
  confondu avec une faiblesse réelle du candidat.
- **`JudgeFeedbackService.record_signals`** — appelée par
  `PopulationEvolutionService` juste après CHAQUE stage de Gauntlet (CHEAP
  et FULL), isolée par `try/except` (même discipline que
  `_process_feedback`, N+5) : un échec d'enregistrement du signal ne casse
  JAMAIS la création du membre ni le résultat déjà obtenu du Gauntlet.
  Anti-double-comptage : le stage FULL ne réutilise que la TRANCHE de
  `judge_results` réellement NOUVELLE (`full_result.judge_results[len(
  cheap_result.judge_results):]`) — un jugement déjà persisté au stage
  CHEAP (court-circuit véto/`enable_expensive_judges=False`, N+8) n'est
  jamais recompté, jamais gonflé dans la fréquence d'une faiblesse.
- **`apex.metalearner.weakness_engine.WeaknessEngineService`** — `aggregate`
  regroupe les signaux `WEAKNESS`/`CRITICAL_FAILURE` par `(specialty,
  dimension, failure_category)`, calculé à la demande (jamais une ligne
  « faiblesse courante » mutable en base — une seule table INSERT seul,
  `judge_learning_signals`, reste la source de vérité). Formules
  déterministes documentées, aucun score magique : `confidence = min(1.0,
  len(group) / 5)` (`_CONFIDENCE_SATURATION_COUNT`) ; `improvement_status`
  = fonction pure de l'écart entre `last_seen_generation` et la génération
  la plus récente (`gap <= 0` -> `PERSISTENT`, `gap == 1` -> `RECOVERING`,
  `gap >= 2` -> `RESOLVED`, fenêtre `_RECENCY_WINDOW_GENERATIONS = 2`).
  `history` renvoie les signaux bruts, filtrables par dimension.
- **Corrélation de faiblesse suspectée héritée (§5, jamais une causalité
  confirmée)** — `_suspected_common_ancestor` parcourt le VRAI lignage
  (`PopulationService.get_lineage`, N+6, inchangé) de chaque membre affecté
  et n'affirme un ancêtre commun QUE s'il est réellement partagé par TOUS
  les membres affectés (`None` sinon, ou si moins de 2 membres affectés) —
  exposé comme `suspected_inherited_from`, jamais un vocabulaire de
  certitude.
- **`apex.metalearner.curriculum_engine.CurriculumEngineService`** —
  génère des TÂCHES ciblées par dimension, ne modifie JAMAIS un score
  directement. Honnête sur ses limites réelles : `REGRESSION` utilise le
  VRAI `GenerationMode.REGRESSION` (`apex.taskgen.deterministic`) avec un
  `base_task_id` récupéré depuis la preuve déjà persistée ; `CORRECTNESS`
  OBSERVE seulement le chemin automatique déjà existant
  (`MetaLearner.select_strategy` -> `GenerationMode.TARGETED_WEAKNESS`,
  N+ antérieur) — ne le redéclenche jamais, pour éviter un second pipeline
  parallèle ; `EFFICIENCY`/`ROBUSTNESS` recommandent le seul champ Genome
  réellement exposé pertinent (`execution.timeout_s`,
  `apex.genome.clone_config._OVERRIDABLE_FIELDS`) ; `SECURITY`/
  `GENERALIZATION` renvoient honnêtement `recommended_mode=None`,
  `focus_genome_paths=()` — aucun générateur/champ réel ne les gouverne
  encore (jamais une recommandation fabriquée pour combler l'absence).
- **Mutation adaptative biaisée, jamais un nouveau mécanisme de sécurité**
  — `apex.genome.mutation.mutate` étendu avec `focus_paths`/
  `focus_rate_multiplier` (défauts = no-op total, les 18 tests N+6
  préexistants passent inchangés, RNG byte-identique à vide) : un chemin
  Genome dans `focus_paths` obtient `effective_rate = min(1.0, rate *
  focus_rate_multiplier)` (défaut `3.0`) au lieu du taux uniforme. Le
  PLAFOND de sécurité reste EXCLUSIVEMENT `apex.genome.clone_config._clamp`
  (N+6, INCHANGÉ, jamais dupliqué ici) — vérifié par un test de résistance
  à 200 essais : aucune valeur mutée biaisée ne dépasse jamais la limite
  administrateur, `network` ne devient jamais mutable même s'il apparaissait
  par erreur dans `focus_paths`.
- **`EvolutionContext` construit à chaque `evolve_generation`, UN SEUL
  moteur d'évolution** — `_build_evolution_context` appelle `aggregate` +
  `build_targets`, union des `focus_genome_paths` de toutes les cibles, et
  passe le résultat à `_create_member`/`_propose_offspring_genome` (les
  DEUX points de mutation hors croisement — le croisement reste inchangé).
  Jamais un second moteur : uniquement un biais d'entrée supplémentaire
  pour `PopulationEvolutionService`, seul point d'évolution du dépôt.
- **Anti-reward-hacking (§9, CRITIQUE) — rien de nouveau n'est affaibli** :
  HELDOUT (`GeneralizationJudge`, N+8), comparaison au CHAMPION
  (`RegressionJudge`, N+8), diversité des juges, agrégation déterministe
  (`GauntletRunner._aggregate`, N+8) restent EXACTEMENT ceux de N+8 — cette
  passe n'ajoute qu'une couche de LECTURE/biais en amont de la mutation ;
  aucun chemin ne permet à un score de juge UNIQUE de promouvoir un
  candidat, aucun chemin ne permet de régresser sur HELDOUT et d'être quand
  même promu (vérifié explicitement par les tests de résistance §9).
- **Persistence** — `judge_learning_signals` (migration `be2826e5de10`,
  INSERT seul, même garantie qu'`eval_runs`/`gauntlet_results`) : un
  enregistrement par `JudgeResult`, colonnes structurées (`dimension`/
  `decision`/`category`/`failure_category`/`severity`/`score`/
  `confidence`/`finding_summary`/`evidence_reference`/`generation`/
  `genome_version_id`/`member_id`) — répond aux questions de récurrence/
  correction/génome/génération SANS dépendre d'un texte narratif seul.
- **API** (`apex/api/routes/learning_feedback.py`, lecture seule) — `GET
  /learning-feedback/{id}`, `GET /weaknesses/{specialty}`, `GET
  /weaknesses/{specialty}/history` (filtrable par dimension), `GET
  /curriculum/{specialty}`, `GET /population/{specialty}/learning-signals`
  (filtrable par membre), `GET /learning-sessions/{id}/weaknesses`
  (délègue à la spécialité de LA session, jamais un second calcul).
  Surface volontairement minimale (§12), aucune route mutative : le Judge
  Feedback ne s'exécute que depuis `PopulationEvolutionService`.
- **Tests** — `tests/test_judge_feedback.py` (24, classification/
  persistence/agrégation/corrélation de lignage/honnêteté du curriculum),
  `tests/test_adaptive_evolution.py` (10, mutation biaisée/plafond
  administrateur/garanties champion+HELDOUT/résistance au reward hacking/
  une preuve de bout en bout RÉELLE — seul le verdict d'UN juge est forcé
  via `unittest.mock.patch.object`, même patron déjà accepté en N+8 ; le
  reste de la chaîne — sandbox, fitness, persistance, agrégation,
  curriculum, mutation, ré-évaluation — s'exécute intégralement sans
  simulation), `tests/test_api_learning_feedback.py` (16, authentification/
  dégradation sans DB/validation d'entrée/PostgreSQL réel).
- **Non fait dans cette passe (différé explicitement)** : `EDGE_CASE`/
  `ADVERSARIAL`/`BUG_FARM` restent des modes de génération PLANNED — aucune
  tâche ciblée réelle pour ces dimensions ; aucun mécanisme réel ne
  gouverne encore `SECURITY`/`GENERALIZATION` côté génération de tâche ou
  champ Genome (honnêtement `None`, jamais fabriqué) ; `CORRECTNESS` ne
  fait qu'OBSERVER le chemin automatique déjà existant, jamais un second
  déclenchement ; aucune calibration de juge (faux positifs/négatifs,
  réputation) au-delà du modèle de données qui le permettrait pour une
  phase future ; frontend NON câblé (API d'observabilité suffisante pour
  cette passe) ; aucun ordonnancement GPU/parallélisme spéculatif.

### Adversarial Curriculum + Capability Benchmarking

Referme les limitations laissées ouvertes par N+9 (pas d'`EDGE_CASE`/
`ADVERSARIAL` réels, curriculum `SECURITY`/`GENERALIZATION` incomplet,
`BUG_FARM` absent, aucun benchmark indépendant de capacité) — § mission
N+10 :

```
WEAKNESS (N+9, inchangée)
  -> CURRICULUM (CurriculumChallenge, tâche RÉELLE persistée)
  -> CHALLENGE (candidat expérimente la tâche ciblée)
  -> CANDIDATE (mutation biaisée, N+9, inchangée)
  -> GAUNTLET (N+8, inchangé)
  -> HELDOUT (N+6/N+8, inchangé)
  -> CAPABILITY BENCHMARK (batterie HELDOUT FIXE, distincte du curriculum)
  -> CAPABILITY DELTA (vs champion, vs meilleur historique)
  -> LEARNING HISTORY (judge_learning_signals + gauntlet_results, réutilisés)
  -> NEXT GENERATION (PopulationEvolutionService, SEUL moteur, inchangé)
```

- **`apex.taskgen.model.EdgeCaseKind`/`AdversarialKind`/
  `GeneralizationVariant`** (nouveau vocabulaire, additif) — `EDGE_CASE`/
  `ADVERSARIAL` sont désormais RÉELS (`apex.taskgen.deterministic.
  DeterministicTaskGenerator`), mais bornés au SEUL domaine de tâche
  existant (le script diagnostic déterministe de `research`) : chaque
  variante dérive d'une propriété RÉELLE de ce domaine — `EMPTY`/
  `MINIMAL`/`MAXIMAL`/`REPEATED_CALLS`/`BOUNDARY_TIMEOUT` (edge cases) et
  `TIMEOUT_PRESSURE`/`REPEATED_EXECUTION_STRESS`/`RESOURCE_PRESSURE`/
  `NETWORK_BOUNDARY_PROBE`/`FILESYSTEM_BOUNDARY_PROBE` (adversarial) —
  jamais un cas arbitraire (§3/§4 de la mission). Un adversarial ne
  demande JAMAIS plus que la politique déjà résolue (`limits`, fournie par
  l'appelant, fractions STRICTEMENT < 1.0 documentées :
  `_ADVERSARIAL_TIMEOUT_FRACTION=0.85`/`_ADVERSARIAL_MEMORY_FRACTION=0.8`)
  — `NETWORK`/`FILESYSTEM_BOUNDARY_PROBE` tentent une action que le
  sandbox est CENSÉ refuser, l'échec de la tentative EST le comportement
  correct attendu, jamais un contournement demandé. `GeneralizationVariant.
  ALTERNATE_PROBE` fournit une variation STRUCTURELLEMENT distincte (cible
  de sonde réseau différente) réservée aux challenges HELDOUT — frontière
  honnête documentée : ceci varie un paramètre réel d'une sonde existante,
  pas un domaine de généralisation sémantique riche (absent de ce dépôt).
- **`CurriculumEngineService` étendu (N+9, additif)** — `SECURITY`
  recommande désormais `GenerationMode.ADVERSARIAL`
  (`NETWORK_BOUNDARY_PROBE`) au lieu d'observabilité seule ;
  `GENERALIZATION` recommande `GenerationMode.NORMAL` avec
  `GeneralizationVariant.ALTERNATE_PROBE` ; `EFFICIENCY`/`ROBUSTNESS`
  gagnent EN PLUS un challenge `EDGE_CASE` ciblé (`BOUNDARY_TIMEOUT`/
  `REPEATED_CALLS`), en plus du biais de mutation déjà existant (jamais
  l'un au lieu de l'autre). `CurriculumTarget` (N+9) porte désormais
  `edge_case_kind`/`adversarial_kind`/`generalization_variant` (champs
  additifs, `None` par défaut).
- **`apex.metalearner.challenge_model`/`challenge_repository`/
  `challenge_service`** (nouveau) — `CurriculumChallenge` référence
  TOUJOURS une `Task` RÉELLEMENT persistée (`task_id`), jamais un résultat
  attendu fabriqué (§2). `weakness_id` est une clé DÉRIVÉE déterministe
  (`weakness_key`), pas une clé étrangère vers une table de faiblesses
  (`GauntletWeakness` reste calculée à la demande, N+9). `build_challenges`
  est IDEMPOTENT par `(weakness_id, generation)` — jamais un doublon de
  tâche/challenge pour la même faiblesse à la même génération (§20 :
  « bounded challenge count »). Table `curriculum_challenges` (migration
  `a3f7c9d21b44`), INSERT seul.
- **`apex.metalearner.bug_farm_model`/`bug_farm_repository`/
  `bug_farm_service`** (nouveau, §7) — `BugRecord` dérivé EXCLUSIVEMENT de
  `WeaknessEngineService.aggregate` (N+9, inchangé) : transitions
  DÉTERMINISTES `OPEN -> FIXED` (`ImprovementStatus.RESOLVED`) `-> 
  REGRESSED` (la même faiblesse réapparaît après avoir été `FIXED` — « le
  bug corrigé est revenu », §7/§15) `-> FIXED` de nouveau possible (jamais
  bloqué dans un état terminal). Table `bug_records` (même migration),
  SEULE table N+10 dont le statut est mutable après création
  (`BugRecordRepository.update_status`, colonnes ciblées).
- **`GauntletRunner.run_stage`** (nouveau, additif) — point d'entrée
  PUBLIC générique exposant `_aggregate` (N+8, INCHANGÉE) à un ensemble de
  `JudgeResult` fourni par l'appelant, avec un nouveau
  `GauntletStage.BENCHMARK` (additif) — zéro duplication de la logique
  d'agrégation stricte (véto décide seul, `INCONCLUSIVE` jamais un `PASS`
  silencieux, ordre des juges sans effet).
- **`apex.metalearner.capability_model`/`capability_benchmark_service`**
  (nouveau, §8/§9/§10/§12/§13) — `CapabilityBenchmarkRun` ENVELOPPE un
  `GauntletResult` de stage `BENCHMARK` (`id` = `result.id`, STABLE d'une
  lecture à l'autre, jamais régénéré) ; `CapabilityMeasurement` (un par
  dimension, dérivé d'un `JudgeResult` RÉEL) ; `CapabilityDelta` (vecteur
  PAR DIMENSION, jamais moyenné) ; `PromotionRuleResult` (réutilise
  `JudgeDecision`). Batterie de tâches FIXE, `TaskSplit.HELDOUT`
  (`dataset_ref="capability_benchmark_v1:{specialty}:{correctness,
  robustness,security,generalization}"`), créée une seule fois,
  STRUCTURELLEMENT DISTINCTE du curriculum d'entraînement (§12,
  vérifié par test : aucun `dataset_ref` en commun). Juges RÉUTILISÉS SANS
  DUPLICATION : `CorrectnessJudge`/`EfficiencyJudge`/`SecurityJudge`/
  `RobustnessJudge`/`GeneralizationJudge` (N+8, inchangés) évalués sur
  cette batterie ; `REGRESSION` est DÉRIVÉE par comparaison directe au
  dernier `CapabilityBenchmarkRun` du champion (jamais une exécution de
  juge supplémentaire) — `PASS` TRIVIAL (confiance 1.0) quand aucune
  référence n'existe encore (même principe qu'`apex.metalearner.judges.
  RegressionJudge`, § sa docstring : « aucun champion existant — rien à
  régresser » — JAMAIS `INCONCLUSIVE` pour cette absence spécifique, qui
  bloquerait à tort TOUTE première promotion).
- **`compute_delta`/`compute_delta_vs_historical_best`** — un delta PAR
  DIMENSION, `None` honnête si preuve manquante (jamais un delta inventé) ;
  `critical_regression` vrai si UNE SEULE dimension régresse au-delà de
  `_CRITICAL_REGRESSION_THRESHOLD` (`-0.10`, documenté, conservateur) —
  bloque la promotion INDÉPENDAMMENT de la moyenne des autres dimensions
  (§9 : « +15% correctness -40% security -> PAS une amélioration
  acceptable »). `get_historical_best` : le MEILLEUR score RÉELLEMENT
  observé par dimension sur TOUT l'historique (`gauntlet_results` stage
  `BENCHMARK`), jamais une estimation — `reference_run_id=None` pour cette
  comparaison (le meilleur par dimension peut provenir de générations
  différentes, honnêtement pas un run unique).
- **`decide_promotion`** — règle STRICTE (§13), jamais un `PROMOTE` forcé :
  `PASS` SEULEMENT SI le Gauntlet du benchmark lui-même est `PASS` ET
  aucune régression critique vs le champion ET confiance suffisante
  (`GauntletPolicy.min_confidence_threshold`, réutilisé — pas un nouveau
  réglage) ; sinon `REJECT`/`INCONCLUSIVE`.
- **Intégration dans `PopulationEvolutionService._promote_champion_if_
  earned` (N+6/N+8/N+9, AUCUN second moteur)** — le benchmark de capacité
  s'exécute SEULEMENT après un Gauntlet FULL déjà `PASS` (§20 : jamais
  avant, pour ne pas payer son coût réel pour un candidat déjà rejeté) ;
  une régression critique de capacité bloque la promotion même après un
  Gauntlet `PASS` (`evolution.capability_benchmark_blocked_champion_
  candidacy`). Isolation des pannes : une panne du benchmark devient une
  décision `INCONCLUSIVE` explicite (`_run_capability_benchmark_gate`),
  jamais une exception qui casserait la génération. `evolve_generation`
  synchronise EN PLUS `CurriculumChallengeService`/`BugFarmService` en fin
  de génération (isolé, jamais fatal).
- **Persistence** — AUCUNE nouvelle table pour les résultats de jugement du
  benchmark (réutilise `gauntlet_results`, stage `BENCHMARK` — §1 : « ne
  réimplémente rien qui existe déjà ») ; `curriculum_challenges`/
  `bug_records` (migration `a3f7c9d21b44`) pour le reste.
- **API** (`apex/api/routes/capability.py`, lecture seule) — `GET
  /capabilities/{specialty}` (statut consolidé : dernier run du champion +
  delta vs meilleur historique), `GET /capabilities/{specialty}/history`,
  `GET /capabilities/{specialty}/latest`, `GET /curriculum/{specialty}/
  challenges`, `GET /bugs/{specialty}`, `GET /learning-sessions/{id}/
  capability` (délègue à la spécialité de la session), `GET /population/
  {specialty}/capability` (alias intentionnel de `/capabilities/
  {specialty}`, même calcul, jamais un second).
- **Judge Calibration Foundation (§16)** — AUCUNE calibration implémentée
  (délibérément hors scope) : `disagreement`/`veto`/`confidence` restent
  persistés PAR JUGE dans `gauntlet_results.judge_results` (JSON structuré,
  N+8, inchangé) — suffisant pour une future phase de calibration
  (accord/désaccord entre juges, faux positifs/négatifs historiques),
  jamais un score de juge modifié arbitrairement ici.
- **Tests** — `tests/test_taskgen_edge_case_adversarial.py` (13, edge
  case/adversarial/generalization variant — provenance et bornes de
  politique vérifiées par EXÉCUTION RÉELLE des scripts générés),
  `tests/test_curriculum_challenge_bug_farm.py` (5, challenge
  SECURITY/GENERALIZATION + cycle de vie complet du bug farm),
  `tests/test_capability_benchmark.py` (15, indépendance de la batterie,
  vecteur multi-dimensionnel, delta/promotion/régression critique/
  déterminisme/désaccord des juges/absence de dominance d'un seul juge),
  `tests/test_api_capability.py` (11, dont une preuve de bout en bout
  RÉELLE couvrant les 14 étapes de la Phase 17 — seul le verdict d'UN juge
  est forcé, tout le reste de la chaîne s'exécute sans simulation).
- **Non fait dans cette passe (différé explicitement)** : `EDGE_CASE`/
  `ADVERSARIAL` restent bornés au seul domaine diagnostic existant (aucun
  domaine de code/fonction réel à muter, `CodeIntelligence` PLANNED) ;
  `GenerationMode.BUG_FARM` reste `PLANNED` comme MODE de génération (le
  suivi de bug réutilise `REGRESSION`, déjà réel) ; aucune calibration de
  juge réelle au-delà du modèle de données qui le permettrait ; frontend
  NON câblé ; aucun ordonnancement GPU/parallélisme spéculatif.

  **Correction N+11** : les deux premières limitations ci-dessus sont
  levées — voir la section suivante.

### Universal Task + Curriculum Engine (N+11)

**Objectif** : supprimer les deux limitations que N+10 avait explicitement
laissées ouvertes (« `EDGE_CASE`/`ADVERSARIAL` bornés au seul domaine
diagnostic » et « `GenerationMode.BUG_FARM` reste `PLANNED` ») en
construisant une infrastructure GÉNÉRIQUE de génération/composition de
tâches — sans jamais fabriquer une deuxième spécialité, un deuxième moteur
d'évolution, ou une capacité non réellement supportée.

- **Architecture** —
  `SPECIALTY -> TaskProvider -> TaskFactory -> Task -> CurriculumGenerator
  (NORMAL/EDGE_CASE/ADVERSARIAL/SECURITY/GENERALIZATION/REGRESSION/
  BUG_FARM) -> Candidate -> EvalRunner -> Gauntlet -> Capability Benchmark
  -> Weakness/Capability History`. Aucun nouveau moteur d'évolution
  (`PopulationEvolutionService` reste l'UNIQUE point d'exécution) ni de
  nouvel orchestrateur (`LearningOrchestrator` inchangé).
- **`TaskKind` (`apex.taskgen.model`)** — vocabulaire UNIFIÉ
  (`NORMAL`/`TARGETED_WEAKNESS`/`REGRESSION`/`EDGE_CASE`/`ADVERSARIAL`/
  `SECURITY`/`GENERALIZATION`/`BUG_FARM`), défini dans `apex.taskgen`
  (couche la plus basse) — `apex.metalearner.challenge_model.
  ChallengeSource = TaskKind` (ALIAS DIRECT, pas un doublon) : tout code
  préexistant qui importait `ChallengeSource` continue de fonctionner à
  l'identique.
- **`TaskProvenance`/`TaskConstraints` (`apex.taskgen.model`)** — champs
  additifs sur `GeneratedTaskSpec` (`provenance`, `None` par défaut) :
  `weakness_id`/`parent_task_id`/`challenge_id`/`bug_record_id`/
  `benchmark_id`/`generator_version`/`seed` — jamais une provenance
  inconnue pour une tâche auto-générée. `TaskConstraints` borne une
  génération à la politique RÉELLEMENT résolue (`ResourceLimits`) et à un
  plafond de difficulté.
- **`SpecialtyTaskProvider` (`apex.taskgen.specialty_provider`)** — contrat
  par lequel une spécialité déclare `capability_matrix()` (SUPPORTED/
  UNSUPPORTED par `TaskKind`, § Phase 5), `create(kind, ...)` et
  `create_variant(parent_task, transformation, seed, ...)`. `apex.taskgen.
  registry.get_provider(specialty_id)` est le SEUL point d'enregistrement
  — miroir volontairement PARTIEL d'`apex.specialties.registry` : seule
  `research` a un provider RÉEL (`ResearchTaskProvider`, délègue
  entièrement à `DeterministicTaskGenerator`, inchangé — zéro
  réimplémentation).
- **`TaskSpec`/`UniversalTaskFactory` (`apex.taskgen.task_factory`)** — le
  point d'entrée UNIQUE « créer une tâche » (§ Phase 2), `create(spec)`
  plutôt que sept méthodes obligatoires : route vers le
  `SpecialtyTaskProvider` réel de `spec.specialty`, lève
  `NotImplementedYet` si aucun n'existe ou si `kind` est `UNSUPPORTED` —
  jamais un contenu inventé.
- **`VariantEngine` (`apex.taskgen.variant_engine`, § Phase 6)** — « à
  partir d'une tâche A, produire A-edge/A-adversarial/A-generalized/
  A-regression sans copier l'originale » : `VariantRequest(parent_task,
  transformation, seed, ...)`, déterministe PAR CONSTRUCTION
  (`ResearchTaskProvider.create_variant` sélectionne sa variante par
  `seed % len(kinds_ordonnés)`, une fonction pure) — même
  `(tâche, transformation, seed, generator_version)` => même résultat ;
  un `seed` différent => variante VALIDE différente dès que plusieurs
  existent. `REGRESSION`/`BUG_FARM` reproduisent TOUJOURS la même
  commande quel que soit le seed (leur définition même).
- **`SECURITY` (§ Phase 10)** — route vers `AdversarialKind.
  NETWORK_BOUNDARY_PROBE`/`FILESYSTEM_BOUNDARY_PROBE` (les deux SEULES
  frontières de sécurité réellement sondables ici) — jamais d'escalade de
  privilège/réseau, tout reste dans le sandbox existant.
- **`GENERALIZATION` (§ Phase 8)** — route vers `GenerationMode.NORMAL` +
  `GeneralizationVariant` (toujours HELDOUT) — variation STRUCTURELLE
  d'un paramètre réel de la sonde existante, jamais un domaine sémantique
  riche fabriqué.
- **`REGRESSION`** — inchangé (reproduction exacte d'une tâche existante).
- **`BUG_FARM` réel (§ Phase 12, LA correction principale de cette
  passe)** — N+10 le déclarait `PLANNED` (« nécessite `CodeIntelligence` »).
  Correction : la reproduction d'un bug réel n'a JAMAIS eu besoin d'un
  domaine de code — exactement comme `REGRESSION`, elle reproduit la
  commande EXACTE de la tâche qui a servi de preuve à ce bug
  (`DeterministicTaskGenerator._bug_farm`, requiert `base_task`, jamais un
  contenu réinventé). `BugFarmService.build_bug_farm_challenges` (1)
  sélectionne les bugs `OPEN`/`REGRESSED` pertinents (triés par sévérité),
  (2) résout leur tâche de base (`evidence_reference` déjà posé, sinon la
  preuve la plus récente de la même faiblesse — jamais fabriquée, `None`
  si aucune preuve résolvable), (3) génère + persiste la reproduction via
  `UniversalTaskFactory`, (4) enregistre la preuve
  (`BugRecord.evidence_reference`, écrite UNE SEULE fois), (5) alimente le
  curriculum (`CurriculumChallenge.source=BUG_FARM`).
- **Difficulté structurée (`apex.taskgen.difficulty`, § Phase 7)** —
  `Difficulty` (EASY..EXPERT) est une façade lisible sur l'entier 1-4 déjà
  réel (`difficulty_from_severity`, N+10, inchangé) — `harder_than`/
  `easier_than`/`same_difficulty_as` (comparaisons pures) et
  `clamp_to_ceiling` (jamais un dépassement de policy).
- **`CurriculumPlan` (`apex.metalearner.curriculum_plan`, § Phase 13)** —
  `CurriculumEngineService.build_plan` groupe les `CurriculumTarget` déjà
  réels (N+9) par `TaskKind` (`resolve_task_kind`, factorisé — MÊME
  mapping que `challenge_service`, aucun doublon), priorité DÉTERMINISTE
  (sévérité maximale du groupe décroissante), plus un item `BUG_FARM`
  dérivé des `BugRecord` éligibles.
- **`CurriculumBudgetPolicy` (`apex.config.schema`, § Phase 14)** —
  `max_tasks_per_generation`/`max_adversarial_per_generation`/
  `max_heldout_per_generation`/`max_bug_farm_per_generation`/
  `max_generation_cost_s`, section `curriculum_budget` de `config.yaml`.
  `CurriculumPlanBuilder._apply_budget` tronque déterministiquement
  (priorité la plus faible éliminée en premier, jamais un dépassement
  silencieux) ; `CurriculumChallengeService.build_challenges` applique le
  MÊME plafond par `TaskKind` avant toute génération (un `TaskKind`
  totalement tronqué reste explicitement à `0`, jamais réinterprété comme
  « illimité » — un bug réel de cette nature a été trouvé et corrigé
  pendant le développement de cette passe, voir §26 du rapport final).
  `BugFarmService.build_bug_farm_challenges` applique indépendamment
  `max_bug_farm_per_generation`.
- **Séparation de dataset (§ Phase 15)** — `apex.taskgen.invariants`
  (`assert_disjoint`/`disjoint`/`assert_unique_ids`), fonctions PURES
  utilisées par les tests pour vérifier des ensembles de `task_id`
  RÉELLEMENT persistés — aucun contrôle runtime supplémentaire qui
  dupliquerait la séparation déjà structurelle (`TaskSplit` +
  GRANT/REVOKE PostgreSQL, ADR-0010, inchangée).
- **Provenance (`apex.metalearner.provenance_service.
  TaskProvenanceService`, § Phase 16)** — reconstruit « pourquoi cette
  tâche existe » depuis le `CurriculumChallenge` qui la référence
  (`task_id`, déjà indexé) — jamais une seconde table de provenance ;
  `None` pour une tâche écrite à la main (hors curriculum).
- **`curriculum_challenges` étendue (migration `f4d8b1e6a903`, § Phase
  23)** — deux colonnes NULLABLES additives (`parent_task_id`, `seed`),
  jamais une nouvelle table : réutilise la table N+10 existante.
- **`research` via le moteur générique (§ Phase 17)** —
  `CurriculumChallengeService` ne connaît plus AUCUN détail interne de
  `research` : il construit un `TaskSpec` et délègue à
  `UniversalTaskFactory`, qui route vers `ResearchTaskProvider`
  (`apex.taskgen.registry`) — la preuve que l'abstraction fonctionne,
  vérifiée par un test qui grep littéralement l'absence de
  `DeterministicTaskGenerator` dans `challenge_service.py`.
- **Deuxième spécialité (§ Phase 18) — HONNÊTEMENT non fabriquée** :
  `apex.specialties.registry` interdit explicitement (docstring
  préexistante) d'ajouter une entrée `STUB`/`IMPLEMENTED` sans qu'un
  moteur réel existe derrière — `AgentRuntime` n'a jamais été exercé de
  bout en bout que pour `Subject.RESEARCH`. Construire une deuxième
  spécialité aurait nécessité soit fabriquer un faux moteur, soit
  contourner ce garde-fou anti-fabrication déjà en place — les deux sont
  exactement ce que la mission interdit (« NE FABRIQUE AUCUNE DEUXIÈME
  SPECIALTY FICTIVE »). `apex.taskgen.registry.supported_specialty_ids()`
  documente cette frontière de façon vérifiable (un test l'exerce
  explicitement).
- **API** (`apex/api/routes/capability.py`, § Phase 20, lecture seule) —
  7 endpoints supplémentaires : `GET /curriculum/{specialty}/capabilities`
  (matrice), `GET /curriculum/{specialty}/plan`, `GET /curriculum/
  {specialty}/tasks`, `GET /curriculum/{specialty}/history` (alias de
  `/challenges`), `GET /tasks/{task_id}/provenance`, `GET /tasks/{task_id}
  /variants`, `GET /bug-farm/{specialty}` (alias de `/bugs/{specialty}`),
  `GET /specialties`.
- **Tests** — `tests/test_taskgen_universal_engine.py` (28, TaskFactory/
  TaskKind/SpecialtyTaskProvider/capability matrix/unsupported/
  déterminisme/seed/policy limits/difficulté — aucune DB requise),
  `tests/test_universal_curriculum_n11.py` (12, provenance/budget/
  séparation de dataset/research via le moteur générique/absence honnête
  d'une deuxième spécialité/intégration LearningOrchestrator/cycle complet
  bout en bout). `tests/test_taskgen_deterministic.py` mis à jour (retrait
  de `BUG_FARM` de la liste des modes non supportés).
- **Non fait dans cette passe** : `EDGE_CASE`/`ADVERSARIAL` restent
  bornés au domaine diagnostic (toujours aucun domaine de code réel,
  `CodeIntelligence` PLANNED) ; aucune calibration de juge au-delà du
  modèle de données ; frontend NON câblé sur les nouveaux endpoints ;
  aucun ordonnancement GPU/parallélisme ; le budget `max_bug_farm_per_
  generation` est appliqué indépendamment de `max_tasks_per_generation`
  partagé avec `CurriculumChallengeService` (deux services distincts,
  chacun respecte honnêtement son propre sous-plafond, jamais un optimum
  global unique prétendu).

### APEX Operator (N+16)

**Objectif** : faire passer APEX d'un système piloté au coup par coup
(`POST /missions/{id}/launch`, un seul `tick()` par appel, rien ne tourne en
continu) à un système pilotable normalement par une future UI/un client
local, sans terminal — voir `docs/AGENT_HANDOFF.md` pour le détail complet
(architecture, machine à états, 10 routes HTTP, permissions, limites).

**Ce qui a été ajouté**, sans dupliquer ni contourner l'existant :
- `apex/operator/{states,model,service}.py` — `OperatorService`, singleton
  applicatif construit AU-DESSUS de `SchedulerService`/`MissionService`
  (inchangés). Ajoute la boucle de supervision permanente que
  `apex.interfaces.registry` (entrée `Scheduler`) documentait explicitement
  comme la seule pièce manquante (« pourra être ajoutée sans modifier ce
  module ») — jamais touché.
- Machine à états fermée (`STOPPED/STARTING/READY/RUNNING/PAUSED/
  STOPPING/FAULTED`), même patron qu'`apex.missions.states`.
- 4 nouvelles `Action` (`START_OPERATOR`/`STOP_OPERATOR`/`PAUSE_OPERATOR`/
  `RESUME_OPERATOR`, `apex.policy.model`) réservées à `Subject.HUMAN` seul —
  `SubmitTask`/`CancelTask` n'introduisent AUCUNE action nouvelle, délégation
  pure à `MissionService.create`/`SchedulerService.cancel_mission`.
- `apex/api/routes/operator.py` (10 routes, `/api/v1/operator/*`), même
  authentification/enveloppe (`DataEnvelope`) que le reste de l'API.
- Entrée `Operator` (STUB) dans `apex.interfaces.registry`.

**Vérifié réellement** : `tests/test_operator_service.py` (33 tests,
lifecycle + concurrence réelle + exécution sandboxée réelle + FAULTED/
récupération) et `tests/test_api_operator.py` (11 tests, dont un parcours
HTTP complet Stopped→Start→Submit→Completed→Stop contre PostgreSQL réel,
disponible dans cet environnement — voir §5.5+ pour la procédure). Suite
complète (unitaire + intégration) exécutée sans régression après l'ajout.

**Non fait dans cette passe** (limites honnêtes, voir §6 d'`AGENT_HANDOFF.md`) :
aucun état de supervision partagé entre plusieurs instances/processus API
(même nature que `SchedulerService.ExecutionRegistry`, jamais un oubli) ;
aucun mapping appelant → identité précise (même limite préexistante que
`/missions`/`/agents`) ; frontend NON câblé sur `/operator/*` (hors
périmètre explicite de N+16 — l'architecture est conçue pour qu'une future
UI puisse le faire directement, aucun changement d'API ne devrait être
nécessaire).

### APEX Design Engine (N+17)

**Objectif** : transformer une demande de conception en langage naturel
en un `DesignPackage` traçable (brief, recherche, concepts, prompts
visuels, résultats visuels, validation, artefacts), pilotable sans
terminal via l'Operator (N+16) — voir `docs/AGENT_HANDOFF.md` §« Design
Engine » pour le détail complet (architecture, machine à états, honnêteté
recherche/visuel, API, scénario E2E).

**Ce qui a été ajouté**, sans dupliquer ni contourner l'existant :
- `apex/design/{states,model,synthesis,research,prompt_engine,
  visual_provider,validation,pipeline,service}.py` — pipeline réel en 7
  étapes (ANALYZE/RESEARCH/SYNTHESIZE/DESIGN/VISUALIZE/VALIDATE/PACKAGE),
  machine à états fermée (même patron qu'`apex.missions.states`).
  PREMIÈRE utilisation réelle d'`apex.artifacts` (phase 10, jusqu'ici non
  câblé) : chaque résultat durable y est persisté, adressé par contenu.
- `ResearchProvider`/`VisualGenerationProvider` — contrats ABC réels, un
  seul point de construction chacun (`get_default_provider()`), AUCUN
  backend réel connecté aujourd'hui (recherche web, génération d'image) :
  comportement de production honnête (`Null*Provider`), jamais une
  fabrication — voir §4 d'`AGENT_HANDOFF.md`.
- 1 nouvelle `Action` (`SUBMIT_DESIGN`, `apex.policy.model`) réservée à
  `Subject.HUMAN` seul — même motif que `CREATE_MISSION`.
- `OperatorService` (N+16) étendu avec `submit_design`/`resume_design`/
  `get_design_status`/`get_design_result` — même garde
  `accepts_submissions`, même journal `events`, `DesignService` optionnel
  (rétrocompatible avec tout `OperatorService` construit sans lui).
- `apex/api/routes/operator.py` (4 routes supplémentaires,
  `/api/v1/operator/designs*`), même `DataEnvelope`/authentification.
- Entrées `DesignEngine`/`VisualGenerationProvider` (STUB) dans
  `apex.interfaces.registry`.

**Vérifié réellement** : `tests/test_design_pipeline.py` (29 tests),
`tests/test_design_service.py` (10 tests), `tests/test_operator_service.py`
classe `TestDesignIntegration` (8 tests), `tests/test_api_operator.py` (4
tests dédiés + le scénario E2E imposé par la mission — « Créer le concept
d'un skatepark urbain moderne », de bout en bout contre PostgreSQL réel,
rapportant honnêtement `VISUAL_BACKEND_UNAVAILABLE`). Suite complète
(unitaire + intégration, 1793 tests hors `test_runtime_packaging.py`)
exécutée sans régression après l'ajout.

**Non fait dans cette passe** (limites honnêtes, voir §10
d'`AGENT_HANDOFF.md`) : aucune intégration `apex.memory`/RAG (exigerait
d'étendre l'allow-list HUMAN/CORE avec `WRITE_OWN_MEMORY`, un changement
de portée de sécurité hors périmètre sans décision explicite) ; aucune
Decision Trace (absente de ce dépôt, confirmé par audit, rien à
intégrer) ; ANALYZE/SYNTHESIZE/DESIGN déterministes, jamais un appel
`ModelProvider` (extension future documentée) ; VALIDATE structurel
seulement, jamais une validation par modèle de vision (aucun modèle de
vision connecté) ; frontend NON câblé sur `/operator/designs*` (même
raisonnement que N+16 — l'architecture est prête pour qu'une future UI le
fasse directement).

### Routing & Execution Observability Hardening (N+18)

**Objectif** : durcir le pipeline modèle → routing → exécution → fallback
DÉJÀ EXISTANT (`apex.models`/`apex.routing`/`apex.agent_registry`) pour
qu'il soit entièrement traçable et fondé sur des signaux réels — voir
`docs/AGENT_HANDOFF.md` §« Routing & Execution Observability Hardening »
pour le détail complet. Note de cadrage : une version antérieure de cette
mission avait mélangé APEX avec le projet Game Dev/UE5 (agents Claude/
Gemini/ChatGPT/Codex à connecter) ; retiré par l'utilisateur avant toute
implémentation — N+18 ne porte QUE sur l'architecture `ModelProvider`
existante.

**Ce qui a été ajouté**, sans dupliquer ni remplacer l'existant :
- `apex.errors.ModelErrorCategory` + `category_from_http_status()` —
  catégorie d'échec RÉELLE (code HTTP/type d'exception réseau), jamais
  devinée depuis un texte libre. `ModelProviderError` porte désormais
  `category`/`http_status`.
- `OllamaProvider`/`OpenAICompatibleProvider` classent réellement chaque
  échec (429/401/403/un champ JSON structuré `context_length_exceeded`/
  connexion impossible/autre code HTTP).
- `apex.routing.generation.GenerationAttempt.category` + réécriture
  complète de `apex.agent_registry.fallback.classify_outcome` — un
  mappage TOTAL et fermé `ModelErrorCategory` → `FallbackReason`, plus
  aucune correspondance de sous-chaîne fragile.
- Axe de score `capacity_risk` (`apex.routing.router`,
  `RouterScoringPolicy.capacity_risk_weight`) — pénalité proportionnelle à
  un ratio RÉEL d'échecs `rate_limited` déjà observés, `UNKNOWN` sans
  historique.
- `apex.agent_registry.observability` (nouveau module) — sérialise la
  décision de routage et les tentatives de fallback dans `events`
  (réutilisé) ; `AgentRegistryService.get_execution_trace()` reconstruit
  après coup « pourquoi ce modèle, quelles alternatives, pourquoi cet
  échec » — exposé via `GET /configured-agents/{agent_id}/executions/
  {execution_id}/trace`.
- `apex.routing.model.ModelPerformanceStats.last_failure_reason` +
  câblage réel de `apex.models.catalog.PerformanceHistory` (anticipé mais
  vide depuis la Phase 10) dans `build_model_catalog(config,
  performance=...)`.
- `ModelsPage.tsx` étendu avec l'historique réel par modèle
  (`usage_count`/`success_count`/`last_failure_reason`).

**Vérifié réellement** : 37 tests nouveaux (`test_models_error_
classification.py`, `test_agent_registry_fallback.py`, classe
`TestCapacityRiskAxis` dans `test_routing_router_scoring.py`, classe
`TestPerformanceWiring` dans `test_models_catalog.py`,
`test_agent_registry_observability.py`) — classification HTTP réelle
(429/401/403/500/timeout/connexion impossible/context-length structuré),
mappage total vers `FallbackReason`, `capacity_risk` UNKNOWN/REAL,
reconstruction complète d'une trace sans fuite de texte généré. Suite
complète (1830 tests, hors `test_runtime_packaging.py`) + build/tests
frontend (100 tests, `tsc -b` propre) exécutés sans régression.

**Découverte pendant l'audit final** (non introduite par cette passe,
confirmée pré-existante par isolation `git stash -u` contre le commit
N+17 déjà poussé) : 4 échecs dans `test_runtime_packaging.py`
(`TestAntiLeakVerification::test_clean_artifact_passes`,
`TestBuildRuntimeArtifact::test_migrations_never_reference_a_research_module`,
2 dans `TestWorktreeValidationBuild`) — une dette d'architecture
« imports Research fuités dans des docstrings Runtime » (`apex.metalearner`/
`apex.tasks`/`apex.taskgen`/`apex.evalrunner` cités en commentaire dans
plusieurs fichiers `apex/api/`/`apex/cli/`/une migration Alembic), jusqu'ici
masquée à chaque passe précédente (N+16/N+17) par une erreur `setUpClass`
antérieure (nouveau sous-paquet non encore committé). N+18 n'ajoutant aucun
nouveau sous-paquet, ces 4 tests s'exécutent enfin jusqu'au bout et
révèlent une dette réelle, hors périmètre de cette passe — non corrigée
ici, signalée honnêtement.

**Non fait dans cette passe** (limites honnêtes, voir §6
d'`AGENT_HANDOFF.md`) : aucun adaptateur provider supplémentaire (hors
périmètre explicite) ; `QUOTA_EXCEEDED` jamais produit (HTTP 429 seul ne
le distingue pas honnêtement de `RATE_LIMITED`) ; `get_execution_trace`
borné aux N événements les plus récents (pas d'index dédié par
`execution_id`) ; la dette Research-imports ci-dessus, pré-existante,
signalée mais non corrigée (hors périmètre).

### Décisions d'architecture importantes

- **Aucune boucle d'apprentissage n'écrit dans le Core** — invariant
  vérifié à chaque phase, jamais contourné.
- **Gouvernance de promotion** : `PolicyEngine` + décisions HUMAIN
  indépendantes dans `SkillRegistryService`/`GenomeStoreService` — pas de
  `PromotionGovernor` séparé (réévalué : le créer aujourd'hui dupliquerait
  une discipline déjà correctement centralisée dans `PolicyEngine`).
- **Sandbox jamais contournée** : `generate_mission_response` appelle
  `ModelProvider` au niveau ORCHESTRATEUR, jamais depuis un clone — un
  second chemin d'exécution de Mission, pas un raccourci autour de
  `AgentRuntime`/`Sandbox`.
- **`network="proxy"` volontairement non implémenté** — un vrai contrôle
  d'egress réseau sandboxé (destination autorisée, protocole, port, refus
  par défaut, secrets protégés, traçabilité, tests négatifs) est un
  chantier de sécurité à part entière, jamais traité comme un simple client
  HTTP (voir §7).
- **Discipline REAL/UNKNOWN partout** (`apex.obs.status.Measurement`) :
  une mesure absente reste `UNKNOWN`, jamais un 0 fabriqué — appliquée
  identiquement à `CostGainEstimator`, `SandboxResult`, `ModelCapabilityReport`.
- **Aucune nouvelle abstraction sans le test en 4 points** (composant
  existant incapable de porter la responsabilité / l'ajouter violerait son
  contrat / ≥2 consommateurs indépendants / pas de duplicata) — appliqué à
  chaque décision de cette passe, y compris pour refuser `PromotionGovernor`
  et un `SelfModelManager`.

## 3. Tests

- **1395 tests** collectés, **1383 passed, 8 failed, 4 skipped**
  (`python -m pytest -q -rA`, dernière exécution complète, après la passe
  « Autonomous Learning Trigger »). Les 8 `failed` sont EXCLUSIVEMENT dans
  les fichiers orphelins hors périmètre du launcher/packaging
  (`tests/test_launcher.py`, `tests/test_runtime_packaging.py`,
  `tests/test_windows_launcher.py`) — pré-existants, jamais causés par
  cette passe ni par aucune passe applicative ; ne jamais les « corriger »
  en modifiant ces fichiers, réservés au chantier Launcher dédié.
- Les 4 `skipped` : aucun Ollama réel joignable
  (`tests/test_missions_model_generation.py::TestRealOllamaMissionIntegration`,
  `tests/test_models_ollama.py`) et aucun endpoint OpenAI-compatible réel
  configuré (`tests/test_models_openai_compatible.py`) — structurel dans ce
  conteneur de développement (voir §7), pas un bug.
- Frontend (`cd frontend && npx tsc -b && npx vitest run`) : compilation
  TypeScript propre, **17 fichiers de test, 98 tests, 0 échec** (dernière
  exécution complète, après la passe « Autonomous Learning Trigger »).
  Aucun script `lint` n'est configuré dans `package.json` (`dev`/`build`/
  `preview`/`test`/`test:watch` seulement) — état inchangé depuis « UI
  Foundation V1 », constaté à nouveau plutôt que supposé.
- Avant de relancer la suite : `rm -f .apex/environment.json` (régénéré par
  `apex gate`, contamine un test si laissé en place).
- Aucun warning significatif au-delà d'un avertissement de dépréciation
  tiers (`StarletteDeprecationWarning`, `httpx`/`starlette.testclient`),
  sans rapport avec le code d'APEX.

## 4. Git

- Branche synchronisée vers `origin/main` lors de ce checkpoint (le
  précédent `origin/main` — `5575351`, « chore: establish APEX v0.1.2
  baseline » — est un ANCÊTRE direct de ce commit : fast-forward, aucune
  perte, aucun historique réécrit).
- Commit/hash exact : voir l'annonce faite par la session à l'utilisateur
  juste après ce commit (`git log -1`) — ce document est écrit avant le
  commit final et ne peut pas citer son propre hash sans se contredire.

## 5. Utilisation réelle — lancer APEX contre un vrai Ollama

### 5.1 Installer Ollama sur VOTRE machine

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### 5.2 Lancer Ollama

```bash
ollama serve &          # si ce n'est pas déjà un service actif
```

### 5.3 Choisir un modèle adapté à votre machine

`config.yaml` (`models.roles`) documente déjà les candidats de référence —
adaptez à votre VRAM réelle :

| VRAM disponible | Modèle recommandé |
|---|---|
| ≥ 18 Go | `qwen3-coder:30b-a3b-instruct-q4_K_M` (référence `coder_main`) |
| ~14-17 Go | `qwen3.6:27b-q4_K_M` ou `devstral-2:22b-q4_K_M` |
| ~13 Go | `gpt-oss:20b-q4_K_M` (rôle `judge`, pas pour la génération) |
| **8 Go (ex. RTX 4060 Ti)** | **`qwen2.5-coder:7b-instruct`** (rôle `fast`, résident, `vram_gb: 5.0` déclaré — ~3 Go de marge réelle) |

**Ne jamais utiliser `gemma4:12b-q4_K_M` comme modèle principal sur une carte
8 Go** : `config.yaml` le déclare à `vram_gb: 8.0`, soit la totalité de la
VRAM disponible sur ce type de carte — aucune marge pour le contexte CUDA
ni le KV-cache. Il est de plus classé `capability_class: judge`, pas
`coder` : ce n'est pas le modèle prévu pour *générer* une réponse à une
Mission, seulement pour en juger une autre indépendamment (`JudgeTier`,
`apex.judge`). `qwen2.5-coder:7b-instruct` est le bon choix sur ce type de
carte : même famille que le modèle de référence `coder_main`
(`qwen3-coder`), correctement dimensionné, et déjà déclaré comme endpoint
réel dans `config.yaml` (`models.endpoints`) — rien à ajouter.

```bash
ollama pull qwen2.5-coder:7b-instruct   # référence pour une carte 8 Go (RTX 4060 Ti et similaires)
```

### 5.4 Vérifier avec `/api/tags`

```bash
curl http://localhost:11434/api/tags
```

Le nom du modèle dans la réponse doit correspondre **exactement** (avec son
tag) à ce que vous mettrez dans `served_model` — sinon `OllamaProvider`
refusera honnêtement (`UNAVAILABLE_ON_DEV`, jamais un faux succès).

### 5.5 Configurer `ModelEndpoint`

`config.yaml` en déclare déjà 7, prêts à l'emploi si Ollama tourne en local
sur le port par défaut (section `models.endpoints`). Pour une carte 8 Go
(ex. RTX 4060 Ti) :

```yaml
{pool_id: local-gpu, runtime: ollama, url: "http://127.0.0.1:11434",
 served_model: "qwen2.5-coder:7b-instruct", max_context: 32768, vram_gb: 5.0}
```

Ajustez `served_model` si votre tag diffère, ou `url` si Ollama tourne sur
une autre machine/port de votre réseau. Les 6 autres endpoints déclarés
(30B/27B/22B/20B/12B) restent disponibles pour une machine plus dotée en
VRAM — ne les utilisez pas sur une carte 8 Go (voir §5.3).

### 5.6 Variables d'environnement (optionnelles, pour les tests automatisés)

```bash
export APEX_OLLAMA_URL="http://127.0.0.1:11434"
export APEX_OLLAMA_MODEL="qwen2.5-coder:7b-instruct"   # ajustez si votre machine porte plus de VRAM (voir §5.3)
```

Sans ces variables, le test réel se rabat sur `127.0.0.1:11434` (port par
défaut) pour décider s'il doit s'exécuter.

### 5.7 Lancer le test réel Ollama

```bash
python -m pytest tests/test_missions_model_generation.py::TestRealOllamaMissionIntegration -v
```

Ce test est **actuellement `skipped`** dans tout environnement sans Ollama
joignable. Dès que §5.1-5.6 sont faits, il s'exécute automatiquement — zéro
modification de code.

### 5.8 Lancer une première Mission APEX réelle

```python
from sqlalchemy import create_engine
from apex.config.schema import ModelEndpoint
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.missions.execution import generate_mission_response
from apex.missions.service import MissionService
from apex.policy.model import Subject

# SQLite fichier local, créé dans le répertoire courant — aucune dépendance
# externe pour un premier essai (ni Docker, ni PostgreSQL/Redis/MinIO :
# generate_mission_response() ne passe jamais par apex.api.dependencies.
# get_session_factory(), qui est la seule voie exigeant PostgreSQL).
engine = create_engine("sqlite:///./first-run.db", future=True)
Base.metadata.create_all(engine)
session_factory = build_session_factory(engine)

mission_service = MissionService(session_factory)
mission = mission_service.create(
    title="Factorielle robuste", description="première mission réelle",
    specialty="research",
    objective=(
        "Écris une fonction Python robuste qui calcule la factorielle, "
        "explique son fonctionnement, gère les entrées invalides et "
        "fournis des tests."
    ),
    created_by=Subject.HUMAN,
)

endpoint = ModelEndpoint(
    pool_id="local-gpu", runtime="ollama", url="http://127.0.0.1:11434",
    served_model="qwen2.5-coder:7b-instruct", max_context=32768,   # référence pour 8 Go VRAM (RTX 4060 Ti et similaires)
)

final = generate_mission_response(mission.id, mission_service=mission_service, endpoint=endpoint)
print(final.state)    # MissionState.REVIEW si succès, FAILED sinon
print(final.result)   # le texte généré par le VRAI modèle
```

Ce chemin passe réellement par `Mission → MissionEngine
(generate_mission_response) → ModelProvider → OllamaProvider → Ollama →
modèle → réponse`, jamais un appel Ollama manuel indépendant du reste
d'APEX : la Mission transite QUEUED → RUNNING → REVIEW/FAILED dans la base
comme n'importe quelle autre Mission, avec `PolicyEngine` appliqué
(`Action.LAUNCH_MISSION`, CORE seul) exactement comme `launch_mission`.

### 5.9 Reconnaître une vraie réponse du modèle (pas le fake de test)

- `_FakeModelProvider` n'existe QUE dans
  `tests/test_missions_model_generation.py` — jamais importé par du code de
  production. `generate_mission_response()` sans `model_provider` explicite
  utilise TOUJOURS `get_default_provider(endpoint)` → `OllamaProvider` réel.
- Le texte de `final.result` sera la rédaction de VOTRE modèle — jamais la
  fonction câblée en dur dans le double de test.
- Si rien ne répond réellement à l'URL donnée, `OllamaProvider.detect()`
  échoue honnêtement (`UNAVAILABLE_ON_DEV`) et `generate_mission_response`
  retourne `FAILED` avec une erreur explicite — jamais un succès simulé.

### 5.10 Si Ollama n'est pas accessible

- Vérifiez `curl http://localhost:11434/api/tags` répond bien.
- Si vous obtenez une erreur de connexion : `ollama serve` n'est pas lancé,
  ou tourne sur un autre port/interface — ajustez `url` dans `ModelEndpoint`
  en conséquence.
- Si `/api/tags` répond mais sans votre modèle : `ollama pull <modèle>`
  d'abord, le nom doit correspondre exactement à `served_model`.
- `generate_mission_response` retournera alors `Mission.state = FAILED`
  avec `Mission.error` expliquant précisément la cause (jamais un plantage
  silencieux) — c'est le comportement ATTENDU et déjà testé, pas un bug à
  corriger.

## 6. Architecture — flux actuels

### Mission avec un modèle réel (HORS sandbox)

```
Mission
  |
  v
MissionEngine (apex.missions.execution.generate_mission_response)
  |
  v
ModelProvider (apex.models.provider)
  |
  v
OllamaProvider (apex.models.ollama — HTTP réel, urllib stdlib)
  |
  v
Ollama (sur VOTRE machine)
  |
  v
résultat (Mission.result, état REVIEW ou FAILED)
```

### Mission via un Agent réel (passe « Agents V1 »)

```
Agent (configuré, Model Router non contourné)
  |
  v
Mission.agent_id
  |
  v
AgentRegistryService.run_execution (POST /missions/{id}/run)
  |
  v
select_model / generate_with_fallback (apex.routing — inchangé, réutilisé)
  |
  v
ModelProvider -> OllamaProvider (ou tout autre provider configuré)
  |
  v
AgentExecution (modèle/provider réellement utilisés, jamais fabriqués)
  |
  v
Mission.state = REVIEW (succès) ou FAILED (échec réel, jamais simulé)
```

Ce chemin est un troisième point d'entrée vers le Model Router (à côté de
`generate_mission_response`/`generate_mission_response_via_router`), jamais
un second moteur : mêmes primitives, mêmes garanties d'échec honnête.

### Boucle d'apprentissage (composants déjà réels, `run_experiment_cycle`)

```
EvalRun
  |
  v
WeaknessDetector  (apex.weakness — agrège les EvalRun réels d'une spécialité)
  |
  v
CurriculumStrategy / MetaLearner  (apex.curriculum / apex.metalearner)
  |
  v
TaskGenerator  (apex.taskgen — génère la prochaine tâche)
  |
  v
EvalRunner  (apex.evalrunner — exécute sous vraie sandbox)
  |
  v
Oracle / Judge  (apex.oracles / apex.judge — jugement indépendant)
  |
  v
StatTest / CostGain  (apex.stattest / apex.costgain — preuve statistique, coût réel)
  |
  v
Skill / Genome / Self-Model  (apex.skills / apex.genome / apex.selfmodel — capitalisation)
```

Ces deux flux sont aujourd'hui **séparés** : `run_experiment_cycle` utilise
`TaskGenerator` (commandes déterministes), pas encore `ModelProvider`. Les
unir (un `EvalRun` produit par une VRAIE génération de modèle, jugée par
`Oracle`, alimentant `WeaknessDetector`) est la suite logique une fois
qu'une Mission réelle contre Ollama aura été validée par l'utilisateur.

## 7. Blocages dépendant de votre machine physique

- **Toute la §5** (Ollama réel) dépend d'Ollama réellement installé et
  lancé sur VOTRE machine — ce conteneur de développement n'a structurellement
  aucun accès (politique d'egress réseau bloque `ollama.com`,
  `huggingface.co`, `github.com` — confirmé empiriquement, pas supposé).
- **`AgentRuntime → Sandbox → ModelProvider`** (un clone SANDBOXÉ appelant
  un modèle réseau) reste bloquée : la sandbox namespaced actuelle
  (`apex.sandbox.linux_namespace`) ne supporte que `network="none"` — tout
  autre mode lève `NotImplementedYet`. Raccorder un clone sandboxé à Ollama
  exigerait un vrai contrôle d'egress réseau (destination autorisée,
  protocole, port, refus par défaut, secrets protégés, traçabilité, tests
  négatifs) — un chantier de sécurité à part entière, explicitement PAS
  résolu dans cette passe. Nécessite une décision architecturale humaine
  avant de commencer.

## 8. Prochaine priorité

**Adaptive Model Router V1 (passe « Intelligence Core V1 », première
étape) est fait** — voir la sous-section dédiée en §2. `eval_quality`/
`cost` restent explicitement `PLANNED` dans le `ScoreBreakdown` du
Router : les rendre mesurables exige toujours le même changement de
périmètre pour `EvalRunner` décrit ci-dessous, non fait dans cette passe.

**Ne pas décider arbitrairement d'une nouvelle phase.** La priorité
suivante se détermine à partir du résultat de la première exécution réelle
(§5.8) :

- **Si la mission réussit** (le modèle répond correctement) : la suite
  naturelle est de faire produire à `TaskGenerator`/`run_experiment_cycle`
  des tâches dont l'EXÉCUTION passe par `ModelProvider` plutôt que par une
  commande déterministe câblée — c'est-à-dire commencer à fermer la boucle
  du §6 (unifier les deux flux), en gardant `Oracle`/`Judge` indépendants
  du modèle jugé. C'est un vrai changement de périmètre pour `EvalRunner`
  (aujourd'hui : `task.command` toujours déterministe, jamais généré) — à
  auditer précisément avant d'écrire du code, pas à supposer.
- **Si la mission échoue** (Ollama injoignable, modèle absent, etc.) :
  la priorité est de diagnostiquer précisément pourquoi avec l'utilisateur
  (§5.10) avant toute nouvelle construction — utiliser APEX prime sur
  ajouter des abstractions.
- **`network="proxy"`** (sandbox ↔ ModelProvider) reste la décision
  architecturale de sécurité en attente, à traiter uniquement sur demande
  explicite et avec les options présentées d'abord (destination autorisée,
  protocole, port, refus par défaut, secrets, traçabilité, tests négatifs
  — voir §7).

Règle de décision à conserver : **UTILISER APEX → MESURER APEX → AMÉLIORER
APEX**, plutôt que construire de nouvelles abstractions avant que les
composants existants n'aient travaillé sur de vraies données.
