# Accélération GPU facultative d'APEX.exe (PyTorch CUDA) — état au 16/10/2026

**Aucune compatibilité GPU n'est affirmée ici.** Rien de ce composant n'a encore tourné sur un GPU. Cette
machine de développement n'en a pas, et l'index de PyTorch (download.pytorch.org) lui est refusé par sa
politique réseau. Ce document décrit ce qui est construit et testé, ce qui manque, et le test matériel qui
qualifiera, ou non, la RTX 4060 Ti 8 Go de l'utilisateur.

## 1. Pourquoi un composant à part

- Le runtime de base d'APEX.exe installe `torch==2.14.0` depuis PyPI (`requirements-windows.lock`).
- Sous Windows, cette roue est une build **processeur seulement**. Sans autre composant, ni le Cerveau, ni le
  moteur intégré (transformers), ni la sonde CUDA de Game Studio ne voient le GPU.
- Une roue CUDA pèse environ 2,5 Go à télécharger, et davantage une fois extraite. Elle n'entre donc pas
  dans l'installateur principal : c'est un composant **facultatif**, installé sur un geste de l'utilisateur.

## 2. Ce qui est construit

| Élément | Fichier | Rôle |
|---|---|---|
| Description figée | `setup/packaging/runtime/gpu/gpu-runtime.json` | version de torch (= celle du runtime de base), variantes `cu130`, `cu128`, `cu126`, tailles |
| Listes figées | `setup/packaging/runtime/gpu/torch-<variante>.lock` | `torch @ <adresse> --hash=sha256:<empreinte>` ; **aucune n'existe encore** (§5) |
| Générateur | `setup/packaging/runtime/gpu/lock_gpu.py` | produit les listes là où download.pytorch.org est joignable |
| Composant | `apex/standalone/gpu.py` | choix de la variante, installation, sonde, activation, retrait |
| Ligne de commande | `python -m apex.standalone gpu status\|install\|verify\|disable\|enable\|remove` | sortie JSON ; journal `--log` dans `.apex\runtime\gpu-install.log` |
| Panel | bouton « Accélération GPU (facultatif) », `APEX.exe --install-gpu` | appelle `gpu install --log` ; affiche l'état mesuré au dernier démarrage |
| API | `GET /api/v1/assistant/gpu` → `component` | ce que le dernier démarrage a mesuré |
| Packaging | `build_desktop_release.sh` | copie la description et les listes (quelques Ko), jamais torch CUDA |

## 3. Fonctionnement

**Choix de la variante.**
- La version CUDA **maximale** que le pilote accepte est relevée deux fois :
  - auprès du pilote lui-même : `cuDriverGetVersion` de `nvcuda.dll`, appelé dans un Python séparé, sans
    initialiser le GPU. Il rend un entier, par exemple 13030 pour 13.3 ;
  - dans l'en-tête de `nvidia-smi` : « CUDA Version » (libellé historique) ou « CUDA UMD Version » (pilote
    610.62, constaté le 17/10/2026 sur le PC de l'utilisateur).
- Si les deux relevés diffèrent, **le plus bas** est retenu. Un seul suffit. S'il n'y en a aucun, l'installation
  est refusée et la raison est donnée.
- Trois valeurs ne sont jamais confondues :
  - la version du **pilote** (610.62), lue dans la sortie structurée `--query-gpu` ;
  - la version CUDA **du pilote** (13.3), seule utilisée pour choisir ;
  - une boîte à outils CUDA éventuellement installée (`nvcc`, `CUDA_PATH`), jamais consultée.
- La variante retenue est la plus récente des variantes figées dont la version CUDA ne la dépasse pas.
  Exemples : un pilote qui accepte CUDA 13.0 à 13.3 reçoit `cu130` ; un pilote à 12.6–12.9 reçoit `cu126`
  (`cu128` n'existe pas pour Windows et CPython 3.12).
- On ne s'appuie pas sur la compatibilité « mineure » de CUDA : jamais de variante plus récente que le pilote.
- Une variante sans liste figée, ou dont les tailles sont inconnues, n'est jamais proposée.

**Installation** (`gpu install`) :
1. Refus si APEX tourne : ses bibliothèques seraient en cours d'usage.
2. Pilote mesuré ; refus expliqué si `nvidia-smi` est absent, en échec, sans GPU, si aucune version CUDA du
   pilote n'est lisible, ou si le pilote est trop ancien (« mettez à jour le pilote NVIDIA »). Le journal
   donne chaque relevé : « CUDA accepté par le pilote : 13.3 (API du pilote 13.3, nvidia-smi 13.3) ».
3. Espace disque vérifié sur le volume d'`APEX_HOME` : roue, plus contenu extrait, plus 1 Go de marge.
   Les fichiers temporaires de pip sont placés sur ce même volume.
4. Installation par pip en mode `--require-hashes --no-deps --only-binary=:all: --no-cache-dir --target`.
   Elle se fait dans un dossier provisoire sous `%LOCALAPPDATA%\APEX\python\gpu\`, **jamais dans le runtime
   de base**, qui n'est pas modifié.
5. Une **sonde** est lancée dans un Python séparé (`python -I`) ; un import qui plante n'atteint pas APEX.
   Elle vérifie :
   - torch est importé depuis la surcouche, et non depuis le runtime de base ;
   - `torch.version.cuda` est renseigné et `torch.cuda.is_available()` vaut vrai ;
   - le nom, la capacité et la mémoire du GPU sont lus ;
   - une multiplication de matrices 512×512 donne sur le GPU le même résultat que sur le processeur
     (TF32 coupé).
6. Si la sonde échoue, le dossier provisoire est supprimé : rien n'est gardé, et la raison est écrite
   (`python\gpu\last-install.json`). Si elle réussit, le dossier est renommé et un marqueur est écrit
   (`installed.json` : variante, empreinte de la liste, Python, pilote, sonde).

**Activation à chaque démarrage.** `serve` appelle `gpu.activate()` avant tout import de torch. La
surcouche est placée en tête de `sys.path` **seulement si** toutes ces conditions sont réunies :
- elle est installée et activée ;
- elle correspond à la liste figée de **cette** version d'APEX et à **ce** Python ;
- sa version de torch est exactement celle du runtime de base (`requirements-windows.lock`) ;
- torch n'est pas déjà chargé ;
- le pilote accepte toujours la variante ;
- la sonde réussit **de nouveau**, ce qui coûte quelques secondes au démarrage.

Le partage du GPU entre le Cerveau et Game Studio (`apex/models/gpu_handoff.py`) s'applique ensuite tel quel.

## 4. Mécanismes de repli

Dans chacun de ces cas, APEX démarre sur le processeur, sans jamais être bloqué. La raison est écrite dans
`.apex\runtime\gpu-activation.json`, le journal de démarrage, le Panel et l'API.

| Situation | État | Raison affichée |
|---|---|---|
| Composant non installé | `absent` | « accélération GPU non installée : calcul sur le processeur » (pas même un appel à nvidia-smi) |
| Désactivé par l'utilisateur (`gpu disable`) | `disabled` | « désactivée par l'utilisateur » |
| APEX mis à jour (liste figée changée) | `inactive` | « APEX a été mis à jour… réinstallez-la » |
| torch du runtime de base changé (par exemple par une fusion) sans listes GPU régénérées | `inactive` (et `install` refusé) | « torch du runtime de base (x) ≠ torch de l'accélération GPU (y) : listes GPU à régénérer » |
| Python changé | `inactive` | « surcouche installée pour Python x.y… » |
| Pilote absent, en échec, ou rétrogradé | `inactive` | « pilote NVIDIA indisponible / insuffisant » |
| Sonde en échec (DLL, CUDA indisponible, noyau absent pour ce GPU) | `inactive` | « sonde GPU en échec : … » (sortie de la sonde jointe) |
| Dossier effacé (antivirus, nettoyage) | `failed` | « surcouche GPU incomplète ou effacée » |
| torch déjà chargé | `failed` | « torch déjà chargé avant l'activation » |
| Toute erreur interne | `failed` | l'exception, en clair ; APEX démarre quand même |

**Retrait.** `gpu remove` supprime les dossiers et le marqueur, et APEX redevient tel qu'avant. Il est refusé
pendant qu'APEX tourne. La désinstallation d'APEX supprime aussi la surcouche : elle vit sous
`%LOCALAPPDATA%\APEX\python`, que l'installateur retire déjà avec le reste du runtime re-téléchargeable.

## 5. Versions, dépendances, pilotes

| Élément | Valeur | Statut |
|---|---|---|
| Python | 3.12 (python.org, NuGet 3.12.10), roues `cp312-win_amd64` | IMPLEMENTED |
| torch du runtime de base | 2.14.0 (PyPI : processeur seulement sous Windows) | MEASURED (liste figée) |
| torch de la surcouche | 2.14.0+`cu130` et `cu126` figées (`cu128` absente de l'index) — **même version** que la base | listes produites sur un runner Windows (`gpu-locks`, 16/10/2026). Existence, taille, empreinte et installation de `cu130` vérifiées par `windows-candidate` (`gpu_overlay_check.py`), sans GPU |
| Dépendances de torch | aucune n'est installée par la surcouche (`--no-deps`). `lock_gpu.py` exige que chaque `Requires-Dist` de la roue Windows, évalué pour Windows x64 / CPython 3.12, soit satisfait par le runtime de base. Celui-ci contient déjà filelock, typing-extensions, sympy, networkx, jinja2, fsspec et setuptools. | TESTED (sur des roues de test) ; à confirmer sur les vraies roues |
| Bibliothèques CUDA | incluses dans les roues Windows de PyTorch d'après leur format habituel. Les dépendances `nvidia-*` des roues Linux portent le marqueur `platform_system == "Linux"`, constaté sur torch 2.14.0 de cet environnement. | INFERRED, à confirmer par `lock_gpu.py` |
| Pilote NVIDIA | doit accepter (API du pilote, `nvidia-smi`) une version CUDA ≥ celle de la variante. Le minimum exact par variante est celui des notes de version de NVIDIA ; APEX applique la règle mesurée sur le PC, jamais un numéro de pilote supposé. | IMPLEMENTED |
| RTX 4060 Ti (Ada, capacité 8.9) | à qualifier par la sonde : présence d'un noyau pour ce GPU, calcul de contrôle | BLOCKED (aucun test matériel) |
| Visual C++ Redistributable | déjà requis par le runtime de base ; la préparation d'APEX l'indique si une DLL manque | IMPLEMENTED (message) |
| `llama-cpp-python` CUDA (planificateur de Game Studio) | **hors de ce composant** : bibliothèque et construction distinctes, à déclarer par Codex | BLOCKED (Codex) |

**Produire les listes figées** (machine avec accès à download.pytorch.org : PC de l'utilisateur, CI, ou
Codex) :

```
python setup/packaging/runtime/gpu/lock_gpu.py          # toutes les variantes
python setup/packaging/runtime/gpu/lock_gpu.py cu128    # une seule
```

Le script :
- refuse toute adresse non https ;
- exige le nom exact de la roue, `torch-2.14.0+<variante>-cp312-cp312-win_amd64.whl` ;
- télécharge la roue et compare son SHA-256 à celui de l'index ;
- vérifie ses dépendances contre le runtime de base ;
- écrit la liste figée et complète `gpu-runtime.json` avec les tailles et l'empreinte.

Une variante absente de l'index est déclarée absente, jamais remplacée par une autre. Les listes produites
sont ensuite à committer. Elles n'ont pas été produites par une CI qui contournerait le refus réseau de
l'environnement de développement : ce choix revient à l'utilisateur.

## 6. Test matériel qui qualifiera la RTX 4060 Ti

À faire sur le PC de l'utilisateur, une fois les listes figées générées et APEX.exe reconstruit :

1. `APEX.exe --install-gpu`, ou le bouton du Panel. Attendu : code 0, et `last-install.json` contient la
   variante (`cu130` pour un pilote qui accepte CUDA 13.3), le nom du GPU, `cuda_available: true` et
   `matmul.ok: true`.
2. « Démarrer APEX », puis `GET /api/v1/assistant/gpu` → `component.state == "active"`.
3. Une réponse du Cerveau : sa ligne de mesures affiche « GPU (CUDA) » (`device: cuda`), au lieu de
   « processeur » ; on compare le débit (tokens/s) à celui sur le processeur.
4. Une mission Game Studio pendant une génération : l'accusé de `request_gpu` contient `released: true` et
   la VRAM rendue ; le Cerveau répond sur le processeur pendant la mission, puis revient sur CUDA.
5. Repli : `gpu disable`, puis redémarrage → `disabled`, et le Cerveau tourne sur le processeur. Ensuite
   `gpu enable`.
6. Retrait : APEX arrêté, `gpu remove` → `absent` au démarrage suivant.

Tant que ces six points n'ont pas été constatés, l'état reste **intégré, non qualifié**.

## 7. Tests (sans GPU)

`tests/test_standalone_gpu.py` contient 44 tests. Le pilote, pip et la sonde y sont des doubles ; ils
prouvent la logique de décision et de repli, pas un GPU.
- La **vraie** sonde est exécutée contre le torch de cet environnement (2.14.0+cu130, Linux, sans GPU). Elle
  mesure `cuda_available: false` et refuse un torch chargé hors de la surcouche.
- Le générateur est testé contre un index local en HTTP, sur 127.0.0.1 : roue vérifiée, empreinte fausse,
  dépendance non satisfaite, variante absente.
- Panel : `NativeModeTests`, 248 tests passent.
