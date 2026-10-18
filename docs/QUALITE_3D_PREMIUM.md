# Vers une 3D automobile haut de gamme dans APEX : audit, architecture, feuille de route (18/10/2026)

Objectif fixé par l'utilisateur : des créations au niveau des meilleurs rendus Blender professionnels (hypercar
agressive, proportions premium, matériaux réalistes, détails, rendu showroom), puis les dépasser sur des tâches
ciblées, **sans casser** la première création réelle validée dans APEX.exe.

Règles de ce document :
- un fait **mesuré** est donné avec sa source ;
- une **proposition** est marquée comme telle ;
- rien n'est annoncé comme qualifié sur la RTX 4060 Ti sans test matériel.

Aucun fichier de Codex n'a été modifié pour ce travail.

Les deux images de référence (voitures jaune et blanche) ne sont pas parvenues à Claude dans la session de travail.
L'audit s'appuie donc sur leur description. Elles serviront de cibles dès qu'elles seront déposées, par exemple dans
`docs/game_studio/references/`.

## 1. Audit de la qualité actuelle

### 1.1 Ce qui est réellement produit

Sources : le code de Game Studio à `550b347` (#31), et les rendus réels archivés par Codex (run 4 sous Windows,
archive `windows-run4-vehicle-pass-probe-failure.zip`, SHA-256 `e565193e…`, vérifiée).

| Constat | Mesure ou preuve |
|---|---|
| **Une seule voiture, codée à la main.** Toute demande de voiture produit le même gabarit (`build_vehicle()`, `scripts/game_graphics/studio/build_assets.py`) | une coque super-elliptique de 4,8 m (49 sections × 32 points), avec des renflements d'ailes en gaussiennes ; des pneus en tores, des jantes en tores et tubes, des phares en cubes, une calandre en ailettes cubiques |
| **Le plan Qwen ne décrit pas la voiture.** Il découpe la demande en tâches | plan réel : une étape `vehicle` dont la description reprend la demande ; aucun paramètre de forme, de proportion ou de style |
| **Les corrections sont bornées à 7 opérations** | `bevel_edges`, `subdivide_surface`, `set_location`, `set_material_pbr` (couleur, rugosité, métal), `add_component` (pour une voiture : `rear_spoiler` seulement), `repair_degenerate_topology`, `inspect_scene` |
| **La seule correction de la mission réelle portait sur la peinture** | `set_material_pbr petrol_paint → base_color [0.5, 0.02, 0.02], roughness 0.4, metallic 0` |
| **Matériaux sommaires** | Principled BSDF à valeurs fixes : ni vernis (coat), ni paillettes, ni texture, ni carbone, ni pneu détaillé |
| **Éclairage et caméras de diagnostic, pas de vitrine** | Cycles **64 échantillons sans débruitage**, 960 × 720, AgX standard ; monde gris uni d'intensité 0,4 ; 3 sources de surface ; **caméras orthographiques**, choisies pour aligner les mesures du juge, sans la perspective d'une photo automobile |

### 1.2 Mesures sur les vrais rendus

Instrument d'APEX (`apex/assistant/render_metrics.py`, §4) appliqué à la mission réelle du run 4. La
silhouette fournie par Game Studio délimite la voiture.

| Vue | Version | Couleur perçue de la carrosserie | ΔE au rouge demandé | Écart de clarté voiture / fond | Arêtes et détails | Part de l'image |
|---|---|---|---|---|---|---|
| 3/4 avant | 1 (pétrole) | cyan peu saturé | 97,7 | 23,0 | 8,8 % | 17,4 % |
| profil | 1 (pétrole) | cyan peu saturé | 94,0 | 21,0 | 9,6 % | 15,0 % |
| 3/4 avant | **finale (« rouge »)** | **saumon clair** (L* 78, C* 31) | **59,3** | **0,6** | 5,8 % | 17,4 % |
| profil | **finale (« rouge »)** | **saumon clair** (L* 74, C* 33) | **55,5** | **2,3** | 5,8 % | 15,0 % |

Ce que ces chiffres disent :
- la valeur « rouge » du shader est passée au contrôle du juge ;
- **le rendu, lui, montre un saumon délavé**, presque de la même clarté que le fond (écart de 0,6 à 2,3) ;
- la voiture n'occupe qu'un sixième de l'image ;
- la densité d'arêtes et de détails est faible, et elle a baissé avec la correction.

### 1.3 Causes précises de la faiblesse visuelle

1. **Formes génériques** : un gabarit fixe et une coque soufflée (« savon »), sans ligne de caractère ni arête vive. Le
   pavillon est haut et les porte-à-faux longs : les proportions d'une coupé des années 70, pas d'une hypercar.
2. **Peu de détails** : pas d'entrées d'air, pas de jeux de carrosserie réels (un joint de porte flotte hors de la
   caisse), pas d'aéro sculptée ; les jantes se lisent comme des disques gris.
3. **Matériaux** : une peinture sans vernis ni paillettes, et du métal à 0,78 sur la première version (physiquement
   faux pour une peinture). Pas de carbone, pas de flanc de pneu, pas de disque de frein détaillé.
4. **Pas de direction artistique** : le style (« agressif »), le rendu (« photoréaliste ») et la mise en scène
   (« showroom ») ne sont lus par aucun constructeur.
5. **Éclairage et cadrage** : pas d'HDRI ni de studio réfléchissant, donc pas de reflets qui dessinent la carrosserie.
   Les caméras orthographiques, le bruit sans débruitage et le sujet trop petit aplatissent tout.
6. **Pas de jugement esthétique** : le juge vérifie l'intégrité (géométrie, unités, triangles, caméras, cadrage,
   exports, composants, UV). Lui-même le dit : `"Shader parameters and material count do not certify convincing PBR
   appearance"`, `"artistic_fidelity_certified": false`. Le juge visuel calibré n'existe que pour les fauteuils.
7. **Couleurs et mots** : Game Studio ne vérifie que le rouge et le bleu, sur la valeur du shader et non sur le rendu.
   Le jaune et le blanc des références ne seraient pas vérifiés. **Mesuré** : « Crée une hypercar agressive
   photoréaliste dans ce style. » est refusée par son propre juge de couverture (`unrequested_object`), car son
   extracteur ne voit pas de voiture dans « hypercar ». « Une voiture hypercar… » passe.
8. **Pas de modèle spécialisé** : Qwen 7B planifie du texte. Aucun modèle ne voit les images, ne juge les rendus ni ne
   génère de géométrie ou de texture.

Ne pas confondre : le PASS actuel est un **PASS technique** (fichiers sains, cadrage, composants). Il ne mesure ni
la beauté, ni le style, ni la couleur perçue.

## 2. Approches possibles, sous 8 Go de VRAM et 16 Go de RAM, en local

| Approche | Apport pour une hypercar | Coût local (RTX 4060 Ti 8 Go, 16 Go de RAM) | Verdict |
|---|---|---|---|
| **Procédural paramétrique « hard surface » dans Blender** (profils, sections, plis, modules aéro, bibliothèque de jantes et feux) | arêtes vives, proportions exactes, détails maîtrisés, topologie propre, pièces séparées pour Unreal ; modifiable et mesurable | CPU ; Blender seul | **cœur de l'architecture** |
| **Bibliothèque de matériaux et rendu vitrine** (peinture vernie à paillettes, carbone, verre, HDRI studio, perspective, débruitage) | le plus grand saut perçu au moindre risque | CPU possible ; Cycles sur GPU plus tard, après test | **premier chantier** |
| **Modèle multimodal local (VLM)** pour lire les références et critiquer les rendus | compréhension d'images, critique par paires | Qwen3-VL-8B quantifié Q4 : environ 6 Go de VRAM selon des estimations tierces ([llmrun.dev](https://llmrun.dev/model/qwen-qwen3-vl-8b-instruct), [Unsloth](https://unsloth.ai/docs/models/qwen3-vl-how-to-run-and-fine-tune)) ; Qwen2.5-VL-3B déjà intégré par Codex (#7) | utile, **jamais juge seul** |
| **Image ou texte vers 3D neuronal** (TRELLIS.2, Hunyuan3D 2.1) | silhouette d'ensemble rapide | Hunyuan3D 2.1 : 10 Go pour la forme, 21 Go pour la texture (fiche officielle) ; 6 à 8 Go avec déchargement communautaire, texture lente ([tspi.at](https://www.tspi.at/2026/05/01/opensource3dassets.html), [README](https://huggingface.co/spaces/dylanebert/Hunyuan3D-2.1/blob/4ba9ccc49d39b296e82df14994f9999592b85c4c/README.md)). TRELLIS.2 : environ 24 Go selon des sources secondaires ([codesota](https://www.codesota.com/news/trellis-2-3d-generation)) | **pas pour la carrosserie** : surfaces molles, topologie inexploitable pour une voiture de vitrine. Possible plus tard pour des accessoires organiques (TripoSR CPU déjà qualifié par Codex, #17) |
| **Reconstruction depuis les références** | proportions et silhouette | segmentation et ajustement des paramètres à la silhouette, sans réseau lourd | **oui, comme guide de proportions** |
| **Passes de raffinement** (générer, rendre, mesurer, corriger) | amélioration bornée et traçable | temps CPU par version | **oui, déjà l'esprit de Game Studio** |
| **Juges visuels spécialisés** | sélection des versions, régressions | légers (mesures) à moyens (VLM) | **oui, calibrés sur des avis humains** |

Ce qu'en dit la recherche (pour les juges) :
- [3DGen-Bench](https://arxiv.org/html/2503.21745v2) bâtit ses évaluateurs sur des préférences humaines par paires ;
- [Gen3DEval](https://openaccess.thecvf.com/content/CVPR2025/papers/Maiti_Gen3DEval_Using_vLLMs_for_Automatic_Evaluation_of_Generated_3D_Objects_CVPR_2025_paper.pdf)
  compare des rendus multivues par paires ;
- [3D-DefectBench](https://arxiv.org/pdf/2607.10826) mesure que le meilleur de 12 VLM reste derrière des annotateurs
  humains entraînés, et recommande de calibrer contre des références humaines.

D'où la règle : un VLM ne décide jamais seul ; il est calibré et ne sert d'abord qu'à conseiller.

## 3. Architecture proposée

```
Demande + images ─► Brief créatif (assistant) ─► confirmation de l'utilisateur
                         │                               │
                         ▼                               ▼
              Compréhension des références      DesignBrief v1 (contrat C1)
              (VLM local, champs confirmés)             │
                                                         ▼
                                    Planificateur (Qwen) : paramètres bornés
                                                         ▼
                    Constructeur paramétrique de véhicule (archétypes, modules, jantes, feux)
                                                         ▼
                        Bibliothèque de matériaux ─► Rendu « vitrine » (et rendu de diagnostic séparé)
                                                         ▼
     Juges : technique (existant) · métriques de design · mesures de rendu · VLM par paires (conseil)
                                                         ▼
           Corrections bornées (catalogue étendu) ─► versions ─► choix et avis de l'utilisateur
                                                         ▼
                          Données de calibration (avis humains, comparaisons par paires)
```

Principes :
- **Déterministe d'abord** : la géométrie vient d'un constructeur paramétrique, inspectable et reproductible (graine).
  Les modèles proposent des paramètres dans des bornes ; ils n'écrivent pas de géométrie libre.
- **Deux rendus distincts** : le rendu de diagnostic (orthographique, fixe) reste celui du juge technique, et un
  rendu vitrine est fait pour l'œil. Le parcours validé n'est pas touché.
- **Mesurer avant d'affirmer** : chaque étage a ses mesures, et chaque juge artistique reste « conseil » tant qu'il
  n'est pas calibré.

## 4. Fait dans le périmètre de Claude (cette livraison)

| Élément | Fichiers | Tests |
|---|---|---|
| **Brief créatif** : la demande rangée par aspect (archétype, style, rendu, mise en scène, couleurs par partie et finition, matériaux, éléments, quantités, références). Chaque élément reçoit un statut honnête : vérifié par Game Studio (d'après SON extracteur), possible, pas encore pris en compte, ou refusé. Joint à la proposition, montré **avant le clic** ; « hypercar » non reconnue est signalée avec la formulation qui passe. La demande reste transmise telle quelle | `apex/assistant/creative_brief.py`, `turns.py`, `ModePanels.tsx` (`BriefSupport`) | `tests/test_creative_brief.py` (dont l'extracteur réel de Codex), `test_assistant_game_studio.py`, `views.test.tsx` |
| **Instrument de mesure des rendus** : couleur perçue de la carrosserie et écart à la couleur demandée, exposition, contraste, reflets, séparation du fond, bruit, densité d'arêtes, part de l'image. Formules fixées ; « instrument non calibré », jamais un verdict | `apex/assistant/render_metrics.py` | `tests/test_render_metrics.py` |
| **Qualité d'une mission dans l'Atelier 3D** : versions et verdicts, défauts, version retenue, ce que le juge vérifie (et la phrase « il ne mesure ni la beauté, ni le style, ni la couleur perçue »), ses réserves, limites, et les mesures des rendus de la version retenue avec leurs silhouettes | `game_studio.quality`, `GET …/game-studio/{id}/quality`, `GameStudioQualityPanel` | idem |
| **Avis humains** : 7 critères (agressivité, proportions, détails, matériaux, lumière, fidélité, impact), de 1 à 5, plus un commentaire. Enregistrés avec les empreintes des rendus vus et la version du brief, dans `APEX_HOME/.apex/game-studio-ratings.jsonl` : données de calibration des futurs juges | `game_studio.rate`, `POST …/ratings` | idem |

Rien de tout cela ne change la mission : lecture seule, en dehors des avis.

Résultats des tests (18/10/2026, Linux) :
- arbre combiné avec Game Studio de Codex (#31) : `test_creative_brief`, `test_render_metrics`,
  `test_assistant_game_studio`, `test_assistant` : **133 réussis, 0 ignoré** ;
- branche de Claude seule (Game Studio absent) : sélection assistant, tours, API : **403 réussis, 127 ignorés**
  (Game Studio absent), 0 échec. `test_gate_is_unknown_without_a_report` échoue aussi **sans** ces changements : un
  rapport Gate existe dans ce bac à sable ; il est exclu de cette sélection et n'a pas été touché ;
- interface : **191 tests réussis** (27 fichiers), `tsc` sans erreur ;
- `ruff` : aucun nouvel écart sur les fichiers ajoutés ou modifiés.

Ces tests prouvent le comportement du code sur des données construites et sur l'extracteur réel de Codex. Ils ne
prouvent **rien** sur la qualité visuelle : l'instrument n'est pas calibré, et aucune mission réelle n'a encore été
notée par un humain.

## 5. Juges artistiques : une méthode mesurable

Quatre niveaux, du plus objectif au plus humain :

1. **Technique** (existant, Codex) : intégrité, exports, cadrage. Bloquant.
2. **Métriques de design**, sur la géométrie : proposition (contrat C7).
   - Proportions comparées aux cibles de l'archétype : diamètre de roue rapporté à la hauteur, empattement rapporté à
     la longueur, porte-à-faux, position de l'habitacle, voie rapportée à la largeur (posture), garde au sol.
   - Plis et arêtes : angles dièdres > 30° le long des lignes de caractère.
   - Densité de détails : composants, longueur de jeux par m², entrées d'air.
   - Validité physique des matériaux : vernis sur la peinture, métal réservé aux métaux, transmission du verre.
   - Symétrie.
3. **Mesures de rendu** (instrument livré, §4) : couleur perçue (ΔE), séparation du fond, structure des reflets, bruit,
   cadrage. Sans masque, elles se limitent à l'image entière.
4. **VLM par paires**, en conseil d'abord :
   - A contre B, positions inversées, 3 tirages, abstention permise ;
   - une grille fixe par critère (les 7 de l'utilisateur) ;
   - jamais « est-ce beau ? » : « laquelle a les entrées d'air les plus marquées ? », « laquelle respecte les
     proportions d'une hypercar ? ».

**Calibration contre des avis humains** :
- **Jeu de référence** : 10 demandes fixes (hypercar jaune agressive, GT blanche élégante…), graines fixes, rendues
  avant et après chaque changement de pipeline.
- **Avis** : notes de 1 à 5 (déjà enregistrées) et comparaisons par paires (à ajouter dans l'Atelier 3D, périmètre
  Claude), transformées en scores de Bradley-Terry.
- **Accord** : le plafond est l'accord entre humains (kappa de Cohen, accord à la majorité) ; un juge automatique est
  mesuré sur des paires tenues à l'écart (tau de Kendall, accuracy).
- **Promotion** : un juge passe de « conseil » à « filtre » seulement si son accord atteint le plafond humain moins une
  marge fixée à l'avance. Il est recalibré à chaque version du pipeline.
- **Anti-triche** : les juges ne voient que les rendus et la géométrie livrés, pas les paramètres du générateur. La
  grille du VLM est figée. « Indisponible » vaut mieux qu'un score deviné.
- **Avant / après** : chaque amélioration est acceptée sur des comparaisons par paires (par exemple ≥ 70 % de
  préférence humaine sur le jeu de référence), pas sur une impression.

## 6. Comprendre les images de référence

Aujourd'hui (mesuré) :
- l'assistant refuse les images jointes (« APEX ne lit que des fichiers texte », HTTP 422) ;
- `game_studio.create` ne transmet aucune référence (`create_dynamic(request, [])`) ;
- `create_dynamic` sait **archiver** des références avec leur provenance, mais Codex l'écrit lui-même : `"the
  text-only controller does not interpret their pixels"` ;
- le spécialiste Vision de Codex (#7, Qwen2.5-VL-3B, transformers, local) existe, orienté documents, non qualifié, et
  n'est branché nulle part.

**APEX ne comprend donc aucune image aujourd'hui.**

Proposition d'intégration, qui respecte le périmètre de Codex :
1. **Assistant (Claude)** : accepter PNG et JPEG en mode 3D, comme « références », avec contrôle des octets magiques,
   de la taille et de la provenance déclarée. Les afficher en vignettes avec la mention « conservée, pas encore
   comprise ».
2. **Contrat C1 (Codex)** : `game_studio.create` reçoit `references` (octets et provenance) et `brief`. Game Studio
   archive les références comme aujourd'hui.
3. **Compréhension (Vision, Codex)** : une tâche `reference_features` du VLM, sous schéma JSON contraint.
   - Champs : type de carrosserie, couleur et finition, éléments visibles (aileron, entrées d'air, portes, signature
     lumineuse), ambiance d'éclairage, indications qualitatives de proportions, plus une confiance par champ.
   - Silhouette extraite par segmentation, pour guider les proportions.
4. **Confirmation humaine (Claude)** : l'Atelier 3D montre « Voici ce qu'APEX a compris de l'image » et l'utilisateur
   corrige. Seuls les champs confirmés entrent dans le brief : pas d'hallucination silencieuse.
5. **Fidélité mesurée** : écart de silhouette (IoU après alignement), écart de couleur perçue, comparaison par paires
   avec la référence (VLM en conseil, puis humains).

## 7. GPU d'abord, sans casser le protocole 2

État :
- **constaté par l'utilisateur** : le Cerveau sur la RTX 4060 Ti (PyTorch CUDA) ;
- Game Studio est en processeur seulement (garde de Codex) ;
- l'adaptateur de Codex sait déjà mettre le Cerveau au repos autour de chaque étape CUDA (protocole 2 : prêts
  comptés, retour atomique) ;
- avec #31, une étape sur processeur ne prend plus le bail du Cerveau.

Proposition, par ordre de gain :
1. **Cycles sur la RTX** (OptiX ou CUDA de Blender) pour les rendus vitrine : le gain de temps le plus direct (plus
   d'échantillons, débruitage). Lancé sous le bail exclusif (`_gpu_lease` de Codex). La VRAM est à mesurer pour une
   scène de voiture.
2. **Planificateur Qwen 7B en CUDA** (`llama-cpp-python` CUDA, absent aujourd'hui) : environ 5,4 Go de poids plus le
   cache. Il exige le GPU seul : le Cerveau est mis au repos (`request_gpu`), le protocole 2 suffit.
3. **VLM 8B quantifié** (environ 6 Go d'après des estimations) : même créneau exclusif, une tâche à la fois.
4. **Ordonnancement strict** : un seul occupant CUDA à la fois. Chaque spécialiste déclare `vram_mib` et `device` dans
   ses reçus. La mémoire CUDA avant et après est relevée (déjà dans l'accusé de `request_gpu`). Un manque de mémoire
   (OOM) rend l'étape échouée et reprenable, et le Cerveau est rendu dans le `finally` de l'adaptateur.
5. **Plus tard, protocole 3 (proposition)** : cohabitation budgétée. Le Cerveau (15,7 M de paramètres) resterait
   chargé si le budget déclaré du spécialiste laisse une marge mesurée. Seulement après des mesures sur la RTX, jamais
   par défaut, et sans changer le protocole 2.

Tests matériels requis avant toute annonce (sur le PC de l'utilisateur) :
- Cycles OptiX : rendu de la scène de référence, VRAM maximale, temps ;
- planificateur CUDA : chargement, inférence, déchargement, VRAM avant et après ;
- enchaînement Cerveau, spécialiste, Cerveau, dix fois, sans fuite ;
- OOM provoqué : échec propre, puis reprise.

## 8. Feuille de route priorisée

| Phase | Qui | Contenu | Critère d'acceptation mesuré |
|---|---|---|---|
| **P0** (livré) | Claude | brief et sort de chaque élément, alerte « hypercar », volet Qualité, instrument de mesure, avis humains | tests ; mesures reproduites sur le run 4 |
| **P1** | Codex (C2, C4, C5) | rendu vitrine séparé (HDRI CC0 embarquée, perspective 50 à 85 mm, 256 à 512 échantillons, débruitage, 1920 × 1080) ; peinture vernie à paillettes ; vocabulaire « hypercar, supercar, sportive… » et toutes les couleurs, vérifiées sur la couleur perçue | jeu de référence : ΔE de la couleur perçue < 15 ; séparation voiture / fond > 15 ; part de l'image 30 à 55 % ; ≥ 80 % de préférence humaine contre les rendus actuels |
| **P2** | Codex (C6, C7) + Claude (C1) | espace de design « véhicule » v1 : archétype hypercar, plis, entrées d'air, aéro, jantes et feux ; le planificateur remplit les paramètres ; métriques de design ; DesignBrief v1 transmis | proportions dans les cibles de l'archétype ; densité de détails ×2 ; préférence humaine ≥ 70 % contre P1 |
| **P3** | Claude (interface des paires, jeu de données) + Codex (VLM en conseil) | comparaisons par paires dans l'Atelier 3D, scores de Bradley-Terry, grille VLM | accord du VLM ≥ plafond humain moins la marge, sur paires tenues à l'écart |
| **P4** | Claude (pièces jointes, confirmation) + Codex (Vision, C1) | références comprises, champs confirmés, silhouette guide | ≥ 80 % de champs justes sur 20 images, confirmés par l'utilisateur |
| **P5** | Codex + test de l'utilisateur | GPU d'abord : Cycles, planificateur et VLM sur la RTX | tests matériels du §7 réussis |
| **P6** | les deux | archétypes, jantes, intérieurs ; boucles de raffinement ; aides neuronales pour les accessoires | préférence humaine croissante, version par version |

## 9. Contrats proposés à Codex (Issue #21)

- **C1, brief et références** : `game_studio.create` accepte `brief` (DesignBrief v1, schéma JSON de
  `creative_brief.extract` sans `support`) et `references` (octets, type, provenance). Le planificateur reçoit le brief ;
  Game Studio rend `brief_coverage` (élément → réalisé, vérifié, non pris en charge).
- **C2, vocabulaire et couleur perçue** :
  - noms de véhicules : hypercar, supercar, sportive, berline, SUV, coupé, GT ;
  - les 12 couleurs du brief ;
  - vérification de la couleur sur le rendu (ΔE perçu, silhouette) en plus de la valeur du shader.
- **C3, lecture de qualité** : `game_studio.results`, ou un outil `game_studio.quality`, rend les empreintes des
  silhouettes et du rapport du juge. L'assistant n'aurait plus à lire l'enregistrement par `service.get`, qu'il lit
  aujourd'hui en lecture seule après la vérification de propriété.
- **C4, profil de rendu** : `render_profile: "diagnostic" | "showroom"`, avec caméras vitrine, échantillons,
  débruitage, résolution, HDRI (licence CC0 jointe). Le diagnostic reste inchangé pour le juge technique.
- **C5, bibliothèque de matériaux** : préréglages nommés (peinture solide, métallisée, nacrée ou mate ; carbone ; verre
  teinté ; chrome ; aluminium brossé ; pneu ; disque de frein) et une opération `set_material_preset`.
- **C6, espace de design véhicule** : un schéma `vehicle_params` (archétype et paramètres bornés) lu par le
  constructeur, et une opération `set_design_param` pour les corrections.
- **C7, métriques de design** : rendues par critère avec `calibrated: false`. Les avis humains
  (`game-studio-ratings.jsonl`, schéma au §4) sont acceptés en lecture comme données de calibration.
- **C8, GPU** : `vram_mib` et `device` dans les reçus de cycle de vie ; Cycles GPU sous `_gpu_lease` ; la garde ne
  se lève qu'après les tests matériels du §7.

## 10. Ce qui est préservé

Le parcours validé est intact :
- lancement depuis APEX.exe, proposition, clic humain ;
- planification réelle, fichiers Blender et exports ;
- juges techniques et corrections, permissions, partage du GPU, historiques.

Garanties :
- la description transmise reste la demande de l'utilisateur, mot pour mot (vérifié par un test) ;
- le brief, la qualité et les mesures sont en lecture seule ;
- les avis vont dans un fichier séparé ;
- aucun fichier de Codex n'est modifié, aucune fusion, aucune release.
