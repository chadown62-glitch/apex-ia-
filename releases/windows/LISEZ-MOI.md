# APEX pour Windows — APEX.exe 0.4.1

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.4.1.exe` (40 506 564 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `7644172359d690afa27c7b603d7f8b3c2d550f78047423441d1c2557a15c19ad` (fichier `.sha256` à côté) |
| Construit depuis | commit `439e108989fc64afa66eaff965ab58d6885e6e87` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |
| Version précédente | 0.4.0 : toujours disponible dans l'historique (commit `8707c4a`, même dossier) |

APEX tourne **directement sous Windows** : ni WSL, ni Docker, ni terminal. Nouveau en 0.4.1, après le premier
essai sur un vrai Windows 11 :

- **L'assistant s'affiche dans la fenêtre d'APEX.exe** (page **Assistant**), plus dans le navigateur. La page
  **État et contrôle** garde le Panel. **Ouvrir dans le navigateur** reste disponible. Affichage par le composant
  Microsoft Edge WebView2 de Windows ; s'il manque, la fenêtre le dit et propose le navigateur.
- **Choix du moteur qui répond** (liste **Moteur** à côté de la zone de saisie, et **Paramètres → Modèles**) :
  **Automatique**, **Ollama**, **APEX intégré** (Qwen2.5-Coder 1.5B exécuté par APEX, sans Ollama), ou **Cerveau
  APEX** — ApexLM, le réseau propre à APEX (version promue `experiment-002:v1`, poids vérifiés à chaque
  chargement). Le Cerveau est **expérimental** : pré-entraîné sur des textes techniques, il ne sait pas encore
  converser ; l'interface le dit.
- Corrigé (constaté sur Windows réel) : bouton **Ouvrir APEX** resté grisé ; sandbox Windows refusée
  (« CreateProcessW : erreur Win32 203 ») ; ligne **Modèles** « non mesuré » tant qu'Ollama n'était pas démarré.

**Mise à jour depuis 0.4.0** : lancez simplement ce nouvel installateur (fermez d'abord APEX). Données,
conversations, réglages et modèle sont conservés ; la liste des bibliothèques n'a pas changé (même empreinte) :
**pas besoin de refaire « Préparer APEX »**.

1. **Télécharger** : ouvrez `APEX-Setup-x64-v0.4.1.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.4.1.exe -Algorithm SHA256`.
2. **Installer** : double-clic (« Informations complémentaires » → « Exécuter quand même » si SmartScreen
   s'affiche).
3. Première installation seulement : **Préparer APEX** (≈ 190 Mo), puis **Installer le modèle** (≈ 3,1 Go).
4. **Démarrer APEX** : l'assistant s'affiche dans la fenêtre dès qu'APEX répond. Choisissez le moteur et le
   mode, écrivez votre message, Entrée.
5. **Arrêter APEX** pour l'arrêter proprement ; projets, conversations et réglages sont conservés.

Premier lancement, moteurs, modes, projets et dépannage : [`docs/INSTALLER_APEX.md`](../../docs/INSTALLER_APEX.md).
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §10](../../docs/DESKTOP_DISTRIBUTION.md).
Le Cerveau APEX (provenance, reproduction, limites) : [`docs/NATIVE_INTELLIGENCE.md` §15](../../docs/NATIVE_INTELLIGENCE.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
