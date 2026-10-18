# Essayer la candidate de qualification d'APEX.exe sur votre PC

Cette candidate réunit le Cerveau APEX, l'assistant et APEX.exe (Claude) avec Game Studio (Codex : #19, #22, #24,
#26, #29, #31) et #5, #11, #13. **Ce n'est pas une release.** Elle sert à mesurer, sur votre PC, ce qui marche et
ce qui bloque. Rien n'a été fusionné dans `apex/integration`.

Elle est construite sur un Windows réel de GitHub par le workflow `windows-candidate`, à partir de l'arbre combiné
(`scripts/integration/rehearse_merge.sh`).

## 1. Télécharger

1. Sur GitHub : **Actions** → **windows-candidate** → le dernier run terminé. Le run de référence est
   [39155790261](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39155790261) du 18/10/2026 (tentative
   10 : Claude `e41d7ce`, Game Studio de Codex à `550b347`). Il est **vert**, parcours 3D par l'interface compris
   (§7) : `APEX-Setup-x64-v0.6.0-candidate-4305e517faa8.exe`, 51 985 455 octets, SHA-256
   `1e16d3d0093bef0253028eb309282a412606f47ef388353ea6d9f923061aa933`. Artefact disponible jusqu'au 17/11/2026.
   Elle remplace celle de la tentative 9 (run 39150876510, `f1850922…`) : même pile Game Studio, avec le brief
   avant le clic et le volet « Qualité de cette création ».
2. En bas de la page, **Artifacts** → `apex-qualification-installer`. Il faut être connecté à GitHub.
3. Décompressez le zip. Il contient :
   - `APEX-Setup-x64-v0.6.0-candidate-<commit>.exe` ;
   - son `.sha256` ;
   - `manifest.json` (contenu exact) et `rehearsal.md` (branches réunies).
4. Facultatif, dans PowerShell : `Get-FileHash .\APEX-Setup-x64-v0.6.0-candidate-*.exe -Algorithm SHA256`. Le
   résultat doit être égal au contenu du `.sha256`.

L'artefact est gardé 30 jours.

## 2. Installer

1. **Fermez APEX** s'il est ouvert.
2. Double-cliquez sur l'installateur. Si SmartScreen s'affiche : « Informations complémentaires » → « Exécuter
   quand même ». L'installateur n'est pas signé.
3. La candidate s'annonce « 0.6.0 », comme la version publiée, et la **remplace**. Vos conversations, réglages et
   modèles sont conservés.
4. **Revenir en arrière** : réinstallez `releases/windows/APEX-Setup-x64-v0.6.0.exe`. Vos données restent
   conservées.

## 3. Premier démarrage

Dans la fenêtre d'APEX :

1. **Préparer APEX** — une fois. La liste des bibliothèques a changé avec Game Studio : elle installe
   maintenant `jsonschema` (constaté sur le runner Windows, tentative 6).
2. **Installer le modèle**, s'il ne l'est pas déjà (moteur intégré, environ 3 Go).
3. **Démarrer APEX**.

## 4. Lire l'état des composants

La fenêtre d'APEX et les Paramètres affichent une ligne par composant. Chaque état vient d'une vraie mesure sur
votre PC. Un composant jamais qualifié chez vous n'est jamais affiché « prêt ».

| Ligne | Ce que vous devriez voir aujourd'hui |
|---|---|
| Cerveau APEX | prêt |
| Moteur intégré | prêt, une fois le modèle installé |
| GPU CUDA | « GPU détecté : NVIDIA GeForce RTX 4060 Ti (pilote 610.62, CUDA 13.3) — accélération GPU non installée », puis l'invitation « Accélération GPU (facultatif) » |
| Game Studio | « manque : … » avec la liste : Blender s'il n'est pas installé, et le planificateur 7B tant qu'il n'est pas installé (§6). `jsonschema` n'y figure plus. Constaté sur le runner Windows |
| Planificateur 3D | absent tant que son moteur et ses poids ne sont pas installés (§6) |
| Blender | sa version, s'il est installé et trouvé ; sinon « introuvable » |
| Game Graphics, Game Engineer | selon la sonde de Game Studio |

Notez ce que vous voyez réellement : une capture de ces lignes suffit.

## 5. Accélération GPU (facultatif)

1. APEX arrêté, cliquez sur **Accélération GPU (facultatif)**.
   - Taille d'après le pilote : CUDA 13 → environ 2,0 Go téléchargés (3,0 Go installés) ; CUDA 12.6 à 12.9 →
     environ 2,6 Go (4,2 Go installés).
   - Un pilote plus ancien est refusé, avec la raison.
2. APEX vérifie le pilote, télécharge des fichiers figés et vérifiés par empreinte, puis teste le GPU.
   - Avec votre pilote 610.62, le journal du Panel doit dire « CUDA accepté par le pilote : 13.3 (API du
     pilote 13.3, nvidia-smi 13.3) », puis « variante cu130 (CUDA 13.0 ≤ CUDA 13.3 accepté par le pilote) ».
   - La candidate précédente refusait ici (code 2, « version CUDA du pilote illisible ») : elle ne lisait que
     l'ancien libellé « CUDA Version ».
3. L'accélération n'est gardée que si ce test réussit. Sinon, APEX reste sur le processeur et dit pourquoi
   (`%LOCALAPPDATA%\APEX\python\gpu\last-install.json`).
4. **Démarrer APEX** : la ligne « GPU CUDA » doit dire « prêt » avec le nom de votre carte, et « accélération
   GPU active (cu130) ».
5. **Le Cerveau sur le GPU** : posez la même question qu'avant (« 37 × 24 ? »). Sous la réponse, la ligne de
   mesures dit maintenant où le calcul a eu lieu : « GPU (CUDA) » au lieu de « processeur », avec le type de
   nombres et le débit en tokens/s. Comparez ce débit à celui mesuré sur le processeur (environ 131 tokens/s
   chez vous).

Votre essai du 17/10/2026, sur la RTX 4060 Ti, est le seul essai sur une carte NVIDIA : d'après votre retour,
le Cerveau ApexLM V2 y tourne avec PyTorch CUDA. Les runners de GitHub n'ont pas de GPU. Game Studio, lui, ne
calcule pas encore sur le GPU (§6).

## 6. Installer les composants de Game Studio

Dans une conversation en mode **3D**, l'Atelier 3D affiche « Composants de Game Studio : N à installer ». Chaque
ligne dit son état mesuré sur votre PC, sa taille et sa provenance quand elles sont connues. Un bouton
« Installer » n'apparaît que si Game Studio fournit une installation vérifiée ; chaque bouton demande votre
confirmation.

1. **Moteur d'inférence du planificateur** (`llama-cpp-python` 0.3.36, processeur, licence MIT) : environ 8 Mo,
   empreintes vérifiées. Constaté sur le runner Windows (run 39140125328) : installé par ce bouton, importé, puis
   exécuté avec les poids pendant la mission.
2. **Planificateur des missions** (Qwen2.5-Coder-7B Q5_K_M, licence Apache-2.0) : **5,4 Go** à télécharger.
   Son bouton n'apparaît qu'après le moteur. La confirmation indique la taille et l'espace libre nécessaire.
   Constaté sur le runner : installé par ce bouton, puis vérifié par Game Studio (licence, empreinte).
3. **Blender** : Game Studio ne l'installe pas. Installez **Blender 4.3.2** depuis blender.org (installateur
   `.msi`, dossier par défaut `C:\Program Files\Blender Foundation\Blender 4.3`). APEX le cherche dans
   `APEX_BLENDER_EXECUTABLE`, puis dans le PATH, puis dans Program Files, où il prend la version la plus récente.
   Si une autre version plus récente est installée, définissez la variable d'environnement Windows
   `APEX_BLENDER_EXECUTABLE` vers le `blender.exe` de la 4.3, puis redémarrez APEX. Le runner a utilisé la version
   portable officielle (zip, même empreinte que la qualification de Codex) par cette variable.
4. **Bibliothèques Python** : installées par « Préparer APEX ». Rien à faire si elles sont « présent ».

Ensuite, dans **Paramètres > Outils > Calcul des missions**, choisissez **Processeur**. Game Studio refuse
encore les missions sur le GPU : le planificateur n'a pas de moteur CUDA, et aucun test n'a eu lieu sur une carte
NVIDIA (garde de Codex).

## 7. La première création 3D

1. Ouvrez une conversation en mode **3D**.
2. Écrivez : « Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export Blender. »
3. APEX doit **proposer** une mission Game Studio, sans la lancer.
4. Cliquez sur **Lancer la mission**.
   - Avec cette candidate, une mission sur le processeur ne prend plus le verrou du GPU : **le Cerveau reste sur
     la RTX** pendant la mission. Vérifié sur le runner avec les verrous Windows réels et un « CUDA » simulé
     (`lease_check.py`, 7 contrôles PASS) ; jamais encore sur une vraie carte.
5. Suivez la mission dans l'Atelier 3D : état, étapes, puis aperçus et fichiers. Les fichiers se téléchargent
   depuis la page Game Studio (lien « Ouvrir dans Game Studio »). Sur le runner (4 cœurs, sans GPU), la mission
   a duré environ 13 minutes ; chez vous, la durée reste à mesurer.
6. Si la mission s'arrête, l'Atelier 3D en donne la raison, et propose **Reprendre la mission** quand c'est
   possible.

Sans les composants du §6, Game Studio refuse la mission avec la raison, par exemple « le modèle planificateur
local de Game Studio (Qwen2.5-Coder-7B) n'est pas installé ». **Ce sont les blocages connus, pas des pannes.**
Si un autre message s'affiche, faites une capture.

**Constaté sur le runner Windows Server** (runs 39150876510 et 39155790261), dans l'APEX.exe installé, par de vrais
clics dans Edge (le moteur de WebView2) sur la page qu'ouvre le Panel :
- « Processeur » enregistré dans Paramètres > Outils, la conversation 3D, la proposition ;
- les deux composants installés par leurs boutons, confirmations acceptées ;
- « Lancer la mission », puis « Annuler la mission » et « Reprendre la mission » : environ 15 minutes jusqu'à
  « terminée, à revoir » ;
- 4 aperçus affichés ; `asset.blend` (3 767 004 octets) et `asset.glb` (1 108 632 octets) téléchargés par les
  boutons de Game Studio, identiques à leur empreinte ;
- une seconde mission par l'API, livrée elle aussi (13 fichiers) ;
- avec la tentative 10 : sous la proposition, « Ce que Game Studio fera de votre demande » ; sur la mission
  livrée, « Qualité de cette création » (versions, ce que le juge vérifie, mesures des rendus) et le formulaire
  d'avis. Notez-y la création : vos avis sont les premières données humaines pour calibrer les futurs juges.

Ce n'est pas encore votre PC : ni Windows 11, ni la fenêtre d'APEX.exe elle-même, ni la RTX. La première mission
chez vous reste la preuve qui compte.

## 8. Quoi renvoyer

- Une capture des lignes de composants (§4) et de la liste des composants de Game Studio (§6).
- Le résultat de l'accélération GPU (§5), et le fichier `%LOCALAPPDATA%\APEX\logs\panel-AAAAMMJJ.log` du jour.
- Pour la mission (§7) : les états successifs, la raison d'un arrêt, les aperçus et la liste des fichiers.

Détails techniques :
- [`docs/REUNION_BRANCHES.md`](REUNION_BRANCHES.md) ;
- [`docs/ACCELERATION_GPU.md`](ACCELERATION_GPU.md) ;
- [`docs/RAPPORT_INTEGRATION_APEX_EXE.md`](RAPPORT_INTEGRATION_APEX_EXE.md) ;
- [`docs/REPRISE_CODEX_GAME_STUDIO.md`](REPRISE_CODEX_GAME_STUDIO.md).
