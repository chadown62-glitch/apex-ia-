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
