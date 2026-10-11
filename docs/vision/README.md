# Spécialiste vision local APEX

Lot isolé : Issue #6, branche `agent/codex/6-vision-specialist`, base
`bd2edd285ad2f796991ce5460715cb4ee9f3f06c`. Les fichiers du cerveau, du runtime
polyvalent, des modèles partagés, de l'assistant et de l'API restent inchangés.

Le point d'entrée est `python -m apex.vision`. Il accepte une image locale et
produit un résultat structuré. Il n'ajoute pas de bouton d'upload à l'assistant
existant : le raccordement nécessite un contrat accepté avec Claude.

## Architecture et choix

Qwen2.5-VL-3B-Instruct est un modèle vision-language préentraîné, utilisé comme
spécialiste d'APEX ; aucun entraînement ou modèle APEX inventé n'est revendiqué.
Les calculs se font localement dans PyTorch/transformers, sans serveur ni service
IA cloud. Le téléchargement explicite des poids est la seule opération réseau
du spécialiste. `analyze` exige les fichiers locaux, refuse tout téléchargement
automatique et tout code distant.

Tesseract est facultatif. Il lit la page avant le chargement du VLM, puis son
processus se termine. Son texte et ses boîtes restent distincts de l'extraction
et de l'analyse du VLM. Les scores OCR ne sont pas des confiances calibrées.
L'image est décodée, orientée selon EXIF, compositée sur fond blanc si nécessaire
et redimensionnée avec conservation des proportions ; aucune binarisation
irréversible n'est imposée aux schémas ou photographies.

Le backend vision est distinct parce que le `GenerationRequest` existant ne
porte pas d'image. Il réutilise les helpers publics de mesure mémoire/précision
du backend natif et l'ArtifactStore d'APEX. Aucun fournisseur textuel parallèle,
route centrale ou registre partagé n'est ajouté.

## Installation cloud/Linux

À la racine du dépôt, Python 3.12 et Node.js 18+ :

```bash
export UV_CACHE_DIR=/workspace/.cache/uv
uv venv .venv
uv pip install --python .venv/bin/python --torch-backend cpu -e '.[db,api,dev,native]'
uv pip install --python .venv/bin/python --torch-backend cpu -r docs/vision/requirements.txt
NPM_CONFIG_CACHE=/workspace/.cache/npm bash scripts/apex-build-ui.sh
```

Les dépendances du spécialiste sont optionnelles pour le reste d'APEX. Tesseract
5 doit être installé pour l'OCR ; `tesseract --list-langs` indique si `fra` et
`eng` existent. Avec seulement `eng`, le résultat signale cette limite.
`torchvision` est requis par le processeur multimodal de transformers 4.57,
y compris pour les images fixes ; aucune vidéo à décoder n'est requise.
`lm-format-enforcer` contraint les tokens au schéma JSON pendant la génération ;
ce composant ne charge aucun modèle. La validation stricte du résultat reste
active après décodage. Les étapes de calcul précèdent la réponse finale et la
consigne demande une vérification par substitution ou unités. Cela améliore
le format et la méthode, sans constituer un oracle de justesse.

Téléchargement explicite, révision immuable observée sur le Hub :

```bash
export HF_HOME=/workspace/.cache/huggingface
export HF_HUB_DISABLE_XET=1
export HF_HUB_DISABLE_TELEMETRY=1
.venv/bin/python -m apex.vision fetch \
  --model-dir /workspace/apex-ia-/apex-data/vision-models/qwen2.5-vl-3b-instruct \
  --revision 66285546d2b821cf421d4f5eb2576359d3770cd3
```

Le fetcher existant d'APEX vérifie les SHA-256 des safetensors contre les
empreintes LFS du Hub. Un manifeste vision ajoute la provenance et les hashes
des configurations/tokenizers. Ils sont revérifiés avant chaque chargement.
Un hash divergent provoque un refus ; le backend vision ne supprime pas les
fichiers existants. Le manifeste local n'est pas une signature contre un
administrateur qui le modifierait lui-même.

Domaines observés : `huggingface.co` et `us.aws.cdn.hf.co`. Les domaines Xet
`cas-bridge.xethub.hf.co`, `cas-server.xethub.hf.co`, `transfer.xethub.hf.co`
sont préparés dans les paramètres. `api.github.com` sert à la coordination.
TLS et vérification des empreintes restent actifs ; le mode HTTP du Hub
(`HF_HUB_DISABLE_XET=1`) conserve ces vérifications.

## Utilisation et exclusivité

```bash
export APEX_HOME=/workspace/apex-ia-/apex-data/cloud-home
# Arrêter uniquement l'instance APEX de ce profil avant une session vision.
.venv/bin/python -m apex.standalone stop
.venv/bin/python -m apex.vision analyze photo.jpg \
  --model-dir /workspace/apex-ia-/apex-data/vision-models/qwen2.5-vl-3b-instruct \
  --question "Lis l'énoncé et aide à résoudre cet exercice." \
  --artifact-dir .apex/vision-results
```

Une seule session vision prend le verrou OS `.apex/model-session.lock` sous
`APEX_HOME` (ou le répertoire courant s'il est absent). Tous les spécialistes
doivent utiliser le même chemin. Le modèle est libéré avant le verrou, y
compris si la génération échoue. Un cœur actif annoncé via les chemins publics
de découverte, le profil standalone ou le profil du verrou fait refuser le
chargement vision. Un état illisible est également refusé. Ce verrou ne
commande pas les processus d'inférence étrangers ni le
code de Claude qui ne l'utilise pas encore : ne pas lancer des modèles externes
à côté et ne pas intégrer le spécialiste au cerveau sans transfert explicite.

Extraction seule, sans prétendre résoudre un exercice :

```bash
.venv/bin/python -m apex.vision analyze photo.jpg --ocr-only
```

Codes CLI : `0` inférence et sortie structurée terminées, `3` extraction OCR
partielle, `2` erreur ou blocage. Une réponse JSON mal formée est une erreur,
jamais une réponse reconstituée artificiellement. `complete` signifie que le
pipeline s'est exécuté, pas que sa réponse est correcte. Les journaux techniques
ne contiennent ni document, ni question, ni réponse ; les artefacts explicitement
demandés contiennent le résultat privé et restent locaux.

## Interface proposée au cerveau principal

```python
from pathlib import Path
from apex.vision import VisionRequest, VisionService
from apex.vision.backend import BackendOptions, LocalQwenVLBackend

backend = LocalQwenVLBackend(BackendOptions(Path("/chemin/vers/poids")))
service = VisionService(backend)
result = service.analyze(VisionRequest(Path("photo.jpg").read_bytes(),
                                     question="Explique l'exercice."))
payload = result.to_dict()
```

Le contrat v1 comporte `request_id`, `status`, `context`, métadonnées de l'image,
OCR séparé, `output`, `error`, `facts` et `limitations`. `output` contient
`extracted_text`, `visual_elements`, `analysis`, `uncertainties`, `answer` et
`help_steps`. `answer=null` est permis pour une image inexploitable. Les erreurs
exposent un code stable et un détail borné sans contenu du document.
Les types visuels sont `text`, `equation`, `table`, `chart`, `diagram` et `object` ;
les noms et descriptions doivent porter sur des observations concrètes.

Protocole proposé à Claude sur #3/#6 : terminer/annuler la génération principale,
libérer ses poids et confirmer la libération ; lancer la session vision seule ;
récupérer son résultat ; attendre sa fin/libération ; reprendre le polyvalent.
Ce transfert et le branchement dans l'assistant ne sont **pas implémentés** dans
les fichiers partagés. Les annulations et délais sont coopératifs entre étapes
et tokens ; le décodage, le hashing, le chargement et un kernel en cours ne
peuvent pas être interrompus instantanément.

## Mémoire et matériel cible

Le Hub indique 7 509 245 952 octets de poids BF16, environ 7,0 GiB, avant
activations et caches. BF16 intégral n'est donc pas une configuration GPU
prudente pour une RTX 4060 Ti de 8 Go.

Sur CUDA, `auto` demande NF4 pour les couches textuelles et conserve le module
visuel hors quantification. Il faut `bitsandbytes>=0.48,<1` et accelerate avec
une paire PyTorch/torchvision CUDA compatible ; le backend vérifie que le
runtime confirme effectivement le chargement en 4 bits et refuse une VRAM libre
insuffisante. L'estimation conservatrice de préparation est d'environ 5,5 GiB
pour ce modèle ; ce n'est **pas** une mesure sur la RTX. NF4, Windows et les pics
VRAM restent à valider ; cette variante n'est pas présentée comme qualifiée.

Installation Windows proposée, **non testée dans ce cloud CPU** :

```powershell
py -3.12 -m venv .venv
# Paire officielle CUDA 12.8 ; nécessite un pilote NVIDIA compatible.
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -e ".[native]" -r docs/vision/requirements.txt "bitsandbytes>=0.48,<1"
$env:APEX_HOME = "$PWD\apex-data\vision-home"
.\.venv\Scripts\python.exe -m apex.vision fetch --model-dir apex-data\vision-models\qwen2.5-vl-3b-instruct --revision 66285546d2b821cf421d4f5eb2576359d3770cd3
.\.venv\Scripts\python.exe -m apex.vision analyze photo.jpg --model-dir apex-data\vision-models\qwen2.5-vl-3b-instruct --quantization nf4 --timeout 1200
```

Tesseract doit être accessible dans `PATH` pour activer l'OCR ; sinon le VLM lit
directement l'image et le résultat signale l'absence d'OCR. `facts.device`,
`facts.quantization`, les versions et les mesures CUDA distinguent une
exécution GPU réelle d'un repli CPU. Une dépendance ou une mémoire insuffisante
renvoie un blocage explicite.

Sur CPU, la précision est choisie selon la mémoire réellement disponible,
la limite cgroup éventuelle et le budget de 12 GiB par défaut : float32 si
possible, sinon BF16, sinon refus. Quatre threads, 1 024 tokens visuels et
8 192 tokens de contexte bornent la demande. Le résultat rapporte les tokens,
temps, identité des poids, RSS après chargement/génération et, sur CUDA, pics
alloués/réservés PyTorch. Les RSS instantanés ne sont pas des pics du processus.
Les petites écritures, exposants, pages denses et symboles manuscrits peuvent
nécessiter un recadrage fourni par l'appelant ; aucune précision OCR universelle
ni réussite sur toutes les matières n'est annoncée.

## Vidéo et validation

`MediaContext(kind="video_frame", source_id="...", timestamp_s=2.5)` permet de
soumettre des frames avec provenance temporelle. Extraction de frames,
sous-échantillonnage, sélection d'images clés et synthèse séquentielle restent
des extensions futures. Aucun résultat vidéo n'est simulé.

```bash
.venv/bin/python scripts/vision/build_fixtures.py --output .apex/vision-fixtures
.venv/bin/python -m pytest tests/test_vision_contract.py tests/test_vision_images.py -ra
# Active les quatre contrôles sémantiques UNIQUEMENT si le manifeste vérifié existe :
APEX_VISION_MODEL_DIR=/workspace/apex-ia-/apex-data/vision-models/qwen2.5-vl-3b-instruct \
  .venv/bin/python -m pytest tests/test_vision_images.py -ra
```

Les fixtures sont synthétiques : exercice imprimé JPEG légèrement déformé,
document avec tableau/schéma, graphique, page vide, page floue et fichier
corrompu. Les tests de contrat emploient des doubles clairement identifiés.
Les contrôles sémantiques exigent une vraie inférence préentraînée ; ils sont
`SKIPPED/BLOCKED` sans poids, pas des PASS. Voir `REPORT.md` pour les résultats
réellement obtenus, les défauts de baseline et les limites restantes.
