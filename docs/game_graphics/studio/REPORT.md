# Mission #14 — résultats réellement exécutés

Lot cloud du 13 octobre 2026, empilé sur Game Graphics PR #12 (`b2334f4`). Les fichiers Blender, GLB/PLY et PNG ci-dessous sont des sorties natives des processus conservés dans les journaux. Aucun test Unreal n'a été exécuté. Aucun poids n'a été entraîné ou adapté.

**Ouvrir la [galerie locale](evidence/index.html)** pour parcourir les trois familles, les comparaisons et télécharger les projets. Les références sont trois briefs originaux autorisés, décrits dans [references.json](references.json). Les critères restent ceux de [benchmark-v1.json](benchmark-v1.json).

## Trois créations distinctes

| Création finale | Projet / export | Triangles | Objets / matériaux | Verdict technique Prototype | Dernière révision : temps / RSS échantillonnée |
|---|---|---:|---:|---|---:|
| Coupé sportif | [Blender](evidence/benchmark/sports_car/v2/asset.blend) · [GLB](evidence/benchmark/sports_car/v2/asset.glb) | 36 288 | 139 / 9 | [PASS](evidence/benchmark/sports_car/v2/judge-report.json) | 80,32 s / 365,74 Mio |
| Atelier de rue | [Blender](evidence/benchmark/street_building/v3/asset.blend) · [GLB](evidence/benchmark/street_building/v3/asset.glb) | 31 806 | 155 / 7 | [PASS](evidence/benchmark/street_building/v3/judge-report.json) | 86,90 s / 352,82 Mio |
| Fauteuil | [Blender](evidence/benchmark/lounge_chair/corrected-2/asset.blend) · [GLB](evidence/benchmark/lounge_chair/corrected-2/asset.glb) | 18 196 | 33 / 5 | [PASS](evidence/benchmark/lounge_chair/corrected-2/judge-report.json) | 94,66 s / 312,37 Mio |

Chaque dossier contient quatre vues Cycles CPU **960×720, 64 samples, seed 14**, silhouettes natives, hiérarchie, transformations et traces, `.blend`, GLB, deux inspections indépendantes et mesures de processus. Les temps du tableau couvrent l'édition/export/rendu de la dernière version, sans additionner ses versions précédentes, Qwen ou les inspections. Les journaux donnent chaque coût séparément.

La voiture possède des profils de carrosserie courbes, des passages de roues découpés, jantes ouvertes/rayons/freins, rétroviseurs, phares, vitres séparées et cockpit. Le bâtiment possède de vrais renfoncements de fenêtres, encadrements, balcon, garde-corps, boutique et jardinières. Le fauteuil possède coque et assise courbes, coussins, passepoils, accoudoirs ouverts, pieds inclinés et matériaux bois/cuir procéduraux. Ces assemblages sont **procéduraux Blender** ; leur géométrie n'est pas attribuée à TripoSR.

Les exports GLB ne contiennent pas la cuisson des shaders procéduraux de bois/cuir en textures. Les niveaux Jouable, Soigné et Photoréaliste restent **UNKNOWN** dans l'[évaluation réelle des niveaux](evidence/benchmark/quality-and-integrity-checks.json), faute de LOD, textures cuites ou jugement esthétique calibré. Les personnalisations `QualityCriteria` sont conservées.

## Améliorations et échecs conservés

La [première carrosserie](evidence/first-pass/sports_car/front_three_quarter.png) passait la géométrie mais présentait des passages de roues ouverts et des éléments mal intégrés. La [version retravaillée](evidence/benchmark/sports_car/v1/front_three_quarter.png) corrige les profils et les garde-boue, avec les mêmes caméras et éclairages. Le châssis passe de 9 476 à 3 580 triangles ; les surfaces sont mieux contrôlées. Le vitrage a aussi été assombri, explicitement séparé de cette amélioration géométrique dans la [comparaison](evidence/car-surface-comparison.json). Il ne s'agit pas d'un score de beauté calibré.

Le bâtiment a d'abord échoué : six faces dégénérées dans les lettres et cadrage coupé. Le cadrage a été élargi. Une réparation locale choisie par Qwen a réduit les faces dégénérées à deux ; le juge a maintenu FAIL. Une seconde réparation limitée à ce composant, avec remeshing voxel borné, a produit zéro défaut mesuré. Les trois versions sont conservées ; les seuils n'ont pas été réduits.

La [démonstration de correction](evidence/benchmark/correction-demo.json) déplace volontairement `arm_left` de 12 cm. Le juge constate **48,84 pixels d'écart** dans les deux vues natives d'identifiants. Qwen confond d'abord déplacement relatif et position absolue : la tentative échoue. Une variation de 0,05 pixel avait aussi été prise pour du progrès ; le processus suivant a été arrêté avant application, puis le contrôle de progrès a été corrigé pour ignorer les gains inférieurs à un pixel.

Après clarification du contrat de position absolue, Qwen produit la correction réelle : **0 pixel d'écart dans les deux vues**. Les autres objets, la topologie/les UV de l'accoudoir, les caméras, les lumières et les paramètres de rendu sont identiques. Les trois scènes — défaut, tentative refusée, correction acceptée — respectent le budget de trois versions. Cette correction porte sur le placement de la géométrie, sans changement d'éclairage.

## Contrôleur et jugement

Qwen2.5-Coder-7B-Instruct Q5_K_M utilise le `ModelProvider` local existant. Ses réponses brutes et les plans appliqués sont conservés. Les appels acceptés de création ont pris **89,43 s, 84,21 s et 62,61 s**. Un premier contexte trop long a atteint 300 s ; une autre réponse a omis la sauvegarde et confondu objet/matériau. Ces échecs sont archivés, sans exécution de leur contenu.

Le catalogue réellement utilisé comprend inspection, ajout de composants, chanfrein, subdivision, matériau PBR, réparation topologique locale, déplacement et sauvegarde/rendu. Les objets concernés et les modifications sont attestés par les traces. Les plans passent par le Tool Engine et le Workspace existants ; le worker exécute du code fixe approuvé, jamais du Python produit par Qwen. Son niveau d'isolation natif est explicitement **NONE**, avec délai, surveillance RSS et arrêt des processus possédés. Les fichiers Blender tiers non fiables ne sont pas qualifiés pour cette exécution native.

Le nouvel oracle spécialisé reste branché sur `OracleProvider`/`Tier0Judge`, avec la politique de récupération existante. La calibration visuelle comprend **quatre configurations de paires courbes, deux vues chacune** : huit observations, zéro faux positif/faux négatif observé. Sa portée est l'alignement projeté d'identifiants isolés ; elle ne juge ni beauté, ni occlusion dans le rendu principal, ni fidélité photoréaliste. Aucun VLM non calibré ne devient juge décisif.

## Véritable génération neuronale sur CPU

Un seul checkpoint TripoSR MIT de **1 677 246 742 octets** a été téléchargé. Code `107cefdc244c39106fa830359024f6a2f1c78871`, poids `stabilityai/TripoSR@5b521936b01fbe1890f6f9baed0254ab6351c04a`, SHA256 `429e2c6b22a0923967459de24d67f05962b235f79cde6b032aa7ed2ffcd970ee`.

| Extraction | Temps réel | RSS échantillonnée | Triangles source | Composantes |
|---|---:|---:|---:|---:|
| [64³](evidence/neural/64/neural.blend) | 22,65 s | 3,48 Gio | 4 716 | 3 |
| [128³](evidence/neural/128/neural.blend) | 25,66 s | 3,47 Gio | 19 360 | 1 |

Entrée et codes neuronaux identiques, caméras et éclairages gelés : les discontinuités des pieds/accoudoirs diminuent réellement à 128. Les deux versions possèdent GLB, PLY, projet Blender rouvert, quatre rendus natifs et [mesures détaillées](evidence/neural/NEURAL_README.md). L'importeur Blender élimine quatre triangles dégénérés de la source128 ; une face dégénérée reste détectée. La qualité, les couleurs affichées, les UV/PBR et l'aptitude au jeu restent **non qualifiées**.

Le lanceur livré a été exécuté une troisième fois : **27,94 s**, 3,49 Gio RSS. Il reproduit les mêmes positions, triangles et couleurs ; l'ordre des sommets varie dans marching cubes CPU. Voir la [vérification du lanceur](evidence/neural/portable-verification/mesh-reproduction.json). Les adaptations amont documentées rendent rembg facultatif, chargent la configuration DINO locale et utilisent `weights_only=True`. Aucun second poids DINO ni substitut procédural n'est utilisé.

## Matériel, dataset et limites restantes

Mesures cloud : Linux x86_64, quota CPU **4 vCPU**, limite mémoire cgroup **32 Gio**, Blender **4.3.2**, PyTorch **2.14.1+cpu**, CUDA indisponible et zéro périphérique CUDA. Les ressources sont réservées par le verrou APEX existant ; Blender et les modèles ne s'exécutent pas concurremment. La VRAM du PC utilisateur n'est pas mesurée.

Le [dataset](evidence/benchmark/dataset.jsonl) conserve dix révisions avec provenance et jugements, dont quatre refusées. Révisions acceptées : TRAIN véhicule 2, VALIDATION bâtiment 1, HELDOUT fauteuil 3 ; ce ne sont pas six tâches indépendantes. Le fauteuil a été observé pendant le débogage du contrôleur : son étiquette HELDOUT conserve la séparation des familles, mais il ne constitue plus une évaluation aveugle indépendante. Une nouvelle famille non exposée devra être réservée après gel du contrôleur. Seuls les plans TRAIN vérifiés sont candidats à l'apprentissage. [LoRA/QLoRA reste BLOCKED](TRAINING.md) faute de corpus et de baseline suffisants. Aucun poids APEX/Qwen n'a changé.

Validation du code : **60 contrôles ciblés réussis**, lint réussi, contrôle d'intégrité rejetant une copie GLB altérée. Les preuves artistiques viennent des processus Blender et des inférences réels, indépendamment de ces tests de contrat.

Restent bloqués ou non qualifiés : Unreal/UE5.8 et PR #16, rendu photoréaliste certifié, juge esthétique général, assets neuronaux prêts pour le jeu, textures cuites/LOD/rig/collisions fonctionnelles, entraînement spécialisé et raccordement final de l'interface. `C:\GameProject`, le cœur et les autres branches n'ont pas été modifiés. Aucune fusion automatique.

**Prochaine expérience utile :** vérifier le transfert sRGB/linéaire des couleurs du mesh TripoSR128 avec plages connues et rendu d'émission, à géométrie/caméras gelées, sans télécharger d'autres poids. Ensuite mesurer le même pipeline sur une seconde famille tenue à l'écart.
