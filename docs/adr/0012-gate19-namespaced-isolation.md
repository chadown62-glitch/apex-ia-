# ADR-0012 — GATE-19 reconnaît NAMESPACED comme un niveau d'isolation distinct

**Statut :** Acceptée — pré-phase 3 (réconciliation GATE-19 ↔ SandboxProvider)

## Contexte

`docs/PHASE_1_GATE.md` et `apex/gate/checks.py::gate_19_isolation_state` ont
été écrits en Phase 1, avant que `SandboxProvider` n'existe. Le modèle retenu
alors n'envisageait que deux mécanismes réels : conteneurisation Docker avec
runsc (`gvisor`) ou sans (`docker_hardened`) ; toute absence de Docker ou de
cgroups v2 au chemin unifié standard (`/sys/fs/cgroup/cgroup.controllers`)
concluait `insufficient` (`FAIL`, `code_execution_allowed=false`) — ou, si
Docker était totalement absent, `UNKNOWN` sans même consulter d'autre
mécanisme.

À l'étape 7, `apex.sandbox` a introduit un troisième mécanisme, réel et
indépendant de Docker : namespaces Linux (mount/pid/net/ipc/uts) + cgroups
**v1** (cpu/memory/pids) + abaissement de privilège réel (setpriv) + réseau
coupé, niveau nommé `IsolationLevel.NAMESPACED`
(`apex/sandbox/model.py`) et détecté empiriquement par
`apex.sandbox.detect.detect_capabilities()`. C'est le SEUL backend
`SandboxProvider` réellement implémenté, et `apex.missions.execution.
launch_mission` l'utilise en production depuis l'étape 7 — `docs/PHASE_1_GATE.md`
ne le connaît pas : GATE-19 sous-déclarait une isolation déjà réelle, testée
et utilisée. `apex/sandbox/detect.py` le documentait déjà explicitement :
« cette détection est INDÉPENDANTE de `apex.gate` : le Gate cible un
déploiement WSL2/Docker et ne teste que cgroups v2 au chemin standard ». Cette
ADR reconcilie enfin les deux, sans modifier ni l'un ni l'autre mécanisme.

## Décision

**GATE-19 consulte désormais `apex.sandbox.detect.detect_capabilities()`**
(injectable via `GateContext.detect_sandbox_capabilities`, comme `ctx.probe`)
quand le chemin Docker n'atteint ni `gvisor` ni `docker_hardened` — jamais à
la place de la mesure Docker, seulement en complément, avant de conclure
`insufficient`.

**`NAMESPACED` n'est JAMAIS promu au niveau `docker_hardened`/`gvisor`.** Il
n'offre ni seccomp-bpf ni jail complet du filesystem (pas de `pivot_root`)
— voir `apex/sandbox/model.py`. C'est un quatrième niveau, distinct :

| Niveau | `isolation_level` | `code_execution_allowed` | `red_team_allowed` | Statut GATE-19 |
|---|---|---|---|---|
| Aucun mécanisme fonctionnel | `insufficient` | `false` | `false` | `FAIL`, bloque phase 3 |
| Namespaces Linux + cgroups v1 (`NAMESPACED`) | `namespaced` | `true` | `false` | `DEGRADED`, bloque phase 21 (Red Team) |
| Docker durci, cgroups v2 | `docker_hardened` | `true` | `false` | `DEGRADED`, bloque phase 21 |
| Docker + runsc (gVisor) | `gvisor` | `true` | `true` | `PASS` |

**`code_execution_allowed=true` pour NAMESPACED n'est pas une décision
nouvelle** : c'est la politique déjà opérante de `SandboxCapabilityReport.
is_available` (« le seuil minimal pour autoriser une exécution réelle : au
moins NAMESPACED »), déjà appliquée en production par
`apex.missions.execution.launch_mission` depuis l'étape 7. GATE-19 arrête de
la sous-déclarer, il ne l'accorde pas.

**`red_team_allowed=false` pour NAMESPACED n'est pas non plus une décision
nouvelle** : NAMESPACED est strictement plus faible que `docker_hardened`
(qui refuse déjà le Red Team) — ni seccomp ni jail filesystem complet, deux
garanties que `docker_hardened` possède et que NAMESPACED n'a pas.

**GATE-14 (« Virtualisation et cgroups ») reste INCHANGÉ.** Il mesure une
question distincte et toujours réelle — cgroups v2 au chemin unifié standard,
KVM, virtualisation imbriquée — pertinente pour l'infrastructure de
conteneurisation/durcissement (GATE-17/18/20, phase 21), pas pour la capacité
de `SandboxProvider` lui-même. Son `FAIL` en l'absence de cgroups v2 reste
honnête et n'est pas rétrogradé pour obtenir un `PASS`.

## Conséquence

Sur une machine avec namespaces Linux fonctionnels mais sans Docker (ce
dépôt, à la date de cette ADR) : GATE-19 passe de `FAIL`/`UNKNOWN` à
`DEGRADED`. **Le Gate global reste `FAIL`** tant que GATE-06 (Docker) et
GATE-14 (cgroups v2) restent absents sur cette machine — cette ADR ne
change ni GATE-06 ni GATE-14, et ne modifie donc pas le verdict global
d'autorisation de phase, seulement l'honnêteté de GATE-19 lui-même.

## Écarté

Assimiler `NAMESPACED` à `docker_hardened` pour simplifier le modèle à trois
niveaux existant : rejeté — masquerait l'absence réelle de seccomp/jail
filesystem derrière un label partagé avec un mécanisme plus fort.

Modifier GATE-14 pour qu'il accepte cgroups v1 comme suffisant : rejeté pour
cette ADR — GATE-14 teste une capacité distincte (v2 unifié, prérequis de
l'infrastructure Docker/gVisor de durcissement), pas la capacité de
`SandboxProvider` ; le fusionner avec GATE-19 aurait été un changement
d'architecture plus large que nécessaire pour cette réconciliation.

Faire de GATE-19 la seule autorité bloquante de la phase 3, indépendamment de
GATE-06/14 : rejeté — chaque Gate reste responsable de son propre fait,
l'agrégation (`EnvironmentReport.blocking`) n'a pas été modifiée par cette ADR.
