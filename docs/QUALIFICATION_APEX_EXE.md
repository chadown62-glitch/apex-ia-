# Essayer la candidate de qualification d'APEX.exe sur votre PC

Cette candidate réunit le Cerveau APEX, l'assistant et APEX.exe (Claude) avec Game Studio (Codex : #19, #22, #24,
#26) et #5, #11, #13. **Ce n'est pas une release.** Elle sert à mesurer, sur votre PC, ce qui marche et ce qui
bloque. Rien n'a été fusionné dans `apex/integration`.

Elle est construite sur un Windows réel de GitHub par le workflow `windows-candidate`, à partir de l'arbre combiné
(`scripts/integration/rehearse_merge.sh`).

## 1. Télécharger

1. Sur GitHub : **Actions** → **windows-candidate** → le dernier run terminé. Il apparaît « en échec » tant que
   le parcours 3D est bloqué : c'est attendu, et l'installateur est quand même joint. Le run de référence est
   [39068337396](https://github.com/chadown62-glitch/apex-ia-/actions/runs/39068337396) du 17/10/2026, avec la
   détection CUDA corrigée : `APEX-Setup-x64-v0.6.0-candidate-8518860124ac.exe`, 51 957 998 octets, SHA-256
   `7ff5f7efd311be05b8d5ee6650ea846f8ff24988eafcce45a14ea9063491a4c1`. Artefact disponible jusqu'au 16/11/2026.
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

1. **Préparer APEX** — une fois. La liste des bibliothèques a changé avec Game Studio.
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
| Game Studio | « manque : … » avec la liste : Blender s'il n'est pas installé, le planificateur 7B, et les bibliothèques Python (`jsonschema`, pas encore déclarée par Codex). Constaté sur le runner Windows |
| Planificateur 3D | absent : le Qwen2.5-Coder-7B épinglé et `llama-cpp-python` ne sont pas fournis |
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

Rien de cela n'a été essayé sur une carte NVIDIA. Votre essai sera le premier ; d'ici là, CUDA n'est pas
déclaré fonctionnel.

## 6. Le parcours 3D

1. Ouvrez une conversation en mode **3D**.
2. Écrivez : « Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export Blender. »
3. APEX doit **proposer** une mission Game Studio, sans la lancer.
4. Cliquez sur le bouton de lancement.

Game Studio refuse aujourd'hui la mission. Message attendu, constaté sur le runner Windows avec cette
candidate : « une bibliothèque nécessaire à Game Studio manque sur ce PC (ModuleNotFoundError: No module named
'jsonschema') ». Une fois `jsonschema` déclarée par Codex, le refus suivant sera « le modèle planificateur local
de Game Studio (Qwen2.5-Coder-7B) n'est pas installé ». **Ce sont les blocages connus, pas des pannes.** Si un
autre message s'affiche, faites une capture.

## 7. Quoi renvoyer

- Une capture des lignes de composants (§4).
- Le résultat de l'accélération GPU (§5), et le fichier `%LOCALAPPDATA%\APEX\logs\panel-AAAAMMJJ.log` du jour.
- Le message obtenu au clic du parcours 3D (§6).

Détails techniques :
- [`docs/REUNION_BRANCHES.md`](REUNION_BRANCHES.md) ;
- [`docs/ACCELERATION_GPU.md`](ACCELERATION_GPU.md) ;
- [`docs/RAPPORT_INTEGRATION_APEX_EXE.md`](RAPPORT_INTEGRATION_APEX_EXE.md).
