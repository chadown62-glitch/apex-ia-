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

## 9 bis. Calcul exact et fonctions composées (12/10/2026, 0.5.1) — Cerveau livré en 0.5.1

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

## 9 ter. Dix langages et demandes 3D (12/10/2026, 0.6.0) — Cerveau livré

Même pré-entraînement, nouvelle instruction (2 500 étapes, CPU, float32 choisi par mesure) avec, en plus des
données de 0.5.1 :

- **le même code dans 10 langages** (`apex/neural/polyglot.py`) : Python, C#, C++, JavaScript, TypeScript,
  Java, Rust, Go, PHP, Ruby — 2 500 exercices par langage, fonctions composées comme en §9 bis ; chaque
  référence d'évaluation est **compilée et exécutée par le vrai compilateur** (csc, g++, tsc, javac, rustc,
  go, php, ruby, node, python) avant d'être gardée, et chaque réponse du modèle aussi ;
- **8 000 demandes de modélisation 3D** en français et en anglais (`apex/neural/model3d_tasks.py`), traduites
  en appel de l'atelier 3D (`vehicle(…)`, `weapon(…)`, `character(…)`) ; deux formulations par langue et
  ≈ 15 % des combinaisons de paramètres sont réservées à l'évaluation.

Comparaison sur les **mêmes 1 220 exercices jamais vus** (le Cerveau 0.5.1 a été réévalué sur cet ensemble, ici,
le 15/10/2026) :

| Famille | 0.5.1 | **0.6.0** |
|---|---|---|
| code C++, C#, Java, JavaScript, TypeScript, Rust, PHP (combinaisons vues / jamais vues) | 0 % | **95 à 100 %** |
| code Go / Ruby (vues / jamais vues) | 0 % | 90 / 65 % · 85 / 90 % |
| code Python (vues / jamais vues) | 60 / 45 % | **100 / 100 %** |
| fonctions composées Python (combinaisons jamais montrées) | 82,5 % | **95 %** |
| demandes 3D (formulations vues sur combinaisons nouvelles / formulations jamais vues) | 0 % | **95 / 87,5 %** |
| fonction écrite (gabarits vus / jamais vue) | 57,5 / 0 % | 67,5 / 5 % |
| honnêteté / logique / traduction | 80 / 82,5 / 37,5 % | 90 / 85 / 42,5 % |
| sortie de programme | 35 % | 30 % (14 → 12 sur 40) |
| connaissances (commandes, Python) | 5 / 10 % | 5 / 10 % |
| **ensemble des 1 220 exercices** | **46,3 %** | **83,3 %** |
| dont les 740 exercices de 0.5.1 | 73,5 % | 75,5 % |

Inchangé : 100 générations sans erreur, refus abusif 0,08 % (0,51 % avant), bits par octet servis 0,9881
(0,9867), vitesse comparable mesurée sur la même machine (même architecture ; médianes de 136–143 jetons/s
pour 0.5.1 et 195–223 pour 0.6.0 sur trois réponses courtes de longueurs différentes, donc sans conclusion de
gain). Seule baisse : sortie de programme, 2 exercices sur 40.

**Essais réels dans APEX** (assistant, moteur « Cerveau », mode 3D, 15/10/2026) : « Modélise un char de 8 m de
long, kaki » → appel `vehicle("tank", length=8, color="kaki")`, fichiers `.glb`, `.obj`, script Blender,
scripts Unity et Unreal écrits ; « Create a 3D model of a red sports car 4.5 meters long » → voiture rouge de
4,5 m ; « Génère une épée de 1,2 m pour Unity » → épée de 1,2 m.

**Limites mesurées, dites telles quelles** : le code n'est fiable que pour des fonctions de la forme apprise
(signature donnée, filtre × transformation × agrégat). Demandée librement (« écris une fonction Rust
somme_pairs… » sans signature), la réponse est du Python faux dans un bloc Rust ; avec la signature, le code
compile (rustc) mais ajoute une valeur absolue : `somme_pairs(&[1, 2, 3, 4, -6])` rend 12 au lieu de 0. Une
fonction écrite à la main jamais montrée : 1/20. Prochain levier : faire exécuter et tester son code au
Cerveau, et corriger d'après le résultat (outils `code.run`/`tests.run` existants).

## 9 quater. Le Cerveau sait quand il n'est pas sûr de lui (15–16/10/2026)

**Mesure.** Chaque réponse du Cerveau porte sa **confiance** : la moyenne géométrique des probabilités que le
modèle donnait aux jetons qu'il a choisis, et la plus faible de ces probabilités. Elle se calcule sur la
distribution brute du modèle, avant température et pénalité de répétition. Les jetons écrits par la
calculatrice ou l'atelier 3D sont exclus : ce ne sont pas des choix du modèle. Le calcul se fait pendant la
génération, sans seconde passe (`GenerationStats.confidence`).

Défaut corrigé le 16/10/2026 : avec la pénalité de répétition (1,1 dans l'application), l'échantillonnage
modifiait sur place les valeurs servant à la confiance. Elle était donc mesurée sur une distribution pénalisée.
`BrainEngine._sample` travaille désormais sur une copie ; le texte généré est inchangé (test de non-régression).

**Calibration sur les 1 220 exercices jamais vus**, mesurée deux fois. La justesse est celle de l'évaluation
(compilateurs réels pour le code). L'AUROC est la probabilité qu'une réponse juste ait une confiance plus haute
qu'une réponse fausse.

- **Conditions de l'application** : fournisseur réel (`ApexBrainProvider`), consignes du mode Normal,
  température 0,4 (réglage par défaut), pénalité de répétition 1,1, une graine par exercice. 1 008 justes
  (82,6 %), AUROC **0,957**. Un seul tirage : la justesse varie d'un tirage à l'autre.
- **Réponse gloutonne** (sans température ni pénalité) : 1 017 justes (83,4 %), AUROC 0,960. Sur ce tirage, la
  température par défaut coûte 9 exercices sur 1 220.

| Seuil (conditions de l'application) | Réponses gardées | Justes parmi les gardées | Erreurs confiées au moteur suivant | Réponses justes confiées |
|---|---|---|---|---|
| 0,90 | 86,1 % | 93,2 % | 141 / 212 | 29 / 1 008 |
| 0,95 | 81,4 % | 96,2 % | 174 / 212 | 53 / 1 008 |
| **0,97** | **77,5 %** | **97,3 %** | **186 / 212** | **88 / 1 008** |
| 0,98 | 74,3 % | 97,6 % | 190 / 212 | 124 / 1 008 |

En glouton, au seuil 0,97 : 78,3 % gardées, 97,6 % justes parmi elles, 180 erreurs sur 203 et 85 réponses
justes sur 1 017 confiées.

Les 26 erreurs gardées au seuil 0,97 sont presque toutes du code : Go 10, Ruby 4, autres exercices de code 7.
S'y ajoutent trois demandes 3D et deux sorties de programme. Le Cerveau peut donc se tromper **en étant sûr de lui**,
surtout en code. À l'inverse, il doute beaucoup, que sa réponse soit juste ou non, en traduction et en
connaissances (en glouton, confiance moyenne 0,53 pour les traductions justes, 0,49 pour les fausses).

**« Je ne peux pas le savoir. »** Le Cerveau a appris deux familles de réponses toutes faites : pas d'accès au
temps réel, et information manquante (`apex.brain.chat.CANNOT_KNOW_REALTIME`, `CANNOT_KNOW_MISSING`, source
unique aussi utilisée par les données d'instruction). Il les donne avec une confiance élevée, y compris à des
questions de culture générale qu'un autre modèle sait traiter. Une réponse identique à l'une de ces phrases
(espaces normalisés, rien n'est deviné) est donc, elle aussi, confiée au moteur suivant. Ses refus sur la vie
privée de l'utilisateur ou sur des actions jamais faites restent au Cerveau : ils sont justes par construction,
et un autre modèle ne pourrait que deviner.

Coût mesuré dans les conditions de l'application : sur les 1 220 exercices, 18 réponses sont ces phrases, toutes
justes (famille honnêteté). La règle n'y attrape aucune erreur et confie 9 réponses justes de plus. Avec seuil et
règle : 76,8 % gardées, 97,2 % justes parmi elles, 186 erreurs sur 212 et 97 réponses justes sur 1 008 confiées.

**Hors des familles d'entraînement** : 16 vraies demandes libres, justesse vérifiée par rustc, g++ ou Python,
sinon par un critère écrit avant de voir la réponse (script hors dépôt). **16 réponses fausses sur 16**, dans
les deux conditions. Dans les conditions de l'application, 13 sont sous 0,97 ; la règle « je ne peux pas le
savoir » en attrape 2 de plus (capitale de l'Australie, 0,986 ; auteur des *Misérables*, 0,978), soit **15 sur
16** confiées au moteur suivant. Seule passe la fonction Rust avec signature : code faux, confiance 0,985. Ces
mesures confirment le §9 ter : hors de ce qu'il a appris, ce Cerveau de 15,7 M de paramètres se trompe presque
toujours.

**Usage dans APEX** (moteur **Automatique** de l'assistant) : une réponse du Cerveau dont la confiance est sous
le seuil, ou qui est l'une de ses phrases « je ne peux pas le savoir », est confiée au moteur suivant (Ollama,
moteur intégré). Le seuil se règle dans Paramètres > Modèles (« Le Cerveau passe la main sous cette
confiance ») : 0,97 par défaut, 0 pour ne jamais passer la main. Sous la réponse, APEX écrit pourquoi :
« confié par le Cerveau APEX (confiance 0.62) » ou « (il dit ne pas pouvoir le savoir) ». Si aucun autre moteur
ne répond, la réponse du Cerveau est gardée, et APEX l'écrit aussi. La décision se prend à la première étape du
tour, qui choisit le modèle de tout le tour. La réponse déclinée reste dans la trace de la génération :
`apex/assistant/turns.py` rappelle `generate_with_fallback`, inchangé, sur les moteurs suivants. Avec le moteur
« Cerveau » choisi explicitement, rien n'est jamais confié ailleurs. Une délégation coûte une seconde
génération.

**Ce qui n'est pas mesuré ici** : aucun autre moteur ne tourne dans cet environnement (Ollama et les poids du
moteur intégré ne sont pas téléchargeables). La chaîne de délégation est vérifiée par des tests avec des doubles
de test (`tests/test_assistant.py`). Le gain réel dépend de la qualité du moteur suivant sur le PC. Pour une
question vraiment en temps réel (la météo), le moteur suivant ne sait pas non plus : il peut le dire, ou
inventer. Le seuil vaut pour **ce** Cerveau. Chaque évaluation (`apex_brain_build.py evaluate`) mesure désormais
la calibration (`tasks.calibration` dans `evaluation.json`, résumé dans la provenance) : pour un nouveau
Cerveau, par exemple celui entraîné sur le GPU, le seuil se règle d'après sa propre mesure.

## 9 quinquies. Vérifier le code du Cerveau, et les questions d'actualité (16/10/2026)

**Les 26 erreurs que le filtre de confiance garde** (conditions de l'application, seuil 0,97, §9 quater),
rejouées avec les vrais compilateurs :

| Nature | Nombre |
|---|---|
| code qui compile et tourne, résultat faux (« < » au lieu de « > », filtre ajouté, mauvaise opération) | 19 (Go 9, Ruby 4, C++ 2, PHP 2, Java 1, Python 1) |
| code qui ne compile pas (Go : `undefined: abs`) | 1 |
| code qui plante à l'exécution (JavaScript) | 1 |
| sortie de programme mal prédite | 2 |
| demande 3D | 3 |

**Pistes mesurées et écartées.**
- Compilation ou exécution seules : 2 erreurs sur 26 attrapées ici, 0 sur un PC où seul Python s'exécute.
- Plus faible probabilité d'un mot (seuil 0,4) : 5 erreurs de plus, mais 4 réponses justes de plus cédées, sur
  les mêmes données.
- Le Cerveau traduit sa propre réponse en Python : 4 erreurs attrapées sur 20, et 18 réponses justes sur 40
  signalées à tort. Il n'a jamais appris à traduire du code.

**Vérification croisée, retenue** (`apex/brain/crosscheck.py`, `apex/assistant/verification.py`). Le Cerveau
écrit juste la même fonction en Python (100 % des exercices Python jamais vus). Il écrit donc sa version Python,
avec un début de réponse imposé (`def nom(nombres):`), en glouton. Les deux versions s'exécutent sur 13 entrées
générées, jamais sur des tests cachés. Une divergence prouve qu'au moins l'une des deux est fausse. Une
concordance n'est jamais une preuve de justesse.

| Mesure (réponses de code gardées par la confiance, hors Python) | Erreurs attrapées | Réponses justes signalées |
|---|---|---|
| 1 220 exercices du §9 quater : 358 réponses | **20 / 20** | 1 / 338 |
| Jeu NEUF : 225 combinaisons réservées, nouvelle graine, module réel | **2 / 2** | **0 / 223** (1 « non vérifiable ») |

Rejoué ensuite avec le module final (`python_twin` du fournisseur, sondes, mêmes commandes que la sandbox,
exécutées en sous-processus) sur les mêmes 358 réponses : **20 / 20** erreurs attrapées, **0 / 338** réponse
juste signalée.

**Comparaison avec la version précédente**, sur les mêmes 1 220 exercices dans les conditions de
l'application :

| Version | Réponses gardées | Justes parmi les gardées | Erreurs confiées | Réponses justes confiées |
|---|---|---|---|---|
| avant (`eb6537f` : confiance + « je ne peux pas le savoir ») | 76,8 % | 97,2 % | 186 / 212 | 97 / 1 008 |
| après (`1d41f3c`, sandbox Linux) | 76,1 % | **99,4 %** | **206 / 212** | **85 / 1 008** |

12 réponses justes de moins sont cédées : ce sont des refus honnêtes à des questions d'actualité, désormais
gardés. Sur Windows, la vérification croisée ne s'exécute pas : il reste 186 erreurs confiées sur 212.

La fausse alerte du premier jeu venait d'une version Python inutilisable (`def f(nombres): int`, type recopié
de la signature PHP). Une telle version est désormais « non vérifiable », jamais « fausse ». Restent hors de
portée 6 des 26 erreurs : sorties de programme, demandes 3D et la fonction Python.

Détail de mise au point : le début imposé s'arrête à `):`. `"\n    "` (retour et indentation) est un seul jeton
du Cerveau ; imposer un retour à la ligne seul le sortait de ce qu'il a appris, et il écrivait du texte
incohérent.

**Où elle s'exécute.** Toujours dans la sandbox d'APEX (réseau coupé, clone tracé, autorisation
`PolicyEngine`), jamais en dehors.

| Sandbox | Ce qu'elle exécute (constaté le 16/10/2026) |
|---|---|
| Linux (espaces de noms) | Python, Ruby, JavaScript, PHP, C++, Java (0,1 à 0,9 s), TypeScript (2,1 s) ; C# (0,9 s, avec deux réglages .NET : sans double mappage W^X, tas plafonné à 256 Mo) ; Go (~9 s, disque porté à 256 Mo pour son cache de compilation) ; pas Rust installé dans `~/.cargo` (« Permission denied » depuis la sandbox) |
| Windows (AppContainer) | **Python seul** : sur Windows, la vérification croisée d'un code Go ou Ruby est « non vérifiable » aujourd'hui |

Avant de vérifier un langage, la sandbox exécute une **sonde**, une fonction connue pour être juste,
mémorisée 10 minutes. Si la sonde échoue, ce langage est « non vérifiable » sur ce PC : un échec n'y prouve
rien. La mise au point l'a montré : avant ses réglages .NET, une réponse C# juste aurait été déclarée fausse.
Une limite de la sandbox atteinte (délai, mémoire, taille de fichier) donne aussi « non vérifiable ».

**Dans APEX** : si l'exécution de code est permise (Paramètres > Outils) et la sandbox vérifiée, une réponse
de code du Cerveau (forme apprise, hors Python) est vérifiée à la première étape du tour. Le coût est la
génération de sa version Python, plus les deux exécutions.
- Mode Automatique : une divergence, ou une réponse qui ne compile pas ou plante, confie la question au moteur
  suivant (« confié par le Cerveau APEX (son code diverge de sa version Python) »).
- Moteur « Cerveau » choisi explicitement : la divergence est signalée sous la réponse, sans délégation
  (« code douteux : pour [1,2], il rend 1, sa version Python 2 »).
- Exécution de code désactivée : rien n'est exécuté.

Tests : `tests/test_brain_crosscheck.py` (vrais compilateurs des 9 langages ; sandbox de type Windows simulée)
et `tests/test_assistant.py`, de bout en bout dans la vraie sandbox Linux avec un Cerveau simulé. Les mesures
avec le vrai Cerveau ont exécuté les mêmes commandes en sous-processus, pas dans la sandbox.

**Questions d'actualité** (`apex/assistant/freshness.py`). Quand le Cerveau répond « je n'ai pas accès au temps
réel », trois cas se présentent :

| Question | Ce que fait APEX |
|---|---|
| d'actualité (« aujourd'hui », « en ce moment », « météo »…), un outil connecté est offert | moteur suivant, qui doit appeler l'outil (consigne ajoutée) |
| d'actualité, aucun outil connecté | **réponse honnête du Cerveau gardée** : aucun autre modèle ne connaît l'actualité ; « question d'actualité : aucun outil connecté » |
| pas d'actualité (capitale, auteur…) | moteur suivant, comme au §9 quater : le Cerveau a mal classé la question |

Le repérage se fait par des marqueurs explicites. Mesuré à l'aveugle sur un jeu neuf écrit avant l'essai : 17
questions d'actualité reconnues sur 20, et 5 questions générales sur 20 prises pour de l'actualité (des pièges
comme « valeur actuelle nette » ou « dernier roi de France »). Une fausse alerte ne coûte que la délégation d'un
refus. Après la mesure, j'ai ajouté « last night » et « last week » : ce chiffre n'est donc plus à l'aveugle.
Limites connues : « Combien coûte l'essence ? » ou « la Coupe du monde 2026 » ne sont pas reconnues.

APEX n'a aujourd'hui **aucun outil connecté**. Contrat proposé dans l'Issue #21 : une permission « lecture
réseau » (`network.read`) dans le moteur d'outils, fichier partagé. Ajouter un tel outil est une décision de
confidentialité pour l'utilisateur.

## 9 sexies. Le Cerveau confie une création 3D à Game Studio (16/10/2026)

Les outils de délégation de Game Studio (`game_studio.create`, `.status`, `.results`, `.cancel`) sont écrits par
Codex (PR #24 et #25, non fusionnées à ce jour). L'assistant les enregistre et les utilise ; il ne réimplémente
rien de Game Studio. Tout ce qui suit est dans les fichiers de l'assistant.

**Ce qui est branché**
- **Registre** (`apex/assistant/tools.py`). Les quatre outils sont enregistrés par `register_game_studio_tools`
  quand le module de Game Studio est installé. Sans lui, rien n'est offert ni annoncé (la liste des outils du
  mode est filtrée) et la raison est dite. Un Game Studio présent mais cassé ne retire aucun autre outil.
- **Mode et permissions** (`apex/assistant/modes.py`). Les outils sont offerts en mode 3D seulement :
  - suivre une mission (état, résultats) est permis avec le mode ;
  - lancer ou annuler une mission demande le réglage « Missions Game Studio » (Paramètres > Outils),
    **désactivé par défaut**.
- **Calcul**. Réglage « Calcul des missions » : automatique (GPU CUDA) ou processeur, choisi explicitement. Le
  choix de l'utilisateur est imposé à chaque lancement ; le modèle ne peut pas le changer.
- **Appartenance**. Une mission appartient à l'espace de travail de la conversation (ou de son projet). Game
  Studio refuse de la montrer à un autre espace : elle y est « introuvable ».
- **Chemin du Cerveau** (`apex/assistant/turns.py`). Le Cerveau n'émet pas d'appel d'outil JSON. La mission
  est lancée quand deux conditions sont réunies :
  - le Cerveau a fait son appel 3D appris, réussi ou non ;
  - le message est formulé comme une demande de création (`apex/assistant/creation.py`).

  La description envoyée est la demande de l'utilisateur telle quelle (20 à 4 000 caractères), jamais l'appel
  du Cerveau. L'ébauche paramétrique (§9 ter) reste construite à part.
- **Modèles qui appellent des outils** (Qwen, Ollama). Ils voient `game_studio.create` dans leur liste, avec une
  consigne : ne jamais dire qu'une mission est terminée sans l'avoir lu avec `status` ou `results`.
- **Une mission par tour au plus**, quel que soit le lanceur : un modèle qui boucle n'en lance jamais plusieurs.
- **Suivi**. Le panneau Atelier 3D lit chaque mission par la route
  `GET /assistant/conversations/{id}/game-studio/{project_id}` (outils `status` puis `results`, lecture seule).
  L'annulation passe par `POST …/cancel`, seulement si les missions sont permises. Les fichiers restent derrière
  l'API de Game Studio : l'assistant n'en montre que les noms et les empreintes.

**Pourquoi deux conditions.** Mesuré avec le vrai Cerveau, en mode 3D, dans les conditions de l'application.
D'abord sur 40 messages écrits pour cette mesure ; la règle de formulation a ensuite été écrite en les
regardant, donc ce jeu ne la valide pas.

| Messages (développement) | Appel 3D du Cerveau |
|---|---|
| demandes de création (12 de son répertoire, 8 hors répertoire, 8 scènes) | 26 / 28 |
| questions du mode 3D | 2 / 12 |

Ensuite, sur 60 messages jamais vus, écrits après avoir figé la règle. Conditions finales exactes : outils et
consigne Game Studio dans les consignes du modèle.

| Messages (validation) | Cerveau seul | règle seule | Cerveau ET règle (déclencheur) |
|---|---|---|---|
| demandes de création | 26 / 30 | 26 / 30 | **22 / 30** |
| autres messages (questions, remerciements, modifications) | 7 / 30 | 1 / 30 | **0 / 30** |

Une mission coûte des minutes de GPU : aucun lancement à tort sur 30 messages qui n'en demandaient pas, au prix
de 8 créations non confiées sur 30.
- 4 de ces créations ne passent pas la règle : « J'aimerais une… », « Prépare… », « Ajoute… », « Dessine-moi… ».
- Pour les 4 autres, le Cerveau n'a fait aucun appel 3D.

Le Cerveau seul aurait lancé une mission pour « Annule la mission en cours », « Lis le script Blender de la
voiture » ou « Change la couleur du tank en bleu ».

Autre constat, sur le jeu de développement, et la raison pour laquelle la description est la demande de
l'utilisateur : pour 16 objets hors de son répertoire ou scènes, l'ébauche du Cerveau construit un modèle sans
rapport dans 10 cas (une chaise devient un personnage, une salle de classe une voiture). Elle échoue dans
5 autres cas.

**Chaîne réelle, de bout en bout.** Arbre d'intégration local : ma branche plus la PR #25, jamais poussé,
dossier APEX vierge. Le vrai Cerveau reçoit « Crée une scène de forêt avec une cabane et une rivière. ». Le
déclencheur part, et le vrai `game_studio.create` (moteur d'outils, permission accordée) atteint le vrai service
de Game Studio :

| Calcul | Réponse de Game Studio | Durée de l'appel |
|---|---|---|
| automatique | refus `cuda_unavailable_choose_cpu_explicitly` (pas de GPU ici) | 2,2 s (sonde CUDA) |
| processeur | refus `local_planner_model_missing` (modèle planificateur absent, téléchargement bloqué ici) | 0,03 s |

Les deux refus sont montrés tels quels, avec leur explication. **Aucune mission n'a pu s'exécuter ici** : qu'une
scène soit réellement produite par Game Studio n'est pas prouvé dans cet environnement. Il faut le GPU et le
modèle planificateur du PC de l'utilisateur.

Le premier essai a aussi révélé que `jsonschema` (et Pillow) manquaient : Game Studio les importe sans les
déclarer. Signalé à Codex (Issue #21).

**Tests** (`tests/test_assistant_game_studio.py`) :
- 6 tests sans Game Studio (toujours exécutés) ;
- 8 avec ses vrais outils, ignorés quand il est absent.

Sur ma branche, 6 passent et 8 sont ignorés. Dans l'arbre d'intégration, les 14 passent, ainsi que les 2 tests
de Codex.

**Limites connues**
- Formulations non reconnues (ci-dessus). Les ajouter demande un nouveau jeu jamais vu pour les mesurer.
- En mode Automatique, un Cerveau peu sûr de lui (confiance 0,69 sur la scène ci-dessus) confie le tour à un
  autre moteur s'il y en a un. C'est alors ce moteur qui décide d'appeler `game_studio.create`.
- L'ébauche paramétrique est construite même quand elle est sans rapport (comportement antérieur, inchangé).

Ces trois limites sont traitées au §9 septies.

## 9 septies. Délégation à Game Studio fiabilisée, ébauches vérifiées, partage du GPU (16/10/2026)

### Reconnaître une demande de création (`apex/assistant/creation.py`, v2)

La règle du §9 sexies (une liste de formulations, combinée à l'appel 3D du Cerveau) est remplacée par une
analyse de la structure de la phrase.

| Élément analysé | Effet |
|---|---|
| acte de langage | question d'explication, négation, ce qui a déjà été fait, hypothèse → rien |
| verbe ou souhait de création | plus de 40 formes : « dessine-moi », « prépare », « ajoute », « j'aimerais », « il me faudrait », subjonctif, anglais |
| objet | son nom (après les adjectifs) doit désigner une création 3D : « fais le point », « crée un fichier », « prépare un résumé » → rien |

Elle a trois issues :
- `create` : demande claire ;
- `ambiguous` : question de capacité (« Sais-tu faire des avions ? »), report (« plus tard »), ajout à un
  objet existant (« ajoute des roues à la voiture »), « refais… » ;
- `none` : tout le reste.

Elle a été écrite sur 129 messages de développement : les 100 messages déjà utilisés, les 5 exemples de
l'utilisateur et des cas écrits pour chaque règle. Puis elle a été **figée** (empreinte SHA-256 vérifiée par
le script de mesure). Ensuite seulement, j'ai écrit un jeu neuf de **120 messages jamais vus** : 50
créations claires, 50 non-créations, 20 ambiguës. La règle de choix entre les variantes a été fixée avant la
mesure : aucun lancement à tort, puis le plus de créations reconnues.

Mesure avec le vrai Cerveau, en mode 3D, dans les conditions finales (outils et consigne Game Studio offerts).
Pour les colonnes « à tort », les ambiguës se lancent seulement après confirmation.

| Variante | Créations lancées | Non-créations lancées à tort | Ambiguës lancées à tort |
|---|---|---|---|
| appel 3D du Cerveau seul | 42 / 50 | 24 / 50 | 10 / 20 |
| §9 sexies : Cerveau ET règle v1 | 29 / 50 | **3 / 50** | **1 / 20** |
| **v2 seule (retenue)** | **48 / 50** (+1 proposée) | **0 / 50** | **0 / 20** (13 proposées) |
| Cerveau ET v2 | 40 / 50 | 0 / 50 | 0 / 20 |

- **Correction du §9 sexies.** Sur ce jeu plus large, la règle livrée n'était pas sans risque : elle lançait
  une mission pour « Crée un fichier de notes », « Fais un résumé des modèles produits », « Crée un nouveau
  projet pour mon jeu » et « Peux-tu créer des arbres ? ».
- **Les 2 créations manquées par v2.**
  - « Ajoute deux arbres et un banc au parc de mon projet » est jugée ajout à un objet existant : elle est
    proposée, pas lancée.
  - « I'd like a small red car. » venait d'une contraction mal écrite dans la règle. Je l'ai corrigée après
    la mesure : sur ce jeu, v2 fait donc maintenant 49 / 50, mais ce chiffre n'est plus à l'aveugle.
- **L'appel 3D du Cerveau ne décide plus.** Il est rapporté comme indice (`brain_3d_call`). Seul, il se
  déclenche sur un message sur deux qui ne demande rien ; combiné à v2, il fait perdre 8 créations sans
  rien gagner.

### Le Cerveau propose, l'utilisateur lance (`apex/assistant/turns.py`, `apex/assistant/game_studio.py`)

**Constat de politique.** Dans le moteur de politique d'APEX, `CREATE_MISSION` est réservé à l'humain
(« jamais le CORE en autonomie »). Le lancement automatique du §9 sexies ne passait que par la permission du
moteur d'outils. De son côté, Game Studio enregistre `created_by=HUMAN` pour toute mission.

| Situation | Ce que fait APEX |
|---|---|
| **par défaut** : demande claire ou ambiguë | le Cerveau **propose** la mission dans l'Atelier 3D |
| clic « Lancer la mission » | acte humain, vérifié par `PolicyEngine` (HUMAN, CREATE_MISSION) ; une seule mission par proposition (deux clics simultanés n'en lancent qu'une) ; le lancement est enregistré comme un **nouveau** message, car les messages ne sont jamais réécrits |
| réglage « Lancement automatique des missions Game Studio » activé, demande claire | le Cerveau lance lui-même, au titre de cette autorisation permanente, quel que soit le moteur qui a rédigé la réponse |
| demande ambiguë, même avec le lancement automatique | toujours proposée |
| annulation | acte humain (HUMAN, CANCEL_MISSION), toujours permise |

Le lancement automatique est tracé (`authorization: "lancement automatique permis"`, `source: "cerveau"`).
Le garder est une décision à confirmer par l'utilisateur et par Codex (Issue #21).

**Ce que voit l'utilisateur** (Atelier 3D) :
- la disponibilité de Game Studio sur ce PC, lue par sa propre sonde (`probe()`), et ce qui manque, en
  clair : Blender, planificateur Qwen, bibliothèques (dont `jsonschema`, non déclarée), GPU CUDA ;
- les propositions, avec leur bouton ;
- les missions : état, étapes, étapes bloquées, aperçus des rendus (images `view`, servies seulement pour
  une mission de cette conversation), fichiers, bouton d'annulation ;
- les refus de Game Studio, expliqués (`apex.assistant.game_studio.explain`), avec le code exact à côté.

### Ébauches du Cerveau vérifiées (`apex/brain/studio3d.py`, `turns.model3d_mismatch`)

Une ébauche paramétrique n'est plus écrite que pour une demande de création, et seulement si l'objet
construit est celui que la demande nomme (`studio3d.matches_request`). Sinon c'est dit. En mode
Automatique, la réponse va alors au moteur suivant (motif `model3d_mismatch`), comme pour le code au §9
quinquies.

Mesure sur les 137 appels 3D du vrai Cerveau relevés dans les trois jeux ci-dessus. Ces messages avaient
déjà été vus : la mesure n'est pas à l'aveugle.

| | Ébauches écrites | dont la bonne sorte d'objet |
|---|---|---|
| avant | 98 | 17 |
| après | 17 | 17 |

Parmi les 81 ébauches qui ne sont plus écrites :
- un pick-up devenu fusil, une chaise devenue personnage ;
- une voiture construite pour « Ne crée pas de voiture » ;
- des modèles construits pour des questions.

Couleur et taille ne sont pas vérifiées : une moto « verte » peut sortir noire.

### Partage du GPU, côté Cerveau (`apex/models/gpu_handoff.py`)

Le protocole est celui de l'Issue #21, accepté par Codex. Le détail est en tête du module et dans
`docs/INTEGRATION_CERVEAU_GAME_STUDIO.md`.

- **Bail.** Le Cerveau et le moteur intégré **tiennent** le bail `APEX_HOME/.apex/model-session.lock` tant
  qu'ils sont sur CUDA, avec la même primitive que `resource_lease` de Game Studio.
- **Demande du spécialiste** (`request_gpu(timeout_s, requester)`), dans l'ordre :
  - les admissions CUDA se ferment ;
  - les générations en cours se terminent, sans être coupées, dans le délai ;
  - les modèles sont déchargés et la mémoire CUDA est mesurée ;
  - le bail est rendu, et un accusé de réception est renvoyé.
- **Délai dépassé** : rien n'est déchargé, et `released: false`.
- **Pendant le prêt** : le Cerveau répond sur le processeur, et ses mesures le disent (`device_reason`).
- **Reprise** : `return_gpu(requester)` rouvre les admissions ; le Cerveau revient sur CUDA au prochain
  chargement.

Tests :
- 10 tests, dont l'exclusion mutuelle avec un autre processus (même primitive) ;
- dans l'arbre d'intégration, avec le vrai `resource_lease` de Codex : jamais les deux à la fois, transfert
  après l'accusé de réception, reprise.

**Aucune mesure CUDA** : cet environnement n'a pas de GPU, et l'accusé le dit (`cuda.measured: false`).

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
