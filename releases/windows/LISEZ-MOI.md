# APEX pour Windows — APEX.exe 0.4.0

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.4.0.exe` (33 157 419 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `925079cb4f92a091e329326ee33b798da35e65f4b65c5171454d5a7c73a3144c` (fichier `.sha256` à côté) |
| Construit depuis | commit `50bfe92103965666df4fd6ee083619cbe1d8bce0` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |
| Version précédente | 0.3.0 : toujours disponible dans l'historique (commit `73b9ed7`, même dossier) |

APEX tourne **directement sous Windows** : ni WSL, ni Docker, ni Ollama, ni terminal. Nouveau en 0.4.0 :
**l'assistant APEX** — projets, conversations persistantes, quatre modes (Normal, Code, Design, Finance),
paramètres centralisés, thèmes APEX Night et APEX Dune.

1. **Télécharger** : ouvrez `APEX-Setup-x64-v0.4.0.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.4.0.exe -Algorithm SHA256`.
2. **Installer** : double-clic (« Informations complémentaires » → « Exécuter quand même » si SmartScreen
   s'affiche). Une mise à jour depuis 0.3.0 conserve vos données et le modèle ; la liste des
   bibliothèques n'a pas changé depuis 0.3.0 (même empreinte), un runtime déjà préparé reste donc valable.
3. **Préparer APEX** (une fois, ≈ 190 Mo) : Python officiel et bibliothèques d'APEX, chaque fichier vérifié
   par son empreinte.
4. **Installer le modèle** (une fois, ≈ 3,1 Go) : Qwen2.5-Coder-1.5B-Instruct, révision figée, empreintes
   SHA-256 vérifiées.
5. **Démarrer APEX**, puis **Ouvrir APEX** : l'assistant s'ouvre dans le navigateur. Choisissez un mode,
   écrivez votre message, Entrée. Sur processeur, une réponse prend de quelques secondes à quelques minutes.
6. **Arrêter APEX** pour l'arrêter proprement ; projets, conversations et réglages sont conservés.

Premier lancement, modes, projets et dépannage : [`docs/INSTALLER_APEX.md`](../../docs/INSTALLER_APEX.md).
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §9](../../docs/DESKTOP_DISTRIBUTION.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
