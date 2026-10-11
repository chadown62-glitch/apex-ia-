# APEX pour Windows — APEX.exe 0.5.0

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.5.0.exe` (51 808 415 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `3d90fc7a9065392001da1ea98f4f7855160dc1ba7a21c29852c8007c1f2e9e9c` (fichier `.sha256` à côté) |
| Construit depuis | commit `4a63cbe560983d4b6bd958cfc7cd18ba564d398e` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |
| Version précédente | 0.4.5 : toujours disponible dans l'historique (commit `b9fcb8b`, même dossier) |

**Nouveau : le Cerveau APEX v2, construit et entraîné de zéro par APEX.** Un modèle de langage propre à APEX
(architecture, tokenizer, données, entraînement et quantification APEX-Q5 écrits dans le dépôt, **aucun poids
externe**), entraîné à **converser**. Il répond en premier en mode **Automatique** ; la liste **Moteur** affiche sa
qualité mesurée.

- **Ce qu'il est** : 15,7 M paramètres, poids APEX-Q5 de 11,8 Mo, contexte de 1 024 jetons ; entraîné sans GPU
  (≈ 7,6 h sur 4 cœurs) pour valider toute la chaîne. Sur le processeur : premier mot en 15 à 100 ms, environ
  150 à 200 jetons par seconde (mesuré sur 4 cœurs, pas sur votre PC).
- **Ce qu'il sait faire (mesuré sur 660 exercices jamais vus : 48 %)** : rappeler un fait donné plus tôt dans la
  conversation (100 %), produire du JSON (100 %), logique simple (85 %), dire ce qu'il ne peut pas savoir ou faire
  (82,5 %), corriger un petit bug (77,5 %).
- **Ce qu'il ne sait pas faire** : calculer (7,5 %), connaissances générales (moins de 5 %), écrire une fonction
  qu'il n'a jamais vue (0 %). Pour cela, choisissez **APEX intégré** ou Ollama dans la liste **Moteur**.
- **Le vrai Cerveau** (≈ 113 M paramètres) s'entraîne sur le GPU de votre PC, sous Ubuntu (WSL) :
  `bash scripts/wsl/apex-brain-train.sh` (une à deux journées, reprise automatique). Il s'installe dans
  `%LOCALAPPDATA%\APEX\brain` et remplace celui-ci aussitôt, même APEX ouvert.

Toutes les mesures et leurs limites : [`docs/CERVEAU_APEX_V2.md` §9](../../docs/CERVEAU_APEX_V2.md).
Le Panel (APEX.exe) est inchangé depuis 0.4.5 (Windows 10/11 déclarés, assistant dans la fenêtre).

## Installer (ou mettre à jour)

1. **Fermez APEX** s'il est ouvert ; s'il est bloqué : Gestionnaire des tâches (**Ctrl+Maj+Échap**) → terminer
   chaque « APEX ».
2. **Télécharger** : ouvrez `APEX-Setup-x64-v0.5.0.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.5.0.exe -Algorithm SHA256`.
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
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §12 à §15](../../docs/DESKTOP_DISTRIBUTION.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
