# APEX Dashboard (`APEX.exe`) — Architecture (N+30)

## Objectif

Le point d'entrée utilisateur quotidien d'APEX sous Windows : double-clic
sur `APEX.exe` → tableau de bord avec l'état réel de chaque composant
(WSL/Docker/PostgreSQL/Redis/MinIO/Ollama/GPU/API/Web Panel) → un bouton
« LANCER APEX » qui orchestre le démarrage réel et ouvre le navigateur une
fois — et seulement une fois — le Web Panel réellement disponible.

## Frontière avec l'existant — jamais de duplication

`ApexSetup.Dashboard` (`setup/src/ApexSetup.Dashboard`) référence
**UNIQUEMENT** `ApexSetup.Application` — même frontière GUI/Core que
`ApexSetup.Gui`/`ApexSetup.Launcher` (voir `LAUNCHER_ARCHITECTURE.md`).
Deux méthodes ont été ajoutées à `ISetupApplicationService`
(`GetDoctorDetailAsync`/`GetGateDetailAsync`) — un pur passe-plat vers
`RuntimeAdapter.GetDoctorAsync`/`GetGateAsync` (déjà appelés par
`ValidateAsync`, mais dont le résultat complet — `results[]`/`checks[]` —
n'était pas exposé au-delà d'un résumé). C'est la modification minimale
nécessaire (mission §23) pour exposer au Dashboard une donnée déjà mesurée,
jamais recalculée.

```
ApexSetup.Dashboard (MainWindowViewModel, ApexOrchestrator, ...)
    │  connaît UNIQUEMENT ISetupApplicationService + Contracts publics
    ▼
ApexSetup.Application (SetupApplicationService — inchangé sauf 2 méthodes ajoutées)
    │
    ▼
ApexSetup.Core (RuntimeAdapter -> wsl.exe -- apex doctor/gate --json, en production)
```

`ApexSetup.Dashboard` NE réimplémente PAS `ApexSetup.Launcher` (décision
UAC/setup manquant) — c'est un outil DIFFÉRENT, à USAGE DIFFÉRENT :
`ApexSetup.Launcher` est le mécanisme headless de démarrage/réparation déjà
existant (invoqué par un raccourci, décide entre Ready/NeedsSetup/Blocked/...
et se ferme) ; `ApexSetup.Dashboard` est une application PERSISTANTE avec
tableau de bord live, boutons Start/Stop/Restart/Repair et Paramètres — le
« daily-use dashboard » demandé par la mission N+30, qui n'existait pas.

## Mécanismes réutilisés, jamais réinventés

| Besoin | Mécanisme réutilisé | Jamais |
|---|---|---|
| Démarrer l'API + l'infrastructure Docker | `scripts/apex-launch.sh serve` (même invocation que `ApexSetup.Launcher.LaunchApexAsync`) | une seconde logique de préflight/compose_up |
| Arrêter l'infrastructure Docker proprement | `scripts/apex-launch.sh stop` (n'arrête QUE ce qu'il a lui-même démarré) | `docker system/volume/network prune`, un arrêt global |
| Réparation sûre | `apex start --dry-run` (doctor → `apex.doctor.repair` SAFE_AUTO_FIX → gate, sans démarrer le serveur) | une nouvelle logique de réparation côté C# |
| État Docker/PostgreSQL/Redis/MinIO/Ollama/GPU | `apex doctor --json` (`RuntimeStatusService`, lit les checks déjà nommés côté Runtime) | un second diagnostic |
| Autorisation de démarrage | `apex gate` (via `apex-launch.sh`/`apex serve`, jamais contournée) | un flag qui saute Gate |

## Ce qui est réellement NOUVEAU (n'existait nulle part avant)

- **Détection WSL** (`IWslProbe`/`WindowsWslProbe`/`WslStatusParser`) —
  audité comme absent : `ApexSetup.Core.Preflight` ne sonde que
  disque/permissions pour l'installation de Setup, jamais l'état de WSL
  lui-même (installé ? distribution ? en cours d'exécution ?).
- **Démarrage du Web Panel** (`npm run dev`, tracké) — rien dans ce dépôt
  n'automatisait le frontend avant N+30 (confirmé : `scripts/apex-launch.sh`
  ne mentionne ni `npm` ni `vite`).
- **Vérification HTTP réelle de l'API et du Web Panel** (`HttpProber`,
  `IApiHealthChecker`, `IWebPanelChecker`) — y compris la lecture du jeton
  Bearer (`.apex/api-token`, `apex/api/auth.py`) désormais exigé par
  `/health` comme par toute autre route.
- **Suivi de processus + arrêt ciblé** (`ITrackedProcess`) — mission §13 :
  seuls les processus que CE Dashboard a lui-même démarrés sont arrêtés,
  jamais une recherche par nom de processus.

## Vocabulaire d'état (mission §4)

`ComponentStatus` : `Running | Starting | Stopped | Error | NotInstalled |
Unavailable | Unknown` — sept valeurs, jamais une huitième. `Running` n'est
JAMAIS attribué sans une mesure réelle (voir `RuntimeStatusService` pour le
mapping exact `DoctorSeverity -> ComponentStatus`, documenté dans son
propre commentaire XML avec la justification de chaque cas).

## Élévation

`app.manifest` déclare `asInvoker` — même choix que `ApexSetup.Launcher`
(jamais `requireAdministrator` : APEX.exe ne nécessite aucun privilège
administrateur pour tourner, seul l'INSTALLATEUR — `ApexSetup.iss` —
demande une élévation, pour écrire dans Program Files).

## Limite assumée de cet environnement

Développé et testé dans un bac à sable Linux — ne sont **pas** vérifiables
empiriquement ici (même limite que `ApexSetup.Gui`/`ApexSetup.Launcher`) :
- un VRAI `wsl.exe`, `docker`, `ollama`, `npm run dev` exécutés depuis
  Windows ;
- une VRAIE fenêtre Avalonia affichée et cliquée par un humain ;
- l'exécutable `APEX.exe` lui-même lancé sur une machine Windows.

Sont en revanche vérifiés empiriquement dans ce bac à sable : la
compilation complète (y compris la compilation XAML/bindings Avalonia), la
suite de 57 tests (parsing WSL, mapping Doctor→statut, sondes HTTP contre
un VRAI serveur HTTP local, orchestration start/stop/restart/repair avec
timeout borné réel, persistance des Paramètres), et la publication win-x64
réelle (`PE32+ executable (GUI) x86-64, for MS Windows`, aucun `.pdb`,
aucun chemin de développement embarqué — voir
`packaging/publish_dashboard.sh`).
