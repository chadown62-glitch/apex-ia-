# Rejouer la qualification sans toucher à l'installation personnelle

L'entrée `scripts/game_studio/qualify_windows.py --output <dossier-neuf>` est
réservée à une machine Windows native. Elle suppose le checkout du commit testé
et le frontend déjà construit (`npm ci`, puis `npm run build` dans `frontend`) ;
la workflow accomplit ces prérequis. Le dossier doit être neuf ; ne pas lui
fournir le profil APEX courant. `C:\GameProject` est refusé. Le script n'installe
ni Blender ni Python globalement : il utilise des distributions portables sous
le dossier demandé et crée son propre `APEX_HOME`.

La workflow `game-studio-windows` l'exécute sur Windows Server 2022 x64. Cela
qualifie un runtime empaqueté avec l'interpréteur APEX, **pas l'installateur ni le
launcher APEX.exe Windows 11 de l'utilisateur**. Elle construit le frontend
existant ; aucun second frontend et aucun Panel modifié.

## Parcours exécuté

1. Télécharger CPython NuGet 3.12.10, vérifier le SHA256 déjà épinglé par APEX.
2. Construire le runtime existant, dont les workers Blender/Engineer et les deux
   contrats JSON. Les contrats sont écrits en LF afin de préserver le SHA256
   convenu même lorsque Git utilise CRLF sur Windows. Le vérificateur Engineer
   conserve sa stricte empreinte d'origine.
3. Installer les dépendances Windows/planificateur avec les locks et leurs hashes.
4. Télécharger Blender portable 4.3.2. SHA256
   `933a62a770c941e316535f893629ff106ddd2aa5a956f0081ec557439073d4f6`,
   fichier `blender-4.3.2-windows-x64.zip`. Source officielle
   `https://download.blender.org/release/Blender4.3/`, miroir du même artefact
   `https://mirrors.ocf.berkeley.edu/blender/release/Blender4.3/` si HTTP indisponible.
   Les deux sources doivent produire le même hash ; une divergence n'est pas
   acceptée comme un nouveau binaire.
5. Charger réellement llama.cpp et importer sa CLI serveur. Mesurer le matériel
   du runner et la version exacte de Blender. Vérifier l’exclusion du bail avec
   un second processus, puis sa disponibilité après libération. Le dossier
   Blender portable contient volontairement un espace.
6. Exécuter le catalogue artistique autorisé pour une voiture rouge, quatre vues,
   sauvegarde Blender et export GLB. Les processus sont bornés et mesurés.
7. Rouvrir chaque fichier dans un nouveau processus Blender, juger les propriétés
   techniques, vérifier le contrat Graphics → Engineer sur les vrais octets GLB.
8. Sur refus du contrat, appliquer une unique correction de groupes/pivots de
   roues à une nouvelle version ; conserver le SHA du parent, juger et vérifier
   à nouveau. Ce défaut n'est pas injecté dans les reçus : il provient du
   constructeur existant. Ni physique ni conduite dans UE5 ne sont qualifiées.
9. Stocker et relire les artefacts dans le véritable LocalFilesystemArtifactStore.
10. Démarrer le backend autonome du paquet, tester l'authentification, servir la
    route Game Studio et soumettre la demande utilisateur par l'API réelle.
    Sans poids Qwen, attendre le 409 `local_planner_model_missing`. Avec les poids,
    suivre la mission bornée, conserver le plan et les versions, télécharger les
    artefacts des sous-missions par l'API et contrôler leurs hashes.
11. Arrêter uniquement ce backend, écrire rapports et SHA256, préserver aussi
    les échecs. Un code CI réussi sur les composants ne vaut pas mission Qwen
    réussie : `report.mission.status` précise le blocage.

Les créations du point 6 sont explicitement marquées
`explicit_fixed_catalog_fixture_not_Qwen`. Elles ne sont jamais injectées comme
une mission simulée dans Game Studio. Le pipeline artistique complet reste
subordonné à la vraie planification et au vrai contrôleur Qwen.

## Poids optionnels

Le déclenchement par push ne télécharge aucun poids. Le champ
`download_pinned_qwen` de `workflow_dispatch`, false par défaut, permet une
exécution avec téléchargement du modèle épinglé après consentement explicite
(volume 5,4 Go et licence Apache-2.0). Il n'a pas été activé pour les essais
sans poids. La même fonction utilise des poids existants uniquement si le
manifeste et son contenu passent le contrôle CoderSession.

## Application réelle : ce que Claude doit raccorder

Le Panel peut lire `python -m apex.game_studio dependencies`, proposer les
composants absents, installer les locks dans son runtime et utiliser le helper
explicite de provisionnement décrit dans [PLANNER.md](PLANNER.md). Le clic humain
et les outils de délégation existants restent la frontière d'autorisation.

Installer ensemble les changements de #26/#29 et le gouverneur de #27 dans une
candidate isolée, avec les mêmes `APEX_HOME` et API propriétaires. Pour le premier
essai fonctionnel sans qualification GPU du spécialiste, faire sélectionner
**CPU explicitement**. Pour la suite CUDA, suivre [GPU-CONTRACT.md](GPU-CONTRACT.md)
et qualifier le cycle matériel complet avant de lever la garde. Aucune commande
locale ou intervention du PC utilisateur n'a été exigée durant ces travaux.
