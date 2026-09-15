# APEX — Démarrage / Validation

Ce document répond à une seule question : **« je reviens sur ma machine,
comment je sais si APEX est prêt, et si non, comment je corrige ? »**

```
commande unique
    ↓
diagnostic (environnement, infrastructure, sandbox, config)
    ↓
corrections sûres appliquées automatiquement (liste blanche fermée)
    ↓
validation (doctor + gate + tests ciblés)
    ↓
READY / READY WITH WARNINGS / BLOCKED / FAILED
```

## La commande unique

```bash
cd ~/apex-ia-
./scripts/apex-preflight.sh
```

Elle :

1. résout `.env`/l'interpréteur Python (`.venv` ou `python3` système) ;
2. démarre l'infrastructure Docker (`docker compose up -d --wait`, y
   compris les correctifs d'idempotence N+23/N+24 déjà en place) ;
3. lance `apex doctor`, **applique les safe-fixes** (liste blanche fermée,
   voir plus bas), puis relit `apex doctor` et consulte `apex gate` ;
4. lance un sous-ensemble rapide de tests (config/sandbox/recovery/
   doctor/taskloop/learning — aucune dépendance Docker/Postgres réelle) ;
5. affiche un verdict final : `READY`, `READY WITH WARNINGS`, `BLOCKED`,
   ou `FAILED`, avec la liste exacte de ce qui bloque le cas échéant.

Codes de sortie : `0` = READY(-WARN), `1` = BLOCKED, `2` = FAILED (erreur
de l'orchestration elle-même, pas de l'environnement).

### Options

| Option | Effet |
|---|---|
| `--dry-run` | n'applique AUCUN safe-fix — affiche seulement le plan |
| `--full` | suite de tests complète + sonde sandbox coûteuse (`apex doctor --full`) |
| `--skip-tests` | diagnostic + safe-fixes seuls, aucun test |

## Ce qui est corrigé automatiquement (safe-fixes)

Liste blanche **fermée**, définie dans `apex/doctor/repair.py` — rien
d'autre n'est jamais exécuté automatiquement :

- création d'un répertoire `APEX_DATA_ROOT` manquant (`mkdir -p`, jamais
  destructeur) ;
- démarrage du service Docker (`service docker start`) si le démon est
  injoignable ;
- `docker compose up -d <service>` pour UN service précis (postgres,
  redis, minio, ollama) qui a une définition Compose mais n'est pas
  joignable — jamais `docker compose up -d` nu, qui recréerait tout.

## Ce qui nécessite une intervention humaine

Tout le reste — jamais deviné, jamais forcé :

- absence de GPU / VRAM insuffisante / pilote NVIDIA ;
- Docker Desktop au lieu de Docker Engine natif (isolation limitée) ;
- `runsc`/gVisor absent (plafond d'isolation Docker durci, pas gVisor —
  acceptable pour du code généré ordinaire, bloquant seulement pour un
  futur Red Team) ;
- modèles Ollama absents (`ollama pull <modèle>`) ;
- `.env` absent ou mal configuré (`cp .env.example .env` puis éditer) ;
- cgroups v2 absent (mise à jour WSL2, `wsl --update`) ;
- espace disque insuffisant.

Chaque cas apparaît dans le rapport `apex doctor` avec sa remédiation
exacte.

## Sandbox — quel backend, et pourquoi

`apex doctor --full` (ou `./scripts/apex-preflight.sh --full`) affiche
désormais (N+26) **quel backend d'isolation serait réellement choisi et
pourquoi** :

1. `LinuxNamespaceSandbox` (NAMESPACED) si les namespaces Linux + cgroups
   v1 + abaissement de privilège réel + coupure réseau sont TOUS
   vérifiés empiriquement sur cet hôte — préféré (aucune dépendance à un
   démon tiers).
2. Sinon `ContainerSandbox` (CONTAINERIZED) SEULEMENT si Docker est
   réellement joignable ET si une sonde de sécurité réelle (réseau coupé,
   utilisateur non-root, racine en lecture seule, vérifiés sur un vrai
   conteneur jetable) réussit — jamais choisi au seul motif que `docker`
   est installé.
3. Sinon : refus explicite. Aucune mission ne s'exécute en sandbox tant
   qu'aucun des deux n'est réellement fonctionnel — jamais une exécution
   dégradée silencieuse.

C'est exactement le cas d'un déploiement WSL2 où `unshare`/cgroups v1 sont
refusés par la politique de virtualisation de l'hôte mais où Docker
fonctionne : `ContainerSandbox` (N+25) prend le relais automatiquement.

## Recovery / retry / reassignment (N+26)

Une mission lancée avec un `pool` (voir `apex.missions.execution.
launch_mission`, paramètre optionnel — le comportement historique à une
seule tentative reste inchangé si omis) bénéficie d'une politique de
récupération bornée (`apex.recovery.policy.decide`) :

| Catégorie d'échec | Décision |
|---|---|
| Isolation sandbox indisponible | BLOCK immédiat (jamais de retry — structurel) |
| Timeout | RETRY borné |
| Annulation explicite | BLOCK, jamais relancé automatiquement |
| Candidat/commande invalide | RETRY avec stratégie différente |
| Échec d'exécution générique | RETRY borné |
| Solution jugée incorrecte | RETRY (feedback du judge), puis ESCALATE si le budget est épuisé |
| Provider modèle en échec/injoignable | REASSIGN si une alternative réelle existe, sinon RETRY/BLOCK |
| Ressources épuisées (OOM, etc.) | RETRY une seule fois (budget plus strict), puis BLOCK |

Chaque tentative est tracée (`mission.started`, `execution.started`,
`sandbox.*`, `recovery.decided`, `retry.started`/`reassignment.decided`,
`mission.review`/`mission.failed`) — jamais un événement fabriqué pour une
étape qui n'a pas eu lieu. `apex.config.schema.RecoveryPolicy` (section
`recovery:` de `config.yaml`) borne le nombre de tentatives —
`max_attempts: 3` par défaut, aucune boucle infinie possible.

## Autonomous Task Loop (N+27) — borné, jamais niveau 3

`apex.taskloop.loop.run_task_loop` exécute RÉELLEMENT une `Task` du
TaskBank (portant un `solution_harness`) :

```
OBJECTIVE -> PLAN -> PROVIDER SELECTION -> GENERATE CANDIDATE -> EXECUTE
    -> TEST -> JUDGE -> COMPLETE (succès)
                     -> ANALYZE -> STRATEGY_UPDATE -> RETRY/REASSIGN/BLOCK
```

En composant EXCLUSIVEMENT des primitives déjà réelles — jamais une
réimplémentation :

- `apex.routing.router.select_model` pour la sélection de provider ;
- `apex.models.provider.ModelProvider.generate` pour la génération ;
- `apex.agents.runtime.AgentRuntime.spawn`/`execute_in_sandbox` pour
  l'exécution sandboxée (aucun contournement) ;
- `apex.judge.get_default_judge_tier()` (Tier0Judge/`ExitCodeOracle`) pour
  le jugement — **jamais câblé nulle part avant cette mission** ;
- `apex.recovery.decide` (N+26) pour CHAQUE décision RETRY/REASSIGN/BLOCK/
  ESCALATE — ce module ne décide jamais lui-même ;
- `apex.metalearner.experience.build_experience` — chaque tentative jugée
  produit une `Experience` réelle, exploitable par N+28.

Garde-fous RÉELLEMENT appliqués, jamais contournables : `check_stop`
(annulation, budget de tentatives, durée maximale — N+26), un nouveau
`detect_no_progress` (arrêt si la même catégorie d'échec/provider/stratégie
se répète, avant même d'épuiser le budget de tentatives), et
`TaskLimits.max_retries`/`max_candidates` (bornes distinctes de
`max_attempts` — combien de fois retenter le MÊME provider, combien de
providers distincts réassigner). `TaskLoopRegistry` (en mémoire,
par-processus) refuse un second `run_task_loop` concurrent pour la même
tâche (`TaskAlreadyRunning`) et borne le nombre de TaskLoop simultanés
(`TaskLoopAtCapacity`).

`TaskExecutionMode` (`MANUAL`/`ASSISTED`/`BOUNDED`) est un vocabulaire
FERMÉ qui n'inclut structurellement AUCUN mode « autonome sans limite » —
`MANUAL`/`ASSISTED` forcent une seule tentative, seul `BOUNDED` active la
boucle de récupération.

**Limites honnêtes** : aucune table « TaskRun » persistée — un process tué
ne reprend jamais une exécution interrompue, un nouvel appel est toujours
une tentative authentiquement nouvelle. `MANUAL`/`ASSISTED` restent
mécaniquement identiques (aucune confirmation humaine intermédiaire réelle
n'existe encore). `FailureCategory.INVALID_CANDIDATE`/`INCORRECT_SOLUTION`
(ce dernier atteint seulement si sandbox RÉUSSIE mais Judge en désaccord)
restent inatteignables par preuve avec les mécanismes réels de ce dépôt
aujourd'hui (voir `apex/interfaces/registry.py`, entrée `TaskLoop`, pour le
détail exact) — câblées et testées, jamais fabriquées.

## Recommendation Engine (N+28) — recommande, ne décide ni n'exécute jamais

`apex.metalearner.recommendation_engine.recommend_for_task(task_id, *,
session_factory)` compare une tâche cible à l'historique réel des
`Experience` (produites par le TaskLoop N+27) et retourne un
`RecommendationReport` :

1. **Pertinence** — deux niveaux réellement calculables (`Experience` ne
   porte ni objectif ni contexte ni mode d'exécution) : `EXACT_TASK` (même
   tâche) et `SAME_SPECIALTY` (autre tâche, même spécialité). Tout le reste
   est exclu, jamais compté.
2. **Statistiques** — taux de succès, catégorie d'échec dominante,
   comparaison par provider (`model_id`) et par stratégie (`strategy_id`) —
   un écart de moins de 15 points n'est jamais présenté comme un signal ;
   une valeur manquante (`model_id is None`) est comptée séparément,
   jamais fondue dans un faux groupe « inconnu ».
3. **Confiance** — `INSUFFICIENT_DATA`/`LOW`/`MEDIUM`/`HIGH`, dérivée
   UNIQUEMENT de la taille de l'échantillon (≥10 HIGH, ≥5 MEDIUM, ≥1 LOW).
4. **Abstention explicite** — `AbstentionReason.UNKNOWN_TASK`/
   `NO_COMPARABLE_EXPERIENCE`/`SAMPLE_TOO_SMALL`/`CONTRADICTORY_RESULTS` :
   « aucune recommandation fiable » porte toujours une raison précise,
   jamais un simple résultat vide.

**Rien dans ce dépôt ne consomme cette sortie pour modifier automatiquement
une politique, relancer le TaskLoop, choisir un provider à la place de
`select_model`, ou exécuter quoi que ce soit** — vérifié par test (aucun
import d'`apex.taskloop`/`apex.recovery`/`apex.sandbox`/`apex.agents.
runtime`/`apex.models.provider` dans le moteur ; le Protocol
`RecommendationSource` n'expose structurellement aucune méthode `decide`/
`apply`/`enforce`/`execute`/`run_task`). `apex.metalearner.recommendation.
recommend_from_experience` (N+26, inchangée) reste disponible pour une
analyse mono-tâche plus simple ; le nouveau moteur (N+28) est la voie
recommandée pour comparer une tâche à l'historique plus large de sa
spécialité.

## Limites connues

- La validation Docker **réelle** (namespaces indisponibles, Docker
  fonctionnel, `ContainerSandbox` réellement exercé) dépend de la machine
  physique — jamais affirmée depuis un environnement où Docker est
  injoignable. `apex doctor --full`/`./scripts/apex-preflight.sh --full`
  le disent honnêtement (SKIP/FAIL selon le cas), jamais un PASS fabriqué.
- `apex-preflight.sh` ne démarre jamais réellement APEX — c'est
  volontaire (`./scripts/apex-launch.sh serve` ou `apex start` restent des
  actions explicites et séparées).
- Le sous-ensemble de tests « quick » ne couvre pas tout — utiliser
  `--full` avant une validation de mise en production.

## Procédures manuelles restantes

- `ollama pull <modèle>` pour tout modèle de référence manquant (jamais
  automatisé — téléchargement volumineux, décision explicite requise).
- Réparation matérielle/pilote (GPU, cgroups v2, Docker Desktop vs
  Engine) — toujours signalée, jamais corrigée automatiquement.
- `cp .env.example .env` puis édition manuelle des valeurs (secrets,
  chemins spécifiques à la machine).
