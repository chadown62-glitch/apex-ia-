# Installer APEX (Windows)

`APEX.exe` est l'application Windows d'APEX. Depuis la version 0.3.0, **APEX tourne directement sous
Windows** (« mode Windows natif ») : ni WSL, ni Docker, ni Ollama, ni terminal. La version 0.4.0 a ajouté
**l'assistant APEX** : projets, conversations persistantes, quatre modes (Normal, Code, Design, Finance),
paramètres centralisés et deux thèmes (APEX Night, APEX Dune). La version 0.4.1 affiche l'assistant **dans la
fenêtre d'APEX.exe** (plus de navigateur) et permet de **choisir le moteur qui répond** : Ollama, le moteur
intégré d'APEX, ou le Cerveau APEX (le réseau propre à APEX, expérimental). La version 0.4.2 ne change que le
**rendu** : fond de soie ivoire (Dune) ou de ciel étoilé (Night), surfaces de verre, pastilles d'état
lumineuses. L'installateur dépose le
Panel (`APEX.exe`) et l'application APEX elle-même (le même Runtime, TaskLoop, outils, mémoire et Judge
que dans WSL — l'artefact APEX Runtime, sans l'extension Recherche). Le Panel démarre, arrête et ouvre
APEX, et affiche son état réel. Rien n'est démarré ni téléchargé sans votre clic.

## Récupérer l'installateur

Dans le dépôt GitHub, dossier [`releases/windows/`](../releases/windows/) : `APEX-Setup-x64-v0.4.2.exe`
(≈ 40 Mo), avec son empreinte `.sha256` et son manifeste (`manifest.json` : commit exact, contenu,
empreintes). Sur la page du fichier, bouton **Download raw file**. Vérification facultative, dans
PowerShell : `Get-FileHash .\APEX-Setup-x64-v0.4.2.exe -Algorithm SHA256` (même valeur que le `.sha256`).

## Prérequis

- Windows 10 (1903 ou plus récent) ou Windows 11, 64 bits, compte utilisateur normal (aucun droit
  administrateur demandé).
- Une connexion Internet pour la **préparation unique** (≈ 190 Mo) et le **modèle** (≈ 3,1 Go).
- ≈ 6 Go d'espace disque et, pour exécuter le modèle sur le processeur, 8 Go de mémoire ou plus. APEX mesure
  la mémoire libre avant de charger le modèle : ≈ 7,5 Go libres pour la précision float32, ≈ 4 Go en
  bfloat16 (choisie automatiquement si la mémoire manque) ; en dessous, la réponse est refusée avec les
  chiffres mesurés, jamais un plantage.
- Sur processeur, le modèle produit quelques mots par seconde (mesuré : 3 à 4 tokens/s sur 4 cœurs) : une
  réponse longue prend plusieurs minutes.

## Installer et utiliser APEX (mode Windows natif)

1. Double-cliquez sur `APEX-Setup-x64-v0.4.2.exe`. Le programme n'est pas signé : si Windows affiche
   « Windows a protégé votre ordinateur », cliquez sur « Informations complémentaires » puis
   « Exécuter quand même » (seulement si le fichier vient bien du dépôt APEX). L'installateur vérifie
   lui-même que le Panel démarre et que l'application est complète, puis propose de lancer APEX.
2. **Préparer APEX** (une seule fois) : le Panel télécharge Python 3.12 officiel (paquet NuGet de
   python.org, **empreinte SHA-256 vérifiée** avant tout usage) et les bibliothèques d'APEX (pip,
   **chaque fichier vérifié par son empreinte**, liste figée), puis vérifie réellement que ce Python
   importe APEX et ses bibliothèques. Quelques minutes. En cas de coupure, cliquez de nouveau : ce qui
   est déjà installé est conservé.
3. **Installer le modèle** (une seule fois, ≈ 3,1 Go) : Qwen2.5-Coder-1.5B-Instruct depuis Hugging Face,
   révision figée ; la progression affiche la taille réellement téléchargée. Sans modèle, APEX démarre
   (APEX DEGRADED) mais une tâche s'arrête « bloquée : aucun modèle disponible » — jamais une réponse
   simulée.
4. **Démarrer APEX** : le Panel lance APEX (base embarquée, `apex doctor`, **Startup Gate** — jamais
   contourné), sans fenêtre de console. Quelques secondes plus tard : **APEX READY**, ou **APEX
   DEGRADED** avec la ligne concernée (ex. modèle non installé).
5. **L'assistant s'affiche dans la fenêtre d'APEX** (page **Assistant**, en haut) dès qu'APEX répond — lien à
   usage unique, rien à copier. La page **État et contrôle** garde les boutons du Panel (préparation, démarrage,
   composants, journal). **Ouvrir dans le navigateur** reste disponible. L'affichage intégré utilise le composant
   Microsoft Edge WebView2 de Windows (présent sur Windows 11 et sur les Windows 10 à jour) ; s'il manque, la
   page le dit, donne le lien d'installation et l'assistant s'ouvre dans le navigateur. Le **Centre de
   contrôle** (état du système, Gate, page **Tâches**, mémoire) reste accessible depuis la barre latérale.
6. **Arrêter APEX** : arrêt propre (APEX termine et libère ses fichiers). Fermer la fenêtre du Panel
   pendant qu'APEX tourne **vous demande** s'il faut l'arrêter ou le laisser tourner. Une réponse en
   cours au moment de l'arrêt est close au redémarrage avec le message « interrompu : APEX s'est arrêté
   pendant la génération » — renvoyez le message.
7. Relancer : double-clic sur **APEX**, **Démarrer APEX** — rien à réparer ; projets, conversations,
   réglages, tâches et mémoire sont conservés (base `%LOCALAPPDATA%\APEX\data\apex.db`).

### Premier lancement de l'assistant (0.4.0)

- **Moteur de réponse** (0.4.1) : la liste **Moteur**, à gauche de la zone de saisie (et **Paramètres →
  Modèles**), choisit qui répond — l'état affiché est mesuré à l'instant par APEX :
  - **Automatique** : Ollama s'il tourne avec un modèle déclaré, sinon le moteur intégré ; jamais le Cerveau.
  - **Ollama** : seulement les modèles servis par Ollama sur ce PC (Ollama démarré).
  - **APEX intégré** : Qwen2.5-Coder 1.5B exécuté directement par APEX (« Installer le modèle »), sans Ollama.
  - **Cerveau APEX** (expérimental) : ApexLM, le réseau de neurones propre à APEX (1,9 M paramètres, tokenizer
    d'APEX, aucun poids externe ; version promue `experiment-002:v1` du registre, poids vérifiés à chaque
    chargement). Il est pré-entraîné à prédire la suite de textes techniques mais **n'a jamais été entraîné à
    converser** : il continue le texte de votre message, et sa réponse n'a souvent pas de sens. L'interface le dit.
  Le moteur choisi répond, ou la réponse échoue avec la raison — jamais un autre moteur à sa place. Chaque réponse
  indique le modèle qui l'a produite.
- **Accueil** : choisissez un mode, écrivez votre message, Entrée. La conversation est créée et la réponse
  du modèle s'affiche quand elle est prête (durée réelle affichée, bouton **Arrêter**).
  - **Normal** : questions, explications, rédaction, raisonnement. Aucun outil.
  - **Code** : APEX lit et écrit des fichiers dans l'espace de travail de la conversation (ou du projet),
    et exécute du Python **seulement** si la sandbox Windows a été vérifiée (sinon il le dit). Panneau
    **Fichiers** : les fichiers réellement présents, lisibles.
  - **Design** : direction artistique, palettes, typographie, CSS ; contrastes WCAG calculés. Pas de
    génération ni d'analyse d'images. Panneau **Nuancier** : les couleurs citées et leurs contrastes.
  - **Finance** : budgets, prêts, épargne ; les calculs passent par des outils exacts (prêt, projection,
    calculatrice) dont les tableaux s'affichent dans le panneau **Calculs**. Ni comptes bancaires ni cours
    en temps réel.
- **Projets** (barre latérale, **+**) : regroupent des conversations, des **consignes** envoyées au modèle
  dans chaque conversation du projet, et des **pièces jointes texte** (code, notes, CSV, JSON… ; 512 Ko,
  30 par projet). Images, PDF et documents bureautiques sont refusés avec la raison : APEX ne sait pas les
  lire. La suppression d'un projet (confirmée) supprime ses conversations, ses pièces jointes et son
  espace de travail.
- **Paramètres** : apparence (APEX Night — ciel étoilé, APEX Dune — soie ivoire ; aussi le bouton ☾/☀ du
  centre de contrôle ; animations, particules, réduction des animations),
  modèles (disponibilité mesurée, modèle préféré, température, longueur), runtime (délai d'une réponse),
  comportement (mode par défaut, mémoire entre conversations), outils, confidentialité, ressources
  (précision du modèle, threads). Enregistrés dans la base d'APEX, validés et bornés.
- Sans modèle installé, l'accueil l'annonce (« Aucun modèle disponible ») et une réponse échoue avec la
  raison exacte — jamais une réponse inventée.

### Où sont les fichiers (mode natif)

| Dossier | Contenu |
|---|---|
| `%LOCALAPPDATA%\Programs\APEX` | le Panel `APEX.exe` et l'application (`app\`) |
| `%LOCALAPPDATA%\APEX\python` | Python et bibliothèques préparés (supprimés à la désinstallation) |
| `%LOCALAPPDATA%\APEX\data` | base (projets, conversations, réglages, tâches), espaces de travail (`workspaces\assistant\` pour l'assistant), artefacts (**conservés**) |
| `%LOCALAPPDATA%\APEX\models` | modèle téléchargé (**conservé**) |
| `%LOCALAPPDATA%\APEX\webview` | session et cache de l'affichage intégré de l'assistant (**conservés**) |
| `%LOCALAPPDATA%\Programs\APEX\app\brain` | Cerveau APEX (poids, provenance), livré avec l'application |
| `%LOCALAPPDATA%\APEX\.apex\runtime` | `launch.log` (démarrage), `prepare.log`, `model-install.log`, enregistrement du backend |

### Exécution de code isolée (sandbox Windows)

Le code produit par le modèle est exécuté dans un **AppContainer** Windows sans aucune capacité (pas de
réseau, pas d'accès à vos fichiers hors de son dossier de travail) et dans un **Job Object** (un seul
processus, mémoire et durée bornées). Au démarrage, APEX **vérifie chacune de ces garanties par une
sonde réelle** ; si l'une n'est pas observée, la ligne **Sandbox** est OFFLINE et **toute exécution de
code est refusée** (jamais d'exécution non isolée). Docker Desktop, s'il est présent et vérifié, sert de
recours.

### Dépannage (mode natif)

| Le Panel affiche | Que faire |
|---|---|
| « APEX n'est pas encore prêt sur ce PC » | **Préparer APEX** |
| « APEX a changé depuis la dernière préparation » (après une mise à jour) | **Préparer APEX** (seul ce qui manque est téléchargé) |
| « Préparation impossible : … pip a échoué » | vérifier la connexion (proxy d'entreprise compris), puis **Préparer APEX** ; détail dans **Journal du démarrage** (`prepare.log`) |
| « … installez Microsoft Visual C++ Redistributable » | installer https://aka.ms/vs/17/release/vc_redist.x64.exe puis **Préparer APEX** (normalement inutile : les bibliothèques Visual C++ sont livrées avec le runtime) |
| « démarrage échoué : runtime APEX incomplet » | **Préparer APEX** (réparation), puis **Démarrer APEX** |
| APEX DEGRADED, ligne **Modèles** OFFLINE | **Installer le modèle**, puis **Arrêter APEX** / **Démarrer APEX** |
| APEX DEGRADED, ligne **Sandbox** OFFLINE | l'isolation Windows n'a pas pu être vérifiée sur ce PC : APEX refuse d'exécuter du code ; le détail de la ligne dit quelle garantie manque |
| APEX BLOCKED | le Startup Gate ou `apex doctor` refuse le démarrage : raison affichée, détail dans **Journal du démarrage** |
| « Un autre programme utilise le port 8000 » | fermer ce programme, puis **Démarrer APEX** |
| « L'installation d'APEX est incomplète » | relancer l'installateur |
| Téléchargement du modèle interrompu | cliquer de nouveau sur **Installer le modèle** : la reprise est automatique ; chaque fichier de poids est vérifié par son empreinte SHA-256 publiée, un fichier altéré est supprimé et retéléchargé |

### Dépannage de l'assistant (0.4.0)

| L'assistant affiche | Que faire |
|---|---|
| « Aucun modèle disponible » | Panel : **Installer le modèle**, puis **Arrêter APEX** / **Démarrer APEX** |
| « Moteur « Ollama » indisponible » | démarrez Ollama sur ce PC, ou choisissez **APEX intégré** dans la liste **Moteur** |
| « Cerveau APEX — expérimental » | normal : le Cerveau ne sait pas encore converser ; choisissez **APEX intégré** ou Ollama pour une vraie réponse |
| Page **Assistant** : « ne peut pas s'afficher dans cette fenêtre » | installez « Microsoft Edge WebView2 Runtime » (https://go.microsoft.com/fwlink/p/?LinkId=2124703), rouvrez APEX ; en attendant, **Ouvrir dans le navigateur** |
| « La réponse a échoué — … mémoire insuffisante pour charger le modèle : … Go disponibles, … Go nécessaires » | fermer des applications, ou **Paramètres → Ressources → Précision : bfloat16**, puis **Réessayer** |
| « La réponse a échoué — délai dépassé » | **Paramètres → Runtime** : allonger le délai (jusqu'à 3600 s), ou réduire **Modèles → Longueur maximale** |
| « Une réponse est en cours : attendez-la ou arrêtez-la » | attendre la réponse, ou **Arrêter** ; une seule réponse à la fois par conversation |
| Suppression refusée : « une réponse est en cours » | **Arrêter** la réponse, puis supprimer |
| Pièce jointe « Refusé — format … non pris en charge » | APEX ne lit que des fichiers texte ; copier le contenu utile dans un `.txt` ou `.md` |
| Mode Code : « Exécution de code indisponible sur ce PC » | la sandbox Windows n'est pas vérifiée (ligne **Sandbox** du Panel) : APEX écrit les fichiers mais n'exécute rien |
| « Session APEX absente ou expirée » | rouvrir APEX depuis le Panel (**Ouvrir APEX**) |
| « APEX INJOIGNABLE » | APEX est arrêté : Panel, **Démarrer APEX** |

Sans fenêtre (automatisation) : `APEX.exe --prepare`, `APEX.exe --install-model`, `APEX.exe --start`,
`APEX.exe --status-json`, `APEX.exe --ui-url`, `APEX.exe --stop`.

## Mode WSL (avancé : APEX dans une distribution Linux)

Si `APEX.exe` est installé SANS application à côté de lui (Panel seul, versions ≤ 0.2.x), ou avec
`APEX_PANEL_MODE=wsl`, le Panel pilote un APEX installé dans WSL (profil serveur : PostgreSQL, Redis,
Docker). Le bouton « Démarrer APEX » exécute alors le launcher officiel, `scripts/apex-launch.sh`,
exactement comme dans un terminal — même préflight (Docker, `apex doctor`) et même **Startup Gate**.

### Prérequis du mode WSL (constatés, jamais installés par APEX Desktop)

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
- Pour qu'APEX **exécute** des tâches (pas seulement démarre) : un modèle exécutable. Depuis le
  06/10/2026, **sans Ollama** : le backend NATIF exécute le modèle dans le processus APEX —
  `.venv/bin/pip install -e '.[db,api,native]'` puis, une fois (réseau, ~3 Go) :
  `.venv/bin/apex models fetch Qwen/Qwen2.5-Coder-1.5B-Instruct --name qwen2.5-coder-1.5b-instruct`
  (poids placés dans `$APEX_MODELS_DIR`, par défaut `/home/apex/apex-data/models`) ; `apex models status` dit ce
  qui est réellement exécutable. Ollama reste un backend **optionnel**. Il faut aussi soit l'isolation
  par namespaces, soit Docker pour la sandbox conteneur (image `python:3.12-slim`, téléchargée au
  premier usage). Sans modèle, APEX démarre (Doctor : `models.native` en WARN, avec la commande) mais
  une exécution de tâche s'arrête « blocked » : aucun candidat disponible — jamais une réponse simulée.
- Base de données : son schéma est créé et mis à jour **automatiquement** à chaque démarrage
  (`launch.sh` exécute `apex db upgrade` après avoir démarré PostgreSQL, avant `apex doctor`). Les
  mots de passe `APEX_DB_*_PASSWORD` de `.env` (présents dans `.env.example`) sont nécessaires ; s'ils
  manquent, le démarrage est refusé avec « schéma de la base » (code 10 de launch.sh).
- MinIO est **facultatif** : l'image `minio/minio` n'est plus distribuée publiquement (constaté le
  30/09/2026) et aucune fonction d'APEX ne l'utilise aujourd'hui. `launch.sh` démarre sans lui ;
  `apex doctor` le signale en WARN (« service optionnel : non bloquant »). Pour l'activer :
  `COMPOSE_PROFILES=minio` dans `.env` (voir `docs/MINIO_IMPACT_ANALYSIS.md`).

### Installer (mode WSL)

1. Téléchargez `APEX-Setup-x64-v<version>.exe` (et, si vous le souhaitez, le fichier `.sha256`
   à côté pour vérifier le téléchargement : `certutil -hashfile APEX-Setup-x64-v<version>.exe SHA256`).
2. Double-cliquez sur `APEX-Setup-x64-v<version>.exe`.
   - Aucun droit administrateur n'est demandé : APEX s'installe pour votre compte uniquement.
   - Le programme n'est pas signé : Windows peut afficher « Windows a protégé votre ordinateur ».
     Cliquez sur « Informations complémentaires » puis « Exécuter quand même » si le fichier vient
     bien de votre source APEX.
3. Laissez l'installation se terminer. L'installateur vérifie lui-même que le Panel démarre.
4. Un raccourci **APEX** est créé sur le Bureau (et dans le menu Démarrer).

### Utiliser APEX au quotidien en mode WSL

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

### Ce que le Panel affiche (mode WSL)

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

### Dépannage (mode WSL)

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
| le programme et l'application (`%LOCALAPPDATA%\Programs\APEX`) | vos données : base, espaces de travail (`%LOCALAPPDATA%\APEX\data`) |
| le runtime préparé (`%LOCALAPPDATA%\APEX\python`) | le modèle (`%LOCALAPPDATA%\APEX\models`) |
| le raccourci Bureau et le menu Démarrer | vos paramètres (`%APPDATA%\APEX`) et les journaux (`%LOCALAPPDATA%\APEX\logs`) |
| l'entrée « Applications installées » | **tout ce qui est dans WSL** : le dépôt APEX, `.apex/`, le jeton, les volumes Docker, le cerveau |

APEX est arrêté proprement avant la suppression s'il tournait.

Pour tout effacer côté Windows après désinstallation : supprimez à la main `%APPDATA%\APEX` et
`%LOCALAPPDATA%\APEX`.

## Mettre à jour

Lancez simplement le nouvel `APEX-Setup-x64-v<version>.exe` (fermez d'abord le Panel ; sinon
l'installateur vous le demande). Un APEX en cours d'exécution est arrêté proprement avant
remplacement ; l'application (`app\`) est remplacée entièrement. Données, modèle, paramètres, journaux
et raccourci sont conservés. Si la liste des bibliothèques a changé, le Panel affiche « APEX a changé
depuis la dernière préparation » : **Préparer APEX**.
