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

- **1125 tests** collectés, **1122 passed, 3 skipped, 0 failed, 0 errors**
  (`python -m pytest -q -rA`, exit 0 — dernière exécution complète, après
  la passe « Agents V1 »).
- Les 3 `skipped` : aucun Ollama réel joignable
  (`tests/test_missions_model_generation.py::TestRealOllamaMissionIntegration`,
  `tests/test_models_ollama.py`) et aucun endpoint OpenAI-compatible réel
  configuré (`tests/test_models_openai_compatible.py`) — structurel dans ce
  conteneur de développement (voir §7), pas un bug.
- Frontend (`cd frontend && npx tsc -b && npx vitest run`) : compilation
  TypeScript propre, **13 fichiers de test, 71 tests, 0 échec** (dernière
  exécution complète, après la passe « UI Foundation V1 »).
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
