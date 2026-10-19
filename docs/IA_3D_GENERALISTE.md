# Intelligence 3D généraliste et apprentissage continu : audit et proposition technique (19/10/2026)

**Statut : proposition, rien n'est développé.** L'utilisateur a demandé l'audit et la proposition d'abord, puis une
validation explicite avant tout développement. Ce document ne modifie aucun code. Aucun fichier de Codex n'a été touché.

Conventions :
- un fait **mesuré** ou **lu dans le code** porte sa source (fichier, run, document) ;
- une **proposition** est marquée comme telle ;
- un chiffre tiers (mémoire d'un modèle, par exemple) est marqué « selon … » et reste à mesurer sur la RTX 4060 Ti ;
- rien n'est annoncé sur CUDA sans test matériel.

Les points qui demandent une décision de l'utilisateur sont regroupés au §11.

---

## 1. Audit technique

Sources : le code de Game Studio à `550b347` (#31, arbre combiné de la tentative 10, lecture seule), le code de
Claude à `78e0215`, les runs Windows 39150876510 et 39155790261, et `docs/game_graphics/studio/TRAINING.md` (Codex).

### 1.1 Ce qui existe réellement

| Domaine | Ce qui existe | Propriétaire | Limites constatées |
|---|---|---|---|
| **Modèle planificateur** | Qwen2.5-Coder-7B-Instruct Q5_K_M (GGUF, llama.cpp, processeur), `temperature=0`, graine fixe, 600 jetons (`apex/game_studio/planning.py`) | Codex | Le plan découpe la demande en tâches : aucun paramètre de forme ni de style. Poids jamais adaptés (`"trained_checkpoint": False`, `capabilities.py`) |
| **Contrôleur de corrections** | même modèle, 384 jetons, grammaire et autorisation des cibles séparées (`apex/game_graphics/studio/controller.py`) | Codex | Catalogue de 7 opérations (`bevel_edges`, `subdivide_surface`, `set_location`, `set_material_pbr`, `add_component`, `repair_degenerate_topology`, `inspect_scene`) |
| **Générateurs** | 3 constructeurs procéduraux codés à la main : `build_vehicle`, `build_building`, `build_chair` (`scripts/game_graphics/studio/build_assets.py`) ; scènes « dynamiques » jusqu'à 5 assets assemblés | Codex | Un gabarit par famille, quelle que soit la demande. Pas de personnage, pas d'environnement ouvert (`open_world: not_implemented`). Bâtiments spécialisés bloqués |
| **Génération neuronale** | TripoSR, branche `agent/codex/14-neural-3d`, exécution processeur qualifiée à part (#17) | Codex | `neural_3d: not_implemented` dans les missions (`capabilities.py`) |
| **Atelier 3D du Cerveau** | véhicules (voiture, camion, char, moto), armes, humanoïde avec squelette, volumes fermés vérifiés, exports `.glb`, OBJ et scripts (`apex/brain/mesh3d.py`, `studio3d.py`). Le Cerveau traduit une demande en appel : **95 % / 87,5 %** de réussite (formulations vues / jamais vues, `docs/CERVEAU_APEX_V2.md` §9 ter) | Claude | Formes géométriques simples (pavés, cylindres, sphères). C'est une maquette rapide, pas un second pipeline de qualité |
| **Outils Blender** | Blender 4.3.2, rendu Cycles **processeur, 64 échantillons, sans débruitage**, caméras orthographiques (`render_asset.py`) | Codex | Rendu de diagnostic, pas de vitrine |
| **Juges** | juge technique déterministe (`StudioGeometryOracle`, `judge.py`) : géométrie, unités, triangles, cadrage, exports, composants, UV ; juge de couverture par expressions régulières (`requirements.py`) ; précontrôle Engineer du véhicule ; alignement visuel **calibré seulement** sur des paires de composants du fauteuil | Codex | `aesthetic_certification: False`. Les juges savent déjà **s'abstenir** (`unavailable`) et séparer « calibré » de « conseil » |
| **Mesures de rendu, avis humains** | couleur perçue, contraste, cadrage (`apex/assistant/render_metrics.py`) ; avis sur 7 critères (`game-studio-ratings.jsonl`) | Claude | Instrument non calibré ; aucun avis humain réel encore |
| **Boucle de correction** | révisions bornées (`max_attempts`, `max_distinct_defect_attempts`), budget de temps cumulé et reprenable (`RunBudget`), arrêt sur stagnation des défauts dès la 3ᵉ révision, politique de reprise (`service.py`) | Codex | **La version retenue est la première qui passe le juge technique** (« aesthetic improvement is unverified ») : aucune comparaison de qualité entre versions valides, une seule lignée, aucune réutilisation des corrections passées |
| **Mémoire** | Game Studio : un reçu par mission, aucune mémoire entre missions. APEX : `apex/experience` (expériences des tâches de code), `apex/memory` (BM25 lexical, sans embeddings), journal d'amélioration chaîné (`apex/improvement/ledger.py`) | les deux | Rien ne relie les missions 3D entre elles |
| **Données d'entraînement 3D** | enregistreur d'exemples séparé **par famille** (véhicule TRAIN, bâtiment VALIDATION, fauteuil HELDOUT), rejets conservés mais jamais éligibles (`apex/game_graphics/studio/dataset.py`) | Codex | Minimums fixés par Codex : 100 TRAIN acceptés, 10 familles, 30 VALIDATION, 30 HELDOUT. **Entraînement BLOCKED** (`TRAINING.md`) |
| **Chaîne d'entraînement et d'adoption** | Cerveau v2 : pré-entraînement, ajustement, checkpoints reprenables, export Q5 ; registre de versions chaîné (promotion seulement si le gain est démontré sur les **mêmes** benchmarks, `apex/neural/registry.py`) ; ancres contre l'oubli (`protection.py`) ; verdict sur plusieurs graines (`variance.py`) ; bootstrap apparié (`apex/stattest`) | Claude | Tout cela existe pour le Cerveau (texte) et n'est **pas** branché sur la 3D |
| **Vision** | spécialiste Qwen2.5-VL-3B, branche `agent/codex/6-vision-specialist`, non qualifié, non branché ; `apex/knowledge/multimodal.py` répond BLOCKED pour la description d'image | Codex / Claude | **APEX ne comprend aucune image aujourd'hui** |
| **GPU** | Cerveau sur la RTX (constaté par l'utilisateur) ; gouverneur protocole 2 (prêts comptés, un seul occupant CUDA) ; Game Studio sur processeur seulement (garde de Codex) ; llama.cpp compilé pour le processeur (`gpu_offload False`, runs Windows) | les deux | Aucune étape 3D mesurée sur CUDA |

### 1.2 D'où peut venir une amélioration : quatre sources à ne pas confondre

| Source | Ce qui change | État aujourd'hui | Comment la mesurer seule |
|---|---|---|---|
| **Modèle neuronal** | les poids d'un modèle (planificateur, juge, vision) | jamais pour la 3D. Seul le Cerveau (texte) a été entraîné | même outillage, même mémoire, mêmes graines, corrections coupées ; seul le checkpoint change |
| **Outils de génération** | constructeurs, opérations, matériaux, rendu | **tous les progrès actuels** viennent d'ici | même modèle ; seuls les outils changent. Un changement de rendu améliore l'image, **pas** le modèle |
| **Mémoire** | ce qui est rappelé au planificateur (exemples, corrections efficaces) | rien | expérience A/B : mémoire activée ou coupée, tout le reste égal (comme le protocole A/B déjà utilisé pour la boucle de code) |
| **Corrections automatiques** | les révisions produites dans une mission | existe, borné, sans comparaison de qualité | première version contre version retenue, sur la même mission |

Mesuré : la correction de la tentative 10 a fait passer le juge (`requested_paint_channels` corrigé sur le
shader), mais la carrosserie perçue reste « saumon clair » (ΔE76 de 55,9 à 68 du rouge demandé). C'est une correction
efficace pour le juge et sans effet visible : seule la mesure séparée des quatre sources le montre.

### 1.3 Écarts principaux, par ordre d'impact

1. **Pas de comparaison de qualité.** La boucle s'arrête au premier PASS technique, qui ne dit rien de la qualité.
2. **Un gabarit par famille.** Ni espace de paramètres, ni archétypes : le modèle n'a rien à choisir.
3. **Aucun juge de qualité calibré**, hors un cas étroit (alignement de paires de composants).
4. **Aucune mémoire entre missions** : chaque mission repart de zéro.
5. **Pas de compréhension d'image.**
6. **Pas assez de données pour entraîner**, de l'aveu même de Codex, et aucune donnée humaine.
7. **GPU inexploité pour la 3D**, faute de tests matériels.

Le point fort : la discipline est déjà là. Juges qui s'abstiennent, partitions par famille, rejets jamais
entraînés, promotion sur benchmarks identiques, ancres contre l'oubli. La proposition s'appuie dessus au lieu de
réinventer.

---

## 2. Architecture de l'intelligence 3D généraliste (proposition)

```
Demande + références ─► Brief (assistant) ─► confirmation humaine ─► lancement humain
                                                     │
                       ┌─────────────────────────────▼─────────────────────────────┐
                       │                 Mission (Game Studio)                     │
  Mémoire 3D ────────► │ Planificateur : DesignSpec de la famille (paramètres bornés) │
  (exemples, corrections│        ▼                                                  │
   efficaces, avis)    │ Constructeur de la famille (spécialiste)  ◄── matériaux    │
                       │        ▼                                                  │
                       │ Rendus : diagnostic (juge technique) + vitrine (œil)       │
                       │        ▼                                                  │
                       │ Panel de juges ─► JudgeReport v1 (défauts localisés,       │
                       │        │           corrections proposées, abstentions)     │
                       │        ▼                                                  │
                       │ Correcteur ─► nouvelle version ─► comparaison ─► meilleure │
                       └────────┬──────────────────────────────────────────────────┘
                                ▼
            Expérience 3D v1 (tout, avec empreintes) ─► Mémoire ─► Données H / A / U
                                                                     ▼
                         Entraînement périodique (adaptateurs) ─► bancs scellés ─► promotion ou refus
```

### 2.1 Un tronc commun, des spécialistes par famille

**Tronc commun**, identique pour toutes les catégories :
- le brief, le planificateur, le correcteur et le format des versions ;
- la bibliothèque de matériaux et les profils de rendu ;
- le format des juges et l'agrégation ;
- la mémoire, les données et la chaîne d'entraînement.

**Spécialiste de famille** : un module déclaratif (« FamilySpec ») qui fournit :
- le schéma de ses paramètres (`vehicle_params`, `building_params`…) et leurs bornes ;
- ses archétypes (hypercar, berline ; maison, tour ; fauteuil, table…) ;
- son constructeur ;
- ses contrôles propres (portes et roues, ouvertures et étages, assise et pieds…) ;
- ses métriques de design (proportions cibles par archétype) ;
- son jeu de références et ses partitions.

| Famille | Constructeur proposé | Spécialiste neuronal possible | Difficulté |
|---|---|---|---|
| Véhicules | paramétrique « hard surface » (profils, sections, plis, modules) | non pour la carrosserie | moyenne |
| Bâtiments | procédural modulaire (grille, étages, façades, toitures) | non | moyenne |
| Objets (mobilier, accessoires) | procédural par sous-famille | oui, pour les formes organiques (TripoSR, déjà qualifié en processeur) | variable |
| Environnements | assemblage d'assets + terrain et végétation procéduraux | non | élevée (composition) |
| Personnages | base paramétrique avec squelette (l'humanoïde de `mesh3d` montre le principe) | le meilleur candidat neuronal (forme organique), à mesurer | **la plus élevée** |

Principe : **déterministe d'abord**. Les modèles choisissent des paramètres bornés, que l'on peut inspecter,
rejouer avec une graine et corriger. Ils n'écrivent pas de géométrie libre. La génération neuronale reste
réservée aux formes organiques, et ses sorties gardent leur provenance (source, poids, licence).

### 2.2 Répartition proposée

| Bloc | Claude | Codex |
|---|---|---|
| Brief, références, confirmation, interface, avis humains | ✔ | |
| Planificateur, constructeurs, opérations, rendus, juges de famille | | ✔ |
| Format commun des juges et des expériences (contrats) | rédaction | revue, implémentation côté Game Studio |
| Mémoire 3D, données H/A/U, bancs d'essai, harnais de calibration | ✔ (lecture seule des reçus de Game Studio) | |
| Chaîne d'entraînement et d'adoption | ✔ (étend `apex/neural/registry.py`) | adaptation du planificateur, avec Claude |
| GPU | gouverneur (protocole 2, inchangé) | ordonnancement des étapes sous `_gpu_lease` |

---

## 3. Juges indépendants (proposition)

### 3.1 Un format commun : JudgeReport v1

Chaque juge rend, par critère :
- son **verdict** : note, réussite ou échec, ou **abstention** motivée (« non qualifié pour cette famille », « vue
  absente »…) ;
- sa **portée** et son état de **calibration** (`calibrated: true|false`, jeu et date de calibration) ;
- ses **preuves** : valeurs mesurées, vue concernée, zone de l'image, objets Blender visés ;
- ses **défauts**, localisés, chacun avec une **correction proposée exploitable** : une opération du catalogue,
  ses paramètres et l'effet attendu ;
- son coût (temps, mémoire).

Codex en a déjà l'essentiel (`defects`, `unavailable`, `advisory`, `calibrated`) : il s'agit de le généraliser et de
le figer comme contrat.

### 3.2 Les critères et le type de juge adapté

| Critère | Juge proposé | Nature |
|---|---|---|
| Fidélité à la demande | couverture (existant) + questions-réponses visuelles à la CADCodeVerify : des questions tirées du brief (« le toit est-il vitré ? »), auxquelles un VLM répond sur les rendus | VLM, calibré |
| Qualité géométrique | variétés fermées, normales, intersections, densité de topologie, échelle (existant, étendu) | déterministe |
| Proportions et formes | métriques de design contre les cibles de l'archétype (empattement, porte-à-faux, hauteur d'étage…) | déterministe, cibles validées par des humains |
| Réalisme des matériaux | validité physique (vernis, métal réservé aux métaux, verre transmissif) + couleur perçue (ΔE) + comparaison par paires | mixte |
| Qualité artistique | modèle de préférence par paires, appris sur **vos** comparaisons ; VLM en conseil | appris, calibré |
| Détails et textures | densité d'arêtes et de composants, densité de texels, couverture UV, résolution | déterministe |
| Cohérence multivue | silhouettes reprojetées, couleurs et symétrie d'une vue à l'autre | déterministe |
| Utilisabilité des fichiers | validation glTF, échelle, pivot, budget de triangles, LOD, import Blender et Unreal | déterministe (existant, étendu) |

### 3.3 Fiabilité mesurée, pas supposée

- **Références humaines indépendantes** : des paires et des notes données par l'utilisateur, jamais produites par
  le juge évalué. L'accord entre humains (kappa) fixe le plafond.
- **Mesures** :
  - détection de défauts : précision et rappel par défaut ;
  - préférences : précision par paire et tau de Kendall ;
  - le tout par famille, sur des paires tenues à l'écart.
- **Abstention** : un juge ne se prononce que dans le domaine où sa calibration a été mesurée (familles, critères,
  types de vue). Ailleurs, il s'abstient.
- **Juge de sélection ≠ juge d'évaluation** : les juges qui choisissent les versions dans la boucle ne servent pas à
  noter le résultat final dans les bancs d'essai. Sinon, la boucle apprend à plaire à son juge (loi de Goodhart).
- **Erreurs corrélées** : on mesure, sur le jeu de calibration, la corrélation des erreurs entre juges. Deux juges
  qui se trompent ensemble ne valent qu'un seul juge.
- Recherche : [BlenderGym](https://arxiv.org/abs/2504.01786) (CVPR 2025) montre que même les meilleurs VLM
  peinent sur des modifications faciles pour un humain, et que la répartition du calcul entre génération et
  vérification compte. [3D-DefectBench](https://arxiv.org/pdf/2607.10826) mesure les VLM derrière des annotateurs
  humains. D'où la règle déjà posée : un VLM conseille, il ne décide pas seul tant qu'il n'est pas calibré.

---

## 4. Boucle autonome de correction (proposition)

**Créer → Juger → Corriger → Comparer → Conserver la meilleure.** La mission reste lancée par l'utilisateur (règle
HUMAN-only) ; tout ce qui suit le clic est automatique.

| Exigence | Aujourd'hui (Codex, `service.py`) | Proposé |
|---|---|---|
| Plusieurs versions | une lignée de révisions | en plus, N variantes initiales (graines ou archétypes différents) si le budget le permet |
| Comparer | non : première version qui passe le juge technique | le juge technique reste **obligatoire** ; parmi les versions qui le passent, comparaison par paires sur les critères calibrés ; égalité → la plus ancienne |
| Détecter les régressions | non | une correction ne doit dégrader aucun autre critère au-delà d'une tolérance fixée d'avance ; sinon la version est rejetée et la correction marquée « nuisible » |
| Préserver les meilleures | la version retenue | toutes les versions restent immuables (déjà le cas) ; la meilleure est marquée, avec la raison du choix |
| Réutiliser les corrections efficaces | non | mémoire des corrections : (famille, défaut, contexte) → taux de succès mesuré ; les meilleures sont proposées au correcteur |
| Budget configurable | temps cumulé, nombre de révisions | budget en unités de calcul (secondes processeur, secondes GPU, temps total) réglable dans Paramètres |
| Arrêt | stagnation des défauts, budget | stagnation de la **qualité** (k versions sans gain > ε), budget, tout critère satisfait, ou abstention sur un critère essentiel (la question passe alors à l'humain) |

---

## 5. Mémoire et sélection automatique des données (proposition)

### 5.1 Expérience 3D v1

Chaque version de chaque mission devient un enregistrement, avec ses empreintes :
- la demande, le brief et la famille ;
- les versions des outils et des modèles ;
- le plan, les opérations et les fichiers ;
- les rapports des juges, avec leurs versions ;
- les corrections et leur effet mesuré ;
- les avis humains, la version retenue et le coût de calcul.

Elle est construite **en lecture seule** depuis les reçus de Game Studio, sans en changer le format.

### 5.2 Retrouver les expériences utiles

- Filtres structurés : famille, archétype, défaut.
- Recherche lexicale BM25 (existante, `apex/memory/retrieval.py`).
- Plus tard, embeddings de texte et d'image, **seulement** quand un modèle sera réellement installé et mesuré.
- Ce qui est injecté au planificateur est borné, avec sa provenance. Son effet est mesuré par A/B (§8).

### 5.3 Trois catégories de données

| Catégorie | Entrée | Usage |
|---|---|---|
| **H : validées humainement** | avis ou comparaisons de l'utilisateur, ou références humaines fiables | entraînement et calibration ; une part fixe sert d'**ancre** permanente |
| **A : validées automatiquement** | voir les règles ci-dessous | entraînement, en proportion plafonnée |
| **U : non validées** | tout le reste, dont les rejets | analyse seulement, **jamais** l'entraînement |

Règles proposées pour A :
1. au moins **deux juges indépendants**, chacun qualifié et calibré pour la famille et le critère, et dont les
   erreurs sont faiblement corrélées ;
2. leurs notes dépassent des **seuils de confiance calibrés** ;
3. des **preuves vérifiables** : empreintes des fichiers, mesures rejouables ;
4. tout désaccord ou toute abstention fait tomber l'exemple en U, ou le place dans la file de validation humaine ;
5. **audit continu** : une part tirée au hasard des exemples A est soumise à l'utilisateur. Si la précision mesurée
   de A tombe sous un seuil, la catégorie A est suspendue pour la famille concernée.

Les jeux d'évaluation réservés sont **scellés** (empreintes). Les quasi-doublons de demandes et de rendus sont
détectés et exclus de l'entraînement. Une famille entière reste tenue à l'écart, comme le fait déjà Codex.

---

## 6. Entraînement périodique avec protection anti-régression (proposition)

### 6.1 Quoi entraîner, par ordre de rentabilité

1. **Les juges de préférence** : petites têtes sur des représentations d'image figées, apprises sur les comparaisons
   humaines. Peu coûteux, et c'est le goulot de tout le reste.
2. **Un adaptateur du planificateur et du correcteur** : LoRA ou QLoRA sur les poids de base Qwen2.5-Coder-7B
   (Apache-2.0), comme Codex l'a déjà esquissé dans `TRAINING.md`. Il apprend à remplir des DesignSpec et à choisir des
   corrections efficaces. Précédent publié : [BlenderLLM](https://arxiv.org/abs/2412.14203), ajusté sur le même
   modèle de base pour écrire des scripts Blender, avec une boucle d'auto-amélioration (gains annoncés par ses
   auteurs, non vérifiés ici).
3. **Le Cerveau** : sa chaîne existe déjà.
4. **Un adaptateur du VLM** pour lire les références, plus tard.

### 6.2 La chaîne

```
Données H + A (versionnées, empreintes) ─► jeu d'entraînement (part H fixe, part A plafonnée, U exclu)
   ─► entraînement de l'adaptateur (checkpoints reprenables, sous le bail GPU)
   ─► évaluation sur bancs scellés et famille jamais vue
   ─► comparaison avec la version en service (mêmes bancs, mêmes empreintes, bootstrap apparié, plusieurs graines)
   ─► ancres par famille : aucune famille ne régresse au-delà de la tolérance
   ─► promotion, ou refus consigné ; la version précédente reste disponible pour un retour arrière
```

Tout cela reprend des mécanismes **déjà écrits et testés** pour le Cerveau : `registry.py` (promotion sur benchmarks
identiques), `protection.py` (ancres, oubli), `variance.py` (plusieurs graines), `apex/stattest`.

### 6.3 Ne pas réapprendre ses propres erreurs

- U n'est jamais entraîné ; la part de A est plafonnée ; une part H fixe sert d'ancre.
- On évalue avec des juges et des humains qui n'ont pas servi à choisir les données.
- On surveille la **diversité** des sorties (archétypes, paramètres) d'une version à l'autre.
- Raison : [Shumailov et al., Nature 2024](https://www.pure.ed.ac.uk/ws/portalfiles/portal/460496122/ShumailovEtalNature2024AIModelsCollapseWhen.pdf)
  montrent qu'un entraînement indiscriminé sur ses propres sorties fait disparaître les cas rares (« effondrement du
  modèle »).

**Déclenchement** : à partir d'un seuil de nouvelles données validées, pas d'un calendrier. Les entraînements
tournent quand le PC est libre, sous le bail GPU.

**Adoption** : pour les premiers cycles, je propose qu'elle soit **validée par l'utilisateur** (décision au §11).

---

## 7. Optimisation matérielle (RTX 4060 Ti 8 Go, i5-14400F, 16 Go)

| Étape | Aujourd'hui | Piste | Ordre de grandeur (à mesurer) |
|---|---|---|---|
| Planificateur 7B Q5 | processeur | llama.cpp compilé CUDA, toutes les couches sur la RTX | 5,4 Go de poids + cache : tient a priori en 8 Go avec un contexte de 4 k |
| Rendu | Cycles processeur, 64 échantillons | Cycles OptiX ou CUDA pour le rendu vitrine, débruitage | gain de temps attendu le plus direct ; mémoire graphique d'une scène de voiture à mesurer |
| Vision | aucune | Qwen3-VL-4B, ou 8B quantifié | selon des sources tierces : environ 6 Go pour le 4B en Q4 ; de 4,8 à 12 Go pour le 8B selon le contexte ([codersera](https://codersera.com/blog/qwen3-vl-4b-vs-qwen3-vl-8b-benchmarks-vram-guide/), [spheron](https://www.spheron.network/tools/gpu-recommender/Qwen/Qwen3-VL-8B-Instruct/)) |
| Formes organiques | TripoSR processeur (branche Codex) | Stable Fast 3D | 7 Go de mémoire graphique dans le banc de Stability ([annonce](https://stability.ai/news/introducing-stable-fast-3d)) ; licence communautaire à vérifier |
| Ajustement QLoRA 7B | aucun | 4 bits, petits lots, accumulation, checkpointing | **juste** : de 5 à 10 Go selon des sources tierces. Codex le dit aussi : 8 Go ne prouvent pas que c'est faisable avec le contexte nécessaire. Le 3B tient largement |
| Juges | processeur | têtes de préférence sur processeur, VLM sur la RTX | légers |

Principes :
- **Un seul occupant CUDA à la fois** (protocole 2, inchangé) et chargement séquentiel : planificateur, puis rendu,
  puis VLM, chacun déchargé avant le suivant. 16 Go de RAM ne permettent pas d'avoir le 7B, un VLM et Blender
  chargés ensemble.
- **Processeur en complément** : construction Blender et juges déterministes sur le processeur pendant que la RTX
  rend ou infère, dans les limites du bail.
- **Reprise après interruption** : missions déjà reprenables ; checkpoints d'entraînement déjà prévus.
- **Qualité par unité de calcul** : chaque version enregistre son coût. On mesure le gain des juges et des humains
  par minute de GPU, et l'on répartit le budget entre générer plus et vérifier mieux (constat de BlenderGym).
- Tests matériels requis avant toute annonce : ceux listés dans `docs/QUALITE_3D_PREMIUM.md` §7.

---

## 8. Bancs d'essai généralistes (proposition)

**APEX-3D-Bench v1.**
- 5 familles : véhicules, objets, bâtiments, environnements, personnages ;
- par famille, des demandes de difficulté graduée, dont une partie avec images de référence ;
- partitions :
  - `dev` : pour régler ;
  - `test` : scellée par empreintes ;
  - une **famille entière** jamais vue à l'entraînement.

| Mesure | Comment |
|---|---|
| Qualité des créations | comparaisons par paires de l'utilisateur (Bradley-Terry) + juges calibrés d'évaluation |
| Conformité aux demandes | couverture + questions-réponses visuelles, contrôlées par des humains sur un échantillon |
| Pertinence des juges | accord avec les références humaines tenues à l'écart (§3.3) |
| Efficacité des corrections | part des corrections qui améliorent sans régression ; gain moyen par correction |
| Gain de la mémoire | A/B mémoire activée ou coupée, mêmes graines |
| Gain de chaque entraînement | version candidate contre version en service, mêmes bancs |
| Ressources | temps, pics de mémoire graphique et vive, secondes de GPU par version |

**Règle d'acceptation**, enregistrée **avant** les mesures :
- gain significatif sur la cible (intervalle du bootstrap apparié au-dessus de zéro) ;
- **aucune famille** ne régresse au-delà de sa tolérance (ancres) ;
- sinon, refus consigné.

---

## 9. Feuille de route priorisée

Elle prolonge celle de `docs/QUALITE_3D_PREMIUM.md` (P1 à P6) sans la remplacer.

| Étape | Qui | Contenu | Critère d'acceptation |
|---|---|---|---|
| **R0** | Claude | Expérience 3D v1 depuis les reçus, en lecture seule ; banc APEX-3D-Bench v1 (demandes, partitions scellées) ; comparaisons par paires dans l'Atelier 3D ; harnais de calibration ; contrats JudgeReport v1 et Expérience 3D v1 pour l'Issue #21 | tests ; le parcours APEX.exe reste vert ; aucun fichier de Codex modifié |
| **R1** | Codex | JudgeReport v1 dans Game Studio ; choix par comparaison parmi les versions valides ; rendu vitrine (C4) et matériaux (C5) | les mesures du banc s'améliorent ; préférence humaine ≥ 70 % contre l'actuel |
| **R2** | les deux | N variantes, mémoire des corrections efficaces, arrêt sur stagnation de la qualité | gain mesuré par A/B ; aucune régression |
| **R3** | Codex (spécialistes) + Claude (données) | DesignSpec et constructeurs paramétriques par famille (C6), métriques de design (C7) | proportions dans les cibles ; préférence humaine en hausse |
| **R4** | les deux | VLM en conseil (questions-réponses, références) ; puis en filtre s'il est calibré | accord ≥ plafond humain moins la marge |
| **R5** | les deux | données H/A/U ; premier adaptateur du planificateur, **seulement** quand les minimums de Codex sont atteints | promotion par la chaîne du §6 |
| **R6** | Codex + test de l'utilisateur | GPU d'abord : llama.cpp CUDA, Cycles OptiX, VLM | tests matériels réussis |
| **R7** | les deux | personnages et environnements complets ; formes organiques neuronales | par famille, mêmes règles |

---

## 10. Première étape d'implémentation proposée : R0

Tout est dans le périmètre de Claude, sans changer Game Studio ni le parcours validé :

1. **Contrats** : `JudgeReport v1` et `Experience3D v1` (schémas JSON), proposés à Codex sur l'Issue #21 avant tout
   branchement.
2. **Journal d'expériences 3D** : construit en lecture seule depuis les reçus et les rapports de juge déjà
   produits, avec les avis humains.
3. **APEX-3D-Bench v1** : les demandes par famille, les partitions et leurs empreintes, et un rapport de mesure qui
   réutilise `apex/stattest` et `apex/neural/variance.py`.
4. **Comparaisons par paires** dans l'Atelier 3D : « laquelle est la meilleure, critère par critère ? ». C'est la
   première donnée humaine de calibration. Les notes de 1 à 5 existent déjà.
5. **Harnais de calibration** : un juge, ses verdicts et les références humaines donnent sa précision, son taux
   d'abstention et son domaine qualifié.

Ce que R0 ne fait pas : entraîner un modèle, télécharger des poids, toucher au GPU, lancer une mission sans
clic humain.

---

## 11. Décisions demandées à l'utilisateur

1. **Valider l'architecture et la première étape (R0).** Rien ne sera développé avant.
2. **Lancement des bancs d'essai.** Un banc lance des dizaines de missions, or la règle actuelle veut un clic humain
   par mission. Proposition : un clic humain autorise un **lot** borné (liste des demandes, budget total affiché),
   ou bien les bancs ne tournent que sur un runner jetable, comme les candidates.
3. **Adoption d'un modèle entraîné.** Proposition : validée par vous pour les premiers cycles, même après les
   contrôles anti-régression ; automatique ensuite seulement si vous l'autorisez.
4. **Téléchargement de nouveaux poids** (VLM, génération neuronale) : composant par composant, avec votre accord et
   sa licence affichée, comme les composants actuels de Game Studio.
5. **Publication des contrats sur l'Issue #21** : maintenant, ou après votre validation.

Rappels :
- vos deux images de référence (voitures jaune et blanche) ne sont toujours pas parvenues ;
- APEX ne comprend aucune image aujourd'hui ;
- rien n'est annoncé sur CUDA sans votre test matériel ;
- aucune fusion, aucune publication.
