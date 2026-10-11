# Cerveau APEX v2 — un modèle de langage construit de zéro par APEX

> Décision de l'utilisateur (10/10/2026) : « on ne veut pas de modèle Qwen, on veut notre modèle de 0 construit
> entièrement par nous ». Le cerveau d'APEX est donc **ApexLM**, dont l'architecture, le tokenizer, les données,
> l'entraînement, la quantification et le moteur d'inférence sont écrits dans ce dépôt. Aucun poids externe,
> aucun texte produit par un autre modèle d'IA.

## 1. Ce qui est réaliste (dit avant tout)

Un modèle entraîné de zéro sur **une** carte RTX 4060 Ti (8 Go) peut viser **50 à 350 millions** de
paramètres. Les modèles comme Qwen en ont plusieurs milliards et ont été entraînés sur des milliers de GPU.
Le Cerveau APEX v2 peut apprendre le français, l'anglais, des consignes simples, de petits programmes et des
réponses honnêtes sur ses limites ; il **n'égalera pas** ces modèles en raisonnement, en programmation ni en
planification. Sa qualité dépend directement du calcul et des données qu'on lui donne : chaque chiffre de ce
document est mesuré, et chaque limite est écrite.

Deux modèles, une seule chaîne de construction :

| Modèle | Paramètres | Contexte | Où il est entraîné | Rôle |
|---|---|---|---|---|
| `cpu-validation` | 15,7 M | 1 024 jetons | environnement de développement (4 cœurs CPU, sans GPU) | prouver la chaîne complète ; premier Cerveau v2 servi |
| `rtx4060ti-8gb` | ≈ 113 M | 2 048 jetons | le PC de l'utilisateur (RTX 4060 Ti, WSL) | le vrai Cerveau v2 |

## 2. Architecture (`apex/brain/model.py`, `apex/brain/config.py`)

Transformeur décodeur pré-norm, toutes dimensions dans `ModelConfig` :

- RMSNorm, encodage de position rotatif (RoPE), SwiGLU, embeddings liés à la tête de sortie ;
- **attention groupée (GQA)** — `num_kv_heads` : moins de têtes clé/valeur, cache d'inférence 3 fois plus
  petit pour le modèle de validation (2 têtes K/V pour 6 têtes de requête) ;
- **normalisation QK** — stabilité de l'entraînement à fort taux d'apprentissage ;
- **cache clé/valeur** (`KVCache`) : chaque jeton n'est calculé qu'une fois en génération ; vérifié identique
  au recalcul complet (écart ≤ 1e-5, test `test_cached_incremental_decoding_equals_full_recompute`) ;
- **recalcul des activations** (entraînement GPU) : mêmes gradients (vérifié), beaucoup moins de mémoire.

Le Cerveau v1 (1,9 M paramètres) se charge toujours à l'identique : les champs de la génération 2 n'apparaissent
dans la configuration que s'ils sont utilisés (même empreinte).

## 3. Tokenizer (`apex/brain/tokenizer.py`)

BPE au niveau octet appris par APEX sur son corpus (tout texte UTF-8 est encodable). Génération 2 :

- tokens de **conversation** (`<|system|>`, `<|user|>`, `<|assistant|>`, `<|end|>`) et d'outils, réserves ;
- **aucun token spécial ne peut sortir du texte** : un message qui contient « <|assistant|> » reste du texte
  (l'utilisateur ne peut pas se faire passer pour le système) ;
- chiffres découpés **un par un** (l'arithmétique s'apprend chiffre à chiffre) ; `_` fait partie des mots ;
- apprentissage **incrémental** : mêmes fusions que l'algorithme naïf (test), 24 fois plus rapide mesuré.

## 4. Données (`apex/neural/corpus.py`, `brain_sources.py`, `tasks.py`)

**Corpus (pré-entraînement)** — textes réels, chacun avec sa source, sa licence et son empreinte :

- paquets Ubuntu **signés** (signature de `InRelease` vérifiée par `gpgv`, puis SHA-256 de l'index et du
  paquet) : documentation Python, Django, SQLAlchemy, Git, PostgreSQL, Rust, GCC, pages de manuel… et en
  **français** : pages de manuel, référence Debian, aide GNOME, KiCad, LilyPond, et les traductions humaines
  anglais → français des logiciels (catalogues gettext) ;
- archives sources **PyPI** de bibliothèques à **licence permissive** (vérifiée à chaque construction ; pylint
  et astroid, GPL, sont refusés) ;
- bibliothèque standard Python, documentation et code d'APEX ;
- pour le grand entraînement : jeux publics du Hub Hugging Face (FineWeb-Edu, FineWeb-2 français, Wikipédia),
  chaque fichier vérifié contre son SHA-256 publié.

Filtres : conversion HTML/man/info en texte, documents trop courts, minifiés ou sans lettres rejetés (comptés),
manuels géants découpés aux paragraphes, doublons exacts retirés. Découpage train/validation **par document**.

**Exercices d'instruction** — générés par APEX avec une réponse **vraie par construction** : arithmétique avec
étapes, problèmes, listes, texte, conversions, dates, logique, JSON, **sortie de programmes exécutés**,
**fonctions Python avec tests**, **correction de bugs** (bug injecté vérifié par les tests, dans un processus
séparé avec délai), explications, **honnêteté** (pas d'accès en temps réel, aucune action prétendue, information
manquante demandée), **mémoire de conversation**, identité, procédures de développement écrites à la main. Les
consignes système incluent celles de l'assistant d'APEX. Jeu d'évaluation fixe, jamais vu à l'entraînement ;
**10 fonctions ne sont jamais montrées à l'entraînement** (mesure de généralisation, pas de mémoire).

**Exercices de connaissance** (`apex/neural/knowledge_tasks.py`) — tirés de textes **écrits par des humains** :
la ligne NOM/NAME des pages de manuel françaises et anglaises du corpus (« À quoi sert la commande `chown` ? »),
la première phrase de la documentation de la bibliothèque standard Python, et les traductions humaines anglais ↔
français des logiciels. L'évaluation pose la **même connaissance avec une formulation jamais vue** (rappel), et
garde **3 % des phrases à traduire hors de l'entraînement** (généralisation). Notation par recouvrement des mots
de la référence au début de la réponse (`overlap`) : un long déversement de texte ne compte pas.

## 5. Entraînement (`apex/neural/pretrain.py`, `scripts/apex_brain_build.py`)

`corpus → tokenizer → tokenize → tasks → train (pretrain) → train (sft) → export → evaluate → install`, chaque
étape écrit ses résultats et leur provenance. Reprise **exacte** après interruption (poids identiques au bit
près, test `test_training_resumes_exactly_where_it_stopped`), point de reprise atomique toutes les 10 minutes,
arrêt propre sur Ctrl+C, perte non finie = arrêt avec la raison. GPU : bfloat16, TF32, AdamW fusionné, et
**ajustement automatique à la VRAM** (lot divisé et accumulation doublée si un lot ne tient pas, puis recalcul des
activations ; décidé une fois et gardé pour les reprises).

## 6. Quantification APEX-Q5 (`apex/brain/quant.py`)

Format propre à APEX : blocs de 32 poids, `w ≈ minimum + q * échelle`, q sur 5 bits, échelle et minimum en
float16, 20 octets par bloc (6 bits par poids au total). Échelle et minimum **ajustés par moindres carrés**
(erreur relative 3,5 % au lieu de 3,8 % sans ajustement, mesuré sur matrices gaussiennes). Fichier 5,3 fois plus
petit qu'en float32. Deux usages : déquantifié au chargement (le plus rapide) ou **Q5 résident** (`Q5Linear`,
poids compressés en mémoire, plus lent : mesuré ×2 sur un petit modèle CPU, faute de noyau fusionné).

## 7. Moteur d'inférence (`apex/brain/inference.py`) et intégration

`BrainEngine` : pré-remplissage puis décodage avec cache, glouton ou température/top-k/top-p/pénalité de
répétition, graine reproductible, arrêt sur fin de réponse, délai et annulation entre deux jetons, texte en
flux (caractères UTF-8 complets), conversation tronquée par le début (consignes système conservées), mesures
réelles (premier jeton, débit, mémoire des poids et du cache), déchargement effectif.

Dans APEX : moteur « Cerveau APEX » de l'assistant (`apex/models/apex_brain.py`). Le Cerveau v2 reçoit la
**conversation structurée** (consignes, tours précédents, message) au lieu d'un texte aplati ; il est déchargé
après 10 minutes sans usage. APEX sert la génération la plus récente installée, en cherchant d'abord dans
`%LOCALAPPDATA%\APEX\brain` (Cerveaux entraînés sur le poste, conservés aux mises à jour) puis dans
l'application. Intégrité vérifiée à chaque chargement (SHA-256 du fichier, empreinte des poids).

## 8. AirLLM

AirLLM charge un modèle **couche par couche** depuis le disque pour faire tourner des modèles de 70 milliards de
paramètres sur 4 à 8 Go de VRAM. Le Cerveau APEX tient entièrement en mémoire (16 à 120 M paramètres) : la
technique a été **mesurée** sur ApexLM (`layer_streaming_benchmark` : mêmes jetons, débit comparé, voir §9) au
lieu d'être supposée. La bibliothèque AirLLM elle-même ne lit que des architectures Hugging Face (Llama…), pas
ApexLM. Décision : non intégrée au chemin principal, faute de bénéfice mesuré.

## 9. Mesures

À compléter avec les mesures du modèle de validation (fin d'entraînement).

## 10. Entraîner le vrai Cerveau sur le GPU du PC

Dans Ubuntu (WSL) :

```bash
cd ~/apex && git pull && bash scripts/wsl/apex-brain-train.sh
```

Le script vérifie le GPU, installe PyTorch avec CUDA, construit le corpus, entraîne, évalue, puis installe le
Cerveau dans `%LOCALAPPDATA%\APEX\brain\apex-brain-v2`. Il peut être interrompu et relancé : il reprend où il en
était. Prévoir 40 Go de disque et **une à deux journées** de calcul (estimation à confirmer par le débit mesuré
au démarrage).
