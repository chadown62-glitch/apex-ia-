# Setup CLI — contrat public (Phase 3 : CLI + Orchestration)

Façade publique au-dessus du Setup Core (Phases 1-2). Architecture :

```
CLI (ApexSetup.Cli)
    │  parsing d'arguments, sérialisation JSON, code de sortie — AUCUNE logique métier
    ▼
Application/Orchestrator (ApexSetup.Application, ISetupApplicationService)
    │  construit les requêtes Core, mappe les résultats Core -> contrat JSON public
    ▼
Setup Core (ApexSetup.Core — Phases 1-2, INCHANGÉ par cette phase)
    │  RuntimeAdapter
    ▼
Runtime CLI (`apex version|doctor|gate --json`, sous-processus)
```

Le CLI n'est PAS la seule façade possible : un futur GUI appelle
directement `ISetupApplicationService` (voir §Frontière GUI/Core) sans
jamais passer par `ApexSetup.Cli` ni connaître les types de
`ApexSetup.Core`.

## 1. Commandes

| Commande | Modifie la machine ? | Description |
|---|---|---|
| `setup version` | non | Version du CLI Setup + version Runtime si joignable |
| `setup detect` | non | Matériel local détecté + profil RECOMMANDÉ (jamais une autorisation) |
| `setup check` | non (voir note ci-dessous) | Preflight complet, jamais d'installation déclenchée |
| `setup plan` | non | Construit et retourne le `InstallationPlan`, déterministe |
| `setup install` | oui | Orchestre le pipeline complet (Preflight→Plan→Download→Verify→Install→Configure→Validate→Commit) |
| `setup recover` | oui, seulement si nécessaire | Classifie puis répare une installation interrompue — jamais silencieusement |
| `setup validate` | non | Version/Doctor/Gate post-installation, sans réinstaller |
| `setup status` | non | État transactionnel courant, lecture pure |

**Note sur `check`/`plan`** : le check `fs.write_permission`, hérité tel
quel de `PreflightChecker`/`RealEnvironmentProbe.CanWriteTo` (Phase 1),
PROUVE la permission d'écriture empiriquement — il peut créer le
répertoire de destination s'il n'existe pas encore (puis y écrit/supprime
un fichier-sonde). C'est l'unique effet de bord possible de ces deux
commandes : au pire un répertoire vide, jamais un artefact, une
configuration, un verrou ou un état transactionnel.

### Arguments globaux (toutes commandes)

| Argument | Défaut | Description |
|---|---|---|
| `--human` | absent (JSON) | Sortie texte lisible au lieu du JSON structuré |
| `--runtime-executable <chemin>` | `apex` | Exécutable invoqué pour parler au Runtime (ex. `wsl.exe` en production, `.venv/bin/python3` en test) |
| `--runtime-arg <valeur>` | (aucun) | Répétable — argument ajouté avant `version`/`doctor`/`gate` (ex. `-- apex` pour `wsl.exe`, ou `-m apex.cli.main`) |
| `--runtime-timeout-seconds <n>` | `30` | Délai avant qu'un appel Runtime soit considéré en timeout |

### Arguments par commande

- `detect` : `--destination <chemin>` (défaut : répertoire courant, pour la mesure d'espace disque)
- `check` / `plan` / `install` : `--apex-version <version>` (obligatoire), `--destination <chemin>` (obligatoire), `--manifest <chemin>` (obligatoire), `--profile <Minimal|Standard|Performance|HighEnd>` (optionnel, sinon la recommandation détectée), `--minimum-apex-version <version>` (optionnel)
- `install` (en plus) : `--working-directory <chemin>` (défaut : un sous-répertoire du répertoire temporaire système — jamais la destination finale)
- `recover` / `status` : `--destination <chemin>` (obligatoire)

### Validation des arguments

Un argument obligatoire (`--apex-version`, `--destination`, `--manifest`,
`--runtime-executable` s'il est explicitement fourni) explicitement VIDE
(`--destination ""`) est rejeté comme `invalid_arguments` (exit 1), jamais
laissé atteindre `Path.GetFullPath`/`Process.Start` (qui lèveraient sinon
une exception non liée à un argument, mal classée en erreur interne — voir
revue adversariale Phase 3). Une valeur de chemin structurellement
invalide (trop longue, caractères interdits) levant une
`ArgumentException`/`PathTooLongException` n'importe où dans la chaîne
d'appel est également mappée à `invalid_arguments`, jamais à `internal_error` —
ces deux types d'exception .NET signalent TOUJOURS un problème d'argument,
jamais une panne interne. Une option dupliquée (`--destination A --destination B`)
retient la DERNIÈRE valeur, silencieusement — convention CLI usuelle,
verrouillée par test de régression. `--human` est TOUJOURS respecté, y
compris sur un chemin d'erreur (argument invalide, commande inconnue,
erreur interne) — le texte est alors écrit sur stderr, jamais de JSON sur
stdout dans ce mode, quel que soit le chemin emprunté.

### Format du manifeste (`--manifest`)

Un tableau JSON d'entrées `Core.Contracts.ModelManifestEntry` (voir
`setup/docs/CONTRACTS.md`) — **plus un champ `source_url` par entrée**,
absent du contrat Core (`source` y reste une étiquette de provenance,
jamais une URL — réserve explicite de la Phase 1 contre le couplage à un
fournisseur de distribution spécifique). `source_url` est LU PAR CE CLI
UNIQUEMENT (`ApplicationManifestLoader`), jamais par `ModelManifestParser`
(qui l'ignore silencieusement, policy « champs inconnus »). N'importe quel
schéma d'URL fonctionne (HTTP statique, S3 pré-signé, etc.) — Setup ne
décide toujours de rien à ce sujet.

## 2. Contrat JSON

Chaque réponse porte au minimum :

```json
{
  "schema_version": 1,
  "operation": "check",
  "success": false,
  "state": null,
  "current_step": null,
  "progress": null,
  "errors": [{"code": "...", "message": "...", "detail": "..."}],
  "warnings": [{"code": "...", "message": "..."}]
}
```

- `schema_version` : version du contrat CLI (indépendante de celle du
  Runtime) — un consommateur qui lit un `schema_version` supérieur à celui
  qu'il connaît doit refuser d'interpréter la réponse, jamais deviner.
- Les énumérations (`selected_profile`, `phase`, `state`, etc.) sont
  toujours sérialisées en **texte `snake_case`**, jamais en entier brut —
  aucune connaissance de l'ordre de déclaration d'un enum C# n'est jamais
  nécessaire côté GUI.
- `errors`/`warnings` sont toujours des tableaux structurés (`code` stable
  + `message` humain + `detail` optionnel), jamais un texte libre à
  parser.

### Exemples de réponse par commande

**`version`**
```json
{
  "setup_version": "1.0.0.0",
  "runtime": {"version": "0.1.2", "phase": 1, "phase_name": "...", "python": "3.12.3", "platform": "Linux ..."},
  "schema_version": 1, "operation": "version", "success": true, "errors": [], "warnings": []
}
```

**`detect`**
```json
{
  "os": "Ubuntu 24.04.4 LTS", "architecture": "x64", "ram_total_gb": 10.75, "cpu_threads": 4,
  "gpu": null, "vram_total_gb": null, "disk_free_gb": 30.1, "environment": "Ubuntu 24.04.4 LTS",
  "recommended_profile": "minimal",
  "profile_note": "recommandation Setup uniquement, jamais une autorisation — Gate reste seul juge...",
  "schema_version": 1, "operation": "detect", "success": true, "errors": [], "warnings": []
}
```

**`check`** — chaque entrée de `checks[]` : `name`, `passed`, `blocking`,
`detail` (les 11 checks `Core.Preflight.PreflightChecker` + un 12ᵉ ajouté
par ce CLI, `gate.phase2_authorized`, qui reflète directement — jamais
n'invente — `RuntimeGateResult.Phase2Authorized`).

**`plan`** — `plan` (le `InstallationPlan` complet, sérialisé tel quel) +
`plan_fingerprint` (`Core.Planning.PlanFingerprint.Compute`, déterministe :
même plan -> même empreinte).

**`install`** — `committed` (true seulement après que `commit` ait
réellement tourné — jamais avant), `executed_steps[]`, `failed_step`,
`failure_detail`, `rolled_back_steps[]`, `failed_rollback_steps[]`.

**`recover`** — `state_detected` (avant action), `persisted_state`
(nullable), `action_taken` (`none` | `rollback_performed` | `refused`),
`result` (état final), `detail`.

**`validate`** — `runtime_reachable`, `version`/`doctor`/`gate` (chacun
nullable si injoignable), `success` = accessible ET Doctor n'a pas mesuré
`fail` ET `gate.phase2_authorized` est vrai (jamais un nouveau seuil
inventé — reflet direct de ce que Doctor/Gate ont déjà calculé). **Jamais**
`gate.status != fail` seul : un Gate `DEGRADED` peut très bien avoir
`phase_2_authorized = false` (voir revue adversariale Phase 3) — `validate`
utilise exactement le même critère que `check`/`install`
(`Phase2Authorized`), pour qu'une même mesure Gate ne soit jamais
interprétée différemment d'une commande à l'autre.

**`status`** — `state` (voir §3), `lock_currently_held`,
`persisted_state` (nullable), `detail`.

## 3. États de cycle de vie (`state`)

7 concepts demandés, réalisés avec 6 valeurs distinctes (voir
`Contracts.SetupLifecycleState`) :

| Valeur | Signification |
|---|---|
| `no_installation` | rien à cette destination |
| `in_progress` | une exécution détient RÉELLEMENT le verrou en ce moment |
| `completed` | marqueur de commit présent, cohérent |
| `recovery_needed` | installation interrompue (personne ne tient plus le verrou) — répond À LA FOIS à « interrompue » et « recovery nécessaire » : sur le disque, c'est exactement la même situation observable |
| `rollback_performed` | uniquement en sortie de `recover`, jamais de `status` (lecture pure, aucune action) |
| `inconsistent` | incohérence entre marqueur de commit et état persisté — intervention manuelle requise, jamais une supposition |

`in_progress` vs `recovery_needed` se distinguent en sondant
`InstallationLock` (voir `Core.Pipeline.InstallationLock`) : le même état
persisté `InterruptedNeedsRollback` de `RecoveryClassifier` peut signifier
soit l'un soit l'autre selon qu'une exécution tient encore le verrou.

## 4. Codes de sortie

| Code | Nom | Sens |
|---|---|---|
| 0 | Success | |
| 1 | InvalidArguments | argument manquant/invalide, commande inconnue |
| 2 | PreflightBlocking | un check Preflight bloquant (hors Runtime/Gate) a échoué |
| 3 | RuntimeUnavailable | Runtime injoignable ou contrat inexploitable |
| 4 | GateBlocking | Gate n'autorise pas la phase suivante (`phase_2_authorized=false`) |
| 5 | DownloadFailed | échec de l'étape `download` |
| 6 | IntegrityFailed | échec de l'étape `integrity_verification` |
| 7 | InstallationFailed | échec de `installation`/`configuration`/`commit` |
| 8 | RollbackFailed | au moins une étape du rollback a elle-même échoué |
| 9 | RecoveryRequired | `status` détecte une installation interrompue non encore réparée |
| 10 | ValidationFailed | Doctor/Gate mesurent un `fail` réel (Runtime joignable) |
| 11 | ConcurrencyRefused | une autre exécution tient déjà le verrou à cette destination |
| 12 | InconsistentState | état transactionnel corrompu — intervention manuelle requise |
| 99 | InternalError | exception non gérée (jamais une trace de pile exposée en sortie) |

2, 11 et 12 sont des AJOUTS explicites au-delà du minimum demandé par la
spécification (« preflight bloquant », « recovery requis », « erreur
interne » couvraient déjà le socle) — jamais une modification du minimum.

L'ordre de priorité quand plusieurs conditions se chevauchent pendant
`install`/`check` : erreur de paramètres > Runtime injoignable > Gate
bloquant > autre check Preflight bloquant > échec d'étape du pipeline
(rollback en échec prioritaire sur la cause d'origine).

## 5. Frontière GUI/Core

`ISetupApplicationService` (`ApexSetup.Application`) est la SEULE
interface qu'un futur GUI doit connaître — ses 8 méthodes correspondent
exactement aux 8 commandes CLI. Remplacer `ApexSetup.Cli` par un GUI ne
nécessite de modifier NI cette interface NI aucun fichier de
`ApexSetup.Core` : `DownloadStage`, `InstallationStage`,
`ConfigurationStage`, `ValidationStage`, le rollback,
`InstallationRecovery`, `PathSafety`, `Integrity`, `InstallationLock`
restent des détails d'implémentation invisibles derrière
`SetupApplicationService`.

## 6. Sécurité

Aucune commande shell n'est jamais construite par ce dépôt Setup (ni CLI
ni Application ni Core). Le seul sous-processus lancé est celui du
Runtime, exclusivement via `RuntimeProcessRunner`
(`ProcessStartInfo.ArgumentList`, un token par argument — jamais une
ligne de commande assemblée par concaténation). Les arguments CLI
(`--destination`, `--manifest`, `--runtime-executable`, etc.) sont lus un
par un (`CliArguments`) et transmis tels quels aux services C# — jamais
réinterprétés, jamais passés à un shell. Voir
`ApexSetup.Cli.Tests.CliSecurityTests` pour la démonstration contre le
vrai binaire (métacaractères shell, substitution de commande,
traversée de chemin — tous traités comme du texte opaque).

## 7. Logging

Aucune infrastructure de logs n'existe dans `ApexSetup.Application`/`Cli`
à ce stade (même constat que la revue adversariale Phase 2 pour
`ApexSetup.Core`) — il n'y a donc structurellement rien qui puisse écrire
un secret dans un journal. Les seuls textes émis sont les réponses JSON
elles-mêmes (`errors[].detail` inclut au plus un message d'exception .NET
ou un détail de check Preflight — jamais une variable d'environnement, un
argument `--runtime-arg`, ou un en-tête HTTP).
