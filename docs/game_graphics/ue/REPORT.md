# Rapport 9 bis — Game Graphics dans Unreal Engine — 12 octobre 2026

## Suite du 13 octobre 2026 : qualification Windows préparée, moteur BLOCKED

Accès revérifié : l'exécution de commandes s'effectue sur Linux x86_64. Aucun exécuteur
Windows/WinRM/desktop distant n'est exposé parmi les outils de la session ; `cmd.exe`,
`powershell.exe`, `wsl.exe`, UnrealEditor et NVIDIA sont absents du PATH. La tentative de
lecture du terminal desktop a expiré. Aucun accès effectif au PC Windows 11/UE5.8/RTX4060Ti
n'est établi. La déclaration de ce PC et les chemins Windows ne sont pas des preuves d'accès.

Livraison supplémentaire : [lanceur reproductible et instructions Windows](WINDOWS.md),
détection UE5.8/Python, dépendances minimales épinglées avec SHA256 dans un venv TEMP,
projet neuf sans paramètre de projet utilisateur. Il réutilise le builder, l'inspecteur,
`UnrealGraphicsOracle` et `Tier0Judge` de #16, avec les critères utilisateur. Le plan Qwen
déjà accepté est l'entrée figée ; aucun modèle n'a été relancé ni téléchargé.

Le parcours demandé au vrai moteur conserve trois versions et leurs empreintes : baseline,
déplacement natif de la route (+150 cm), correction du seul transform. Il exige des processus
distincts pour la sauvegarde et la relecture, quatre captures natives par version, PASS initial,
FAIL unique `pose:road`, puis PASS après une correction. Il s'arrête sur absence de preuve,
défaut différent, API non qualifiée ou correction sans succès. Les anciennes versions sont
conservées. Cette correction ciblée est distincte de l'ancienne reconstruction du CLI général.

Améliorations indépendantes : journal natif Unreal privé, erreurs Python liées au plan,
refus des captures préexistantes, suivi des descendants observés, mesures RAM horodatées
et requêtes NVIDIA réelles lorsqu'elles sont disponibles. Aucune correction d'API UE5.8
n'est présentée comme validée ; les premières erreurs moteur doivent être recueillies sur le PC.

Validation cloud : 35 tests **existants** repassés, zéro test ajouté, aucune simulation UE.
Analyse/format Python et typage des trois modules modifiés concernés ; parsing du lanceur
dans un véritable PowerShell 7.6.6 Linux, puis refus d'exécution sous Linux (code 2).
Le contrôle du bundle et l'import du contrôleur ne prouvent ni PowerShell 5.1 Windows,
ni l'éditeur UE. Preuves séparées dans `evidence/windows-launcher/` ; les anciennes preuves
du 12 octobre sont conservées.

Résultats natifs livrés à cette date : **zéro scène UE exécutée, zéro UMAP/UASSET/capture
native produite par ce lancement, zéro correction UE démontrée, aucune mesure RTX locale**.
Ces éléments restent BLOCKED jusqu'à exécution du lanceur Windows et examen de son archive.
La prochaine action minimale est d'extraire le bundle puis lancer `Run-APEX-UE58.cmd` sur
le PC cible ; si Python livré avec UE ne permet pas le venv, le diagnostic demande Python
3.12 x64. Aucun accès à `C:\GameProject`, aucune modification du cœur ou des autres PR.
#14 reste indépendante : le cloud n'a toujours pas de GPU CUDA utilisable ; aucun calcul
neuronal 3D n'est revendiqué ici. Aucun élargissement PCG/Nanite/Lumen/paysages/villes.

Le reste du rapport ci-dessous décrit la livraison initiale du 12 octobre.

## État : BLOCKED pour toute création/capture UE réelle

Le code prépare un connecteur artistique UE et son premier démonstrateur, mais aucun Unreal Editor n’a été exécuté. Le moteur reste absent sur PATH (`UnrealEditor`, `UnrealEditor-Cmd`, `UnrealEditor.exe`, `RunUAT.sh`) et dans les racines connues examinées. Aucun plugin, version, GPU/FPS UE ou scène native ne peut être qualifié ici. `evidence/environment-probe.json` contient les observations exactes. Le PC Windows n’est pas accessible depuis ce cloud Linux.

Aucun `.umap`, `.uasset`, capture ou rendu Unreal n’est produit dans les preuves. Aucune simulation de l’API Unreal, aucun faux header et aucun rendu Blender présenté comme un rendu UE. Le projet utilisateur `C:\GameProject` est protégé et n’a pas été modifié.

## Implémentation et contrôles réellement testés

Sélection Blender/Unreal/hybride selon tâche et destination, qualification des outils, contraintes de qualité et coûts comparables lorsque mesurés. Pas de préférence fixe pour Blender : niveaux/décors/lumières/matériaux recommandent UE ; maillage/UV recommandent Blender ; maillage complexe pour UE recommande hybride. Outils/capacités absents rendent la sélection BLOCKED. Les tests de coût utilisent des données hypothétiques pour vérifier le sélecteur ; ils ne sont pas des benchmarks UE ni une preuve de fit VRAM.

Contrat SceneAssetContract v1 : scene_id, révision, SHA parent, IDs stables d’acteurs/matériaux, owner, dépendances Engineer, unités/axes, empreintes et stockage ArtifactStore. SceneIntegrationReport v1 : méthode, critères, tentatives, résultats, défauts/corrections et blocages. La méthode de géométrie reste `not_generated` tant que l’éditeur n’a pas réellement construit le niveau.

Projet neuf avec plan strict, nonce, scripts de confiance, copies privées du validateur et logs distincts. Les parents et versions précédentes sont conservés. Une révision incohérente, owner Engineer, ID protégé, propriété/script/commande inconnue ou chemin personnel est refusé. Les niveaux déjà intégrés avec logique Engineer restent bloqués : pas de reconstruction silencieuse omettant les acteurs fonctionnels. Les paths/GUID UE ne sont pas supposés stables ; Game Engineer doit résoudre les IDs et revalider une nouvelle version avant promotion.

La candidate d’éditeur crée/assemble une route, un bâtiment, deux potelets, un bac et deux lumières avec trois matériaux PBR, à partir des shapes natives du moteur. Elle manipule directement les objets et paramètres Unreal via scripting graphique autorisé, sans C++/Blueprint gameplay. Le renderer normal est requis ; NullRHI est exclu des captures. Un second processus doit rouvrir la scène, mesurer bounds/triangles/vertices/LOD/matériaux/lumières et capturer quatre angles avec AutomationLibrary. Le juge existant Tier0/OracleProvider ne prend pas un PASS Blender ou une déclaration modèle comme preuve UE.

La boucle candidate utilise RecoveryPolicy, trois essais max, versions nouvelles, défauts précis par acteur/origine et arrêt sans gain/régression. Ses reprises sont une reconstruction contrôlée du niveau graphique isolé, pas une mutation chirurgicale d’un monde gameplay intégré. Aucun cycle réel de correction UE n’a été observé. Les références attendent des captures natives ; RMSE brute comparative consultative, jamais calibration esthétique. Les niveaux élevés conservent FPS/textures/référence calibrée et restent UNKNOWN/BLOCKED.

API officielles UE5.6 consultées : MaterialEditingLibrary, EditorAssetLibrary, LevelEditorSubsystem, AutomationLibrary/AutomationEditorTask, StaticMeshComponent, StaticMesh/StaticMeshEditorSubsystem, SystemLibrary. Les réponses HTTP 200, URLs et SHA sont archivées dans `official-api-review.json`. Cette lecture ne remplace pas une exécution ou la vérification de signatures dans le moteur cible. Les scripts restent UNVERIFIED pour UE5.6+/Windows.

## Modèle réellement utilisé pour le plan

Qwen2.5-Coder-7B-Instruct officiel GGUF Q5_K_M, même fichier que PR #12, Apache-2.0 examinée avant téléchargement. Commit `13fb94bfda8c8cf22497dc57b78f391a9acb426a`, SHA `586844eac4d6d6321689f0192c8aa8e69cd8625974a5cc2d925b1a03366e4d16`. ModelProvider APEX existant, serveur llama.cpp loopback CPU possédé, aucun téléchargement à l’inférence.

Une demande limitée et un exemple de schéma ont réellement donné un plan JSON valide : sept acteurs (road/building/bollard_a/bollard_b/decor_planter/sun/fill), trois matériaux. Les valeurs reprennent largement l’exemple fourni ; cela prouve le parcours de génération/validation des opérations, pas une généralisation artistique ou une qualité de création UE. Le plan est préparé, jamais appliqué au moteur.

975 tokens d’entrée, 914 sortie, finish stop ; 182,679 s de requête, chargement 1,827 s. RSS échantillonnée après chargement 6 045 216 768 octets ; après génération 7 856 226 304 octets (~7,32 GiB). Ces valeurs ne sont pas des pics ni une preuve sur RAM 16 Go/RTX 8 Go. PID 25521 arrêté et fin vérifiée, code -15 de notre SIGTERM, avant toute tentative graphique. Aucun modèle concurrent lancé, aucun entraînement.

Les poids n’ont pas changé ; aucune LoRA/QLoRA ni géométrie neuronale libre. Le chantier #14 reste indépendant, non commencé dans ce lot.

## Validation locale

35 nouveaux tests PASS, sans skip : parsing/whitelist, rôle et IDs, protections Windows/WSL, choix des méthodes/budgets, versions/parents, fichiers privés et absence de faux outputs, refus des affirmations modèle, références sans moteur, arrêt d’un vrai processus Python Linux en conservant un peer, AST des scripts sans importer un faux `unreal`. Douze tests de contrat #12 passent également : 47 tests de contrôle locaux au total. Aucun test Unreal exécuté.

Ruff/format PASS sur 13 nouveaux fichiers, mypy PASS sur neuf modules UE (follow-imports=silent). CLI démonstrateur réel : code 2, raison `unreal_editor_missing`, engine_executed=false, captures_created=false. Projet et script/plan stockés ; géométrie `not_generated`. Preuves dans `evidence/`, avec rapports et JUnit. Commandes reproductibles dans README.md.

## Limites ouvertes

Première candidate restreinte, pas un spécialiste UE qualifié. Import artistique GLB/FBX dans ce connecteur, paysages natifs, splines/routes/intersections/villes, végétation, outils de modélisation, PCG, Nanite/Lumen, LOD/instancing natifs, textures complexes, budgets GPU/FPS et jugement visuel calibré restent à implémenter/qualifier. La préparation d’import distincte de PR #13 n’est pas transformée en succès graphique UE. Le sélecteur les marque absents/non qualifiés et ne substitue pas des primitives ou un rendu Blender.

Les opérations, captures et boucle de correction présentes doivent être exécutées sur une vraie installation UE avant toute déclaration de maîtrise, d’import correct, de cohérence visuelle ou de photoréalisme. Mutation sûre d’un niveau gameplay partagé et dispatch du cerveau restent à raccorder avec leurs contrats/tests. Les branches Claude, main, integration et PR #5/#7/#11/#12/#13 sont préservées ; PR de ce lot empilée sur le commit gelé #12, sans fusion automatique.
