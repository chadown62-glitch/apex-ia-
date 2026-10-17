# Planificateur Windows : CPU qualifiable, CUDA candidat

## Dépendances CPU reproductibles

Le runtime APEX utilise CPython x64 **3.12.10**, paquet NuGet officiel référencé
avec SHA256 dans `setup/packaging/runtime/python-runtime.json`. Les deux fichiers
suivants doivent être installés avec cet interpréteur, dans une installation
isolée, avant le démarrage du backend :

- `setup/packaging/runtime/requirements-windows.lock`
- `setup/packaging/runtime/requirements-planner-cpu-windows.lock`

Commande du lanceur de qualification : `python.exe -m pip install --require-hashes
--only-binary=:all: --no-deps -r <runtime.lock> -r <planner-cpu.lock>`.
Aucune résolution libre de dépendances. Les locks incluent jsonschema et les
extras serveur (pydantic-settings, sse-starlette, starlette-context, etc.).

Roue CPU officielle : `llama_cpp_python-0.3.36-py3-none-win_amd64.whl`,
7 767 370 octets, SHA256
`ae5e88a2cf464e5a2dde7c8898c2d5c71cc24d03cac6396bf7509456bde0b9fb`.
Source : release GitHub `abetlen/llama-cpp-python`, tag `v0.3.36`, licence MIT.
La qualification native distingue import de DLL, import de CLI serveur, serveur
écoutant avec un modèle et génération effective. `--help` ne prouve pas les deux
derniers points.

## Poids et contrat pour le Panel (Claude)

`python.exe -m apex.game_studio dependencies` retourne les capacités et le contrat
`planner_installation`. Il ne télécharge rien. Le chemin dérive du même
`APEX_HOME` que l'application ; ne pas créer un deuxième profil sans le vouloir.

Après consentement humain au volume et à Apache-2.0, le Panel peut lancer dans un
processus suivi :

```
python.exe -m apex.game_studio install-planner --accept-download --accept-apache-2-license
```

Ce n'est pas une commande que l'utilisateur doit taper dans le parcours produit.
Le Panel affiche le consentement, lance la commande, conserve les logs et traite
le code retour. L'installateur reste à Claude. Le helper conserve tout manifeste
existant non vérifié, vérifie le disque, sérialise les installations et contrôle
licence/poids avant de produire `coder-model.json`. L'absence de poids ne bloque
pas le démarrage normal d'APEX.

Poids exacts : Qwen/Qwen2.5-Coder-7B-Instruct-GGUF,
commit `13fb94bfda8c8cf22497dc57b78f391a9acb426a`, Q5_K_M,
5 444 831 232 octets, SHA256
`586844eac4d6d6321689f0192c8aa8e69cd8625974a5cc2d925b1a03366e4d16`.
Aucun autre checkpoint, modèle Vision ou plan fixe ne le remplace.

La demande de référence complète est conservée dans `qualification.REQUEST`.
Le backend utilisé est **CPU explicitement choisi pour la qualification**. Le
modèle utilise quatre threads et un contexte 8192. Sans poids vérifiés, ni débit,
ni temps de planification, ni succès d'inférence Qwen ne sont annoncés.

## CUDA : solution candidate concrète

L'index officiel et l'API des releases ont été inspectés, puis **une seule** roue
CUDA a été téléchargée et vérifiée : `v0.3.36-cu124`, 511 381 574 octets, SHA256
`7086b98420cf9cca5e3f14181edc4332038bfb0a0e380370a693806bb527911f`.
L'inspection PE de `ggml-cuda.dll` établit les imports `cudart64_12.dll`,
`cublas64_12.dll`, `nvcuda.dll`, MSVCP140/VCRUNTIME140 et Universal CRT.
**Ces bibliothèques CUDA/cuBLAS ne sont pas contenues dans la roue.** La présence
du pilote NVIDIA seul ne suffit donc pas à constituer cet environnement.

`planner-cuda-candidate.lock` épingle la roue et deux distributions NVIDIA Windows
(CUDA runtime 12.4.127 et cuBLAS 12.4.5.8) avec leurs SHA publiés sur PyPI.
Les deux packages NVIDIA n'ont pas été exécutés ici. Leurs licences de
redistribution doivent être examinées avant inclusion dans l'installateur.
Volume total des trois téléchargements : environ 909 Mo, hors torch/poids ; la
roue llama seule contient deux copies d'une DLL CUDA de 915 Mo décompressée.
Prévoir plusieurs Go d'espace temporaire.

Dans un **nouveau runtime de qualification**, installer d'abord les locks CPU et
serveur, puis remplacer la seule distribution llama et installer ses bibliothèques
avec `--require-hashes --only-binary=:all: --no-deps --force-reinstall -r
<planner-cuda-candidate.lock>`. Ne pas installer les variantes CPU et CUDA dans
la même invocation. Ne pas appliquer cette procédure à l'installation personnelle
active ni mélanger les répertoires de DLL de plusieurs toolkits.

Le chargeur amont consulte `CUDA_PATH/bin` et `CUDA_PATH/lib`. Il faut fournir
les DLL NVIDIA vérifiées dans un répertoire isolé cohérent et enregistrer leurs
répertoires de recherche dans **chaque processus**, y compris le serveur enfant.
Les packages pip NVIDIA ne suffisent pas à configurer automatiquement ce chemin.
Le runtime torch CUDA fourni par Claude doit aussi être conservé/qualifié :
`torch.cuda.is_available()` est une condition du contrat actuel de CoderSession.
PR27 fournit `setup/packaging/runtime/gpu/torch-cu126.lock` et `torch-cu130.lock` ;
son choix matériel appartient au runtime Claude. La roue cu124 inspectée ici
n’est pas une recommandation de mélange de versions : vérifier le chargement
et l’isolation des DLL avec l’overlay réellement sélectionné.
Ne pas le remplacer par torch CPU lors du raccordement au paquet principal.

Qualification encore nécessaire : import DLL dans ce runtime, backend **CUDA**
observé (un simple support générique d'offload pourrait être Vulkan), GPU identifié,
chargement Qwen, couches réellement transférées, génération et débit mesuré,
VRAM avant/pendant/après, bail et quiescence réels, OOM/annulation, reprise du
cerveau. La garde d'entrée Game Studio CUDA reste active jusque-là.

## Repli de construction depuis les sources

Si la roue ne convient pas, base immuable : llama-cpp-python commit
`1652066e0af45f2313b339670ef9555e8a54e545`, sous-module llama.cpp
`0c1e57098bba43ac29e6e3b677cdceebdd22334f`. Construire sur une machine Windows x64
isolée, Python 3.12, MSVC 2022 et toolkit NVIDIA compatibles, avec
`GGML_CUDA=ON`, `CMAKE_CUDA_ARCHITECTURES=89` pour la RTX 4060 Ti et
`CMAKE_BUILD_TYPE=Release` ; `pip wheel --no-deps` produit un artefact à hacher.

Conserver les versions exactes de MSVC, SDK, nvcc, CMake, scikit-build-core, Ninja,
le graphe de dépendances, le journal et les hashes de la roue et des DLL. Ce
parcours est un **plan de construction non exécuté**, pas un binaire reproductible
déjà qualifié. La roue officielle vérifiée est la première candidate à tester ;
aucun téléchargement de toolkit ni compilation lourde n'a été lancé ici.
