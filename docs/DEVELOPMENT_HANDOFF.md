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

- **931 tests** collectés, **0 échec** (`python -m pytest -q`, exit 0).
- Quelques dizaines de tests `skipped` : PostgreSQL réel non configuré dans
  cet environnement, et le SEUL test d'intégration contre un vrai Ollama
  (`tests/test_missions_model_generation.py::TestRealOllamaMissionIntegration`)
  — sauté ici car aucun Ollama n'est joignable dans ce conteneur de
  développement (voir §7, c'est structurel, pas un bug).
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
| ~8 Go | `gemma4:12b-q4_K_M` |
| ~5 Go | `qwen3-coder:8b-q4_K_M` (rôle `fast`, résident) |

```bash
ollama pull qwen3-coder:30b-a3b-instruct-q4_K_M   # ou le modèle choisi ci-dessus
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
sur le port par défaut (section `models.endpoints`) :

```yaml
{pool_id: local-gpu, runtime: ollama, url: "http://127.0.0.1:11434",
 served_model: "qwen3-coder:30b-a3b-instruct-q4_K_M", max_context: 65536, vram_gb: 18.0}
```

Ajustez `served_model` si votre tag diffère, ou `url` si Ollama tourne sur
une autre machine/port de votre réseau.

### 5.6 Variables d'environnement (optionnelles, pour les tests automatisés)

```bash
export APEX_OLLAMA_URL="http://127.0.0.1:11434"
export APEX_OLLAMA_MODEL="qwen3-coder:30b-a3b-instruct-q4_K_M"
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

# SQLite fichier local — aucune dépendance externe pour un premier essai
# (PostgreSQL reste l'option de production, voir apex/db/).
engine = create_engine("sqlite:////home/apex/apex-data/first-run.db", future=True)
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
    served_model="qwen3-coder:30b-a3b-instruct-q4_K_M", max_context=65536,
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
