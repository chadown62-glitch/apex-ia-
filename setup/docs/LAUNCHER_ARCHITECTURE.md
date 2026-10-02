# Launcher de production — Architecture (N+15)

> **État au 02/10/2026 (audit launcher + app/web) : NON LIVRÉ, NE PAS ÉTENDRE.** L'installateur Windows
> ne contient que le Panel `APEX.exe` (`ApexSetup.Dashboard`) ; c'est lui qui démarre, arrête et ouvre
> APEX, par le launcher officiel `scripts/apex-launch.sh` (boutons « Démarrer / Arrêter / Ouvrir APEX »,
> voir `docs/INSTALLER_APEX.md`). Ce projet-ci est un second launcher parallèle, jamais publié, et il
> décide sur `GateSummary.Phase2Authorized` (règle `Blocked` ci-dessous) : il confond l'autorisation de
> la PHASE 2 avec celle du DÉMARRAGE (`startup_authorized`, docs/PHASE_1_GATE.md §10) et refuserait
> donc toute machine. Toute la chaîne `ApexSetup.Application`/`Core` partage ce défaut. À retirer ou à
> réaligner sur `startup_authorized` — décision à prendre, hors de ce chantier.

## Objectif

Transformer APEX en application Windows lançable en un clic : double-clic
sur un exécutable → détection d'état → vérification Runtime/Gate → mode
Utilisateur ou Admin → démarrage réel d'APEX. L'utilisateur final n'ouvre
jamais PowerShell/CMD et ne tape jamais de commande.

## Frontière — réutilisation, jamais de duplication

`ApexSetup.Launcher` (`setup/src/ApexSetup.Launcher`) référence **UNIQUEMENT**
`ApexSetup.Application` — même frontière que `ApexSetup.Cli` et
`ApexSetup.Gui` (voir `GUI_ARCHITECTURE.md`). Il ne connaît ni
`ApexSetup.Core`, ni les stages d'installation, ni le contenu de
`RuntimeAdapter` — il consomme les 8 méthodes d'`ISetupApplicationService`
exactement comme les deux autres façades.

```
ApexSetup.Launcher (LauncherStateEngine, Program)
    │  connaît UNIQUEMENT ISetupApplicationService + Contracts publics
    ▼
ApexSetup.Application (SetupApplicationService, SetupApplicationServiceFactory)
    │
    ▼
ApexSetup.Core (RuntimeAdapter -> wsl.exe -- apex ... en production)
```

Le launcher ne réimplémente jamais Gate/Doctor/Setup : il lit les verdicts
déjà produits par `StatusAsync`/`ValidateAsync`, jamais recalculés.

## Les 8 états (`LauncherOutcome`)

Dérivés PUREMENT depuis `StatusResponse` (état d'installation persisté,
lecture seule) et `ValidateResponse` (Runtime/Doctor/Gate) par
`LauncherStateEngine.Derive` — fonction statique sans E/S, sans
sous-processus, entièrement testée par
`ApexSetup.Launcher.Tests/LauncherStateEngineTests.cs` :

| État              | Source                                                          |
|-------------------|------------------------------------------------------------------|
| `Ready`           | `Completed` + Runtime joignable + Doctor OK + Gate autorise + version à jour |
| `NeedsSetup`      | `NoInstallation`/`RollbackPerformed`/`InProgress` (verrou tenu) |
| `NeedsRecovery`   | `RecoveryNeeded` — réparable via `RecoverAsync`                 |
| `NeedsUpdate`     | version Runtime observée < plancher configuré (`ApexVersionComparer.IsAtLeast`) |
| `Blocked`         | `GateSummary.Phase2Authorized == false` ou `Blocking` non vide — **jamais contourné** |
| `InvalidInstall`  | `Inconsistent` — nécessite le GUI Setup, jamais réparé seul     |
| `RuntimeError`    | `RuntimeReachable == false` ou `DoctorSummary.Status == "fail"` |
| `UnknownError`    | toute réponse non reconnue — jamais un crash silencieux         |

Aucun état n'est fabriqué : les 8 états exigés sont une PROJECTION des
contrats Setup existants, jamais une nouvelle machine à états parallèle.

## Élévation (UAC)

`app.manifest` déclare `asInvoker` — **jamais** `requireAdministrator` (qui
déclencherait UAC à chaque lancement, y compris quand rien ne le
nécessite). `IElevationContext.RequiresElevationFor` vérifie
EMPIRIQUEMENT l'accessibilité en écriture du répertoire d'installation
(jamais une supposition statique sur le chemin) ; si déjà élevé, aucune
élévation n'est redemandée (pas de boucle UAC). L'élévation elle-même
passe par une ré-exécution du launcher via
`Process.Start(UseShellExecute=true, Verb="runas")` — la SEULE utilisation
de `UseShellExecute=true` dans tout le projet, sans aucun argument shell
interprété (`ArgumentList` reste utilisé).

## Démarrage réel d'APEX

Aucune commande `apex serve`/`apex start` n'existait avant N+15
(`apex/cli/main.py` n'avait que `doctor`/`gate`/`version`/`interfaces`/
`config`/`learn`/`learning`). Deux ajouts, tous deux additifs :

1. **`apex serve`** (Python, `apex/cli/main.py`) — démarre l'API FastAPI
   (`apex.api.app.create_app()` + `uvicorn`, déjà une dépendance optionnelle
   `api`). Vérifie Gate **elle-même**, inconditionnellement, avant de
   démarrer — un appel direct à `apex serve` (hors launcher) refuse
   exactement comme le launcher : Gate n'est jamais contournable en
   changeant de point d'entrée.
2. **`scripts/apex-launch.sh serve`** — sous-commande additive. Réutilise
   EXACTEMENT le même préflight que l'invocation par défaut (`.env`,
   détection Python/venv, `compose_up`, `apex doctor`, `apex gate`,
   factorisés dans `run_preflight()`), puis `exec python -m apex.cli.main
   serve` au lieu de s'arrêter après « préflight OK ». L'invocation par
   défaut (`./apex-launch.sh` sans argument) est **inchangée** — toujours
   exit 0 après le préflight seul (contrat déjà testé par
   `tests/test_launcher.py`, non modifié).

`ApexSetup.Launcher` invoque `wsl.exe -- bash scripts/apex-launch.sh serve`
— **exactement** le même mécanisme que `apex.bat` (même binaire `wsl.exe`,
même script, même respect de `APEX_WSL_DISTRO`) — jamais un second chemin
de démarrage.

## Codes de sortie (`LauncherExitCode`)

Contrat séparé de `SetupExitCode` (Setup a son propre contrat, jamais
modifié) mais qui réutilise les MÊMES valeurs numériques pour les concepts
partagés (`RuntimeError=3`, `Blocked=4`, `RecoveryRequired=9`,
`InvalidInstall=12`, `InternalError=99`) et ajoute des valeurs propres au
launcher pour les concepts sans équivalent Setup (`NeedsSetup=20`,
`ElevationDeclined=21`, `NeedsUpdate=22`) — jamais en collision avec la
plage 0-12 de Setup. Garde-fou testé dans `LauncherExitCodeTests.cs`.

## UX des erreurs

`IUserNotifier` (implémentation réelle : `WindowsTaskDialogNotifier`, P/Invoke
`TaskDialogIndirect` de `comctl32.dll`) affiche un titre + message humain +
boutons `[Réparer]`/`[Détails]`/`[Fermer]` — jamais un code d'erreur brut
seul. `[Réparer]` déclenche soit `RecoverAsync` (état `NeedsRecovery`), soit
le lancement du GUI Setup existant (`ApexSetup.Gui.exe`, publié à côté du
launcher — jamais une seconde interface d'installation) pour `NeedsSetup`/
`InvalidInstall`.

## Limite assumée de cet environnement

Ce dépôt est développé et testé dans un bac à sable Linux. Ne sont donc
**pas** vérifiables empiriquement ici (même précédent que
`GUI_ARCHITECTURE.md`) :

- une VRAIE invite UAC Windows (acceptée ou refusée par un humain) ;
- un VRAI double-clic depuis l'Explorateur Windows ;
- le mode Admin réellement élevé sur une machine Windows ;
- `WindowsElevationContext`/`WindowsTaskDialogNotifier` eux-mêmes (compilent
  et sont couverts par des tests de structure, jamais exécutés ici — voir
  leurs docstrings `[SupportedOSPlatform("windows")]`).

Sont en revanche vérifiés empiriquement dans ce bac à sable : la dérivation
des 8 états (`LauncherStateEngineTests`), la sécurité de
`RealProcessLauncher` (aucune interprétation shell, même sous argument
hostile — `RealProcessLauncherSecurityTests`), le comportement de bout en
bout du binaire compilé sur un répertoire sans installation, y compris
depuis un chemin contenant des espaces et sous deux lancements simultanés
(`LauncherEndToEndTests`), la publication win-x64 réelle (PE32+, aucun
`.pdb`, aucun chemin de développement embarqué — `packaging/publish.sh`).
