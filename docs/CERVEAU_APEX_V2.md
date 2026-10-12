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
poids compressés en mémoire, plus lent faute de noyau fusionné : ×2 sur un modèle de test, **×6,5** sur le
Cerveau livré — voir §9).

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
lieu d'être supposée : **10 fois plus lent** sur le modèle livré. La bibliothèque AirLLM elle-même ne lit que des
architectures Hugging Face (Llama…), pas ApexLM. Décision : non intégrée au chemin principal, faute de bénéfice
mesuré.

## 9. Mesures — première version du modèle de validation (11/10/2026, 0.5.0)

Tout est mesuré sur le Cerveau **réellement servi** (`brain/apex-brain-v2/evaluation.json`, même répertoire et
même moteur d'inférence que l'application). Environnement : 4 cœurs CPU (AVX-512), aucun GPU, torch 2.14.

**Entraînement.** 15,7 M paramètres (8 couches, largeur 384, 6 têtes dont 2 clé/valeur, contexte 1 024).
Pré-entraînement : 6 000 étapes, **98,3 M jetons**, 5,9 h (3 sessions reprises au checkpoint après des
redémarrages de l'environnement), ≈ 5 100 jetons/s compilé. Instruction : 1 500 étapes, 24,6 M jetons
(20 % de rappel du corpus contre l'oubli), 1,7 h.

| Validation du corpus (bits par octet, plus bas = mieux) | étape 500 | fin du pré-entraînement | après instruction, servi en Q5 |
|---|---|---|---|
| français | 1,312 | 0,621 | 0,637 |
| code | 1,602 | 0,846 | 1,007 |
| traduction | 1,931 | 1,046 | 1,046 |
| anglais | 1,827 | 1,126 | 1,188 |
| **ensemble** | 1,665 | **0,910** | **0,987** |

L'instruction a coûté **8 %** de modélisation du corpus (0,910 → 0,985 en float32), surtout sur le code, malgré
le rappel de 20 %. **Coût de la quantification APEX-Q5 : 0,9854 → 0,9869 bit/octet (+0,15 %)** pour un fichier
de 11,8 Mo au lieu de 62,9 Mo (erreur relative moyenne des poids 3,5 %).

**Exercices jamais vus** (660, décodage glouton, notation automatique ; plancher = réponse vide ou question
recopiée, mesuré à 0 % partout sauf traduction 2,5 %) : **316/660 = 47,9 %**, refus abusif 0 %.

| Famille | Réussite | | Famille | Réussite |
|---|---|---|---|---|
| mémoire de conversation | 100 % | | conversions | 60 % |
| JSON | 100 % | | problèmes en mots | 52,5 % |
| logique | 85 % | | listes | 45 % |
| honnêteté (limites) | 82,5 % | | traduction (phrases jamais vues) | 37,5 % |
| correction de bug | 77,5 % | | dates | 30 % |
| fonction Python (fonctions vues) | 62,5 % | | sortie de programme | 25 % |
| | | | opérations sur du texte | 17,5 % |
| | | | arithmétique | 7,5 % |
| | | | connaissances : fonctions Python | 5 % |
| | | | connaissances : commandes | 2,5 % |
| | | | **fonctions jamais montrées** | **0 %** |

Lecture honnête : le modèle a appris les **formats** (JSON, structure des réponses, limites, rappel d'un fait
de la conversation) et des fonctions **vues** ; il **ne généralise pas** à une fonction jamais montrée (0/20),
calcule mal (7,5 %) et ne retient presque rien des connaissances (2,5–5 %). C'est la limite attendue de 15,7 M
paramètres et 98 M jetons : le vrai Cerveau (≈ 113 M paramètres, ≈ 2,6 G jetons sur le GPU) est fait pour ça.

**Vitesse (CPU, 4 fils, poids déquantifiés)** : premier jeton en 15 ms (invite de 32 jetons) à 96 ms
(768 jetons) ; génération **157 à 205 jetons/s** ; 63 Mo de poids en mémoire. Mode « Q5 résident » : 22 Mo en
mémoire mais **24 jetons/s** contre 157 à invite égale (6,5 fois plus lent, faute de noyau fusionné) — d'où le
mode déquantifié par défaut.

**Stabilité et mémoire** : 100 générations d'affilée, **0 erreur**, mémoire résidente stable (+1,6 Mo) ;
5 cycles chargement/génération/déchargement sans croissance (634,5 Mo après chaque cycle) ; le déchargement
rend la mémoire (742 → 632 Mo).

**Conversation plus longue que le contexte** (83 messages) : les 9 plus anciens sont retirés, la génération
aboutit ; le fait donné au début n'est plus dans le contexte, le modèle n'invente pas le nom mais produit une
réponse **dégénérée** (répétitions) au lieu de dire qu'il ne sait pas. Ce n'est pas un effet de la longueur :
48 exercices (JSON, logique, conversions, correction de bug, honnêteté, problèmes) précédés d'un historique de
plus en plus long réussissent **37 à 39 fois sur 48** de 130 à 868 jetons d'invite, sans tendance. C'est la
question sur un fait sorti du contexte qui le met en échec.

**Chargement couche par couche (AirLLM)** : mêmes jetons, mais **10 fois plus lent** (19 contre
194 jetons/s), sans gain utile puisque le Cerveau tient en mémoire. Non intégré.

**Exemples réels** (même Cerveau, `apex_brain_build.py chat`) : « Quelle est la météo à Paris ? » → « Je n'ai
pas accès à Internet ni à des informations en temps réel… » (juste) ; « Combien font 37 × 24 ? » → « 37 × 24 =
37 × 20 + 37 × 4 = 1960 + 37 / 37 × 24 = 2856. » (faux : 888) ; « Qui es-tu ? » → la même phrase sur l'accès à
Internet (hors sujet).

## 9 bis. Calcul exact et fonctions composées (12/10/2026, 0.5.1) — Cerveau livré

Objectif de l'utilisateur : une IA « extrêmement bonne en code, maths, calculs ». Un modèle de 15,7 M
paramètres ne calcule pas de tête (7,5 % mesuré) : il **délègue**. Deux leviers, mesurés sur le **même**
pré-entraînement et les **mêmes 660 exercices** que §9 (questions et vérifications identiques, 540/540 vérifié) :

1. **Calculatrice exacte** (`apex/brain/calc.py`) : le modèle écrit l'opération (`<|tool_call|> 974*89
   <|tool_result|>`), le moteur la calcule et place le résultat dans la réponse (et dans le contexte du
   modèle). Arithmétique, et fonctions PURES pour les listes (`sorted`, `max`, `sum`, `evens`…), le texte
   (`reverse`, `len`, `upper`, `spell`…) et le calendrier (`weekday`, `add_days`, `days_between`,
   `is_leap`) : le modèle recopie l'énoncé, la calculatrice fait le reste. Rien d'autre ne s'exécute (pas de
   nom, d'attribut ni d'appel hors de la liste ; tailles bornées ; testé). Le résultat n'est jamais appris : le
   modèle apprend à écrire l'appel, pas à deviner le résultat.
2. **Fonctions composées** (`apex/neural/compose_tasks.py`) : filtre × transformation × agrégat, ≈ 6 000
   fonctions distinctes (23 648 exercices), tests obtenus en exécutant la référence ; ≈ 15 % des combinaisons
   jamais montrées à l'entraînement.

| Famille (exercices jamais vus) | 0.5.0 | **0.5.1** |
|---|---|---|
| arithmétique | 7,5 % | **100 %** |
| problèmes en mots | 52,5 % | **100 %** |
| conversions | 60 % | **100 %** |
| dates (jour de la semaine, ajout de jours, écart, bissextile) | 30 % | **100 %** |
| listes (tri, max, somme, pairs…) | 45 % | **100 %** |
| opérations sur du texte | 17,5 % | **97,5 %** |
| sortie de programme | 25 % | 35 % |
| correction de bug / fonction vue / logique / honnêteté / mémoire / JSON | 77,5 / 62,5 / 85 / 82,5 / 100 / 100 % | 77,5 / 57,5 / 82,5 / 80 / 100 / 100 % |
| traduction / connaissances (commandes, Python) | 37,5 / 2,5 / 5 % | 37,5 / 5 / 10 % |
| fonction écrite à la main jamais montrée | 0 % | 0 % |
| **ensemble des 660 exercices** | **47,9 %** | **71,7 %** |
| fonctions composées, combinaisons vues (nouvelles constantes) | — | 95 % |
| fonctions composées, **combinaisons jamais montrées** | — | **82,5 %** |

Inchangé ou mieux : refus abusif 0,14 % (1 sur 700), bits par octet servis 0,9867 (0,9869 avant), 100
générations sans erreur, 210 à 261 jetons/s sur 4 cœurs. Essais réels (`chat`) : « 37 × 24 » → 888 ;
« quel jour était le 14 juillet 1789 » → mardi ; « jours entre le 1 janvier et le 12 octobre 2026 » → 284 ;
dans APEX, « 1234 × 5678 » → 7006652, l'appel `1234*5678` figurant dans les faits de la réponse.

**Limites mesurées** : le code n'est réussi que si la demande ressemble aux gabarits appris — « somme des
carrés des nombres impairs » formulée librement donne une fonction fausse ; une fonction écrite à la main jamais
montrée : 0/20. Prochain levier : formulations variées des exercices, et vérification du code par exécution.

## 10. Entraîner le vrai Cerveau sur le GPU du PC

Dans Ubuntu (WSL) :

```bash
cd ~/apex && git pull && bash scripts/wsl/apex-brain-train.sh
```

Le script vérifie le GPU, installe PyTorch avec CUDA, construit le corpus, entraîne, évalue, puis installe le
Cerveau dans `%LOCALAPPDATA%\APEX\brain\apex-brain-v2`. Il peut être interrompu et relancé : il reprend où il en
était. Prévoir 40 Go de disque et **une à deux journées** de calcul (estimation à confirmer par le débit mesuré
au démarrage). Le lot est ajusté à la mémoire GPU **réellement libre** (l'affichage de Windows en occupe une
partie). À la fin, pour converser avec le Cerveau dans le terminal avant de l'utiliser dans APEX :

```bash
~/apex-brain/venv/bin/python scripts/apex_brain_build.py chat --brain ~/apex-brain/brain/apex-brain-v2
```
