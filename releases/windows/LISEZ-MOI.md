# APEX pour Windows — APEX.exe 0.4.4

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.4.4.exe` (40 510 113 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `e8c132cb9e957f82cc00c01a07feed8745620881ab5bf9f7a4cdaca7b2f443c7` (fichier `.sha256` à côté) |
| Construit depuis | commit `301bac0262cb4faea425ae5243d8f2e610518664` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |
| Version précédente | 0.4.3 : toujours disponible dans l'historique (commit `aa6323e`, même dossier) |

**Correctif de 0.4.3** : sur un vrai Windows 11, APEX.exe se fermait environ 2 s après **Démarrer APEX**
(« APEX n'a pas pu s'ouvrir — InvalidOperationException — Call from invalid thread »). Cause trouvée et reproduite :
un bouton était mis à jour depuis un fil d'arrière-plan, ce que Windows refuse. Corrigé pour tous les boutons de la
fenêtre, y compris **Enregistrer** dans les paramètres, qui avait le même défaut.

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
2. **Télécharger** : ouvrez `APEX-Setup-x64-v0.4.4.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.4.4.exe -Algorithm SHA256`.
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
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §12 et §13](../../docs/DESKTOP_DISTRIBUTION.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
