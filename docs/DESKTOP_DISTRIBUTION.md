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
`kill -9`, journal, réinstallation, **mise à jour 0.2.0 -> 0.2.1** (build d'essai ; refaite par la
release gate avec l'installateur propre 0.2.0 de `649e376` -> 0.2.1 final : PASS ; raccourci et paramètres
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

### 6.1 Cycle 4 — nouvelle exécution sur le code final 0.2.1

`v3-836db75 -> v4-b3b1171` (fonctionnalité, juge figé à `b3b1171`) : **UNCHANGED**, juge **PASS** —
16 métriques déterministes à 1,0, `idle_cpu_percent` 2,12 -> 2,23 % (IC 95 % [-0,05 ; 0,33] :
non distinguable du bruit), 8 sondes passées.

## 7. Release gate (`setup/packaging/release_gate.py`)

Première exécution (HEAD `74fcee5`) : **NOT_RELEASE_READY**, deux FAIL, tous deux réels :
- `W14_mise_a_jour` : l'installateur propre de `649e376` et HEAD annonçaient tous deux 0.2.0 alors
  que le Panel avait changé (cycle 2) — deux binaires différents sous un même numéro. Correctif :
  produit en **0.2.1** (`b3b1171`).
- « Gauntlet validé » : la règle « aucun cycle en échec, jamais » ne pouvait plus passer une fois un
  cycle en échec, le ledger étant append-only. Règle remplacée **après l'avoir constaté** (c'est écrit
  dans le script) : dernier cycle de chaque nature réussi, et dernier cycle mesuré sur le code produit
  actuel ; l'historique complet reste cité comme preuve. D'où le cycle 4.

Deuxième exécution (HEAD `b0a751b`) : BLOCKED_ON_REAL_WINDOWS (22 PASS, 3 BLOCKED), artefact
`33f76c20…`. La séquence de robustesse sur cet artefact (ci-dessous) a ensuite trouvé un défaut :
`APEX.exe --diagnostic > fichier` écrivait les accents dans la page de codes OEM de la console
(illisibles dans un fichier UTF-8). Corrigé (`9d8d24b` : sortie redirigée en UTF-8 sans BOM, console
interactive inchangée), séquence ajoutée à la gate (`wine_robustness_e2e.sh`), cycle 5 sur ce code.

### 6.2 Cycle 5 — après le correctif d'encodage

`v4-b3b1171 -> v5-9d8d24b` (adversarial, juge figé à `9d8d24b`) : **UNCHANGED**, juge **PASS**.
Observation : `idle_cpu_percent` monte à chaque mesure (V2 1,97 ; V3 2,12 ; V4 2,23 ; V5 2,33 %) sans
qu'aucune comparaison ne soit significative, alors qu'entre V3 et V5 rien ne touche le Panel fenêtré
(numéro de version, sortie redirigée) : dérive probable de l'environnement de mesure — INFÉRENCE, non
mesurée.

### 7.1 Exécution finale

HEAD `4a37092`, `--older-installer` = 0.2.0 propre de `649e376` : **BLOCKED_ON_REAL_WINDOWS** — les
23 éléments vérifiables ici PASS (build, SHA256, reproductibilité, WINE E2E dont mise à jour 0.2.0 ->
0.2.1, séquence de robustesse 10/10 — installation, redémarrage de session Wine (pas Windows),
double-clic, Panel, diagnostic UTF-8 sans secret, fermeture, réouverture, désinstallation vérifiée,
réinstallation —, détection offline/online, health checks, authentification, suite adversariale,
diagnostic, journaux, régression .NET 475/475 et Python 129 réussis / 1 ignoré, Gauntlet sur le code
actuel, documentation) ; 3 éléments BLOCKED faute de Windows réel (redémarrage de Windows, vrai WSL +
`launch.sh` vus depuis Windows, raccourci créé par le vrai shell). Jamais RELEASE_READY sans preuve
Windows.

Artefact final : `APEX-Setup-x64-v0.2.1.exe`, 32 540 322 octets, SHA256
`bf4e54c5161a4f7e04e374b246742e1b5cf8102281ba96dda7e696973f37ec70` (deux builds identiques), commit
`4a37092` sans modification locale, 213 fichiers publiés, non signé. L'artefact n'est pas versionné
dans git (binaire de 32 Mo). Il se reconstruit à l'identique par
`setup/packaging/build_desktop_release.sh <sortie>` **sur le commit `4a37092`** (les dates des fichiers
sont celles du commit : un commit ultérieur, même de documentation, change l'empreinte).

## 8. APEX.exe 0.3.0 — mode Windows natif (APEX autonome, sans WSL ni Docker)

### 8.1 Architecture

```
Windows ─► APEX.exe (Panel .NET, %LOCALAPPDATA%\Programs\APEX)
            │  NativeBackendHost : lit %LOCALAPPDATA%\APEX\.apex\runtime\backend.json et .apex\api-token
            │  BackendLauncher (natif) : <APEX_HOME>\python\3.12.10\python.exe -m apex.standalone serve --log | stop
            ▼
          python -m apex.standalone (app\ = artefact APEX Runtime, runtime_build, sans l'extension Recherche)
            │  verrou d'instance → base SQLite (apex db upgrade) → apex doctor → apex serve (Startup Gate)
            ▼
          API + interface web (127.0.0.1:8000, jeton) ─► TaskLoop, Tool Engine, Workspace, Memory, Judge/Retry,
                                                         ModelRuntime natif (transformers), sandbox AppContainer
```

- **Profil autonome** (`APEX_PROFILE=standalone`, posé par le lanceur) : même code que le profil serveur ;
  seules changent les dépendances d'infrastructure (SQLite au lieu de PostgreSQL, pas de Redis, pas de
  Docker). Doctor et Startup Gate mesurent l'infrastructure serveur sans l'exiger, jamais un PASS supposé
  (`docs/PHASE_1_GATE.md` §11).
- **Livraison** : un binaire de plus de 100 Mo ne peut pas être versionné sur GitHub, et la création de
  « releases » est refusée à cette session (HTTP 403). L'installateur (≈ 33 Mo) est donc versionné dans
  `releases/windows/` ; Python (≈ 15 Mo) et les bibliothèques (≈ 190 Mo, torch compris) sont téléchargés
  au premier lancement par « Préparer APEX », **vérifiés par empreintes** (SHA-256 du paquet NuGet
  python.org ; `pip --require-hashes` sur `setup/packaging/runtime/requirements-windows.lock`, résolu
  pour win_amd64 avec les versions testées ici). Le modèle (≈ 3,1 Go) par « Installer le modèle ».
- **Bibliothèques Visual C++** : torch 2.14 exige `VCRUNTIME140_THREADS.dll` (constaté : WinError 126) ;
  le paquet `msvc-runtime` (figé par empreinte, DLL signées par la même autorité Microsoft que la
  `vcruntime140.dll` du Python officiel) les dépose à côté de `python.exe`. Contrôle statique : les 993
  imports des DLL de torch et du runtime vers les DLL Visual C++ sont tous exportés par les DLL livrées.

### 8.2 Validation — ce qui a réellement été exécuté (et où)

| Niveau | Résultat |
|---|---|
| REAL E2E Linux (ce conteneur) — Panel réel (mode natif forcé) + backend réel, artefact Runtime | `--start` → APEX DEGRADED en ≈ 10 s (API, base SQLite, sandbox, interface READY ; modèle absent) ; `--ui-url` → connexion 303 puis interface 200 ; tâche « écrire une fonction » créée et lancée par l'API → TaskLoop : planification, sonde des fournisseurs, `recovery.decided block (provider_unavailable)`, `task.blocked` — raison explicite, aucun résultat simulé ; `--stop` → « APEX arrêté proprement » ; relance puis arrêt |
| WINE E2E — **Windows simulé, pas Windows** — APEX.exe win-x64 publié + `python.exe` officiel | « Préparer APEX » réel : Python téléchargé, empreinte vérifiée, extrait ; 47 paquets installés par pip `--require-hashes` depuis PyPI ; la vérification finale échoue sur `import torch` à cause de fonctions **non implémentées par Wine 9** (`GetNumaNodeProcessorMaskEx`, `ucrtbase.crealf`) — le marqueur « prêt » n'est donc pas écrit (comportement voulu) |
| WINE E2E — chaîne Windows sans torch (aménagement de test déclaré : torch retiré, marqueur écrit à la main) | Panel → `python.exe -m apex.standalone serve` → SQLite (chemin Windows) → doctor → Gate → API ; Panel : chemins Windows, jeton, image du processus vérifiés ; statut DEGRADED (modèle absent ; **sandbox AppContainer indisponible sous Wine → toute exécution refusée**, fail-closed) ; interface ouverte ; tâche créée → « blocked » explicite ; second démarrage sans doublon ; arrêt propre ; relance ; **la tâche est retrouvée après relance** (persistance) ; arrêt sans backend → « rien à arrêter » |
| WINE E2E — installateur 0.3.0 (`setup/packaging/wine_installer_e2e.sh`, 0.2.2 → 0.3.0) | **18/18 PASS** : installation sans administrateur, 216 fichiers, raccourcis Bureau/Menu Démarrer, entrée « Applications installées », validation post-installation, instance unique, premier lancement **« APEX NOT READY — RuntimeNotPrepared — native »** (W9), installation refusée Panel ouvert, relance après plantage, réinstallation, **mise à jour 0.2.2 → 0.3.0**, désinstallation, données conservées, installation interrompue puis relancée |
| WINE E2E — application installée en mode natif (`setup/packaging/wine_native_e2e.sh`, même aménagement torch) | **9/9 PASS** : `app\` installé (284 fichiers) ; premier lancement « runtime à préparer » ; démarrage refusé sans runtime ; runtime prêt ; **Démarrer APEX → DEGRADED en 23 s** (Runtime, Backend, API, Database, Interface READY ; Modèles OFFLINE ; Sandbox OFFLINE sous Wine) ; interface ouverte (303) ; arrêt propre ; relance ; **désinstallation : APEX arrêté d'abord, runtime supprimé, base conservée** |
| Tests | Python : `tests/test_standalone.py`, `tests/test_windows_sandbox.py`, `tests/test_runtime_packaging.py` (artefact, complétude des imports, lanceur depuis l'artefact) ; C# : `NativeModeTests` (30) et 229 tests du Panel |

### 8.3 Non vérifié ici (bloquants de CET environnement, pas du produit)

- **Aucun Windows réel** n'est disponible (GitHub Actions indisponible : facturation) : la sonde AppContainer
  n'a été exécutée que sous Wine, où elle **refuse** (attendu) ; son succès sur un vrai Windows est à constater
  sur la ligne **Sandbox** du Panel.
- **Inférence réelle du modèle** : Hugging Face est bloqué par la politique réseau de ce bac à sable (HTTP 403) ;
  le téléchargement du modèle et une tâche résolue par le modèle n'ont pas pu être exécutés ici.

## 9. APEX.exe 0.4.0 — l'assistant APEX (projets, conversations, modes, paramètres, interface Liquid Glass)

### 9.1 Ce qui a été ajouté (relié au Runtime réel, aucune donnée fictive)

- **Backend** (`apex/assistant/`, `apex/api/routes/assistant.py`) : projets complets (consignes, pièces jointes
  texte réellement lues, suppression en cascade), conversations multiples par projet ou autonomes, quatre modes
  (Normal, Code, Design, Finance) qui changent les consignes, les outils offerts au modèle et la vue ; tours
  **asynchrones** (`conversation_turns`) avec boucle d'outils, délai, annulation entre deux tokens, reprise
  explicite des tours interrompus par un arrêt ; paramètres en sept sections validés et persistés ; précision du
  modèle choisie selon la mémoire **mesurée** (refus chiffré sinon) ; empreintes SHA-256 des poids vérifiées.
- **Robustesse** : une seule réponse active par conversation (verrou : 6 envois simultanés → 1 tour, 5 refus
  409) ; suppression d'une conversation ou d'un projet refusée (409) tant qu'une réponse y écrit ; CSP stricte
  et en-têtes de sécurité sur toute l'interface (aucun script en ligne).
- **Interface** (`frontend/src/assistant/`) : thèmes APEX Night et APEX Dune sur les mêmes jetons (contrastes
  WCAG AA mesurés sur `theme.css` par un test), barre latérale coulissante et repliable, accueil, conversation
  (suivi du tour en direct, arrêt, réessai, renommage, mode, suppression confirmée), projet, paramètres, vues de
  mode (fichiers réels de l'espace de travail, nuancier + contraste, tableaux des outils financiers), rendu
  Markdown sans HTML injecté ; animations, particules et réduction des mouvements réglables ; le centre de
  contrôle reste sous `/control`.

### 9.2 Validation — ce qui a réellement été exécuté (et où)

| Niveau | Résultat |
|---|---|
| Tests Python ciblés | `tests/test_assistant.py` (24 : projets, modes, tours, outils, annulation, reprise, concurrence, suppression refusée sous un tour, pièces jointes, paramètres, précision mémoire, empreintes des poids, vraie sandbox Linux en mode Code) ; `tests/test_assistant_ci_script.py` (3) ; `tests/test_api_ui.py` (CSP) ; `tests/test_tool_engine.py` (drapeaux Windows absents simulés) |
| Suite Python complète (`pytest tests`, JUnit) | **3 997 tests : 3 815 réussis, 2 échecs, 180 ignorés, 0 erreur** (44 min). Les 2 échecs ne viennent pas de 0.4.0 : `test_gate_is_unknown_without_a_report` lit un rapport du Gate laissé dans `.apex/` de ce conteneur le 08/10 (réussit sans ce fichier, vérifié) ; `test_static_probes_do_not_refute_the_current_tree` échouait déjà au commit de départ `73b9ed7` (la désinstallation 0.3.0 supprime le runtime téléchargé `%LOCALAPPDATA%\APEX\python`, que la sonde classe « hors fichiers installés ») — sonde laissée intacte, à trancher |
| Panel (.NET, `dotnet test setup/ApexSetup.sln`) | **571 réussis, 0 échec** (8 projets de tests), version 0.4.0 |
| Interface (vitest) | **162 réussis, 0 échec** (25 fichiers), dont contrastes WCAG mesurés sur `theme.css` et rendu Markdown sans injection |
| REAL E2E Linux (ce conteneur) — vrai backend (profil autonome) + Chromium (Playwright) | accueil → projet créé → pièce jointe CSV acceptée, PDF refusé avec la raison → conversation Finance → tour réel : **échec explicite « génération impossible (modèle local non installé) »**, détails techniques repliés, aucun texte inventé → renommage, passage en mode Code (fichier réel listé et lu) → Paramètres : Dune appliqué et enregistré, valeur hors bornes refusée par le backend, valeur valide enregistrée → cache local effacé : thème relu depuis le serveur → mobile (barre latérale coulissante) → suppression du projet en cascade (conversation 404). **0 violation CSP, 0 erreur de page** ; 34 routes de l'interface parcourues : 0 violation CSP |
| Arrêt propre / redémarrage (Linux, vrai backend) | `python -m apex.standalone stop` → « APEX arrêté proprement » ; relance : conversations, messages et réglages identiques |
| WINE E2E — **Windows simulé, pas Windows** — installateur 0.4.0 final (SHA-256 `925079cb…3a3144c`, commit `50bfe92`) | **44/44 PASS** : cycle de vie (`wine_installer_e2e.sh`) **17/17** ; mise à jour **0.3.0 → 0.4.0** (même script, W14) **18/18** ; mode natif (`wine_native_e2e.sh`, aménagement torch déclaré) **9/9** — démarrage par le Panel : DEGRADED (Runtime, Backend, API, Database, Interface READY ; Modèles OFFLINE ; Sandbox OFFLINE sous Wine), arrêt propre, relance, désinstallation (APEX arrêté d'abord, base conservée) |
| Interface contre la **version Windows** du backend (python.exe 3.12.10 officiel sous Wine, démarré par `APEX.exe --start`) | même parcours d'interface que ci-dessus, avec le même résultat : pièce jointe CSV acceptée (après correctif, voir 9.3), PDF refusé, tour → échec explicite, fichier listé et lu en mode Code, réglages enregistrés et relus du serveur, suppression en cascade ; **0 violation CSP, 0 erreur de page** ; `assistant_e2e.py run` → **BLOCKED** (code 3 : aucun modèle) ; `APEX.exe --stop` puis `--start` : conversation et thème conservés |
| CI Windows réel (`.github/workflows/windows-native.yml`) | écrite et poussée ; run 37896293634 **refusé par GitHub avant démarrage** : « recent account payments have failed or your spending limit needs to be increased » |

### 9.3 Défauts trouvés et corrigés pendant la validation

- **Windows natif — écritures d'outils et pièces jointes** : `os.O_NOFOLLOW` n'existe pas dans le Python Windows ;
  toute écriture de l'espace de travail échouait (pièces jointes, `fs.write` du mode Code et du TaskLoop — défaut
  antérieur à 0.4.0, invisible sous Linux). Corrigé (`apex/tools/workspace.py`) : identité du fichier ouvert
  comparée au chemin vérifié, mode binaire ; vérifié avec le Python Windows sous Wine.
- **Pièce jointe incohérente** : refusée à l'écran mais conservée en base si l'écriture du fichier échouait.
  Corrigé : la ligne est retirée et l'erreur rapportée.
- **Deux envois simultanés** pouvaient créer deux tours concurrents ; **suppression** d'une conversation sous un
  tour actif possible. Corrigés (verrou ; 409 explicite).
- `wine_native_e2e.sh` rendait le code de `wineserver -k` au lieu du verdict des vérifications.

### 9.4 Non vérifié ici (bloquants de CET environnement, pas du produit)

- **Aucune inférence réelle du modèle** : `huggingface.co` est refusé par la politique réseau de ce bac à sable
  (HTTP 403) et aucun poids n'est présent sur le disque ; aucune réponse du vrai modèle n'a donc été produite
  pour 0.4.0. Le parcours qui le prouvera est prêt (`windows-native.yml`, `assistant_e2e.py`).
- **Aucun Windows réel** : Wine n'est pas Windows (pas de torch sous Wine 9, pas de sandbox AppContainer) ; GitHub
  Actions est bloqué par la facturation du compte.
- Non mesurés sur un PC utilisateur : durée réelle des réponses, consommation mémoire, sandbox AppContainer
  (ligne **Sandbox** du Panel), exécution de code en mode Code, SmartScreen.

## 10. APEX.exe 0.4.1 — l'assistant dans la fenêtre, choix du moteur, premiers défauts de Windows réel

Point de départ : le **premier essai sur un vrai Windows** (Windows 11, 10.0.26200, PC de l'utilisateur, le
09/10/2026, installateur 0.4.0). Constaté par l'utilisateur, captures à l'appui : préparation reprise puis
réussie, modèle installé, APEX DEGRADED ; Ollama présent sur le PC (qwen2.5-coder:7b-instruct) ; l'interface
s'ouvrait dans le navigateur et c'est Ollama qui répondait. Demandes : une interface **100 % dans APEX.exe**, et
le **choix** entre Ollama et « le modèle qu'on développe nous-même » (précisé ensuite : les trois — Ollama, le
moteur intégré, et le Cerveau APEX, marqué expérimental).

### 10.1 Ce qui a été ajouté

- **L'assistant dans la fenêtre** (`setup/src/ApexSetup.Dashboard/Views/AssistantHost.cs`) : composant Microsoft
  Edge WebView2 (cœur seulement, `Microsoft.Web.WebView2` 1.0.4191.47) hébergé dans une fenêtre native enfant.
  Deux pages : **Assistant** et **État et contrôle**. Lien à usage unique dès qu'APEX répond ; page vidée quand
  APEX s'arrête ; nouveau lien après un redémarrage (autre processus) ; vue native masquée sous les calques
  (diagnostic, fermeture) ; seule l'origine locale d'APEX s'affiche, tout autre lien part au navigateur du
  système ; sans le composant : raison affichée, lien d'installation, repli sur le navigateur.
  `APEX_PANEL_BROWSER=1` garde l'ancien comportement.
- **Choix du moteur** (`apex/assistant/engines.py`, `GET /assistant/engines`, liste **Moteur**, Paramètres →
  Modèles) : Automatique, Ollama, APEX intégré, Cerveau APEX. Le moteur choisi répond ou la réponse échoue avec la
  raison — jamais un autre ; « Automatique » ne prend jamais le Cerveau.
- **Cerveau APEX servi** (`apex/brain/`, `brain/apex-brain-v1/`, backend `apex_brain`) : la version promue
  `experiment-002:v1`, reconstruite à l'identique (empreintes des poids, du dataset et du tokenizer égales au
  registre ; voir `docs/NATIVE_INTELLIGENCE.md` §15). Expérimental, dit comme tel dans l'interface.

### 10.2 Validation — ce qui a réellement été exécuté (et où)

| Niveau | Résultat |
|---|---|
| Tests ciblés ajoutés | `tests/test_assistant_engines.py` (11 : chaque moteur ne propose que ses modèles, « Automatique » jamais le Cerveau, le moteur choisi répond et jamais un autre, modèle préféré hors moteur refusé, **vrai tour de conversation servi par ApexLM**, Cerveau = version promue du registre (empreinte recalculée sur les poids chargés), poids modifiés jamais servis, provenance contredite refusée, le Cerveau ne reçoit que le dernier message, l'application embarque l'inférence et pas l'entraînement) ; `tests/test_windows_sandbox.py` (environnement AppContainer) ; `tests/test_native_runtime.py` (sondes de modèles lentes en parallèle) ; `EmbeddedAssistantTests` (.NET, 6 : accès conservé pendant une mesure lente, assistant dans la fenêtre et jamais le navigateur, suivi arrêt/redémarrage avec un nouveau lien, vue native masquée sous les calques, composant absent → raison + navigateur, hors Windows → navigateur) |
| Suite Python complète (`pytest tests`, JUnit) | **4 011 tests : 3 828 réussis, 3 échecs, 180 ignorés, 0 erreur** (57 min, en parallèle de la validation Wine). Les deux échecs connus de 0.4.0, inchangés (§9.2) ; `test_experience_v2_script.py::test_calibration_then_experiment_then_analysis_end_to_end` a compté une panne d'infrastructure sous cette charge, puis **réussi relancé seul (23/23)** — il ne touche ni l'assistant ni le Cerveau |
| Panel (.NET, `dotnet test setup/ApexSetup.sln`) | **577 réussis, 0 échec** (571 + 6), version 0.4.1 ; solution compilée sans avertissement |
| Interface (vitest, `tsc -b`) | **165 réussis, 0 échec** ; typage OK |
| Cerveau APEX reconstruit | ré-entraînement de `experiment-002` (code et documents de `59cdbd1`) : empreintes des poids, du dataset et du tokenizer **identiques** au registre ; BPB held-out 2,53844 ; verdicts du Judge identiques |
| REAL E2E Linux — vrai backend autonome (`python -m apex.standalone serve`) + Chromium | parcours complet (projet, pièces jointes, tour, mode Code, paramètres, persistance serveur) **plus** : 4 moteurs mesurés, liste « Moteur » (« Ollama — indisponible », « Cerveau APEX (expérimental) »…), bandeau expérimental, **tour réel servi par ApexLM dans l'interface** (`apex_brain:apex-brain-v1`, 160 jetons, affiché avec le modèle), moteur indisponible nommé avec quoi faire, cartes des moteurs dans Paramètres, suppression en cascade ; **0 violation CSP, 0 erreur de page** ; seules réponses ≥ 400 : les deux refus voulus (PDF, valeur hors bornes) |
| WINE E2E — **Windows simulé, pas Windows** — installateur 0.4.1 (SHA-256 `76441723…5c19ad`, commit `439e108`) | **44/44 PASS** : cycle de vie **17/17**, mise à jour **0.4.0 → 0.4.1** (W14 : raccourci et paramètres conservés) **18/18**, mode natif **9/9** (DEGRADED : Ollama OFFLINE et Modèles mesuré OFFLINE — plus de « UNKNOWN » ni d'« ERROR aucune réponse sous 2 s ») |
| Fenêtre réelle d'APEX.exe 0.4.1 sous Wine (rendu logiciel, captures) | barre du haut (état, pages **Assistant** / **État et contrôle**, **Démarrer APEX**, **Ouvrir dans le navigateur**) ; Wine n'ayant pas WebView2, la page Assistant affiche la raison et le lien d'installation, et le journal du Panel le consigne — **le chemin « composant absent » est vérifié, pas l'affichage intégré** ; APEX démarré : DEGRADED, navigateur proposé ; arrêté : **Démarrer APEX** revient |
| Interface contre la **version Windows** du backend (python.exe 3.12.10 sous Wine, `APEX.exe --start`) | même parcours que 0.4.0, mêmes résultats ; moteurs mesurés (Cerveau « indisponible : bibliothèque absente (torch) » — torch n'existe pas sous Wine, il est dans le runtime Windows) ; `--stop` / `--start` : 2 conversations, thème Dune et moteur choisi **conservés** |
| WebView2 sous Wine | impossible : le téléchargement du composant (go.microsoft.com, msedge.sf.dl.delivery.mp.microsoft.com) est refusé par le réseau de ce bac à sable (403) |

### 10.3 Défauts trouvés sur Windows réel, et corrigés

| Constaté (capture de l'utilisateur) | Cause | Correctif |
|---|---|---|
| **Ouvrir APEX** grisé alors qu'APEX répondait | une mesure de 6,1 s, enchaînée sans pause (intervalle 5 s) ; l'accès à APEX était vidé au DÉBUT de chaque mesure, donc presque toujours vide quand le bouton était réévalué (et un clic n'aurait rien fait) | l'accès n'est remplacé qu'à la fin de la mesure (`PanelStatusService`) ; test qui bloque une mesure en cours |
| **Sandbox OFFLINE** : « sonde AppContainer en erreur : OSError: [Errno 203] CreateProcessW : erreur Win32 203 » | ERROR_ENVVAR_NOT_FOUND : l'environnement passé au processus AppContainer ne contenait que SYSTEMROOT/TEMP/TMP ; pour un processus AppContainer, CreateProcessW recalcule les dossiers du conteneur à partir de cet environnement (LOCALAPPDATA…) | variables système standard transmises quand elles existent (jamais PATH, ni jeton, ni clé) ; en cas de refus, l'erreur nomme les variables passées et le texte Windows. **Non vérifiable ici** (Wine n'implémente pas AppContainer) |
| **Modèles UNKNOWN** « non mesuré sous 6s », **Ollama ERROR** « aucune réponse sous 2s » | sous Windows, une connexion refusée sur 127.0.0.1 met ~2 s à échouer ; quatre endpoints Ollama sondés l'un après l'autre dépassaient le budget du statut | sondes en parallèle ; délai sur la boucle locale rapporté OFFLINE (« Ollama n'est probablement pas démarré ») |

### 10.4 Non vérifié ici (bloquants de CET environnement, pas du produit)

- **L'assistant réellement affiché dans la fenêtre** (WebView2) : ni Windows réel, ni composant WebView2 sous Wine
  (téléchargement refusé). Vérifiés : compilation, logique (6 tests), et le repli quand le composant manque.
- **La sandbox AppContainer** après correctif de l'erreur 203 : Wine n'implémente pas AppContainer. Si l'erreur
  persiste, le message nomme désormais les variables passées et le texte Windows.
- **Réponses du moteur intégré et d'Ollama sous Windows** : pas de modèle ici (Hugging Face et Ollama refusés
  par le réseau). Constaté par l'utilisateur sur son PC en 0.4.0 : Ollama répondait.
- Durée réelle des réponses, mémoire, SmartScreen sur un PC utilisateur.
