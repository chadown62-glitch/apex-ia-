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

## 1. Architecture retenue (IMPLEMENTED)

```
Windows (utilisateur, sans administrateur)
│
├── APEX-Setup-x64-v<version>.exe      Inno Setup 6.4.3, par utilisateur
│     ├── %LOCALAPPDATA%\Programs\APEX\APEX.exe (+ runtime .NET autonome, multi-fichiers)
│     ├── Bureau\APEX.lnk, Menu Démarrer\APEX.lnk (icône APEX)
│     ├── validation : APEX.exe --version
│     └── désinstalleur (programme + raccourcis ; données utilisateur et WSL conservées)
│
└── APEX.exe = Panel (observateur)
      │  wsl.exe -l -v            (noms + distribution par défaut — ne démarre rien)
      │  wsl.exe -l --running -q  (distributions en cours : noms seuls, indépendant de la langue)
      │  wsl.exe --exec cat …     (seulement dans une distribution qui tourne déjà : la choisie,
      │                            sinon la par défaut puis les autres en cours — cycle 2)
      │     ~/.apex/runtime/backend.json   <- écrit par launch.sh serve (starting) puis apex serve (serving)
      │     /proc/<pid>/cmdline            <- le processus annoncé vit-il ?
      │     <projet>/.apex/api-token       <- jeton, en mémoire seulement
      │  HTTP 127.0.0.1:<port> (redirection localhost WSL2)
      ▼
    WSL ── scripts/apex-launch.sh serve ── apex serve ── GET /api/v1/status
                                                        database / redis / ollama / sandbox / brain
```

Principes : le Panel ne remplace pas launch.sh et ne démarre ni WSL ni le backend ; READY n'est
attribué que sur une réponse authentifiée au bon protocole ; aucun secret côté Windows ; aucun
chemin, port ou jeton demandé à l'utilisateur.

## 2. Validation — ce qui a réellement été exécuté

### 2.1 REAL E2E (Linux, ce conteneur) — binaire Panel réel + backend APEX réel

Panel `linux-x64` compilé depuis les sources ; `apex serve` réel (worktree de validation, Gate
autorisé par exceptions datées propres à ce conteneur, jamais commitées) ; PostgreSQL et Redis
réels (conteneurs Docker, images `mirror.gcr.io`) ; cerveau P3 réel (champion attesté).

| Scénario (mission) | Verdict du Panel (mesuré) |
|---|---|
| rien de démarré (#5) | APEX NOT READY — BackendNotStarted — Backend OFFLINE, API OFFLINE, services UNKNOWN (code 2) |
| `launch.sh serve` réel en préflight (#3) | Backend STARTING (BackendStarting), puis NOT READY après l'échec réel de compose (image MinIO introuvable ici) : annonce retirée |
| `apex serve` réel (#6 backend lancé après le Panel) | APEX DEGRADED : Backend, API, Database, Redis, Sandbox, Brain READY ; Ollama OFFLINE (absent ici — vrai) |
| Redis arrêté puis relancé (#29) | Redis OFFLINE puis READY, le reste inchangé |
| jeton refusé (#9) | NOT READY — TokenRejected, API ERROR |
| backend figé, SIGSTOP (#14, #15) | NOT READY — ApiTimeout ; rétabli après SIGCONT |
| `kill -9` du backend (#7, #30) | NOT READY — BackendStopped (enregistrement périmé, pid mort) |
| Ctrl+C sur `apex serve` | NOT READY — BackendNotStarted ; enregistrement retiré |
| autre programme sur le port (#13) | NOT READY — PortConflict |
| fenêtre réelle (Xvfb) | capture : APEX DEGRADED, 7 lignes, journal des transitions |

Défauts trouvés par ces E2E et corrigés : (1) STARTING jamais visible pendant le préflight de
launch.sh -> annonce `starting` par launch.sh (`9218af8`) ; (2) enregistrement laissé après SIGTERM
/ Ctrl+C (uvicorn relance le signal après l'arrêt, le `finally` ne s'exécute jamais) -> retrait sur
`lifespan.shutdown.complete` (`29c8ba8`, test de processus réel + témoin qui reproduit le défaut) ;
(3) Brain UNKNOWN à la première requête (import à froid 3,8 s) -> préchauffage dans `apex serve` ;
(4) consigne « dans WSL » affichée sur un hôte Linux -> consignes selon l'hôte. Deux défauts du
banc de test lui-même (pid d'un sous-shell au lieu de Python ; hypothèse fausse sur le jeton) ont
été identifiés et corrigés avant de conclure.

### 2.2 WINE E2E — installateur Windows réel sous Wine 9 (pas Windows)

`setup/packaging/wine_installer_e2e.sh`, préfixe neuf : **18/18** — installation silencieuse (5,7 s),
215 fichiers, raccourci Bureau vers `%LOCALAPPDATA%\Programs\APEX\APEX.exe`, menu Démarrer, entrée
HKCU de désinstallation, validation post-installation, double-clic depuis `/tmp`, instance unique,
verdict sans WSL (WslNotInstalled), installation refusée pendant que le Panel tourne, relance après
`kill -9`, journal, réinstallation, **mise à jour 0.2.0 -> 0.2.1** (raccourci et paramètres
conservés), désinstallation (programme, raccourcis, entrée retirés ; paramètres et journaux
conservés), réinstallation après désinstallation, installation interrompue (84 fichiers copiés)
puis relancée. Défauts trouvés sous Wine : `{autopf}` non résolu en mode utilisateur ->
`{localappdata}\Programs` explicite ; `APEX.exe` fichier unique ne démarre pas -> multi-fichiers.

### 2.3 PACKAGING TEST

Build (`build_desktop_release.sh`, 43 s) : installateur PE32, SHA256, manifeste, journal. Fichiers
publiés bit-identiques après un build propre ; installateur bit-identique après normalisation des
dates (`SOURCE_DATE_EPOCH`) — mesuré sur deux builds dont un propre, à une minute d'intervalle.

### 2.4 MOCK TEST (chemins Windows impossibles ici)

`wsl.exe` simulé : argv (`--exec`, jamais de shell), distributions arrêtées jamais touchées, sortie
UTF-16, chemins hostiles refusés avant tout processus. Ne prouve pas le comportement d'un vrai WSL.

## 3. Performance (MEASURED, Linux, rendu logiciel sous Xvfb)

| Mesure | Valeur |
|---|---|
| installation silencieuse (Wine) | 4,8 à 5,7 s |
| lancement du Panel -> premier verdict | 1,25 s |
| `--status-json` contre un backend en marche | 0,38 à 0,42 s (5 mesures) ; 0,24 s sans backend |
| mémoire résidente du Panel | 141 Mo |
| CPU au repos (rafraîchissement 5 s) | 2,8 % d'un cœur (mesure manuelle) ; Gauntlet d6 : moyenne 1,97 % (V2) et 2,12 % (V3), 6 fenêtres de 10 s après 30 s — différence non distinguable du bruit |
| build complet de l'installateur | 43 s |

## 4. Sécurité (audit)

- **Privilèges** : installateur `PrivilegesRequired=lowest`, `APEX.exe` `asInvoker` ; aucune élévation.
- **Processus** : le Panel ne lance que `wsl.exe`, en commandes courtes et bornées, avec `ArgumentList`
  et `--exec` (jamais le shell Linux) ; chemins issus de l'enregistrement validés (absolus, sans `..`,
  sans caractère de contrôle, jamais une option) ; nom de distribution validé. Il n'exécute jamais une
  commande reçue du backend : la réponse n'est que des données, affichées comme texte.
- **Secrets** : jeton lu dans WSL, gardé en mémoire, jamais écrit côté Windows ; masqué dans les
  journaux (`Redactor` : valeurs lues et en-têtes `Bearer`) et le diagnostic ; l'enregistrement de
  découverte ne contient aucun secret (dossier `~/.apex/runtime` en 700).
- **Fichiers** : paramètres sans secret, fichier corrompu toléré et signalé ; journaux par jour,
  14 conservés, 5 Mo max par fichier ; aucune écriture hors `%APPDATA%\APEX` et `%LOCALAPPDATA%\APEX`.
- **DLL / chemins** : application autonome chargée depuis son dossier d'installation, inscriptible par
  le seul utilisateur (comme toute installation par utilisateur) ; aucun chemin de développement
  embarqué (vérifié à chaque publication).
- **Signature** : aucune (pas de certificat) — avertissement SmartScreen attendu.

## 5. Limites et éléments BLOCKED

- **Windows réel : NOT VERIFIED** — vrai `wsl.exe`, redirection localhost WSL2 -> Windows, raccourcis
  créés par le vrai shell, SmartScreen, redémarrage de Windows, DPI réels.
- **`minio/minio:latest`** (docker-compose.yml) introuvable dans ce conteneur (Docker Hub :
  « repository does not exist », quay.io refusé par la politique réseau) : le vrai `launch.sh serve`
  y échoue à `compose up` (code 2). Sur une machine utilisateur : NOT VERIFIED ; si l'image est
  réellement retirée de Docker Hub, `launch.sh` échouera partout — à vérifier en priorité.
- Interface web (Vite) hors de cette distribution : serveur de développement uniquement.
- Ollama : aucun modèle téléchargeable ici (ollama.com refusé) : au mieux DEGRADED.

## 6. Gauntlet loop — capacité `desktop_distribution` (ledger `docs/improvement/desktop_distribution.ledger.jsonl`)

Le moteur (`apex/improvement`) mesure chaque version DEUX fois (reproductibilité constatée), refuse de
comparer deux définitions de banc différentes (INCONCLUSIVE) et fait juger chaque cycle par un juge
externe exécuté dans un processus séparé depuis un commit figé (`--judge-ref`) : un REJECT du juge rend
le cycle FAILED quelles que soient les mesures. Le ledger est append-only et chaîné par empreintes :
les tentatives ratées y restent.

Banc : 11 scénarios Panel contre le vrai backend (binaire Panel compilé, vraie API, vraie découverte,
vrai `launch.sh` pour STARTING), 3 métriques d'installateur (build, reproductibilité, cycle de vie
WINE E2E), 2 métriques MOCK (chemins `wsl.exe`), et depuis d5 le CPU au repos du Panel fenêtré
(stochastique, bootstrap apparié). Sondes du juge : READY sans backend, enregistrement hostile,
protocole inconnu, paramètres/journaux corrompus, shell dans les commandes WSL, sortie WSL traduite,
backend dans une autre distribution, politique de l'installateur.

| Cycle | Avant -> après | Verdict moteur | Juge | Constat |
|---|---|---|---|---|
| 1 fonctionnalité, tentative 1 | v0-e84f9f1 -> v1-8cb2d3f | **INCONCLUSIVE** | plantage (`IndexError`) | définitions différentes ; racine du dépôt déduite par `parents[3]` dans le juge figé -> corrigé (`6a1d234`) |
| 1, tentative 2 | v0-…-d3 -> v1-6a1d234 | **FAILED** | REJECT | définitions encore différentes : l'empreinte hachait le script E2E Wine de l'arbre MESURÉ (absent à `e84f9f1`) -> empreinte prise dans l'arbre du banc (`649e376`) |
| 1, tentative 3 | v0-…-d4 -> v1-649e376 | **FAILED** | REJECT | 14 métriques 0 -> 1 ; `wsl_any_language` et `wsl_backend_in_other_distro` restent à 0 : défauts réels confirmés par le juge |
| 2 adversarial | v1-649e376 -> v2-dc6a57c | **IMPROVED** | PASS | les 2 métriques WSL 0 -> 1 (état WSL sans mot traduit, recherche dans les autres distributions en cours), 14 inchangées à 1 |
| 3 amélioration | v2-34e243c-d6 -> v3-836db75 | **UNCHANGED** | PASS | hypothèse (lignes mises à jour en place -> moins de CPU au repos) NON confirmée : 1,97 -> 2,12 %, IC 95 % [-0,30 ; 0,65] |

Défauts du banc lui-même trouvés en route, corrigés avant de conclure : empreinte dépendante de l'arbre
mesuré (ci-dessus) ; fuite d'une publication du Panel (~90 Mo) par processus de mesure, qui a rempli
le disque : le snapshot `v2-6c06441-d5` mesure l'installateur à 0/17 faute d'espace pour Wine — il est
**invalide** pour cette métrique, conservé dans le ledger et remplacé par `v2-34e243c-d6` (17/17)
après correction (`34e243c` : publication retirée à la sortie, installateur BLOQUÉ sous 4 Gio libres
au lieu d'un 0).

Limites : sondes, correctifs et banc écrits par le même agent (le juge est figé à un commit mais pas
indépendant) ; les métriques MOCK ne prouvent pas le comportement d'un vrai `wsl.exe`.

