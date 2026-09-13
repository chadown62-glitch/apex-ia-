# Comment lancer APEX

Passe « Self-Healing Launcher » (N+19, AXE B).

## Lancement normal

```
apex start
```

Enchaîne réellement :

1. **DIAGNOSTIC** — `apex doctor` (lecture seule, inchangé) : Python,
   dépendances, configuration, Docker, PostgreSQL/Redis/MinIO/Ollama, GPU,
   ports, cohérence des interfaces.
2. **AUTO-REPAIR** — `apex.doctor.repair` classe chaque problème détecté :
   - `SAFE_AUTO_FIX` : réparation déterministe et réversible, exécutée
     réellement (ex. démarrer le démon Docker, `docker compose up -d
     <service>` pour PostgreSQL/Redis/MinIO/Ollama arrêtés).
   - `RETRY_ONLY` : rien à faire, tout va bien.
   - `MANUAL_REQUIRED` : nécessite une intervention humaine (voir
     « Limitations » ci-dessous) — jamais tenté automatiquement.
   - `BLOCKED_BY_POLICY` : une réparation existerait mais la politique
     administrateur l'interdit dans ce contexte.
3. **VERIFY** — re-exécute le diagnostic après réparation. Si l'environnement
   reste `FAIL`, le lancement s'arrête ici (`exit 1`) — jamais un démarrage
   forcé sur un environnement encore cassé.
4. **GATE** — `apex gate`, JAMAIS court-circuitée (même garantie que
   `apex serve` invoqué directement). Un `FAIL`/refus arrête le lancement.
5. **START** — démarrage réel de l'API (`apex serve`, inchangé).

Un historique JSONL append-only de chaque lancement est conservé dans
`state/startup_history.jsonl` (`apex.doctor.startup_history`) : horodatage,
problèmes détectés, réparations tentées/réussies/échouées, statut final,
durée. Purement informatif — ce n'est pas encore de l'apprentissage
automatique (voir `apex.doctor.startup_history.recurring_problems`, une
télémétrie structurée, pas un modèle).

## `--dry-run`

```
apex start --dry-run
```

Exécute le DIAGNOSTIC et produit le plan de réparation (quel check, quelle
action, quelle commande serait lancée) sans RIEN exécuter réellement —
aucune commande, aucun démarrage. Utile pour prévisualiser ce qu'un
lancement réel ferait.

## `--verbose`

```
apex start --verbose
```

Affiche également les checks `PASS`/`RETRY_ONLY` (masqués par défaut, seuls
les problèmes réels sont affichés normalement).

## Logs et diagnostic

- `apex doctor` / `apex doctor --json` : le même diagnostic, isolément,
  sans réparation ni démarrage.
- `apex gate` / `apex gate --json` : le rapport Gate complet, écrit dans
  `gate-report.json` (ou `--output`).
- `state/startup_history.jsonl` : historique brut, une ligne JSON par
  lancement.

## Limitations réelles (nécessitent une intervention humaine)

Ces situations sont TOUJOURS `MANUAL_REQUIRED` — jamais une réparation
automatique, par construction :

- **GPU absent ou insuffisant** (`gpu.present`, `gpu.config_match`) — limite
  matérielle, rien à « réparer » côté logiciel.
- **Isolation Docker renforcée absente** (`docker.isolation`, `runsc`) —
  nécessite un hôte différent, jamais installé automatiquement.
- **Docker totalement absent** (`docker.present`, « docker introuvable ») —
  seul le DÉMARRAGE d'un Docker déjà installé est automatique ; l'installer
  ne l'est jamais.
- **Modèle de référence Ollama absent** (`ollama.models`) — le SERVICE
  Ollama peut être démarré automatiquement (`docker compose up -d ollama`),
  mais AUCUN modèle n'est jamais téléchargé automatiquement (les modèles de
  référence 20B+/27B+/30B+ ne sont pas supposés disponibles sur une machine
  modeste — voir `config.yaml`, la machine de référence actuelle dispose de
  `qwen2.5-coder:7b-instruct`/`gemma`-class local uniquement).
- **Placement de fichiers sur un montage Windows** (`fs.placement`),
  **espace disque insuffisant** (`fs.space`) — décision de l'administrateur,
  jamais automatisée.
- **Conflit de port** (5432/6379/9000/11434 occupés par un processus qui
  n'est pas identifiable comme APEX) — jamais un `kill` automatique d'un
  processus externe ; le launcher rapporte le conflit, à l'humain de
  décider.

## Idempotence

Relancer `apex start` sur un environnement déjà sain ne fait RIEN de
destructeur : aucune suppression de volume, aucun reset, aucun
téléchargement, aucun `kill` arbitraire — seul un service RÉELLEMENT arrêté
déclenche une réparation, et `docker compose up -d <service>` est
idempotent par construction (ne recrée rien qui tourne déjà).

## La boucle Generated-Solution (AXE A)

En parallèle du launcher, `apex.evalrunner.service.EvalRunnerService.
run_generated_solution()` connecte pour la première fois un `ModelProvider`
réel à une exécution sandboxée réelle :

```
TASK (solution_harness) -> ModelProvider.generate() -> code candidat
    -> sandbox NAMESPACED réel -> ExitCodeOracle -> EvalRun
       (model_id ET sandbox_status persistés ensemble)
```

`apex.metalearner.generated_solution_cycle.compare_generated_solutions()`
compose ce chemin sur plusieurs stratégies (`generation.temperature`,
câblée au Genome) et retient le meilleur candidat par fitness réel
(`apex.metalearner.population_fitness.compute_fitness`, réutilisé).

Un test contre un VRAI Ollama local existe (`tests/
test_evalrunner_generated_solution.py::TestRealOllamaGeneratedSolution`) —
sauté automatiquement si aucun Ollama n'est joignable sur `127.0.0.1:11434`
(ou `APEX_OLLAMA_URL`). Pour l'exécuter réellement :

```
APEX_OLLAMA_MODEL=qwen2.5-coder:7b-instruct python -m pytest \
    tests/test_evalrunner_generated_solution.py::TestRealOllamaGeneratedSolution -v
```

**Limitation réelle** : dans un environnement sans Ollama accessible
(comme le bac à sable de développement utilisé pour produire cette passe),
ce test est honnêtement SAUTÉ, jamais remplacé par une simulation présentée
comme équivalente — le reste de la boucle (sandbox, oracle, fitness,
sélection) est vérifié avec un double de test fidèle au contrat
`ModelProvider` (voir la docstring du fichier de test).
