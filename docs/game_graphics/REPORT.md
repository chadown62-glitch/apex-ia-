# Rapport Game Graphics — 12 octobre 2026

## Résultat réel

Trois voitures paramétrées par le vrai Qwen2.5-Coder-7B-Instruct officiel Q5_K_M, géométrie Blender procédurale exécutée sur CPU. Première voiture rouge dans `evidence/red/` : `.blend`, GLB, LOD1, quatre PNG, paramètres, inspections et contrat d’asset SHA-256. Deux nouvelles demandes bleue et argent, préassignées heldout avant génération, respectent toutes leurs valeurs explicites et passent les mêmes contrôles. Un modèle langage ne produit ici ni maillage neuronal libre ni nouveau poids appris.

Environnement accessible : Linux, Python 3.12.14, Blender 4.3.2, torch 2.14.1+cpu, aucune CUDA. CPU plafonné à quatre cœurs ; RAM cloud environ 32 Go, pas le PC Windows 16 Go / RTX 4060 Ti 8 Go. Le clone Windows demandé est inaccessible ; le développement est dans des worktrees cloud isolés, branches propres à #8/#9, sans modification du projet utilisateur ni des branches de Claude, #5, #7, main ou integration.

## Mesures

| Parcours | Mesure observée |
|---|---|
| Premier contrôleur rouge | 338 tokens entrée, 136 sortie, 37,408 s pour la requête ; chargement 2,039 s |
| Contrôleur bleu heldout | 34,641 s ; spécification correspondant aux dix champs demandés |
| Contrôleur argent heldout | 28,171 s ; spécification correspondant aux dix champs demandés |
| Modèle, benchmark deux demandes | RSS après chargement 6 044 876 800 octets ; après génération 6 764 589 056 octets |
| Blender bleu, export et quatre rendus | 26,056 s |
| Blender argent, export et quatre rendus | 23,916 s |
| Défaut roue puis correction | 22,697 s puis 22,409 s, hors processus d’inspection supplémentaires |
| Voiture rouge | 38 pièces ; 14 096 triangles, LOD1 10 120 triangles |
| Dimensions rouge reimportées GLB | 4,506500 × 1,900000 × 1,354000 m, dans tolérances explicites |

RSS sont des échantillons après chargement/génération, pas des pics. Les métriques CPU/mémoire Blender non instrumentées restent UNKNOWN. Pas de mesure VRAM, de FPS UE ou de temps Windows. Les rendus Cycles CPU 640×420 à 32 samples ne sont pas une qualification photoréaliste.

Les trois sessions Qwen ont effectivement chargé le même fichier immuable et terminé : PIDs 12211, 13966, 14317, fin vérifiée, code -15 correspondant à notre SIGTERM. Le modèle du benchmark s’arrête avant les processus Blender. Le chargement/déchargement/rechargement CPU est donc observé ; la bascule GPU n’est pas qualifiée. Le contrôle des processus actifs a bloqué un lancement pendant une tâche Blender. La réservation reste coopérative et ne contrôle pas tous les processus externes.

## Juge et corrections

Le juge existant Tier0 reçoit une OracleProvider graphique : réouverture séparée des `.blend`, GLB et LOD par Blender. Aucun résultat de topologie produit par le builder n’est accepté sur parole. Les fichiers, unités, dimensions, budgets, normales/volume signé, triangles dégénérés, fermeture après soudure des coutures glTF à 1 µm, UV, matériaux, roues, pivots et quatre vues non vides sont contrôlés.

Échecs réels conservés : l’OpenImageDenoise absent a empêché le premier rendu, puis désactivation explicite du denoising et rendu CPU complet. Le premier export GLB présentait 12 arêtes non manifold malgré un `.blend` fermé. La triangulation BEAUTY explicite avant export ramène le compte à zéro ; EAR_CLIP conservait les 12 défauts. Le juge renforcé a détecté le volume signé négatif d’une cabine au profil croisé ; le profil utilise désormais des coins proportionnels à son épaisseur et des normales orientées correctement. Les budgets et tolérances n’ont pas été relâchés.

La faute injectée de 12 cm sur le maillage local de `wheel_front_left` est détectée dans les trois formats : FAIL. La politique Recovery existante autorise une réparation bornée ; le builder rouvre la version fautive, recentre seulement les vertices de roue, préserve le pivot et réexporte/rend. Nouvelle inspection : PASS, aucun nouveau défaut. Les deux versions complètes sont conservées dans `evidence/pivot-repair-train/` (nom historique ; les exemples sont HELDOUT, pas entraînés).

Le dataset contient cinq enregistrements archivés : une baseline train, deux demandes heldout, défaut et correction heldout. Script, demande, résultats, manifestes et fichiers sont liés par empreintes. Aucun entraînement effectué ; corpus insuffisant pour mesurer un gain de fine-tuning. Voir TRAINING.md.

## Validation et limites

Validation finale : 18 tests PASS, sans skip ; Ruff et format PASS (14 fichiers), mypy PASS (9 modules nouveaux, follow-imports=silent). Commandes dans README.md. Les tests géométriques exécutent le vrai Blender, avec fixtures CPU, reimport, faux rendu blanc à empreinte mise à jour, altération de GLB, photoréalisme affirmé par le producteur et roue réellement décentrée. Les compte-rendus JUnit et logs sont archivés avec les preuves de fin de tests.

L’orchestrateur PR #11 est aussi vérifié avec ce vrai Qwen local : deux TaskRuns Python successifs sous Docker, PASS, un essai chacun, artefacts SHA, Mission REVIEW. Projet `47aff463-8b04-46fd-a072-03871f6e6f57`, runs `49c1397a-d036-492b-bbaa-e2f581f875f0` et `925af6c2-d0f5-49b3-8750-59ae98e7a3a5`. Temps de boucle 13,817 s puis 12,090 s. Ce test du MVP ne constitue pas un test de conduite UE5.

BLOCKED / non qualifié : TripoSR GPU (aucune CUDA, poids non téléchargés ; CPU non benchmarké), Windows/WSL2, compatibilité mémoire du PC cible, textures PBR 2K/4K, second LOD, juge vision indépendant calibré, photoréalisme et respect esthétique de références. Le niveau élevé reste UNKNOWN/BLOCKED même si le producteur affirme être calibré. Les checks de pixels détectent du blanc et des vues identiques, pas toute falsification visuelle.

Le contrat GLB transporte des maillages statiques séparés avec pivots, pas un squelette Chaos ni un PhysicsAsset. Import UE, compilation, collisions, caméra, commandes et conduite relèvent du lot #10 et restent sans validation moteur à ce stade. Routes, villes, terrains et mondes ouverts ne sont pas livrés. Les réparations autres que le recentrage local démontré sont bloquées avec défauts précis.
