# APEX pour Windows — APEX.exe 0.4.3

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.4.3.exe` (40 515 094 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `8633f0de789d65d1c1915eb768407c83c213f0bfa162e2b0adc397786fa3be24` (fichier `.sha256` à côté) |
| Construit depuis | commit `d341a2454cf087de92278b5f4bdc9c09c2df88b6` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |
| Version précédente | 0.4.2 : toujours disponible dans l'historique (commit `d33cbf5`, même dossier) |

**Correctif de 0.4.2** : sur un vrai Windows 11, un double-clic sur l'icône d'APEX pouvait ne rien afficher.
En 0.4.3, la fenêtre s'ouvre toujours, et un lancement qui échoue le dit :

- la fenêtre s'affiche d'abord ; le composant qui affiche l'assistant dans la fenêtre (Microsoft Edge WebView2)
  n'est créé qu'une fois APEX démarré ;
- si ce composant a planté ou bloqué la fenêtre au lancement précédent, APEX le reconnaît : l'assistant s'ouvre
  dans le navigateur, la page le dit, et **Réessayer dans la fenêtre** est proposé ;
- si une fenêtre d'APEX est restée bloquée, le double-clic suivant affiche « APEX est déjà ouvert, mais sa
  fenêtre ne répond pas » : **Oui** la ferme et rouvre APEX ;
- un plantage au démarrage affiche « APEX n'a pas pu s'ouvrir » avec le fichier journal à envoyer.

Ce que vous verriez dans ces cas (captures prises sous Wine, pas sous Windows) :
[fenêtre bloquée](apercu/043-fenetre-bloquee.png) · [lancement précédent interrompu](apercu/043-tentative-interrompue.png).
Le rendu de 0.4.2 (Dune et Night) est inchangé ; aperçus dans [`apercu/`](apercu/).

## Installer (ou mettre à jour)

1. **Si APEX est bloqué** : Gestionnaire des tâches (**Ctrl+Maj+Échap**) → terminer chaque « APEX ».
2. **Télécharger** : ouvrez `APEX-Setup-x64-v0.4.3.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.4.3.exe -Algorithm SHA256`.
3. **Installer** : double-clic (« Informations complémentaires » → « Exécuter quand même » si SmartScreen
   s'affiche). Données, conversations, réglages et modèle sont conservés ; bibliothèques inchangées : pas besoin
   de refaire « Préparer APEX ».
4. **Ouvrir** : double-clic sur l'icône **APEX** du bureau. Première installation seulement : **Préparer APEX**
   (≈ 190 Mo), puis **Installer le modèle** (≈ 3,1 Go).
5. **Démarrer APEX** : l'assistant s'affiche dans la fenêtre dès qu'APEX répond.

## Si cela ne marche toujours pas

Envoyez le journal du Panel : touche **Windows + R**, collez `%LOCALAPPDATA%\APEX\logs`, **Entrée**, puis le
fichier `panel-AAAAMMJJ.log` du jour (ou une capture de ses dernières lignes). Il dit à quelle étape le lancement
s'est arrêté.

Pour forcer l'assistant dans le navigateur : PowerShell,
`[Environment]::SetEnvironmentVariable('APEX_PANEL_BROWSER','1','User')`, puis rouvrir APEX.

Premier lancement, moteurs, modes, projets et dépannage : [`docs/INSTALLER_APEX.md`](../../docs/INSTALLER_APEX.md).
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §12](../../docs/DESKTOP_DISTRIBUTION.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
