# APEX pour Windows — APEX.exe 0.6.0

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.6.0.exe` (51 855 256 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `9c4955cf55d29c78edd41778fe3c744adcda0899f4f928d89e4b080de034087b` (fichier `.sha256` à côté) |
| Construit depuis | commit `132d764297bd` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |
| Version précédente | 0.5.1 : toujours disponible dans l'historique (commit `a55fe61`, même dossier) |

**Nouveau en 0.6.0**

- **Le Cerveau APEX écrit du code dans 10 langages** : Python, C#, C++, JavaScript, TypeScript, Java, Rust, Go,
  PHP et Ruby. Mesuré sur **les mêmes 1 220 exercices jamais vus** que le Cerveau 0.5.1 (réévalué) : **83,3 %**
  contre 46,3 %. Code en C++, C#, Java, JavaScript, TypeScript, Rust et PHP : **95 à 100 %** (0 % avant), chaque
  réponse compilée et exécutée par le vrai compilateur du langage.
- **Mode 3D** : décrivez un véhicule, une arme ou un personnage (« un char de 8 m kaki »). APEX construit le
  modèle et écrit pour **Blender** (`.glb` et script), **Unity** (`.obj` et script C#) et **Unreal** (`.glb` et
  classe C++). Les personnages ont un squelette de 20 os lié au maillage. Le panneau **Atelier 3D** montre
  l'aperçu et les fichiers. Avec le Cerveau, les demandes 3D sont comprises à 95 % (87,5 % sur des formulations
  jamais vues). Guide : [`docs/ATELIER_3D.md`](../../docs/ATELIER_3D.md).
- **Extension Blender** « APEX Atelier 3D » : [`releases/blender/apex_atelier3d.zip`](../blender/apex_atelier3d.zip).
- **Gros projets en mode Code** : chercher dans tout le projet (`fs.search`), modifier un passage sans réécrire
  le fichier (`fs.edit`), et jusqu'à 40 étapes d'outils par réponse (Paramètres > Outils).

**Ce qu'il ne sait toujours pas faire.** Le Cerveau ne réussit le code que pour des fonctions de la forme apprise
(signature donnée : filtrer, transformer puis agréger une liste). Une demande formulée librement donne souvent un
code faux. Pour cela, choisissez **APEX intégré** ou Ollama dans la liste **Moteur**. Ses connaissances générales
restent faibles (moins de 10 %). Les scripts Unity et Unreal sont vérifiés pour la syntaxe seulement : à essayer
dans les moteurs. Les modèles 3D sont des bases low-poly, sans textures. Toutes les mesures :
[`docs/CERVEAU_APEX_V2.md` §9 ter](../../docs/CERVEAU_APEX_V2.md).

## Installer (ou mettre à jour)

1. **Fermez APEX** s'il est ouvert ; s'il est bloqué : Gestionnaire des tâches (**Ctrl+Maj+Échap**) → terminer
   chaque « APEX ».
2. **Télécharger** : ouvrez `APEX-Setup-x64-v0.6.0.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.6.0.exe -Algorithm SHA256`.
3. **Installer** : double-clic (« Informations complémentaires » → « Exécuter quand même » si SmartScreen
   s'affiche). Données, conversations, réglages et modèle sont conservés ; bibliothèques inchangées : pas besoin
   de refaire « Préparer APEX ».
4. **Ouvrir** : double-clic sur l'icône **APEX** du bureau, puis **Démarrer APEX** : l'assistant s'affiche dès
   qu'APEX répond.

## Si cela ne marche toujours pas

- **Le moteur d'APEX ne démarre pas** (la fenêtre reste ouverte et le dit) : bouton **Journal du démarrage**, puis
  une capture.
- **La fenêtre se ferme ou n'apparaît pas** : touche **Windows + R**, collez `%LOCALAPPDATA%\APEX\logs`, **Entrée**,
  puis envoyez le fichier `panel-AAAAMMJJ.log` du jour (ou une capture de ses dernières lignes).

Pour forcer l'assistant dans le navigateur : PowerShell,
`[Environment]::SetEnvironmentVariable('APEX_PANEL_BROWSER','1','User')`, puis rouvrir APEX.

Premier lancement, moteurs, modes, projets et dépannage : [`docs/INSTALLER_APEX.md`](../../docs/INSTALLER_APEX.md).
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §12 à §17](../../docs/DESKTOP_DISTRIBUTION.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
