# Première mission dans APEX.exe — contrat de candidate

La PR #31 est empilée sur #29 (`8fc1df9`), elle-même sur #26 → #24 → #22 → #19.
Le gouverneur/assistant/Panel de Claude reste dans #27, audité à `cf76ff1`.
Ne pas fusionner automatiquement ces lignes ni réinstaller le lock torch CPU par-dessus
le runtime CUDA déjà validé par l'utilisateur. Les travaux historiques #12/#13/#16/#17
ont déjà des adaptations dans la pile Studio : ne pas les réimporter aveuglément.

## Côté Codex

- POST `/api/v1/game-studio/projects` avec `compute_backend: "cpu"`,
  `controller_model: "pinned_7b"`, outil Blender, qualité Prototype, style réaliste.
- Les modèles absents restent un refus explicite. Le helper d'installation existant
  conserve les consentements, licence/révision/SHA et manifeste ; aucun nouvel installateur.
- Le CPU n'emprunte pas le GPU. Les travaux Studio utilisent
  `APEX_HOME/.apex/game-studio-workload.lock` et leur verrou de tâche. Attente bornée
  à 30 secondes, annulable, décomptée du RunBudget persistant ; épuisement =
  `shared_resource_busy`, mission en échec reprenable, jamais une attente infinie.
- Qwen CPU utilise `n_gpu_layers=0`, Blender le moteur Cycles CPU. Le choix automatique
  ou CUDA ne bascule jamais silencieusement vers CPU. Les gardes CUDA restent fermées.
- Le port Qwen fixe 8082 est protégé par la sérialisation des travaux. Tout autre
  service écoutant ce port est refusé ; il n'est ni réutilisé ni arrêté.
- Logs et reçus `model-lifecycle.json` conservent démarrage, backend, mémoire et arrêt.
  Une disponibilité du serveur n'est pas une preuve d'inférence : lire aussi la trace
  réelle `planner-trace.json`, sa réponse, son verdict et les artefacts.
- Annulation/reprise : mêmes identifiants projet, tentatives MissionService distinctes,
  budgets cumulés et artefacts conservés. Une tentative interrompue peut consommer
  son budget ; la reprise n'est pas une remise à zéro. Les contrôles existants restent actifs.

## Côté Claude — aucune modification de ses fichiers dans cette PR

1. Composer le paquet avec les changements Codex et #27, sans changer l'installation
   personnelle en cours pendant cette construction. Conserver `PYTHONUTF8=1` pour les
   processus enfants, les chemins du profil, les scripts et schémas empaquetés.
2. Garder les boutons existants : installation du moteur CPU vérifié, puis des poids
   Qwen épinglés après le consentement 5,4 Go/Apache-2.0. Le provisioning passe par
   `dependencies` et `install-planner`, déjà branchés par Claude.
3. Avec #27 `cf76ff1`, l'assistant ne prend plus de prêt englobant lorsque
   `apex.game_studio.gpu` est présent. C'est compatible avec notre CPU explicite,
   qui n'emprunte rien. Sur une pile plus ancienne, seul le propriétaire assistant
   rend son éventuel prêt ; Studio ne le modifie pas.
4. Claude a livré le protocole 2 demandé (`98c7ebb`, inclus dans `cf76ff1`) :
   compteurs `lenders`, retour atomique d'un propriétaire connu, ACK `returned`
   distinct de `resumed`. L'adaptateur Codex utilise désormais ce contrat ; un autre
   prêt actif n'empêche plus de restituer le nôtre. `lent_to` reste un affichage.
   Le protocole 1 conserve son chemin exclusif pour les anciennes piles.
5. Gardes CUDA toujours fermées : qualifier sur RTX le binaire CUDA, ses DLL,
   les chargements séquentiels, les OOM, la VRAM et la restitution avant activation.
   Le contrôle du vrai gouverneur v2 avec l'adaptateur et les verrous OS passe sur
   Linux CPU ; aucun modèle CUDA résident ou déchargement GPU n'a été testé.

## Recette utilisateur pour la candidate installée

Ces étapes sont une procédure de validation, pas une affirmation d'exécution sur son PC.
Aucun terminal n'est requis :

1. Ouvrir la candidate APEX.exe assemblée par Claude, puis l'Atelier 3D.
2. Dans les composants, vérifier Blender 4.3.2, moteur CPU local et Qwen 7B installé/vérifié.
   Accepter les téléchargements uniquement via les boutons existants si nécessaire.
3. Choisir **CPU explicitement**, Qwen 7B, Blender, Véhicule, Réaliste, Prototype,
   juges et corrections automatiques ; budget 3 600 s, mémoire 12 000 Mio maximum,
   3 tentatives. Le budget mémoire est une limite surveillée, pas une réservation
   globale : la RAM disponible doit aussi suffire avec les autres applications.
4. Demander : « Crée une voiture sportive rouge avec quatre roues indépendantes et
   prépare son export Blender. » Confirmer la mission dans l'interface existante.
5. Vérifier l'identifiant stable, le plan Qwen accepté, les étapes, les verdicts et
   les corrections réelles. Ouvrir les rendus et télécharger `.blend` et `.glb`.
6. Vérifier que le précontrôle Engineer confirme les quatre composants de roues.
   Ce PASS ne certifie ni conduite ni physique UE5, ni photoréalisme.
7. Sur une seconde mission, arrêter pendant le chargement ou l'inférence ; vérifier
   l'arrêt du seul serveur possédé, puis reprendre avec le même projet et un budget
   cumulatif. Ce scénario sur le PC réel reste à qualifier même si les tests de contrat passent.
8. Conserver reçus, plans, versions, SHA et logs ; ne pas modifier `C:\GameProject`.

Le runner Windows Server 2022 qualifie le runtime empaqueté et l'API. Il ne remplace
pas cette vérification des clics/rendus/téléchargements dans l'Atelier 3D Windows 11.
