# APEX — Hardware & Environment Gate (Phase 1)

**Statut de ce document : l'instrument est terminé et testé. La mesure ne l'est pas.**

Le Gate a été exécuté ici, dans un conteneur de développement Linux sans GPU,
sans Docker et sans WSL2. Ce résultat est une mesure réelle — d'une machine qui
n'est pas la vôtre. Il ne peut en aucun cas autoriser la phase 2.

---

## 1. Comment le Gate refuse de se tromper de machine

Trois mécanismes, tous couverts par des tests.

**La provenance est constatée, jamais déclarée.** `run_gate` inspecte le type
réel de la sonde. Avec autre chose qu'une `SystemProbe`, tous les statuts
concluants sont dégradés en `SIMULATED` avant écriture du rapport, et
`phase_2_authorized` vaut `false` quoi qu'il arrive. Il est donc impossible de
produire depuis un environnement de test un rapport qui prétende décrire une
machine réelle.

**L'empreinte matérielle identifie la machine.** Chaque rapport porte un
`fingerprint` calculé sur la distribution, le noyau, le CPU, la RAM, les GPU, la
version de pilote et le niveau d'isolation. Deux rapports d'empreintes
différentes décrivent deux machines : les mesures de l'une ne valent pas pour
l'autre, et les scores d'expériences ne sont comparables qu'à empreinte
constante.

**Le contrat de lecture refuse de répondre sur l'inconnu.** Une capacité non
testée vaut `null`, jamais `false`. `Environment.capability()` lève
`EnvironmentUnknown` plutôt que de renvoyer une valeur plausible. Une phase
ultérieure ne peut donc pas prendre une décision sur une non-mesure.

---

## 1bis. Les cinq statuts

| Statut | Signification | Bloque le Gate |
|---|---|---|
| `PASS` | Mesuré, conforme | non |
| `FAIL` | Mesuré, non conforme | **oui** |
| `DEGRADED` | Mesuré, fonctionne hors de l'enveloppe recommandée ou sur un repli | non |
| `UNKNOWN` | Non mesuré. Porte toujours un motif : `not_authorized`, `not_applicable`, `unmeasurable` | oui, tant que non levé ou excusé |
| `SIMULATED` | Mesure non issue d'une sonde système réelle | **oui, toujours** |

`UNKNOWN` sans motif serait indistinguable d'un oubli : le motif est obligatoire
et vérifié par test. Les trois cas n'appellent pas la même action —
`not_authorized` se lève en relançant avec un drapeau, `not_applicable` est
définitif sur cette machine, `unmeasurable` signale que la mesure est empêchée
par une contrainte délibérée (typiquement une garantie de sécurité, cf. GATE-01).

Correspondance avec `apex.obs.health.Status`, qui décrit la santé d'un
composant à l'exécution et non un contrôle d'environnement :
`PASS`↔`OK`, `DEGRADED`↔`DEGRADED`, `FAIL`↔`FAILED`. `UNKNOWN` et `SIMULATED`
n'ont pas d'équivalent : un composant en fonctionnement est toujours observable.

---

## 2. Les 20 contrôles

Résultat de l'exécution dans le conteneur de développement. La colonne
« machine cible » est ce qui reste à faire chez vous.

| # | Contrôle | Ici | Sur la machine cible |
|---|---|---|---|
| GATE-01 | Version de l'hôte Windows | `UNKNOWN` | À relever manuellement (voir §3) |
| GATE-02 | WSL2 actif | `DEGRADED` — Linux natif | À mesurer |
| GATE-03 | Distribution Linux | `PASS` — Ubuntu 24.04.4 | À mesurer |
| GATE-04 | Noyau | `PASS` — 6.18 | À mesurer |
| GATE-05 | Python | `PASS` — 3.12.3 | À mesurer |
| GATE-06 | Docker Engine et Compose | `FAIL` — absent | À mesurer |
| GATE-07 | Accès GPU depuis WSL2 | `DEGRADED` — aucun GPU | À mesurer |
| GATE-08 | Modèle exact du GPU | `UNKNOWN` | À mesurer |
| GATE-09 | VRAM réellement disponible | `UNKNOWN` | **Critique** — conditionne le catalogue |
| GATE-10 | Pilote et runtime CUDA | `UNKNOWN` | À mesurer |
| GATE-11 | Espace disque | `DEGRADED` | À mesurer |
| GATE-12 | RAM disponible | `DEGRADED` — 4 Go | À mesurer |
| GATE-13 | CPU et threads | `DEGRADED` — 1 thread | À mesurer |
| GATE-14 | Virtualisation et cgroups v2 | `FAIL` — cgroups v2 absent | **Bloquant phase 3** |
| GATE-15 | Accès réseau | `UNKNOWN` | `--allow-network-probe` |
| GATE-16 | Outils requis | `DEGRADED` — ollama absent | À mesurer |
| GATE-17 | Exécution réelle d'un conteneur | `UNKNOWN` | `--allow-container-exec` |
| GATE-18 | GPU utilisable dans un conteneur | `UNKNOWN` | `--allow-container-exec` |
| GATE-19 | État de l'isolation sandbox | `UNKNOWN` | **Détermine ce qui est permis** |
| GATE-20 | runsc — go/no-go | `UNKNOWN` | **Conditionne le Red Team** |

Statut global ici : `FAIL`. `phase_2_authorized: false`.

> **Addendum, pré-phase 3 (ADR-0012, ADR-0013).** Ce tableau reste le relevé
> historique de la Phase 1 — non modifié. Depuis :
>
> - GATE-19 a été réconcilié avec `apex.sandbox.detect.detect_capabilities()`
>   (le SEUL backend `SandboxProvider` réellement implémenté, étape 7, absent
>   de ce document à l'origine) : sur une machine avec des namespaces Linux
>   fonctionnels mais sans Docker, GATE-19 rapporte désormais `DEGRADED`
>   (`isolation_level: namespaced`, `code_execution_allowed: true`,
>   `red_team_allowed: false`), plus jamais `UNKNOWN`/`FAIL` par
>   sous-détection (ADR-0012).
> - GATE-14 reste `FAIL` sur une machine sans cgroups v2 — sa sévérité n'a
>   PAS changé — mais ne déclare plus bloquer la phase 3 : cgroups v2/KVM/
>   virtualisation imbriquée conditionnent l'infrastructure d'isolation
>   renforcée du Red Team (phase 21, ADR-0005), pas le cœur fonctionnel de
>   la phase 3 (ADR-0013).
>
> **Deux niveaux distincts, à ne jamais confondre** : le Gate d'environnement
> (`apex gate`) relève ce que CETTE machine offre à l'instant présent — un
> `FAIL` sur GATE-06/14 est honnête et n'indique aucune régression du code.
> La validation fonctionnelle de la phase 3 (Sandbox) est indépendante et
> déjà apportée par des tests d'intégration noyau réels
> (`tests/test_sandbox_security.py`, `tests/test_scheduler_service.py`) —
> un Gate global `FAIL` ne signifie jamais que le code de la phase 3 est
> cassé ou non fonctionnel.

---

## 3. Deux contrôles où la sécurité l'emporte sur la complétude

**GATE-01 — la version de Windows n'est pas lisible, et c'est correct.**
La lecture depuis WSL2 exige l'interopérabilité Windows, que nous recommandons
de désactiver : elle offre à un processus Linux un chemin direct vers
l'exécution de binaires Windows, sur une machine qui va exécuter du code généré
par un modèle. Le Gate déclare donc `UNKNOWN` et demande un relevé manuel
(`winver`). Il ne réactive pas l'interop pour satisfaire une case à cocher.
Test : `test_interop_disabled_yields_not_tested_not_a_workaround`.

**GATE-17, 18, 20 — aucun conteneur n'est démarré sans consentement.**
Ces trois contrôles exigent de tirer une image et d'exécuter un conteneur.
C'est la seule action non passive du Gate, d'où le drapeau explicite
`--allow-container-exec`. Sans lui : `UNKNOWN` avec le motif `not_authorized`, jamais une supposition.
Test : `test_no_container_is_started_without_consent`.

Quand GATE-17 s'exécute réellement, il n'utilise pas `docker run` nu : il
applique le profil durci de production (`--network none`, `--user 65534`,
`--read-only`, `--cap-drop ALL`, `--pids-limit 64`). Vérifier que Docker
fonctionne n'a aucun intérêt ; vérifier que Docker fonctionne **avec les
contraintes réelles de la sandbox** en a.

---

## 4. Isolation : ce que le Gate autorise, pas seulement ce qu'il constate

GATE-19 ne se contente pas de lister les runtimes disponibles. Il publie trois
faits exploitables par les phases suivantes :

| Niveau détecté | `code_execution_allowed` | `red_team_allowed` | Conséquence |
|---|---|---|---|
| `gvisor` | `true` | `true` | Périmètre complet |
| `docker_hardened` | `true` | **`false`** | Red Team différé (phase 21 bloquée) |
| `insufficient` | **`false`** | `false` | **Phase 3 bloquée** — pas d'exécution de code généré |

GATE-20 ajoute la nuance qui compte : **runsc déclaré ne signifie pas runsc
fonctionnel.** Les toolchains Rust et C++ sollicitent des appels système que
gVisor n'implémente pas toujours. Le verdict `GO` obtenu sur un conteneur
minimal reste partiel — `runsc_toolchain_validated: false` — et la validation
complète appartient à la phase 3.

Aucun de ces cas ne dégrade une garantie pour débloquer une phase. Une capacité
non disponible est une capacité différée.

---

## 5. Dépendances manquantes — les quatre axes

Le rapport JSON publie `missing_dependencies`, chaque entrée documentant
pourquoi, comment installer, quelle phase en dépend, quel repli existe. Un test
vérifie que les quatre axes sont renseignés pour toutes les dépendances.

| Dépendance | Phase | Repli |
|---|---|---|
| `python3.12` | 1 | aucun — dépendance dure |
| `docker-engine` (natif, pas Desktop) | 1 | aucun pour la sandbox |
| `docker-compose-plugin` | 1 | `docker run` manuel, non reproductible |
| `git`, `pyyaml` | 1 | aucun — dépendances dures |
| `nvidia-driver` (hôte Windows) | 6 | CPU seul : interactif oui, autonome non viable |
| `nvidia-container-toolkit` | 6 | Ollama sur l'hôte WSL2, moins reproductible |
| `ollama` | 6 | llama.cpp derrière la même interface `ModelProvider` |
| `runsc` (gVisor) | 21 | Docker durci ; Red Team désactivé |

---

## 6. Rapport machine-lisible

Écrit dans `.apex/environment.json`, schéma versionné (`schema_version: 1`).
Les phases suivantes le lisent par `apex.gate.contract.load_environment()` et
jamais en parsant le texte.

```json
{
  "schema_version": 1,
  "measured_on_target_machine": true,
  "gate_status": "DEGRADED",
  "phase_2_authorized": true,
  "fingerprint": "sha256:...",
  "capabilities": {
    "gpu_visible_from_wsl": true,
    "gpu_in_container": null,
    "containers_runnable": true,
    "cgroups_v2": true,
    "isolation_level": "docker_hardened",
    "red_team_allowed": false,
    "code_execution_allowed": true,
    "runsc_functional": false,
    "network_egress": true,
    "vram_total_gb": 23.9
  },
  "hardware": { "...": "..." },
  "blocking": [],
  "unresolved": [],
  "exceptions": [ { "check_id": "GATE-20", "expires_on": "2026-12-31", "active": true } ],
  "missing_dependencies": [ "..." ],
  "checks": [ "... 20 entrées avec preuves ..." ]
}
```

`gpu_in_container: null` dans cet exemple signifie **non mesuré**, pas
**indisponible**. Toute phase qui interrogerait cette capacité recevrait une
erreur explicite.

---

## 7. Exceptions : dérogations périssables

`gate.exceptions.yaml` permet d'accepter explicitement un contrôle non satisfait.
Chaque entrée exige un motif, un responsable et **une date d'expiration
obligatoire**. Passé ce délai, l'exception cesse de s'appliquer et le contrôle
redevient bloquant.

Une dérogation permanente n'est pas une dérogation : c'est un changement de
spécification silencieux. Elles sont donc conçues pour pourrir.

Point important, couvert par
`test_exception_does_not_change_the_underlying_fact` : excuser un contrôle
débloque la phase mais **ne modifie pas la capacité rapportée**. Une exception
sur GATE-19 n'active pas le Red Team, elle acte qu'on avance sans lui.

---

## 8. Protocole d'exécution sur votre machine

```bash
# 1. Diagnostic passif d'abord — rien n'est démarré, rien n'est modifié
python -m apex.cli.main gate

# 2. Puis les contrôles à effet de bord, avec consentement explicite
python -m apex.cli.main gate --allow-container-exec --allow-network-probe

# 3. Le rapport machine-lisible
cat .apex/environment.json
```

Attendez-vous à des ajustements. Les formats de sortie de `nvidia-smi`,
`docker info` et `/etc/wsl.conf` sont analysés par des expressions dont
**aucune n'a jamais rencontré la sortie réelle de ces outils sur WSL2**. Les
faux positifs de parsing sont le mode d'échec attendu, et le premier travail de
la phase 2.

Envoyez-moi `.apex/environment.json` : c'est le seul document qui permette de
clore la phase 1.

---

## 8bis. Deux machines, deux mesures

Le Gate sera exécuté sur plus d'une machine : un poste temporaire, puis la
machine cible. Trois garanties encadrent ce cas, toutes couvertes par des tests.

**L'archivage par empreinte.** `write()` produit `.apex/environment.json` (la
machine courante) *et* `.apex/environments/<empreinte>.json`. Exécuter le Gate
ailleurs n'écrase donc jamais la mesure précédente — sans quoi on perdrait la
seule preuve qui autorisait une phase.

**Le refus de rapport étranger.** `load_environment(path,
expected_fingerprint=...)` lève `EnvironmentUnknown` si le rapport vient d'une
autre machine. Un rapport reste un fichier copiable ; l'empreinte rend cette
copie inopérante plutôt que trompeuse.

**L'étiquette de machine.** `APEX_MACHINE_LABEL` permet de nommer la mesure :

```bash
APEX_MACHINE_LABEL="poste-temporaire" python3 -m apex.cli.main gate
APEX_MACHINE_LABEL="machine-cible"    python3 -m apex.cli.main gate --allow-container-exec
```

Seule la mesure de la machine cible peut autoriser une phase. Une mesure sur
poste temporaire a une autre valeur, réelle mais limitée : elle éprouve
l'instrument sur un vrai WSL2, ce que le conteneur de développement ne permet
pas.

### Python 3.14

`MIN_PYTHON` est un plancher, pas un plafond : 3.14 passe GATE-05. L'audit
statique ne trouve aucune API retirée en 3.13/3.14 dans le code
(`utcnow`, `distutils`, `imp`, `asyncio.coroutine`, `unittest.makeSuite`…), et
les annotations différées de PEP 649 sont sans effet puisque le code emploie
déjà `from __future__ import annotations`.

Cela reste un **audit statique**. Les 111 tests n'ont tourné que sur 3.12.3.
« Les tests passent sur 3.14 » est un point de validation machine cible au même
titre que les autres, pas un acquis.

---

## 9. Ce que le Gate ne couvre pas encore

**La capacité mesurée des modèles.** La règle §59 de la vision — ne jamais
supposer les performances documentaires — dépasse le matériel. Un modèle
annoncé à 18 Go peut en consommer 21 avec un contexte réel de 64 k tokens.
`ModelEndpoint` porte désormais deux champs distincts : `vram_gb` (documenté,
une hypothèse) et `measured` (VRAM réelle, tokens/s, horodatage, empreinte
matérielle). `effective_vram_gb` privilégie systématiquement la mesure, et une
mesure rattachée à une autre empreinte est considérée comme invalide.

Le remplissage de ces champs demande de charger les modèles : c'est un
**Model Capability Gate** distinct, prévu en phase 6, pas un contrôle
d'environnement. Tant qu'il n'a pas tourné, tout budget de calcul exprimé en
VRAM ou en task-runs repose sur de la documentation, et doit être traité comme
tel.
