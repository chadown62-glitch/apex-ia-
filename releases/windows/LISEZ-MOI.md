# APEX pour Windows — APEX.exe 0.3.0

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.3.0.exe` (33 116 313 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `fbe3246d2d33826d581f9e0436b7a5f0a42278c055d92f1b4080adffed075bee` (fichier `.sha256` à côté) |
| Construit depuis | commit `800bde305ad7d8f51f801d926db9be0b7899fbeb` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |

APEX tourne **directement sous Windows** : ni WSL, ni Docker, ni Ollama, ni terminal.

1. **Télécharger** : ouvrez `APEX-Setup-x64-v0.3.0.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.3.0.exe -Algorithm SHA256`.
2. **Installer** : double-clic (« Informations complémentaires » → « Exécuter quand même » si SmartScreen
   s'affiche). Un raccourci **APEX** est créé sur le Bureau.
3. **Préparer APEX** (une fois, ≈ 190 Mo) : Python officiel et bibliothèques d'APEX, chaque fichier vérifié
   par son empreinte.
4. **Installer le modèle** (une fois, ≈ 3,1 Go) : Qwen2.5-Coder-1.5B-Instruct, révision figée.
5. **Démarrer APEX**, puis **Ouvrir APEX** : l'interface s'ouvre dans le navigateur. Page **Tâches** :
   créez une tâche, lancez-la, suivez ses étapes et son résultat.
6. **Arrêter APEX** pour l'arrêter proprement ; le relancer ne demande aucune réparation.

Procédure complète, emplacements des fichiers et dépannage : [`docs/INSTALLER_APEX.md`](../../docs/INSTALLER_APEX.md).
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §8](../../docs/DESKTOP_DISTRIBUTION.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
