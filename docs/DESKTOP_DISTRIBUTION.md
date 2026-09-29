# APEX Desktop Distribution — audit, architecture, validation

Étiquettes : IMPLEMENTED, TESTED, MEASURED, REAL E2E (Linux), WINE E2E (binaire Windows réel exécuté
sous Wine — **jamais** une preuve Windows), MOCK TEST, PACKAGING TEST, BLOCKED, NOT VERIFIED.
Environnement de ce travail : conteneur Linux (Ubuntu 24.04) ; **aucune machine Windows, aucun WSL
réel**. Toute affirmation sur Windows réel est NOT VERIFIED sauf mention contraire.

## 0. Audit (avant toute modification de code — HEAD `e84f9f1`)

### 0.1 Mesures de départ (MEASURED dans ce conteneur)

| Mesure | Résultat |
|---|---|
| `dotnet build setup/ApexSetup.sln` (.NET 8.0.131) | OK, 0 avertissement |
| `dotnet test setup/ApexSetup.sln` | **422 / 422** (Core 176, Dashboard 80, Launcher 42, Application 41, Gui 31, Cli 26, Release 18, Release.Cli 8) |
| `setup/packaging/publish_dashboard.sh` | `APEX.exe` PE32+ GUI x86-64, **fichier unique 92,5 Mo**, aucun `.pdb`, aucun chemin de développement |
| `ApexSetup.iss` compilé par ISCC 6.4.3 (NuGet `Tools.InnoSetup`) sous Wine 9.0 | OK (24 s), `APEX-Setup.exe` 30,5 Mo ; 2 avertissements ISCC (identifiant d'architecture `x64` obsolète ; zones par utilisateur en mode admin) |
| Installation silencieuse sous Wine (préfixe win64) | OK : `C:\Program Files\APEX\APEX.exe`, désinstalleur, raccourci **Menu Démarrer seulement** — **aucun raccourci Bureau** (tâche décochée par défaut) |
| `APEX.exe` fichier unique lancé sous Wine | **ne démarre pas** : `Failed to load System.Private.CoreLib.dll (0x8007046C)` (alignement de projection du bundle). Peut être une limite de Wine : NOT VERIFIED sur Windows |
| Même code publié en **multi-fichiers** (215 fichiers, 95 Mo) sous Wine | le processus démarre et reste vivant ; fenêtre **noire** sous Xvfb (rendu ANGLE/Direct3D 11 non fonctionnel sans GPU) |

### 0.2 EXISTANT ET RÉUTILISABLE

- **`setup/src/ApexSetup.Dashboard` = `APEX.exe`** (Avalonia 11, MVVM, asInvoker) : tableau d'état,
  sondes HTTP réelles (`HttpProber` : refus / délai / erreur HTTP distingués), sonde WSL
  (`wsl.exe -l -v`, `WslStatusParser` UTF-16 compris), paramètres JSON atomiques dans `%AppData%`,
  commandes asynchrones protégées (`IsBusy`, `Faulted`). C'EST le Panel : il est conservé et corrigé.
- **`setup/packaging/ApexSetup.iss`** (Inno Setup 6) : installateur d'`APEX.exe`, désinstalleur,
  ne supprime jamais les données. Conservé et corrigé.
- **`publish_dashboard.sh`** (contrôles PE, `.pdb`, chemins de développement), `Directory.Build.props`
  (version unique `SetupProductVersion`), `ApexSetup.Release` (manifeste de release).
- **`scripts/apex-launch.sh`** = le launch.sh dédié (préflight .env / Docker / Python / doctor / gate,
  `serve`, `stop`) ; `apex.bat` (délégateur Windows) ; `apex serve` (gate inconditionnelle).
- **Authentification** : `apex/api/auth.py` — jeton `.apex/api-token` (600), comparaison à temps
  constant, jamais journalisé ; toutes les routes, `/health` compris, exigent `Authorization: Bearer`.
- **`/api/v1/health`** (base de données réellement interrogée) ; `apex.obs.health` (agrégation) ;
  checks doctor `service.*` ; `apex.sandbox.detect` ; `apex.neural.live.BrainWorkdir` (`/brain`).
- **Gauntlet loop** : `apex/improvement` (Continuous Improvement Engine) — mesure double,
  sondes de réfutation jugées par `apex.metalearner.gauntlet.GauntletRunner`, ledger chaîné
  append-only, juge en processus séparé épinglé à une révision git. Réutilisé tel quel : une
  nouvelle capacité (`desktop_distribution`) = un adaptateur + un juge.

### 0.3 EXISTANT MAIS INSUFFISANT (défauts constatés)

| # | Défaut | Preuve |
|---|---|---|
| D1 | Les lignes Docker/PostgreSQL/Redis/MinIO/Ollama/GPU d'`APEX.exe` appellent `apex doctor` comme un **programme Windows** (`SetupApplicationServiceFactory.Create()` sans `wsl.exe`) : sur une machine Windows réelle, `apex` n'existe pas côté Windows -> toutes ces lignes UNKNOWN. Le Launcher, lui, route par `wsl.exe` et documente exactement ce piège. | lecture du code (`App.axaml.cs` l.30 contre `ApexSetup.Launcher/Program.cs` l.113-141) ; non reproductible ici (pas de Windows) |
| D2 | Le chemin du dépôt dans WSL doit être SAISI par l'utilisateur ; suggestion codée en dur `/home/spy/projects/apex-project` (nom d'utilisateur d'une machine de développement). Sans lui : API « UNKNOWN », rien ne démarre. | `Models/DashboardSettings.cs` |
| D3 | « APEX est prêt » exige que les 9 lignes soient `Running`, GPU compris : une machine sans GPU (état légitime, `Unavailable`) n'est **jamais** prête ; le Web Panel (serveur de dev Vite) aussi. | `MainWindowViewModel.RefreshAsync` |
| D4 | Le Panel démarre lui-même WSL, `apex-launch.sh serve` et `npm run dev` (bouton LANCER) : contraire au flux demandé (l'utilisateur lance launch.sh). Toute commande `wsl.exe --` passe par le shell Linux par défaut : un chemin lu dans un fichier de paramètres modifiable devient une ligne de commande shell (`ApiHealthChecker` : `wsl.exe -- cat <chemin>` non protégé). | code ; comportement de `wsl.exe --` NOT VERIFIED ici |
| D5 | Case « Lancer APEX au démarrage de Windows » enregistrée mais **jamais appliquée** (aucun code ne l'utilise). | `grep LaunchOnWindowsStartup` |
| D6 | Installateur : élévation administrateur obligatoire (Program Files) alors qu'aucun privilège n'est nécessaire ; raccourci Bureau décoché ; aucune icône (ni exe, ni raccourci, ni setup) ; nom sans version (`APEX-Setup.exe`) ; aucune garde « application en cours d'exécution » à la désinstallation/mise à jour ; identifiant d'architecture obsolète. | `.iss` + compilation/installation mesurées (§0.1) |
| D7 | `APEX.exe` fichier unique ne démarre pas sous Wine ; le multi-fichiers démarre. | §0.1 |
| D8 | Journal uniquement en mémoire : rien ne reste après fermeture pour diagnostiquer. | `InMemoryLogSink` |
| D9 | Pas de page Diagnostic ni de copie ; aucune vérification de compatibilité de version ; `/health` ne rapporte ni versions ni Redis/Ollama/Sandbox/Brain. | code |
| D10 | Aucune garde d'instance unique (double-clic répété = plusieurs Panels). | code |
| D11 | Aucun artefact installateur versionné, haché, ni manifeste qui le décrive ; `docs/PACKAGING.md` affirme encore « aucun installeur » alors que `ApexSetup.iss` existe. | docs |

### 0.4 MANQUANT

Découverte automatique du backend (port, racine du projet) ; états Panel READY / STARTING /
OFFLINE / DEGRADED / ERROR / UNKNOWN et verdict global ; distinction « WSL arrêté » /
« launch.sh non exécuté » / « en démarrage » / « API indisponible » / « partiel » / « prêt » ;
VERSION_MISMATCH ; journal fichier ; diagnostic copiable sans secret ; icône ; installation par
utilisateur sans administrateur ; politique de désinstallation documentée ; build reproductible
(artefact + SHA256 + manifeste + journal) ; release gate ; documentation utilisateur ; capacité
Gauntlet `desktop_distribution`.

### 0.5 RISQUES

- Aucun Windows réel : WSL, `wsl.exe`, la redirection localhost WSL2 -> Windows, les raccourcis
  `.lnk` du vrai shell, SmartScreen (binaire non signé), le redémarrage Windows : **NOT VERIFIED**.
- Wine n'est pas Windows : ses résultats sont WINE E2E, jamais présentés comme preuve Windows.
- Binaire non signé : avertissement SmartScreen probable au premier lancement (aucun certificat).
- Le jeton API ne doit jamais être recopié côté Windows ni journalisé.

### 0.6 DÉCISIONS

1. **Panel = `APEX.exe` existant** (pas de second Panel). Mode **observateur** : il ne démarre ni
   WSL ni le backend. Les boutons LANCER / ARRÊTER / REDÉMARRER / RÉPARER et l'orchestrateur qui
   lançait WSL, `apex-launch.sh serve` et `npm run dev` sont retirés (D4). La case « démarrage de
   Windows » morte est retirée (D5).
2. **Découverte** : `apex serve` (donc `launch.sh serve`) écrit un enregistrement sans secret
   `~/.apex/runtime/backend.json` (racine du projet, hôte, port, pid, versions, heure de démarrage),
   supprimé à l'arrêt. Le Panel le lit **seulement si la distribution WSL tourne déjà** (`wsl.exe -l -v`
   ne démarre rien), via `wsl.exe --cd ~ --exec cat …` (jamais le shell), puis lit le jeton
   `<racine>/.apex/api-token` de la même façon, en mémoire uniquement. Plus aucun chemin à saisir (D2).
3. **Source d'état** : `/api/v1/health` (authentifié, inchangé pour l'existant) gagne des composants
   réellement mesurés (Redis PING, Ollama HTTP, sandbox, brain) avec un `state` explicite et un bloc
   `versions` (APEX, API, protocole Panel). READY n'est attribué que sur une réponse réelle.
4. **Verdict global** : APEX READY seulement si WSL, Backend, API, Database, Redis, Ollama, Sandbox,
   Brain sont READY ; DEGRADED si l'API répond mais pas tout ; sinon NOT READY avec la cause précise ;
   VERSION_MISMATCH si le protocole diffère. GPU et interface web ne conditionnent pas READY (D3).
5. **Installateur** : `ApexSetup.iss` corrigé — installation **par utilisateur sans administrateur**
   (`%LOCALAPPDATA%\Programs\APEX`), raccourci Bureau « APEX » créé d'office, icône réelle, nom
   `APEX-Setup-x64-v<version>.exe`, `AppMutex`, publication **multi-fichiers** (D7).
6. **Configuration / données** : paramètres `%APPDATA%\APEX\` ; journaux `%LOCALAPPDATA%\APEX\logs\` ;
   aucun secret côté Windows ; conservés à la désinstallation (politique §6).
7. **Hôte Linux natif** : le même Panel sait observer un backend local sans WSL (même découverte,
   même jeton, ligne WSL masquée) — c'est le mode dans lequel il peut être exercé de bout en bout ici
   contre un VRAI backend ; le transport `wsl.exe` n'est testé qu'avec un double (MOCK TEST).
8. **Gauntlet** : capacité `desktop_distribution` dans `apex/improvement` (mesures réelles du build,
   de l'installateur sous Wine, du Panel contre un vrai backend ; sondes adversariales) + release gate.
