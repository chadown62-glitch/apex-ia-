# APEX Desktop — validation sur Windows réel + WSL2

## 0. Audit (avant toute modification de code)

HEAD `c4a57d9` sur `claude/apex-official-development-4kao8t`, arbre propre, identique à `origin`.
Le code Desktop Distribution est présent : Panel `setup/src/ApexSetup.Dashboard`, installateur
`setup/packaging/ApexSetup.iss`, `build_desktop_release.sh`, `wine_installer_e2e.sh`,
`wine_robustness_e2e.sh`, `release_gate.py`, route `GET /api/v1/status`, `apex/discovery.py`,
annonce STARTING de `scripts/apex-launch.sh`, capacité Gauntlet `desktop_distribution` (ledger
intact, 18 entrées, cycles 1 à 5). Release 0.2.1 : `APEX-Setup-x64-v0.2.1.exe`, SHA256
`bf4e54c5…ec70`, construite sur `4a37092` (code produit identique à `c4a57d9`, seuls docs/ledger
diffèrent). Aucun CI dans le dépôt (pas de `.github/workflows`) ; GitHub Actions est actif
(exécution dependabot constatée).

### 0.1 Classement des preuves existantes

| Élément | Classement | Où |
|---|---|---|
| Panel observateur, découverte, jeton, `/status`, états, diagnostic, journaux | IMPLEMENTED, TESTED | .NET 475/475, pytest |
| Verdicts contre le vrai backend (10 scénarios) | REAL E2E **Linux** (binaire Panel linux-x64) | conteneur |
| Installateur : install, raccourcis, désinstallation, réinstallation, mise à jour, interruption, double-clic, instance unique | TESTED **sous Wine 9** | `wine_installer_e2e.sh`, `wine_robustness_e2e.sh` |
| Reproductibilité de l'installateur | MEASURED (deux builds identiques) | release gate |
| Transport `wsl.exe` (argv, `--exec`, langues, multi-distributions) | **MOCK** uniquement | tests .NET |
| Performance (1,25 s premier verdict, 141 Mo, ~2 % CPU) | MEASURED Linux/Xvfb | Gauntlet |
| Windows 10/11 réel, vrai `wsl.exe`, WSL2, localhost WSL2 -> Windows, vrai `launch.sh` vu depuis Windows, vrai shell (raccourcis, double-clic Explorer), redémarrage Windows, SmartScreen, DPI | **BLOCKED / NOT VERIFIED** | aucun Windows utilisé |

BLOCKED_ON_REAL_WINDOWS dans la release gate : redémarrage de Windows ; vrai WSL + `launch.sh`
vus depuis Windows ; raccourci du vrai shell. En réalité tout ce qui touche `wsl.exe` et Windows
est non vérifié, pas seulement ces trois lignes.

### 0.2 Défauts identifiés par lecture du code (à REPRODUIRE sur Windows réel avant de conclure)

| # | Défaut | Conséquence probable |
|---|---|---|
| W1 | `WslBackendHost.ProbeAsync` ignore le code de sortie de `wsl.exe -l -v` et analyse sa sortie. Sans distribution installée (ou WSL non activé : `wsl.exe` d'amorçage de Windows), `wsl.exe` imprime un message de plusieurs lignes ; le parseur saute la 1re ligne et prend le premier mot des suivantes pour des noms de distribution. | diagnostic faux : « <mot> — installée, arrêtée » (WslStopped) au lieu de « aucune distribution » |
| W2 | Backend vivant dans WSL2 (pid vivant, API qui répond DANS WSL) mais port injoignable depuis Windows (redirection localhost désactivée, VPN, pare-feu, réseau) : après 120 s le Panel conclut « processus présent mais muet » et conseille d'arrêter et relancer le backend. | mauvaise consigne ; la vraie cause (localhost WSL2 -> Windows) n'est jamais nommée |
| W3 | Aucune source structurée : noms et distribution par défaut viennent du texte de `wsl.exe -l -v`. Windows fournit un registre structuré, indépendant de la langue (`HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss` : `DistributionName`, `Version` 1/2, `DefaultDistribution`). | fragilité aux formats futurs de `wsl.exe` ; version WSL 1/2 inconnue du Panel |
| W4 | Course : si la distribution s'arrête entre la sonde et `wsl.exe -d D --exec cat`, cette commande la redémarre. | le Panel pourrait démarrer WSL (fenêtre de quelques ms à chaque rafraîchissement) — à mesurer, à minimiser |
| W5 | Jusqu'à 5 lancements de `wsl.exe` par rafraîchissement (toutes les 5 s). | coût CPU réel sous Windows NON MESURÉ |
| D1 | `DESKTOP_DISTRIBUTION.md` §0.6 point 3 annonce des composants ajoutés à `/api/v1/health` ; le code les expose sur `GET /api/v1/status`. | écart documentation / code |
| D2 | `INSTALLER_APEX.md` : aucun prérequis explicite (WSL **2**, Docker accessible dans la distribution, dépôt APEX cloné dans WSL) ni dépannage de la redirection localhost. | utilisateur bloqué sans explication |

### 0.3 Ce qui peut réellement être testé, et où

| Lieu | Réel | Pas réel |
|---|---|---|
| Ce conteneur (Linux) | backend réel, Panel linux-x64, Wine | Windows, `wsl.exe`, WSL2 |
| Runner GitHub Actions `windows-latest` (si le dépôt accepte un workflow) | **Windows Server** réel (VM hébergée), vrai installateur, vrais raccourcis `.lnk`, vrai `APEX.exe`, `--status-json`, `--diagnostic`, désinstallation, mise à jour, instance unique, crash | pas Windows 10/11 client ; **pas de WSL2** (virtualisation imbriquée absente sur ces VM) ; pas de redémarrage piloté ; pas le PC de l'utilisateur |
| PC Windows 10/11 de l'utilisateur | tout : WSL2 réel, distributions réelles, `launch.sh` réel, backend réel, localhost WSL2, Explorer, redémarrage, SmartScreen, performance réelle | — |

Conséquence : les critères D à L de la mission (WSL2, backend réel vu depuis Windows, READY réel,
reboot) exigent le PC de l'utilisateur. Rien de ce qui est exécuté ailleurs n'en tiendra lieu.

### 0.4 Dépendances sur le PC de validation (détectées par le script, jamais installées par lui)

Windows 10 2004+ ou 11 x64 ; PowerShell 5.1 (inclus) ; WSL2 avec au moins une distribution ; dans
cette distribution : le dépôt APEX, Docker accessible (`docker compose`), Python — c'est-à-dire ce
qu'exige déjà `launch.sh`. Le script de validation n'installe ni WSL, ni une distribution, ni
Docker : il constate et marque BLOCKED ce qui manque.
