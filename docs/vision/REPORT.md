# Rapport de validation — spécialiste vision, Issue #6

Travail Codex isolé sur `agent/codex/6-vision-specialist`, PR #7 vers
`apex/integration`. Base `bd2edd285ad2f796991ce5460715cb4ee9f3f06c`.
Ce rapport distingue exécution réelle, tests avec doubles et qualification
matérielle. Aucun entraînement de modèle APEX n'est revendiqué.

Le prototype effectue une inférence image réellement locale. Les 46 contrôles
contrat/OCR passent ; les quatre contrôles préentraînés donnent deux PASS
minimaux et deux FAIL. La qualité pédagogique reste **NON QUALIFIÉE** et le
raccordement central attend la revue du contrat avec Claude. L'installation,
le frontend et l'API/SQLite sont vérifiés ; la configuration cloud est sauvée
en brouillon, sans publication ni restauration indépendante revendiquées.

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
- Verrou OS entre spécialistes et refus d'un cœur actif annoncé via les chemins
  publics de découverte ou du profil standalone ; un chemin de verrou
  personnalisé ne contourne pas ces contrôles. Persistance optionnelle dans
  l'ArtifactStore existant, logs de
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

- 46 contrôles de contrat/OCR réussis, dont cinq avec le binaire Tesseract réel
  et deux vérifications de libération des ressources sur exception native.
  Les doubles des tests de contrat ne prouvent aucune qualité de modèle.
- Ruff, formatage et mypy du namespace vision réussis.
- CLI OCR seule : statut `partial`, réponse VLM absente, sortie `3`.
  Fichier corrompu : `invalid_image`, sortie `2`, aucun backend appelé.
- Les deux shards de poids, environ 7,0 GiB, sont vérifiés contre les empreintes
  LFS du Hub ; configurations/tokenizers revérifiés dans le manifeste local.
- Processeur multimodal réel vérifié sans charger un autre modèle : min/max
  200 704/802 816 pixels, fusion 2 ; 897 tokens visuels pour l'équation,
  1 008 pour le tableau et le graphique, 900 pour la page vide, sous le plafond
  configuré de 1 024.
- Installation réexécutée avec 70 contraintes de dépendances et reconstruction
  de l'interface ; fichiers centraux/lockfiles inchangés. 173 tests frontend
  réussis dans 27 fichiers.
- APEX autonome démarré par sa commande documentée : migrations SQLite,
  Doctor/Startup Gate, API authentifiée et interface compilée fonctionnelles.
  Santé API/base et création/lecture/suppression d'une conversation vérifiées ;
  accès sans token refusé par `401`. Instance arrêtée proprement avant le VLM.
  Cela ne prouve pas la génération du modèle principal, absent de cette setup.

## Qualité préentraînée : prototype non qualifié

Les images sont des fixtures synthétiques reproductibles : exercice imprimé
JPEG légèrement déformé, tableau/schéma de mesures, graphique à barres et image
vide. Elles ne sont pas des photographies prises par un utilisateur. Chaque
appel utilise les pixels réels et les poids vérifiés, en CPU/BF16, sans
service IA ; les réponses attendues ne sont jamais envoyées au modèle.

### Dernière série avec décodage borné

| Cas | Contrôle minimal | Réponse observée | Temps total / génération | Tokens générés | RSS après génération |
| --- | --- | --- | --- | --- | --- |
| Équation `2x + 3 = 11` | **FAIL** | `null`, malgré `x = 4` dans les étapes | 562 / 528 s | 444 | 8,34 GiB |
| Tableau masse/volume | PASS numérique/schéma, qualité **PARTIAL** | `3 g/cm3` | 969 / 922 s | 608 | 8,74 GiB |
| Graphique A/B | **FAIL** | `null`, malgré différence `3` dans l'analyse/aide | 645 / 624 s | 522 | 8,59 GiB |
| Image blanche via CLI | PASS abstention/schéma, texte **PARTIAL** | `null`, texte/éléments vides | 493 / 459 s | 332 | 8,22 GiB |

Bilan des quatre contrôles préentraînés : **deux PASS minimaux, deux FAIL**.
Ils s'ajoutent aux 46 contrôles de contrat/OCR réussis ; aucun bilan « tout vert »
ni taux de qualité sur documents réels n'est revendiqué.

Les sorties complètes sont dans `evidence/`, accompagnées d'un résumé des
JUnit. Les contrôles positifs vérifient identité de backend, tokens réellement
générés, contrat, types visuels concrets et réponse attendue dans `answer`.
Ils ne valident pas toutes les phrases ni la justesse universelle. La relecture
ci-dessous est donc distincte du résultat minimal de pytest.

- **Équation** : transcription et éléments titre/équation corrects ; étapes
  `2x = 11 - 3`, `2x = 8`, `x = 8 / 2`, `x = 4`. L'analyse atteint 600 caractères
  et finit au milieu d'une phrase. Le modèle invente ensuite un manque
  d'informations et renvoie `answer=null` : assertion sémantique échouée.
  Aucun `null` n'est remplacé artificiellement par le calcul des étapes.
- **Tableau/schéma** : le VLM lit `6 g` et `2 cm3`, que Tesseract n'avait pas
  extraits, et calcule correctement `3 g/cm3`. Le tableau et le rectangle sont
  décrits concrètement. Mais l'analyse est coupée, des incertitudes saturent
  leur limite, les listes contiennent les noms `help_steps`/`answer` et quelques
  caractères inappropriés. Le résultat numérique est correct ; l'aide complète
  reste partielle et ne doit pas être présentée comme qualifiée.
- **Graphique** : barres A=2 et B=5 reconnues, différence 3 explicitement écrite.
  L'extraction textuelle est vide malgré le titre/les étiquettes visibles.
  Des incertitudes de contexte inutiles conduisent à `answer=null`, donc échec.
  Analyse, description et une incertitude saturent leurs bornes.
- **Image blanche** : vraie exécution de la CLI avec code `0`, `answer=null`,
  aucun texte ni élément visuel inventé, incertitude sur l'absence d'énoncé.
  L'analyse et une incertitude sont coupées ; l'aide générique reste peu utile.
  Le JSON est réellement persisté dans l'ArtifactStore existant et relu avec
  vérification SHA-256 ; sa sortie correspond au résultat CLI.

Un statut `complete` décrit la fin du pipeline et un contrat valide, pas la
réussite de la tâche. `facts.text_budget_hits` et les limitations signalent
les champs saturés. Les défauts observés sont conservés sans masquer ni
réécrire la réponse du modèle. Ce prototype fournit une vraie lecture visuelle,
mais sa sortie pédagogique doit encore être améliorée avant raccordement
à un parcours utilisateur. Les assertions/valeurs attendues restent inchangées.

### Échecs et corrections intermédiaires

La première inférence a lu l'équation mais répondu `5`, avec une clé invalide
`description_textual` : échec après 419 secondes. Le décodage JSON contraint et
une consigne de calcul avant réponse ont ensuite donné `4` avec substitution
vérifiée (782 secondes, 363 tokens, 8,15 GiB RSS), mais des libellés visuels
recopiaient l'exemple du prompt. Celui-ci a été retiré et les types visuels
bornés ; une autre exécution a donné `4` avec titre/équation concrets en 934
secondes, 468 tokens et 8,29 GiB RSS, avec encore une ambiguïté d'unité injustifiée.
Ces réussites intermédiaires ne valident pas la version bornée actuelle.

Le tableau répétait sa description jusqu'à 768 tokens : JSON inachevé,
`invalid_output`, 1 002 secondes ; aucune réponse valide au calcul. Des bornes
par champ ont été ajoutées au décodage et à la validation stricte, avec remontée
des saturations. Le budget est passé à 1 024 tokens et le délai à trente minutes.
Le tableau produit alors un JSON complet et une réponse numérique correcte,
mais les deux échecs actuels montrent que cette correction de format ne suffit
pas à qualifier la qualité. Les preuves intermédiaires restent disponibles.

## Mesures et limites

Les faits de chaque exécution rapportent identité/empreintes des poids,
paramètres, précision, quantification réelle, tokens, temps de chargement et
génération, RSS après chargement/génération. Ces RSS sont des instantanés,
pas des pics. Sur CUDA seulement, le backend prévoit les pics alloués/réservés
PyTorch ; ils ne représentent pas toute la VRAM du système.

Le budget CPU par défaut est 12 GiB, avec choix float32/BF16 selon la mémoire
disponible et la limite cgroup. Le contexte est borné à 8 192 tokens, l'image à
1 024 tokens visuels par défaut. Le délai par défaut est trente minutes ; un
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

Le script d'installation cloud et les instructions de démarrage sont
enregistrés en brouillon, avec caches inscriptibles, installation figée,
poids immuables et sept variables non secrètes. Aucun secret IA n'est requis.
Publication des paramètres et restauration dans une nouvelle tâche ne sont
pas validées par cette session.
