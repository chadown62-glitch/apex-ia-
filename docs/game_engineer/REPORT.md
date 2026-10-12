# Rapport Game Engineer — 12 octobre 2026

## État : BLOCKED pour le démonstrateur pilotable

Le moteur UE5, son SDK et ses plugins sont absents de l’environnement accessible. PATH : UnrealEditor, UnrealEditor-Cmd, RunUAT.sh, clang++ et dotnet introuvables ; g++ présent mais aucun header/moteur Unreal. Racines `/opt/UnrealEngine`, `/opt/unreal-engine`, `/workspace/UnrealEngine`, `/usr/local/UnrealEngine` absentes. Machine Linux, accès à Windows non disponible. Les observations exactes sont dans `evidence/engine-discovery.json`. Il s’agit des emplacements examinés, pas d’une preuve d’absence sur le PC personnel inaccessible.

Le projet utilisateur `C:\GameProject` n’a pas été modifié. Tous les fichiers sont dans le nouveau répertoire `/workspace/game-studio-output/engine-probe-final/`. Aucun Unreal Editor, UBT/UAT, compilation UE, chargement de map ou scénario de conduite n’a été exécuté. Aucun faux header.

## Parcours réellement effectué

Bundle rouge reçu de PR #12, commit `b2334f4c26a7a56f7f6d72453cbf25a7b14eaaea` : schéma JSON v1 SHA `1ba530565c0869760aac9fa08273f033d449401cb5bb5e880cee8874ba29ae51`, GLB réel SHA `9c1e9ae26ce46ed4d343bff411cc934eb62bbaf8b20034ec9b48918c0032b129` (410 248 octets), `.blend`, LOD et vues. Le ToolEngine existant contrôle permissions, schéma convenu, empreintes/taille des neuf fichiers et contenu binaire GLB. Quatre nodes de roues indépendants, sept matériaux et UV/normales sont présents. Les pivots GLB correspondent à la conversion Blender→glTF `[x,z,-y]` dans une tolérance de 2 mm. La latéralité/échelle/orientation réellement importées dans UE restent non vérifiées.

Le backend ArtifactStore existant stocke les dix objets (manifest + neuf fichiers) puis les relit avec contrôle SHA. Une copie isolée est écrite après nouvelle vérification de l’empreinte source. `.uproject`, script d’import éditeur fixe et requête à nonce sont préparés. Leur simple présence ne valide ni l’API Python Unreal ni les options de l’importeur de la version cible. La commande préparée n’est pas exécutée. `evidence/prepared/` conserve les fichiers de préparation, et `evidence/received/` le vrai bundle d’entrée.

Le lot consomme le JSON sans importer le code Python non fusionné de #9. Le moteur ToolEngine/ArtifactStore/OracleProvider/RecoveryPolicy et ModelProvider est réutilisé, aucun doublon global. Le juge est un contrôle de prérequis, pas encore un juge de conduite qualifié. Il retourne UNKNOWN : moteur absent, absence de rig/PhysicsAsset Chaos et de preuves compilation/import/map/collisions/suspension/inputs/caméra/conduite. Le niveau photoréaliste conserve les 60 FPS et le scénario de 15 s exigés ; aucune mesure ne les satisfait par défaut.

Le bundle statique contient quatre roues avec pivots et pièces séparées, mais aucun squelette, bone de roue ou PhysicsAsset. Même avec le moteur installé, il faudrait créer et qualifier ces données avant une simulation Chaos. Le TaskLoop existant n’a pas d’adaptateur UE qualifié. Le MVP #8 a des TaskRuns Python réels ; l’appel automatique des spécialistes depuis celui-ci reste à intégrer. La liaison réalisée dans ce lot est la consommation vérifiée des vrais artefacts de #9.

## Baseline Qwen réelle, non qualifiée pour UE

Même modèle officiel Qwen2.5-Coder-7B-Instruct GGUF Q5_K_M que #9, commit `13fb94bfda8c8cf22497dc57b78f391a9acb426a`, SHA `586844eac4d6d6321689f0192c8aa8e69cd8625974a5cc2d925b1a03366e4d16`, Apache-2.0 examinée avant téléchargement. Modèle servi sur loopback CPU via la session possédée de #9 et le ModelProvider existant de #10. Réservation du profil partagé ; aucun modèle simultané avec Blender/UE.

Demande réelle : Pawn UE5/Chaos, caméra/spring arm, fonctions throttle/steering, modules et prérequis. Réponse de 871 tokens pour 112 tokens d’entrée, finish stop, 147,827 s. Chargement 1,231 s. RSS après load 6 045 450 240 octets ; après génération 6 814 920 704 octets (échantillons, pas des pics). PID 17572, arrêt vérifié et code -15 de notre SIGTERM. Aucun téléchargement ou accès réseau distant pour l’inférence.

La génération présente un défaut de conception visible : elle crée un UStaticMeshComponent et remplace la racine du Pawn Chaos, au lieu d’employer correctement le maillage squelettique demandé. Déclarations/includes de composants, accessor de mouvement et module `ChaosVehicle` singulier exigent un contrôle sur le vrai SDK. Ces constats sont une revue de texte, pas des erreurs de compilation mesurées. La réponse n’est pas installée comme source du projet. `evidence/model/review.json` conserve les risques ; aucun PASS C++/Blueprint/Chaos ni supériorité générale n’est revendiqué. Aucun exemple UE qualifié pour entraînement.

## Validation locale

19 tests PASS, aucun skip : octets et structure du vrai GLB, corruption avec empreinte obsolète ou recalculée, schéma non convenu, lien symbolique, traversal, permissions ToolEngine, chemins Windows/WSL protégés, niveaux/budgets, copies réelles et refus des assertions modèle de compilation/conduite. Ce sont des tests de contrat/prérequis, aucun test du moteur.

Ruff et format PASS (10 fichiers), mypy PASS (7 modules nouveaux, follow-imports=silent). CLI réel code 2 avec réception vérifiée et juge UNKNOWN ; `evidence/engine-probe-final.json`, logs et JUnit sont archivés. Commandes reproductibles dans README.md.

BLOCKED : accès à une installation UE5 et SDK/plugins appropriés, rig automobile/PhysicsAsset, compilation/import/Blueprint/physique/tests de conduite, budgets FPS/VRAM sur PC cible, boucle de correction UE fondée sur erreurs mesurées et juge fonctionnel complet. Windows/WSL2/RTX ne sont pas qualifiés. Aucune LoRA/QLoRA, aucun entraînement neuronal 3D, aucune comparaison avant/après adaptation. TRAINING.md décrit les preuves nécessaires avant entraînement lourd. Les futures qualités graphiques et mondes ouverts restent hors démonstration.
