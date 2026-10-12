# APEX pour Windows — APEX.exe 0.5.1

| | |
|---|---|
| Installateur | `APEX-Setup-x64-v0.5.1.exe` (51 836 202 octets, Windows 10/11 64 bits, sans droits administrateur) |
| SHA-256 | `45d35a808e172843dc025a1671e4f091ee55a24bc7cbf269e289cd39c1832187` (fichier `.sha256` à côté) |
| Construit depuis | commit `e0725a5cef84` — détail dans `manifest.json` |
| Signature | aucune (aucun certificat de signature de code) : Windows SmartScreen peut demander confirmation |
| Version précédente | 0.5.0 : toujours disponible dans l'historique (commit `cbf9772`, même dossier) |

**Nouveau en 0.5.1 : le Cerveau APEX calcule juste.** Le Cerveau APEX (le modèle construit et entraîné de zéro par
APEX, sans aucun poids externe) écrit l'opération et APEX la calcule exactement — arithmétique, listes (trier,
maximum, somme…), texte (inverser, compter les lettres…) et calendrier (jour de la semaine, ajout de jours, écart
entre deux dates, années bissextiles). Rien d'autre n'est exécuté par cette calculatrice.

Mesuré sur **les mêmes 660 exercices jamais vus** qu'en 0.5.0 : **72 %** de réussite (48 % en 0.5.0) ; **100 %** en
calcul, problèmes, conversions, dates et listes (7,5 à 60 % avant), 97,5 % sur du texte. Exemples réels : 37 × 24 →
888 ; le 14 juillet 1789 → mardi ; 1234 × 5678 → 7006652.

**Ce qu'il ne sait toujours pas faire** : connaissances générales (moins de 10 %), et du code qui ne ressemble pas à
ce qu'il a appris (une demande formulée librement donne souvent une fonction fausse ; une fonction jamais montrée :
0 %). Pour cela, choisissez **APEX intégré** ou Ollama dans la liste **Moteur**. Le vrai Cerveau (≈ 113 M
paramètres) s'entraîne sur le GPU de votre PC sous Ubuntu (WSL) : `bash scripts/wsl/apex-brain-train.sh` (une à
deux journées, reprise automatique) ; il bénéficie des mêmes outils.

Toutes les mesures : [`docs/CERVEAU_APEX_V2.md` §9 bis](../../docs/CERVEAU_APEX_V2.md). Panel (APEX.exe) inchangé.

## Installer (ou mettre à jour)

1. **Fermez APEX** s'il est ouvert ; s'il est bloqué : Gestionnaire des tâches (**Ctrl+Maj+Échap**) → terminer
   chaque « APEX ».
2. **Télécharger** : ouvrez `APEX-Setup-x64-v0.5.1.exe` dans GitHub puis **Download raw file**.
   Vérification facultative (PowerShell) : `Get-FileHash .\APEX-Setup-x64-v0.5.1.exe -Algorithm SHA256`.
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
Ce qui a été vérifié, et ce qui ne l'a pas été : [`docs/DESKTOP_DISTRIBUTION.md` §12 à §16](../../docs/DESKTOP_DISTRIBUTION.md).
Reconstruire l'installateur depuis le dépôt : `bash setup/packaging/build_desktop_release.sh`.
