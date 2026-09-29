# Installer APEX (Windows)

Le Panel APEX est une petite application Windows qui **affiche l'état réel d'APEX**. APEX lui-même
(le « backend ») tourne dans Linux, via WSL, et se lance avec le `launch.sh` dédié. Le Panel ne
démarre jamais WSL ni le backend : il les observe et vous dit quoi faire.

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

## Utiliser APEX au quotidien

5. Ouvrez votre distribution Linux (par exemple « Ubuntu » dans le menu Démarrer) : c'est WSL.
6. Placez-vous dans le dossier APEX et lancez le launch.sh dédié :
   ```
   bash scripts/apex-launch.sh serve
   ```
   Laissez cette fenêtre ouverte : c'est elle qui fait tourner APEX.
7. Double-cliquez sur **APEX** sur le Bureau.
8. Vérifiez que le Panel indique **APEX READY**.

Le Panel se met à jour tout seul toutes les 5 secondes : vous pouvez l'ouvrir avant ou après
`launch.sh`. Fermer le Panel n'arrête pas APEX ; pour arrêter APEX, faites Ctrl+C dans la fenêtre
Linux (puis `bash scripts/apex-launch.sh stop` pour arrêter l'infrastructure Docker).

## Ce que le Panel affiche

| Ligne | Ce qui est vérifié |
|---|---|
| WSL | la distribution Linux est installée et démarrée (sans jamais la démarrer) |
| Backend | APEX a été lancé par `launch.sh` (en démarrage, en marche, arrêté) |
| API | APEX répond réellement, avec son jeton d'accès |
| Database, Redis, Ollama, Sandbox, Brain | l'état mesuré à l'instant par APEX lui-même |

États : **READY** (vérifié), **STARTING** (en démarrage), **OFFLINE** (arrêté / absent),
**DEGRADED** (répond mais incomplet), **ERROR** (répond mal), **UNKNOWN** (pas mesurable pour
l'instant). En tête : **APEX READY** (tout est prêt), **APEX DEGRADED** (APEX répond, certains
services non), **APEX NOT READY** (avec la raison et quoi faire), **VERSION_MISMATCH** (Panel et
APEX de versions incompatibles).

## Dépannage

| Le Panel affiche | Signification | Que faire |
|---|---|---|
| « WSL n'est pas installé » | `wsl.exe` absent ou aucune distribution | installer WSL 2 : `wsl --install` dans PowerShell, redémarrer, puis installer APEX dans la distribution |
| « WSL n'est pas démarré » | la distribution est arrêtée | ouvrir la distribution Linux puis lancer `launch.sh` |
| « Le backend APEX n'est pas démarré » | `launch.sh` n'a pas été lancé | `bash scripts/apex-launch.sh serve` dans le dossier APEX |
| « Le backend APEX démarre… » | `launch.sh` prépare Docker, doctor, gate | attendre ; le premier lancement peut durer plusieurs minutes |
| « Le backend APEX s'est arrêté » | fenêtre fermée ou arrêt brutal | relancer `launch.sh` ; lire son dernier message d'erreur |
| « jeton … refusé » / « jeton introuvable » | le Panel ne lit pas le jeton du backend en marche | arrêter les autres backends APEX, relancer `launch.sh` depuis le dossier APEX |
| « Un autre programme utilise le port 8000 » | port occupé | fermer ce programme, ou `launch.sh serve --port 8010` (le Panel suit) |
| « ne répond pas à temps » | machine surchargée ou APEX bloqué | attendre ; sinon Ctrl+C puis relancer |
| APEX DEGRADED | APEX répond, un service manque | la ligne concernée dit lequel (ex. Ollama sans modèle) |
| VERSION_MISMATCH | Panel et APEX incompatibles | mettre à jour le Panel **ou** le dépôt APEX, selon le message |
| Fenêtre noire | pilote graphique | lancer `APEX.exe --software-rendering` (ou variable `APEX_PANEL_RENDERING=software`) |

Plusieurs distributions Linux : ouvrez **Paramètres** dans le Panel et indiquez celle où tourne
APEX (vide = distribution par défaut). C'est le seul réglage ; rien d'autre n'est à saisir.

## Diagnostic et journaux

- Bouton **Diagnostic** → **Copier le diagnostic** : versions (Panel, APEX, API, protocole), état de
  chaque composant, emplacements des fichiers. **Aucun secret** (le jeton n'y figure jamais).
- Journaux du Panel : `%LOCALAPPDATA%\APEX\logs\panel-AAAAMMJJ.log` (14 derniers jours).
- Journal d'installation : `%TEMP%\Setup Log AAAA-MM-JJ #NNN.txt`.
- Sans fenêtre : `APEX.exe --diagnostic` ou `APEX.exe --status-json` (code de sortie 0 READY,
  1 DEGRADED, 2 NOT READY, 3 VERSION_MISMATCH).

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
