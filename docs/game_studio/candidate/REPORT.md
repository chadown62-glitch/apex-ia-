# Candidate Game Graphics / Game Studio — Issue #25, PR #26

État au 18 octobre 2026 (Europe/Paris). **BLOCKED pour la mise à disposition finale**.
Les correctifs sont proposés à l'intégration, mais le parcours installé complet ne peut pas encore être qualifié : modèle planificateur absent du cloud restauré, binaire Windows à exécuter réellement, raccordement du bouton d'installation côté Panel et qualification GPU/Windows encore ouverts. Aucun merge ni release.

La restauration du cloud a récupéré un ancien snapshot (`bd48096`), sans le worktree récent, les poids Qwen 7B ni les preuves locales non poussées. Le dernier état récupérable de #26 était `1e2db2f`. Les essais décrits dans la conversation avant cette restauration **ne sont pas comptabilisés comme preuves finales récupérées**. La branche distante a été récupérée sans réécriture ; les corrections de reprise sont dans `c80ce11` puis les commits suivants. Les artefacts des PR précédentes, déjà versionnés, restent disponibles.

## 1. Fonctionnalités opérationnelles et périmètre

Le logiciel existant expose Game Studio, ses API authentifiées, le registre Specialty, la planification bornée et les services de missions/artéfacts. Les scripts Blender de catalogue exécutent réellement les opérations autorisées ; aucune chaîne Python libre produite par le modèle n'est exécutée. Les preuves historiques récupérables sont dans [mission23/REPORT.md](../mission23/REPORT.md), [mission20](../mission20/) et [evidence](../evidence/).

Dans cette reprise : **assemblage Blender réel de trois placements**, contrôle du fichier rouvert, quatre vues natives, export GLB, démarrage du runtime Linux empaqueté et contrôle Chromium du frontend. Les deux modèles sources (bâtiment et fauteuil) proviennent de #24 ; le fauteuil est placé deux fois. Ce n'est ni une nouvelle génération Qwen, ni un mesh neuronal, ni un parcours de création complet depuis le cerveau.

## 2. Corrections nouvelles

- `jsonschema` ajouté aux extras de développement/runtime et au lock Windows. Cinq paquets ajoutés (`attrs`, `jsonschema`, `jsonschema-specifications`, `referencing`, `rpds-py`), **aucune version préexistante changée**. L'extra utilise maintenant `llama-cpp-python[server]`, avec les dépendances du serveur.
- `runtime_build/build.py::_assemble_common` inclut aussi `scripts/game_engineer/`. La correction Windows de Claude dans `_extract_commit` n'est pas touchée.
- Découverte Blender commune au diagnostic et au worker : `APEX_BLENDER_EXECUTABLE` absolu, puis PATH, puis dossiers Windows standard versionnés sous PROGRAMFILES. Un chemin explicite invalide bloque sans choisir silencieusement un autre logiciel. Les chemins Windows sont testés par fixtures, **pas qualifiés sur Windows**.
- Installation explicite du planificateur via le contrat décrit plus bas ; aucun téléchargement de poids pendant cette reprise.
- Garde GPU fermée pour les entrées projet dynamique, recette historique et asset individuel jusqu'à qualification du handoff. Une simple détection CUDA ne suffit plus à autoriser le chargement.
- Sol de cour distinct de la route historique, sélectionné depuis les mots « cour » / « courtyard », vérifié après réouverture. Une ancienne scène de contexte différent est conservée et refusée à la réutilisation silencieuse.
- Game Studio utilise les couleurs des thèmes et le composant Button existants. Correction d'un contraste réellement défectueux observé dans Chromium. Description munie d'un nom accessible stable. Raisons de blocage affichées.
- La garde historique d'entraînement continue d'exiger **Research comme seul SpecialtyTaskProvider** ; Game Graphics est reconnu comme service de missions, sans inventer un moteur de clones ou d'entraînement.

## 3. Benchmark indépendant et disponibilité des preuves

Les six consignes et critères restent figés dans [benchmark.json](benchmark.json). Ils ne sont pas assouplis après un échec.

| Cas | Qualification nouvelle du modèle après restauration |
|---|---|
| Coupé bleu | Non exécutée : poids du planificateur absents |
| Pharmacie reconnaissable | Non exécutée ; constructeur spécialisé toujours indisponible |
| Cour, bâtiment, deux fauteuils en cuir | Non exécutée par Qwen ; comparaison de sol séparée ci-dessous, sans certification du cuir |
| Trois voitures distinctes | Non exécutée |
| Voiture strictement low-poly | Non exécutée ; style non qualifié |
| Voiture et intégration pilotable UE5 | Non exécutée ; UE/physique bloqués |

Les chiffres de réussite des douze essais antérieurs non récupérés ne sont pas publiés comme résultat du benchmark. Les tests de schéma/couverture passent, mais **ne constituent pas un benchmark d'intelligence**. Les rapports et échecs historiques de #24 sont conservés, sans les relabelliser comme demandes inédites de #25.

## 4. Fidélité et comparaison native avant/après

Le défaut de contexte identifié avant la restauration est reproductible : l'assembleur ajoutait systématiquement une route et ses marquages, même pour une cour. Le diagnostic est issu d'une revue du rendu par Codex, **pas d'un juge esthétique appris**.

La [version rue](evidence/recovery-20261018/street/front_three_quarter.png) et la [version cour](evidence/recovery-20261018/courtyard/front_three_quarter.png) sont produites par de vrais processus Blender 4.3.2. Chaque version possède `.blend`, `.glb`, quatre PNG, rapports de cadrage et réouverture. Le contrôleur numérique du fichier rouvert trouve 143 meshes du bâtiment, 32 par fauteuil ; la correction ajoute 90 pavés, avec aucune géométrie `street*`. Le [contrôle indépendant du GLB](evidence/recovery-20261018/courtyard/independent-glb-check.json) confirme ce contexte dans les octets exportés.

Cette correction est une modification de géométrie du décor, pas une amélioration de lumière. Elle ne certifie ni beauté, ni photoréalisme, ni compréhension générale des références. Les bâtiments restent génériques ; les rendus sont bruités. Les noms historiques `garage.blend`/`garage.glb` ne constituent pas une classification sémantique de la scène.

Le juge de couverture est une extraction lexicale indépendante, limitée au français/anglais déclaré : objets, quantités, quelques styles/couleurs/contextes, négations et omissions reconnues. Il ne distingue pas universellement préférences et obligations, ne résout pas toutes les contradictions et peut manquer une exigence hors vocabulaire. Seuls deux contextes de sol sont implémentés. La fidélité artistique générale reste non qualifiée.

## 5. Juges et corrections

Contrôles obligatoires : intégrité des fichiers, SHA256, exécution, inspections de géométrie et contrats. Les options « juges sur toutes les tâches » et « corrections automatiques » restent indépendantes. Les révisions et retours sont archivés ; budgets et limites de tentatives sont persistants. Les juges évaluent des propriétés annoncées, pas une esthétique universelle.

La planification reçoit les défauts de couverture et peut réessayer dans la limite autorisée. Les contrôles de topologie, alignement, cadrage et matériau sont limités aux opérations/mesures existantes. Le précontrôle véhicule Graphics → Engineer et sa correction réelle sont documentés avec artefacts dans #24 ; **aucune voiture pilotable n'est revendiquée**.

Pour la reprise actuelle, seul le cycle rue → cour est réexécuté nativement. Aucun nouveau score de précision d'un juge vision, aucune calibration esthétique ni amélioration des poids. Prototype reste le niveau de disponibilité visé ; les niveaux Jouable, Soigné et Photoréaliste sont des exigences demandables, pas des certifications acquises.

## 6. Persistance et budgets

`RunBudget` persiste temps actif cumulé et tentatives avant calcul. Réservation de dix secondes : un crash peut facturer au plus dix secondes inutilisées, sans remettre le compteur à zéro ni facturer le temps hors ligne. Un bail de fichier par mission évite deux workers simultanés. Les écritures JSON passent par remplacement atomique après flush/fsync ; ce n'est pas une garantie universelle contre toute panne de stockage.

La reprise conserve l'identifiant projet, les identifiants des tentatives MissionService et les artefacts déjà validés. Les anciens reçus sans budget exploitable ne sont pas silencieusement réinitialisés. Annulation par marqueur/événement ; nettoyage seulement des processus dont PID, date de création et commande correspondent au reçu propriétaire. Aucun arrêt fondé sur le seul nom du processus.

Les tests utilisent notamment de vrais verrous interprocessus, compteurs persistants, stockage MissionService et refus de nettoyage en cas d'identité différente. La nouvelle preuve complète API → Qwen interrompu → reprise, antérieure à la restauration, n'étant pas récupérée, n'est pas recertifiée ici. Comportement des processus/verrous Windows à qualifier ; pas de garantie d'absence absolue d'orphelin dans toute fenêtre de crash.

## 7. Catalogue et délégation cerveau

Game Graphics reste dans le registre Specialty existant : **spécialiste composé** Qwen + Blender + contrats + juges, pas un checkpoint entraîné. `capabilities.probe()` expose dépendances, états et limites ; présence d'un binaire ne vaut pas validation d'une mission. Aucun deuxième catalogue.

Outils stables : `game_studio.create/status/cancel/results`, permissions distinctes, workspace propriétaire, projet stable, états MissionService, empreintes d'artefacts. Les octets restent derrière l'API authentifiée. Une réponse terminale sans artefact ne signifie pas livraison disponible. L'API locale utilise son principal authentifié existant ; ne pas en déduire une isolation multi-tenant complète.

Réponses et coordination publiées dans [Issue #21](https://github.com/chadown62-glitch/apex-ia-/issues/21#issuecomment-6242063296). La description brute est conservée ; Prototype est un défaut visible, pas une réécriture des exigences. La création démarre un worker mais la sonde synchrone peut prendre quelques secondes : pas de promesse de retour instantané sur le thread UI. Claude conserve la proposition et le clic humain selon son contrat courant. Ses fichiers `apex/assistant/**` n'ont pas été modifiés ; son branchement n'a pas été réexécuté dans cette reprise.

## 8. GPU FIRST et installation du planificateur

Auto/CUDA ne basculent pas implicitement sur CPU. La garde exige encore une qualification de quiescence propriétaire, ACK, acquisition du bail commun, libération vérifiée et retour des admissions cerveau. Aucun `unload_all()` forcé ni arrêt d'un processus tiers. Le contrat de Claude a été lu ; son exécution CUDA demeure non mesurée ici.

Commande de diagnostic, sans téléchargement : `python -m apex.game_studio dependencies`.
Contrat pour le Panel, **uniquement après consentement humain** :
`python -m apex.game_studio install-planner --accept-download --accept-apache-2-license`.
Sans les deux consentements, sans serveur local ou sans espace suffisant : refus. Modèle épinglé, licence vérifiée, SHA256 vérifié par `fetch_coder`, verrou d'installation, manifeste existant préservé. Le bouton et la gestion du processus d'installation restent à raccorder côté Claude ; une commande seule ne termine pas le parcours novice.

La [roue officielle Windows 0.3.36](evidence/recovery-20261018/windows-wheel-audit.json) a été téléchargée pour inspection ZIP, avec SHA identique au digest GitHub. Serveur et DLL CPU présents, licence MIT. Une liste optionnelle `requirements-planner-cpu-windows.{in,lock}` prépare sa qualification, contrainte par le lock de base. Elle n'est pas installée automatiquement. **Chargement Windows, inférence CPU Windows et CUDA non qualifiés.** La roue PyTorch CUDA optionnelle de Claude ne fournit pas automatiquement un llama.cpp CUDA compatible.

TripoSR via missions, sa voie GPU et la compréhension des images de référence restent bloqués/non qualifiés. Aucun poids téléchargé, aucun entraînement ni modèle neuronal exécuté dans cette reprise.

## 9. Backend, frontend et runtime mesurés

- **157 tests backend/runtime passent**, plus 16 sous-tests ; un avertissement Starlette/httpx. Suite ciblée, pas toute la suite APEX. La dernière correction du contrôle d'espace de provisioning est vérifiée séparément par **13 tests de provisioning** (voir logs).
- **174 tests frontend passent**, TypeScript et build Vite passent.
- Artefact runtime réellement construit depuis les sources ; vérification anti-fuite : zéro violation. Scripts Game Engineer présents. Démarrage Linux via `apex.standalone`, migrations/Startup Gate existants, arrêt propre et redémarrage vérifiés.
- Sans poids installés : API santé 200, capacités 200, requête non authentifiée 401. Chromium charge la page empaquetée, sans erreur JavaScript ; choix natif non qualifié désactivé. [Night](evidence/recovery-20261018/packaged-game-studio.png), [Dune](evidence/recovery-20261018/packaged-game-studio-dune.png).
- Soumission depuis le vrai formulaire avec CPU explicitement choisi : HTTP **409 `local_planner_model_missing`**, affiché dans l'application. C'est un blocage réel, pas un succès de génération. [Preuve](evidence/recovery-20261018/packaged-smoke.json).
- Aucun nouveau test Windows exécuté par Codex. Les résultats Windows Server de Claude cités dans #21 sont des observations externes, pas une qualification RTX/UE ou du nouveau paquet.

Mesures des processus Blender, CPU explicitement utilisé :

| Opération | Temps écoulé | CPU cumulé | RSS maximal échantillonné |
|---|---:|---:|---:|
| Rue + quatre rendus | 16,383 s | 46,36 s | 367,84 Mio |
| Réouverture rue | 1,004 s | 0,74 s | 212,25 Mio |
| Cour + quatre rendus | 18,653 s | 57,33 s | 380,49 Mio |
| Réouverture cour | 1,257 s | 1,03 s | 260,25 Mio |

Hôte : cinq CPU logiques, 35,74 Go de RAM totale, Blender 4.3.2, CUDA absent. Voir [mesures](evidence/recovery-20261018/measurements.json) et [hôte](evidence/recovery-20261018/host.json). Aucune extrapolation vers RTX 4060 Ti.

## 10. Échecs conservés et risques

Les **onze échecs Tool Engine historiques sont reproduits inchangés** (20 tests passent). Le conteneur de détection fonctionne ; l'exécution ne trouve pas Docker après restriction du PATH à `/usr/bin:/bin`, alors que le binaire est `/usr/local/bin/docker`. Le diagnostic réel produit FileNotFoundError avant lancement. [Log complet](evidence/recovery-20261018/sandbox-tests.log), [diagnostic](evidence/recovery-20261018/sandbox-diagnostic.json). Aucun affaiblissement des tests ni modification du sandbox partagé. Les scripts Blender de confiance ne sont pas présentés comme du code arbitraire sandboxé.

Autres échecs rencontrés : perte des données non poussées à la restauration ; erreur de champ `return_code` dans le premier harnais de collecte (Blender avait terminé, puis le harnais a échoué), corrigée sans effacer la version ; contraste Night ; erreur TypeScript lors de l'ajout des raisons de blocage, corrigée avant build final. Le premier échec du harnais est conservé dans les preuves. Le manque de modèle reste visible, sans simulation.

## 11. Dépendances Git et intégration

Chaîne : **#26 → #24 (`10bd282`) → #22 (`c0a8591`) → #19 (`db58f30`) → apex/integration (`bd2edd2`)**. [Inventaire exact](git-dependencies.json). Integration est inchangée lors de cette reprise. Aucun check CI n'est remonté pour #26 ; les tests locaux ne sont pas attribués à la CI.

#19 a importé/adapté des développements des PR #11/#12/#13/#16/#17. Les têtes historiques ne sont pas toutes des ancêtres ; fusionner chaque ancienne PR en plus de la pile introduirait duplications/conflits. L'intégrateur doit comparer les écarts résiduels. Ne fermer ni fusionner automatiquement ces PR.

La PR #27 de Claude reste distincte. Ses propositions de correction `_extract_commit` et du Panel sont laissées intactes. Ses répétitions de fusion sont documentées dans #21 ; aucun merge combiné n'est publié par Codex. La classification `apex/vision` lors de l'ajout séparé de #7 reste à traiter par le lot d'intégration concerné, sans modifier #7 ici.

## 12. Étapes exactes avant disponibilité

1. Revue croisée humaine de #26 et des contrats #21 ; préparer l'arbre combiné avec #27 puis la pile #19/#22/#24/#26, une seule intégration contrôlée à la fois. Comparer les anciennes branches importées, sans merges aveugles.
2. Résoudre le défaut sandbox partagé sous son propriétaire, conserver les tests d'isolation ; relancer les régressions de l'arbre combiné.
3. Construire le paquet complet (frontend, modules, scripts, locks). Qualifier la roue llama.cpp Windows candidate et ses dépendances dans le runner, sans annoncer CUDA ; vérifier imports et chargement réel.
4. Côté Panel, présenter taille/licence/source avant installation explicite du planificateur, appeler le contrat ci-dessus, afficher progression/erreurs, re-sonder les dépendances. Conserver le démarrage APEX sans ce composant.
5. Restaurer un modèle Qwen épinglé vérifié dans un environnement de qualification autorisé ; réexécuter les six consignes figées, deux fois, puis la mission multi-assets et l'annulation/reprise avec preuves persistées dans Git/artifact storage. Aucun résultat absent ne doit devenir PASS.
6. Qualifier sur matériel réel le bail GPU commun et l'ACK cerveau, les chargements séquentiels, les OOM et la restitution des ressources avant de lever la garde. Pas de repli CPU implicite.
7. Installer et valider dans le véritable APEX Windows le parcours proposition/clic → mission → Blender → juges → correction → artefacts → frontend. Garder UE5/physique/TripoSR GPU bloqués tant que leurs propres preuves manquent. Aucun accès au PC utilisateur demandé dans ce lot.

Reproduction cloud de la comparaison de sol : depuis la racine, avec les dépendances installées et Blender, `PYTHONPATH=. python docs/game_studio/candidate/reproduce_ground_context.py /tmp/un-nouveau-dossier`. Le script refuse un dossier existant et le projet personnel protégé. Les sources et critères sont fixes, les sorties sont des fichiers natifs ; aucune dépendance à Qwen pour cet essai explicitement limité.

Le script de reproduction a été exécuté une seconde fois : les deux rapports de réouverture sont identiques. Les empreintes des fichiers PNG diffèrent, mais les pixels RGB sont identiques dans les huit vues (RMSE = 0) ; la comparaison est conservée dans [reproduction-check.json](evidence/recovery-20261018/reproduction-check.json). Cette répétition mesure uniquement l’assemblage Blender, pas la stabilité du planificateur. Les preuves sont inventoriées dans [SHA256SUMS.json](evidence/recovery-20261018/SHA256SUMS.json).

Les instructions de démarrage de l’environnement ont été enregistrées dans le brouillon de configuration, sans publication. La restauration future de cette candidate n’est pas certifiée.
