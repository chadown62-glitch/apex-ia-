# Installer APEX (Windows)

Le Panel APEX (`APEX.exe`) est le point d'entrée Windows d'APEX : il **démarre, arrête et ouvre
APEX**, et affiche son état réel. APEX lui-même (le « backend ») tourne dans Linux, via WSL. Le
bouton « Démarrer APEX » exécute le launcher officiel, `scripts/apex-launch.sh`, exactement comme
dans un terminal — même préflight (Docker, `apex doctor`) et même **Startup Gate**, jamais contourné.
Rien n'est démarré sans votre clic.

## Prérequis (constatés, jamais installés par APEX Desktop)

- Windows 10 (2004 ou plus récent) ou Windows 11, 64 bits, compte utilisateur normal (pas besoin
  d'administrateur).
- WSL 2 avec une distribution Linux (`wsl --install -d Ubuntu` dans PowerShell si besoin).
- Dans cette distribution : le dépôt APEX, Docker utilisable par votre compte (`docker compose`), et
  l'environnement Python d'APEX (`.venv`) — c'est-à-dire ce qu'exige déjà `scripts/apex-launch.sh`.
- L'interface web d'APEX, construite **une fois** (puis après chaque mise à jour du dépôt), comme
  `.venv` : dans le dossier APEX, `bash scripts/apex-build-ui.sh` (Node.js 18+ requis :
  `sudo apt install nodejs npm`). Sans elle, APEX fonctionne mais « Ouvrir APEX » affiche comment la
  construire (ligne **Interface** du Panel : OFFLINE).
- APEX tourne sous le **compte Linux par défaut** de WSL : le Panel lit `~/.apex/runtime/` de ce
  compte. Le dépôt est cherché dans `~/apex` ; s'il est ailleurs, indiquez-le une fois dans
  **Paramètres** (« Dossier APEX dans WSL »).
- Pour qu'APEX **exécute** des tâches (pas seulement démarre) : Ollama dans la distribution avec un
  modèle de génération de code déclaré par `config.yaml` (référence : `ollama pull
  qwen2.5-coder:7b-instruct`), et soit l'isolation par namespaces, soit Docker pour la sandbox
  conteneur (image `python:3.12-slim`, téléchargée au premier usage). Sans modèle, APEX démarre
  (ligne **Ollama** OFFLINE ou DEGRADED) mais une exécution de tâche s'arrête « blocked » : aucun
  candidat disponible — jamais une réponse simulée.
- Base de données : son schéma est créé et mis à jour **automatiquement** à chaque démarrage
  (`launch.sh` exécute `apex db upgrade` après avoir démarré PostgreSQL, avant `apex doctor`). Les
  mots de passe `APEX_DB_*_PASSWORD` de `.env` (présents dans `.env.example`) sont nécessaires ; s'ils
  manquent, le démarrage est refusé avec « schéma de la base » (code 10 de launch.sh).
- MinIO est **facultatif** : l'image `minio/minio` n'est plus distribuée publiquement (constaté le
  30/09/2026) et aucune fonction d'APEX ne l'utilise aujourd'hui. `launch.sh` démarre sans lui ;
  `apex doctor` le signale en WARN (« service optionnel : non bloquant »). Pour l'activer :
  `COMPOSE_PROFILES=minio` dans `.env` (voir `docs/MINIO_IMPACT_ANALYSIS.md`).

## Installer

1. Téléchargez `APEX-Setup-x64-v<version>.exe` (et, si vous le souhaitez, le fichier `.sha256`
   à côté pour vérifier le téléchargement : `certutil -hashfile APEX-Setup-x64-v<version>.exe SHA256`).
2. Double-cliquez sur `APEX-Setup-x64-v<version>.exe`.
   - Aucun droit administrateur n'est demandé : APEX s'installe pour votre compte uniquement.
   - Le programme n'est pas signé : Windows peut afficher « Windows a protégé votre ordinateur ».
     Cliquez sur « Informations complémentaires » puis « Exécuter quand même » si le fichier vient
     bien de votre source APEX.
3. Laissez l'installation se terminer. L'installateur vérifie lui-même que le Panel démarre.
4. Un raccourci **APEX** est créé sur le Bureau (et dans le menu Démarrer).

## Utiliser APEX au quotidien (sans terminal)

5. Double-cliquez sur **APEX** sur le Bureau.
6. Cliquez sur **Démarrer APEX**. Le Panel affiche « démarre… » pendant le préflight (Docker,
   `apex doctor`, Startup Gate — le premier lancement peut durer plusieurs minutes), puis
   **APEX READY** ou **APEX DEGRADED** (APEX tourne, un service facultatif manque : la ligne
   concernée dit lequel).
7. Cliquez sur **Ouvrir APEX** : l'interface web d'APEX s'ouvre dans votre navigateur (lien à usage
   unique, valable 60 s ; la session dure jusqu'au prochain arrêt d'APEX). Si elle dit « session
   expirée », cliquez de nouveau sur **Ouvrir APEX**.
8. Pour arrêter : **Arrêter APEX** (APEX, puis l'infrastructure Docker que son lancement a démarrée).

Fermer le Panel n'arrête pas APEX ; rouvert, il le retrouve tout seul. Deux démarrages ne peuvent
pas se chevaucher : un second clic, ou un `launch.sh serve` lancé ailleurs, est refusé tant qu'APEX
tourne (« déjà en cours », code 8 de launch.sh). **Journal du démarrage** montre la sortie complète
du dernier démarrage (`~/.apex/runtime/launch.log` dans WSL).

Si le démarrage est refusé, le Panel affiche **APEX BLOCKED** et la raison : contrôle(s) mesuré(s)
en FAIL ou précondition non vérifiable du Startup Gate (identifiants `GATE-xx` cités), ou échec
d'`apex doctor`. Le Panel ne propose jamais de contourner ce refus : corrigez la cause puis cliquez de
nouveau sur **Démarrer APEX**. « Phase 2 autorisée : NON » n'empêche jamais le démarrage : c'est une
décision séparée de la feuille de route.

Toujours possible depuis un terminal WSL (dans le dossier APEX) : `bash scripts/apex-launch.sh serve`
(Ctrl+C pour arrêter) — le Panel le voit de la même façon.

## Ce que le Panel affiche

| Ligne | Ce qui est vérifié |
|---|---|
| WSL | la distribution Linux est installée et démarrée (sans jamais la démarrer) |
| Backend | APEX a été lancé par `launch.sh` (en démarrage, en marche, arrêté) |
| API | APEX répond réellement, avec son jeton d'accès |
| Database, Redis, Ollama, Sandbox, Brain | l'état mesuré à l'instant par APEX lui-même |
| Interface | l'interface web est construite et servie par APEX (`scripts/apex-build-ui.sh`) |

États : **READY** (vérifié), **STARTING** (en démarrage), **OFFLINE** (arrêté / absent),
**DEGRADED** (répond mais incomplet), **ERROR** (répond mal), **UNKNOWN** (pas mesurable pour
l'instant). En tête : **APEX READY** (tout est prêt), **APEX DEGRADED** (APEX répond, certains
services non), **APEX BLOCKED** (démarrage refusé par le Startup Gate ou `apex doctor`, raison
affichée), **APEX NOT READY** (avec la raison et quoi faire), **VERSION_MISMATCH** (Panel et APEX
de versions incompatibles). L'interface web affiche le MÊME verdict (calculé par APEX, `GET
/api/v1/status`).

## Dépannage

| Le Panel affiche | Signification | Que faire |
|---|---|---|
| « WSL n'est pas installé » | `wsl.exe` absent | installer WSL 2 : `wsl --install` dans PowerShell, redémarrer, puis installer APEX dans la distribution |
| « WSL est présent mais aucune distribution Linux n'est installée » | WSL activé, aucune distribution | `wsl --install -d Ubuntu` dans PowerShell, puis installer APEX dans cette distribution |
| « WSL ne répond pas correctement » | `wsl.exe` répond en erreur (service WSL) | `wsl --shutdown` dans PowerShell, rouvrir la distribution ; sinon redémarrer Windows |
| « WSL n'est pas démarré » | la distribution est arrêtée | **Démarrer APEX** (la distribution démarre, puis APEX) |
| « Le backend APEX n'est pas démarré » | APEX n'a pas été démarré | **Démarrer APEX** |
| « launch.sh introuvable » | le dossier APEX n'est pas `~/apex` | **Paramètres** → « Dossier APEX dans WSL », puis **Démarrer APEX** |
| APEX BLOCKED | le Startup Gate (FAIL mesuré, précondition non vérifiable) ou `apex doctor` refuse le démarrage | lire la raison et **Journal du démarrage**, corriger, puis **Démarrer APEX** (voir docs/PHASE_1_GATE.md §10) |
| « démarrage impossible : schéma de la base » | `apex db upgrade` a échoué (mot de passe `APEX_DB_*_PASSWORD` manquant dans `.env`, base injoignable) | compléter `.env` depuis `.env.example`, lire **Journal du démarrage**, puis **Démarrer APEX** |
| « Interface APEX non construite » (navigateur) | l'interface web n'a pas été construite | `bash scripts/apex-build-ui.sh` dans le dossier APEX (WSL), puis **Ouvrir APEX** |
| « Session APEX absente ou expirée » (navigateur) | APEX a redémarré, ou lien déjà utilisé | **Ouvrir APEX** dans le Panel |
| « Le backend APEX démarre… » | `launch.sh` prépare Docker, doctor, gate | attendre ; le premier lancement peut durer plusieurs minutes |
| « Le backend APEX s'est arrêté » | fenêtre fermée ou arrêt brutal | relancer `launch.sh` ; lire son dernier message d'erreur |
| « démarre… » puis « n'est pas démarré », et `launch.sh` finit sur « Démarrage d'APEX : REFUSÉ » | `apex gate` a mesuré un FAIL (code 5) ou n'a pas pu vérifier une précondition (code 6) ; code 7 = erreur interne du Gate. Une sonde non autorisée ou l'absence de GPU ne refusent jamais le démarrage (« non vérifiés »). Le Panel n'y est pour rien | lire « FAIL mesurés » / « Préconditions non vérifiées » dans la sortie de `launch.sh` et lever ce point (voir docs/PHASE_1_GATE.md §10). « Phase 2 autorisée : NON » seul n'empêche pas le démarrage |
| « jeton … refusé » / « jeton introuvable » | le Panel ne lit pas le jeton du backend en marche | arrêter les autres backends APEX, relancer `launch.sh` depuis le dossier APEX |
| « Un autre programme utilise le port 8000 » | port occupé | fermer ce programme, ou `launch.sh serve --port 8010` (le Panel suit) |
| « ne répond pas à temps » | machine surchargée ou APEX bloqué | attendre ; sinon **Arrêter APEX** puis **Démarrer APEX** |
| « écoute dans WSL … mais Windows ne l'atteint pas par localhost » | APEX tourne, mais la redirection localhost WSL2 -> Windows ne fonctionne pas | vérifier `%UserProfile%\.wslconfig` (pas de `localhostForwarding=false`), couper un VPN ou vérifier le pare-feu, puis `wsl --shutdown` et relancer `launch.sh` |
| APEX DEGRADED | APEX répond, un service manque | la ligne concernée dit lequel (ex. Ollama sans modèle) |
| VERSION_MISMATCH | Panel et APEX incompatibles | mettre à jour le Panel **ou** le dépôt APEX, selon le message |
| Fenêtre noire | pilote graphique | lancer `APEX.exe --software-rendering` (ou variable `APEX_PANEL_RENDERING=software`) |

Plusieurs distributions Linux : sans réglage, le Panel cherche APEX dans la distribution par défaut
puis dans les **autres distributions déjà démarrées** (il n'en démarre jamais aucune) et affiche
celle où il l'a trouvé. Pour fixer une distribution, ouvrez **Paramètres** et indiquez son nom : le
Panel ne regarde alors plus qu'elle. C'est le seul réglage ; rien d'autre n'est à saisir. La langue
de Windows n'a pas d'importance (l'état des distributions est lu sans dépendre des mots affichés).

## Diagnostic et journaux

- Bouton **Diagnostic** → **Copier le diagnostic** : versions (Panel, APEX, API, protocole), état de
  chaque composant, emplacements des fichiers. **Aucun secret** (le jeton n'y figure jamais).
- Journaux du Panel : `%LOCALAPPDATA%\APEX\logs\panel-AAAAMMJJ.log` (14 derniers jours).
- Journal d'installation : `%TEMP%\Setup Log AAAA-MM-JJ #NNN.txt`.
- Sans fenêtre : `APEX.exe --diagnostic` ou `APEX.exe --status-json` (code de sortie 0 READY,
  1 DEGRADED, 2 NOT READY, 3 VERSION_MISMATCH, 4 BLOCKED).
- Mêmes actions que les boutons, sans fenêtre (automatisation) : `APEX.exe --start` (démarre puis
  attend un verdict établi, même code de sortie), `APEX.exe --stop` (code de `launch.sh stop`),
  `APEX.exe --ui-url` (écrit un lien d'ouverture à usage unique de l'interface).

## Désinstaller

Paramètres Windows → Applications → **APEX** → Désinstaller (ou menu Démarrer).

| Supprimé | Conservé |
|---|---|
| le programme (`%LOCALAPPDATA%\Programs\APEX`) | vos paramètres (`%APPDATA%\APEX`) |
| le raccourci Bureau et le menu Démarrer | les journaux (`%LOCALAPPDATA%\APEX\logs`) |
| l'entrée « Applications installées » | **tout ce qui est dans WSL** : le dépôt APEX, `.apex/`, le jeton, les volumes Docker, le cerveau |

Pour tout effacer côté Windows après désinstallation : supprimez à la main `%APPDATA%\APEX` et
`%LOCALAPPDATA%\APEX`.

## Mettre à jour

Lancez simplement le nouvel `APEX-Setup-x64-v<version>.exe` (fermez d'abord le Panel ; sinon
l'installateur vous le demande). Paramètres, journaux et raccourci sont conservés.
