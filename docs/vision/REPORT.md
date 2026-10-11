# Rapport de validation — spécialiste vision, Issue #6

Travail Codex isolé sur `agent/codex/6-vision-specialist`, PR #7 vers
`apex/integration`. Base `bd2edd285ad2f796991ce5460715cb4ee9f3f06c`.
Ce rapport distingue exécution réelle, tests avec doubles et qualification
matérielle. Aucun entraînement de modèle APEX n'est revendiqué.

## Implémenté

- Pipeline image local PNG/JPEG/WebP : limites 20 MiB/25 MP, EXIF, transparence,
  proportions et plafond 2 048 pixels sur le côté long.
- OCR Tesseract facultatif avec texte, boîtes et scores ; processus terminé avant
  le chargement du VLM. Son extraction reste distincte de la compréhension VLM.
- Qwen2.5-VL-3B-Instruct préentraîné, safetensors vérifiés, processeur recevant
  réellement les pixels, inférence PyTorch sans téléchargement implicite ni code
  distant. Révision `66285546d2b821cf421d4f5eb2576359d3770cd3`.
- Contrat v1, JSON contraint pendant le décodage puis validé strictement ; erreurs
  et blocages explicites, délais/annulation coopératifs, libération du backend.
- Verrou OS entre spécialistes et refus d'un cœur actif enregistré dans le même
  profil ; persistance optionnelle dans l'ArtifactStore existant, logs de
  métadonnées uniquement. Les échecs de sortie conservent les faits d'exécution
  disponibles sans retourner une réponse inventée.
- CLI `python -m apex.vision fetch/analyze` et API Python `VisionService`.
  Provenance des frames vidéo préparée dans `MediaContext`.

Aucun fichier du runtime polyvalent, cerveau, TaskLoop, assistant, API ou
manifeste central n'a été modifié. Aucun upload dans l'interface existante ni
transfert automatique depuis Claude n'est livré.

## Testé dans le cloud

Cloud Linux CPU, 32 GiB RAM, quota quatre CPU ; aucun GPU/CUDA disponible.
Python 3.12.14, PyTorch 2.14.1+cpu, torchvision 0.29.1+cpu, transformers 4.57.6,
lm-format-enforcer 0.11.3, Pillow 12.3.0 et Tesseract 5.5.0.

- 39 contrôles de contrat/OCR réussis, dont cinq avec le binaire Tesseract réel.
  Les doubles des tests de contrat ne prouvent aucune qualité de modèle.
- Ruff, formatage et mypy du namespace vision réussis.
- CLI OCR seule : statut `partial`, réponse VLM absente, sortie `3`.
  Fichier corrompu : `invalid_image`, sortie `2`, aucun backend appelé.
- Les deux shards de poids, environ 7,0 GiB, sont vérifiés contre les empreintes
  LFS du Hub ; configurations/tokenizers revérifiés dans le manifeste local.
- Installation réexécutée avec 70 contraintes de dépendances et reconstruction
  de l'interface ; fichiers centraux/lockfiles inchangés. 173 tests frontend
  réussis dans 27 fichiers.
- APEX autonome démarré par sa commande documentée : migrations SQLite,
  Doctor/Startup Gate, API authentifiée et interface compilée fonctionnelles.
  Santé API/base et création/lecture/suppression d'une conversation vérifiées ;
  accès sans token refusé par `401`. Instance arrêtée proprement avant le VLM.
  Cela ne prouve pas la génération du modèle principal, absent de cette setup.

## Qualité préentraînée : validation en cours

Les images sont des fixtures synthétiques reproductibles : exercice imprimé
JPEG légèrement déformé, tableau/schéma de mesures, graphique à barres et image
vide. Elles ne sont pas des photographies prises par un utilisateur.

La première inférence CPU/offline a correctement transcrit `2x + 3 = 11`, mais
a répondu `5` et émis `description_textual` au lieu de `description`. Elle a
échoué au contrôle de schéma après 419 secondes ; aucune réussite de calcul
n'est attribuée à cette exécution. Les assertions et réponses attendues sont
restées inchangées. Le décodage contraint JSON et une consigne de calculs et
vérification avant réponse ont ensuite produit `4`, avec `2 × 4 + 3 = 11`
explicitement vérifié : test réussi en 782 secondes, 363 tokens générés,
8,15 GiB RSS après génération. Les éléments visuels reprenaient toutefois des
libellés génériques de l'exemple du prompt. Celui-ci a été remplacé par une
description du contrat sans exemples de valeurs ; les types visuels sont
maintenant bornés et les tests exigent un tableau/graphe et l'absence de texte
inventé sur une image vide. La série finale est en cours.

Les résultats de cette nouvelle série seront ajoutés avec temps, tokens,
mesures mémoire et sorties réellement produites. À ce stade, le chargement
préentraîné et l'inférence existent ; la qualité finale des quatre cas n'est
pas encore déclarée validée. Les sorties intermédiaires réelles sont conservées
dans `evidence/` pour exposer ces défauts, sans les présenter comme validation
finale.

## Mesures et limites

Les faits de chaque exécution rapportent identité/empreintes des poids,
paramètres, précision, quantification réelle, tokens, temps de chargement et
génération, RSS après chargement/génération. Ces RSS sont des instantanés,
pas des pics. Sur CUDA seulement, le backend prévoit les pics alloués/réservés
PyTorch ; ils ne représentent pas toute la VRAM du système.

Le budget CPU par défaut est 12 GiB, avec choix float32/BF16 selon la mémoire
disponible et la limite cgroup. Le contexte est borné à 8 192 tokens, l'image à
1 024 tokens visuels par défaut. Le délai par défaut est vingt minutes ; un
kernel, le chargement ou le hashing en cours ne sont pas interrompus
instantanément. La qualité est privilégiée à la latence.

RTX 4060 Ti 8 Go, Windows, NF4/bitsandbytes, pic de RAM/VRAM et qualité après
quantification sont **UNVERIFIED**. NF4 conserve le module visuel hors
quantification ; l'estimation préparatoire d'environ 5,5 GiB n'est pas une
mesure GPU. Aucun test de ce cloud CPU ne qualifie cette configuration.

OCR `eng/osd` disponible ; `fra` absent, limite signalée dans les résultats.
Les pages denses, petits symboles, exposants et manuscrits ne sont pas qualifiés.
`complete` signifie exécution et contrat terminés, jamais vérité garantie.
La correction mathématique demandée au modèle n'est pas un oracle indépendant.

## Blocages et travail futur

La baseline Python du dépôt présente huit échecs liés à l'exécution sandbox :
`ContainerSandbox` impose `PATH=/usr/bin:/bin`, mais le vrai CLI Docker est dans
`/usr/local/bin/docker` dans ce cloud. La détection peut réussir, puis le
lancement échoue avec `FileNotFoundError`. Un neuvième échec de timing a passé
sa relance isolée. L'exécution TaskLoop reste **BLOCKED** ; aucun contournement
de l'isolation ou changement des fichiers partagés n'a été ajouté.

Le contrat de transfert reste à accepter avec Claude : terminer/annuler le
polyvalent, libérer ses poids, confirmer, exécuter/libérer le spécialiste,
puis reprendre le polyvalent. Le verrou n'impose aucune exclusion aux modèles
externes ou au code central qui ne l'utilise pas encore. Cross-review demandé
sur GitHub ; aucun merge ni auto-approbation.

Extraction vidéo, sélection de frames, analyse séquentielle et synthèse finale
sont **PLANNED**. Seule la provenance temporelle d'une frame est implémentée.
Les tests sur photographies de téléphone et documents scolaires réels restent
à ajouter ; les fixtures présentes constituent une première preuve limitée.

La configuration cloud réutilisable est préparée avec caches inscriptibles,
installation figée, poids immuables et instructions de démarrage. Publication
des paramètres et restauration dans une nouvelle tâche ne sont pas encore
validées par cette session.
