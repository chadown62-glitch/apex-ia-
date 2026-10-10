# APEX pour Windows — APEX.exe 0.4.5

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.4.5.exe` (40 515 182 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `c7a2707b2a6955333dd7489f7dd8154c961f96f0b4ced6f82171d3477f630278` (fichier `.sha256` à côté) |
| Construit depuis | commit `d83fcd71925dbd7ed3981cfa5cf86b6edd2c8891` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |
| Version précédente | 0.4.4 : toujours disponible dans l'historique (commit `b68ef48`, même dossier) |

**Correctif de 0.4.4** : sur un vrai Windows 11, APEX démarrait (APEX READY), puis APEX.exe se fermait à l'ouverture
de l'assistant dans la fenêtre (« Unable to create child window for native control host… »). Cause : APEX.exe ne
déclarait pas Windows 10/11 dans son manifeste, et Windows refusait la fenêtre qui affiche l'assistant. Corrigé ; et
si cet affichage échoue encore pour une autre raison, APEX reste ouvert : l'assistant passe au navigateur, avec la
raison et **Réessayer dans la fenêtre**.

Depuis 0.4.4 : plus de fermeture après **Démarrer APEX** (« Call from invalid thread »).

Toujours présent depuis 0.4.3 : la fenêtre s'ouvre toujours, et un lancement qui échoue le dit.

- Le composant qui affiche l'assistant dans la fenêtre (Microsoft Edge WebView2) n'est créé qu'une fois APEX démarré.
- S'il a planté ou bloqué la fenêtre au lancement précédent, l'assistant s'ouvre dans le navigateur, la page le dit,
  et **Réessayer dans la fenêtre** est proposé.
- Si une fenêtre d'APEX est restée bloquée, le double-clic suivant affiche « APEX est déjà ouvert, mais sa fenêtre
  ne répond pas » : **Oui** la ferme et rouvre APEX.
- Un plantage au démarrage affiche « APEX n'a pas pu s'ouvrir » avec le fichier journal à envoyer.

Ce que vous verriez dans ces cas (captures prises sous Wine, pas sous Windows) :
[fenêtre bloquée](apercu/043-fenetre-bloquee.png) · [lancement précédent interrompu](apercu/043-tentative-interrompue.png).
Le rendu de 0.4.2 (Dune et Night) est inchangé ; aperçus dans [`apercu/`](apercu/).

## Installer (ou mettre à jour)

1. **Fermez APEX** s'il est ouvert ; s'il est bloqué : Gestionnaire des tâches (**Ctrl+Maj+Échap**) → terminer
   chaque « APEX ».
2. **Télécharger** : ouvrez `APEX-Setup-x64-v0.4.5.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.4.5.exe -Algorithm SHA256`.
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
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §12 à §14](../../docs/DESKTOP_DISTRIBUTION.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
