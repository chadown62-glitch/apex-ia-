# APEX — Handoff de développement

Ce document est le point de reprise pour la prochaine session. Il résume
l'état réel du dépôt à ce checkpoint — jamais un état espéré ou visé.

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
  HORS sandbox (voir §5) : `apex.missions.execution.generate_mission_response`.

Rien n'est prétendu `IMPLEMENTED` sans preuve empirique. Tout ce qui reste
`PLANNED`/déféré est documenté avec sa raison exacte dans le registre
(`apex/interfaces/registry.py`) — notamment `network="proxy"` (egress
sandboxé, §5) et `PromotionGovernor` (réévalué, toujours non justifié par
le test en 4 points).

## 2. Tests

- **931 tests** collectés, **0 échec** (`python -m pytest -q`, exit 0).
- Quelques dizaines de tests `skipped` : PostgreSQL réel non configuré dans
  cet environnement, et le SEUL test d'intégration contre un vrai Ollama
  (`tests/test_missions_model_generation.py::TestRealOllamaMissionIntegration`)
  — sauté ici car aucun Ollama n'est joignable dans ce conteneur de
  développement (voir §5, c'est structurel, pas un bug).
- Avant de relancer la suite : `rm -f .apex/environment.json` (régénéré par
  `apex gate`, contamine un test si laissé en place).

## 3. Git

- Branche : `claude/apex-official-development-4kao8t`
- Commit/hash du checkpoint : voir la sortie de `git log -1` juste après ce
  commit — ce document est écrit AVANT le commit, donc il ne peut pas
  citer son propre hash sans se contredire lui-même. La session qui a créé
  ce commit l'a annoncé explicitement à l'utilisateur (branche, hash,
  message, résultat du push, `git status` final).

## 4. Utilisation réelle — lancer APEX contre un vrai Ollama

### 4.1 Installer Ollama sur VOTRE machine

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### 4.2 Lancer Ollama

```bash
ollama serve &          # si ce n'est pas déjà un service actif
```

### 4.3 Choisir un modèle adapté à votre machine

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

### 4.4 Vérifier avec `/api/tags`

```bash
curl http://localhost:11434/api/tags
```

Le nom du modèle dans la réponse doit correspondre **exactement** (avec son
tag) à ce que vous mettrez dans `served_model` — sinon `OllamaProvider`
refusera honnêtement (`UNAVAILABLE_ON_DEV`, jamais un faux succès).

### 4.5 Configurer `ModelEndpoint`

`config.yaml` en déclare déjà 7, prêts à l'emploi si Ollama tourne en local
sur le port par défaut (section `models.endpoints`) :

```yaml
{pool_id: local-gpu, runtime: ollama, url: "http://127.0.0.1:11434",
 served_model: "qwen3-coder:30b-a3b-instruct-q4_K_M", max_context: 65536, vram_gb: 18.0}
```

Ajustez `served_model` si votre tag diffère, ou `url` si Ollama tourne sur
une autre machine/port de votre réseau.

### 4.6 Variables d'environnement (optionnelles, pour les tests automatisés)

```bash
export APEX_OLLAMA_URL="http://127.0.0.1:11434"
export APEX_OLLAMA_MODEL="qwen3-coder:30b-a3b-instruct-q4_K_M"
```

Sans ces variables, le test réel se rabat sur `127.0.0.1:11434` (port par
défaut) pour décider s'il doit s'exécuter.

### 4.7 Lancer le test réel Ollama

```bash
python -m pytest tests/test_missions_model_generation.py::TestRealOllamaMissionIntegration -v
```

Ce test est **actuellement `skipped`** dans tout environnement sans Ollama
joignable. Dès que §4.1-4.6 sont faits, il s'exécute automatiquement — zéro
modification de code.

### 4.8 Lancer une première Mission APEX réelle

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

### 4.9 Reconnaître une vraie réponse du modèle (pas le fake de test)

- `_FakeModelProvider` n'existe QUE dans
  `tests/test_missions_model_generation.py` — jamais importé par du code de
  production. `generate_mission_response()` sans `model_provider` explicite
  utilise TOUJOURS `get_default_provider(endpoint)` → `OllamaProvider` réel.
- Le texte de `final.result` sera la rédaction de VOTRE modèle — jamais la
  fonction câblée en dur dans le double de test.
- Si rien ne répond réellement à l'URL donnée, `OllamaProvider.detect()`
  échoue honnêtement (`UNAVAILABLE_ON_DEV`) et `generate_mission_response`
  retourne `FAILED` avec une erreur explicite — jamais un succès simulé.

### 4.10 Si Ollama n'est pas accessible

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

## 5. Blocages dépendant de votre machine physique

- **Tout Étape A/B/D** (ci-dessus) dépend d'Ollama réellement installé et
  lancé sur VOTRE machine — ce conteneur de développement n'a structurellement
  aucun accès (politique d'egress réseau bloque `ollama.com`,
  `huggingface.co`, `github.com` — confirmé empiriquement, pas supposé).
- **Étape C — `AgentRuntime → Sandbox → ModelProvider`** (un clone
  SANDBOXÉ appelant un modèle réseau) reste bloquée : la sandbox namespaced
  actuelle (`apex.sandbox.linux_namespace`) ne supporte que
  `network="none"` — tout autre mode lève `NotImplementedYet`. Raccorder un
  clone sandboxé à Ollama exigerait un vrai contrôle d'egress réseau
  (destination autorisée, protocole, port, refus par défaut, secrets
  protégés, traçabilité, tests négatifs) — un chantier de sécurité à part
  entière, explicitement PAS résolu dans cette passe (directive : « ne
  l'implémente pas comme un simple client HTTP »). Nécessite une décision
  architecturale humaine avant de commencer.

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

## 7. Prochaine priorité

**Ne pas décider arbitrairement d'une nouvelle phase.** La priorité
suivante se détermine à partir du résultat de la première exécution réelle
(§4.8) :

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
  (§4.10) avant toute nouvelle construction — utiliser APEX prime sur
  ajouter des abstractions.
- **Étape C** (sandbox ↔ ModelProvider, `network="proxy"`) reste la
  décision architecturale de sécurité en attente, à traiter uniquement sur
  demande explicite et avec les options présentées d'abord (destination
  autorisée, protocole, port, refus par défaut, secrets, traçabilité, tests
  négatifs — voir §5).

Règle de décision à conserver : **UTILISER APEX → MESURER APEX → AMÉLIORER
APEX**, plutôt que construire de nouvelles abstractions avant que les
composants existants n'aient travaillé sur de vraies données.
