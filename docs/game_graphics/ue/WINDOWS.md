# Validation UE5.8 sur Windows

**Statut : lanceur préparé, exécution native BLOCKED dans le cloud Linux.** Une archive
contenant les scripts ou un `.uproject` préparé ne constitue pas une scène créée dans UE.
La compatibilité réelle des API UE5.8, les captures et la correction restent UNVERIFIED.

## Action minimale

Extraire **APEX-UE58-validation.zip** dans un dossier local ordinaire hors de
`C:\GameProject`, puis double-cliquer **Run-APEX-UE58.cmd** à la racine.
Fermer d'abord les sessions Unreal/Blender/modèle concurrentes ; le lanceur les refuse
et ne les ferme pas. Garder la session Windows ouverte pendant les rendus.

Le lanceur détecte UE5.8 par le registre Epic, le manifeste du Launcher, les dossiers
standards ou `APEX_UE_ROOT`. Il vérifie `Build.version`, l'éditeur et les plugins Python
et Editor Scripting. Leur présence ne qualifie pas les API.
Il essaie le Python livré avec UE puis les Python installés : **CPython x64 3.11 à 3.13**
avec `venv` et `ensurepip`. Si ce prérequis manque, il indique BLOCKED ; installer alors
[Python 3.12 x64 officiel](https://www.python.org/downloads/windows/) et relancer.
Aucun droit administrateur n'est nécessaire au lanceur.

Trois dépendances (`psutil`, Pillow, NumPy ; environ 21 Mo de roues Windows) sont téléchargées
par HTTPS depuis PyPI/files.pythonhosted.org, versions et SHA256 imposés, dans un environnement
virtuel temporaire. Aucun téléchargement du moteur, de poids, de CUDA ni modification de
l'installation Python UE. Une indisponibilité réseau produit un diagnostic conservé.

Chaque lancement crée `%TEMP%\APEX-UE58-<uuid>`. Ce dossier est conservé intégralement.
Il n'existe aucun paramètre permettant de choisir un projet existant. Les chemins personnels
protégés, jonctions/liens, chemins réseau et alias courts sont refusés par le bootstrap.
Le contrôleur Python vérifie de nouveau le chemin résolu et le marqueur du dossier.
Le `ExecutionPolicy Bypass` du CMD s'applique uniquement au processus PowerShell lancé ;
il ne modifie aucune politique persistante. Il ne contourne pas les restrictions d'entreprise.

## Parcours réel demandé au moteur

1. `01-baseline` : nouveau projet, plan Qwen accepté de PR16 (sept acteurs/trois matériaux),
   création/sauvegarde native, arrêt puis **second processus éditeur** pour relire et capturer.
2. Le juge `UnrealGraphicsOracle` via `Tier0Judge` contrôle les critères utilisateur,
   géométrie, poses, PBR, lumière, collisions et quatre captures natives non vides distinctes.
   Sans PASS initial, aucun défaut n'est injecté.
3. `02-fault` : copie vérifiée du contenu sauvegardé dans un projet neuf, ouverture native
   de la carte précédente, sauvegarde sous une nouvelle version, déplacement réel de `road`
   de +150 cm sur X. Un autre éditeur doit constater **FAIL, uniquement `pose:road`**.
4. `03-corrected` : nouvelle copie/version, correction native du seul transform de `road`
   vers la valeur du contrat, relecture indépendante, quatre captures et jugement PASS exigés.
   Une seule correction est autorisée, dans le budget utilisateur ; pas de reconstruction
   pour masquer le défaut, pas de boucle sans gain ni de régression acceptée.

Les plans attendus ne sont pas modifiés pour accepter le défaut. Les anciens projets,
cartes et rapports sont conservés et leurs SHA contrôlés. Les PID, durées, codes de sortie,
journaux natifs (`*-native.log`) et captures sont recueillis. Une API indisponible doit
produire son erreur réelle et arrêter la qualification ; aucune substitution d'image ou
faux module `unreal` n'existe dans ce parcours.

Limites : 15 minutes par processus, délai de capture 8 minutes, limite d'arrêt applicative
sur RSS échantillonnée 12 000 Mio. Minimum initial : 4 Gio RAM et 8 Gio disque disponibles.
Le dossier partagé DDC est désactivé ; l'éditeur peut encore écrire ses caches utilisateur.
Ce n'est pas une sandbox OS. Les descendants observés sont suivis par identité de processus,
mais l'absence de descendants non observés n'est pas certifiée par un Job Object Windows.
Aucun processus existant n'est terminé par nom.

## Résultats et retour

Le chemin est affiché dans la console. Le dossier contient `validation-report.json`,
les projets versionnés (`project/ApexGraphics.uproject`, Content/Config/Requests/Tools),
les captures et jugements de chaque étape, `correction-request.json`, les critères,
les mesures matérielles Windows et les échantillons NVIDIA par opération.
L'archive voisine **APEX-UE58-<uuid>.zip** contient les preuves et un index SHA256 ; elle
exclut le venv et les caches. Aucun envoi automatique. Conserver le dossier et transmettre
l'archive après l'avoir examinée pour poursuivre le diagnostic dans Codex.

Les mesures NVIDIA sont celles du GPU entier (UUID, pilote, VRAM totale/utilisée,
utilisation), avec horodatages ; elles ne sont pas une attribution exclusive à Unreal,
ni un pic exact ni un benchmark FPS. Si NVIDIA n'est pas interrogeable, ces données restent
UNKNOWN. Les quatre vues et le juge objectif ne certifient pas une esthétique photoréaliste.
Les niveaux `jouable`, `soigne`, `photorealiste` restent bloqués lorsqu'ils exigent des mesures
FPS ou références calibrées encore absentes. PCG, Nanite/Lumen, paysages, villes, gameplay
et la génération neuronale de #14 ne sont pas qualifiés par ce lancement.

## Options et reconstruction

Depuis le dossier extrait, PowerShell :

```powershell
.\scripts\game_graphics\windows\Run-APEX-UE58.ps1 -EngineRoot 'D:\Epic Games\UE_5.8'
.\scripts\game_graphics\windows\Run-APEX-UE58.ps1 -PreflightOnly
.\scripts\game_graphics\windows\Run-APEX-UE58.ps1 -PythonExe 'C:\Python312\python.exe' -CriteriaFile '.\criteria.json'
```

`-PreflightOnly` ne lance pas Unreal. Les options se combinent. Un fichier de critères peut
contenir par exemple `{"level":"prototype","max_triangles":100000,"position_tolerance_cm":0.2}` ;
les autres valeurs suivent `SceneCriteria.for_level`, les seuils ne sont pas abaissés automatiquement.
Le plan Qwen archivé est rejoué tel quel ; aucune nouvelle inférence n'est prétendue.

Dans un checkout de PR16, avec Python, créer une nouvelle archive (chemin inexistant) :

```bash
python scripts/game_graphics/package_ue_windows.py --output /tmp/APEX-UE58-validation.zip
```

L'archive embarque les sources nécessaires existantes et un manifeste SHA256 avec le commit
et l'état propre/modifié du checkout. Aucun binaire Unreal, modèle ou faux projet exécuté.
