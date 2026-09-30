# MinIO — analyse d'impact (aucune modification effectuée)

Date : 2026-09-30. Statut : **DÉCISION REQUISE** (propriétaire du projet). Rien n'a été remplacé, migré,
supprimé, et `docker-compose.yml` n'est pas modifié.

## 1. Constat

`docker-compose.yml` déclare `image: minio/minio:latest`. Mesuré le 30/09/2026 :

| Source | Résultat | Où |
|---|---|---|
| `minio/minio:latest` (Docker Hub) | `pull access denied … repository does not exist` | runner GitHub Linux et Windows Server + WSL2 (runs 4 et 6), conteneur de développement |
| `minio/minio:RELEASE.2025-04-22T22-12-26Z` | même refus : le dépôt entier n'existe plus | runner GitHub Linux (run 6) |
| `quay.io/minio/minio` (`latest` et deux tags datés) | `unauthorized: access to the requested resource is not authorized` | runner GitHub Linux (run 6) |
| sources officielles `github.com/minio/minio` et `github.com/minio/mc` | disponibles (compilation Go) | runner GitHub Windows Server + WSL2 (run 8, en cours au moment de l'écriture) |

Conclusion : c'est un problème de **distribution de l'image**, pas une incompatibilité d'APEX. Le code
source de MinIO reste public ; seules les images prêtes à l'emploi ne sont plus téléchargeables.
Une machine qui possède déjà `minio/minio:latest` dans son cache Docker démarre ; une machine neuve
(ou après `docker image prune`) ne démarre plus.

## 2. Tous les usages de MinIO dans APEX

| Où | Usage | Nature |
|---|---|---|
| `docker-compose.yml`, service `minio` | conteneur `apex-minio`, ports 127.0.0.1:9000/9001, volume nommé `minio-data`, healthcheck `mc ready local`, limites mémoire/CPU | infrastructure démarrée par `launch.sh` |
| `config.yaml` : `services.minio {host 127.0.0.1, port 9000, required: true}` | déclaration du service | **obligatoire** pour `apex doctor` |
| `apex/doctor/checks/services.py` : `service.minio` | sonde TCP 9000 ; `required: true` ⇒ **FAIL** si injoignable | bloque `launch.sh` (code 4) |
| `apex/doctor/repair.py` | `service.minio` fait partie des services réparables par Compose | réparation |
| `scripts/apex-launch.sh` | `docker compose up -d --wait` sur tous les services du fichier | bloque `launch.sh` (code 2) si l'image manque |
| `apex/artifacts/minio_backend.py` : `MinioArtifactStore` | `detect()` = sonde TCP ; **`put`/`get`/`get_metadata`/`exists` lèvent `NotImplementedYet`** (fallback `LocalFilesystemArtifactStore`) | **PLANNED** : aucune donnée n'est lue ni écrite |
| `apex/interfaces/registry.py` | ArtifactStore « MinIO local » : **STUB** délibéré (étape 10) | documentation d'état |
| `.env` / `.env.example` | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` (obligatoire pour Compose), `APEX_MINIO_MEM/CPUS` | configuration |
| Gate (`apex/gate`), API (`/api/v1/status`), Panel | **aucun** usage de MinIO | — |
| scripts d'apprentissage (`apex_autonomous_loop.py`, `transfer_ladder.py`), tests | `LocalFilesystemArtifactStore` uniquement | — |

## 3. Données, buckets et artefacts qui peuvent exister

- **Écrits par APEX : aucun.** Aucune ligne de code APEX ne crée de bucket ni n'écrit dans MinIO
  (les opérations de stockage lèvent `NotImplementedYet` ; aucun client S3 n'est une dépendance).
- **Possibles quand même** dans le volume `<projet>_minio-data` : les métadonnées internes de MinIO
  (`.minio.sys`), et tout ce qu'un humain aurait créé à la main via la console (port 9001) ou `mc`.
  **Non vérifié sur votre PC** : le script de validation Windows relève désormais, en lecture seule,
  la présence de l'image dans le cache, le volume, sa taille et la liste de ses dossiers de premier
  niveau (= buckets), sans rien modifier.
- Artefacts réels d'APEX : dans les répertoires `artifacts/` du `LocalFilesystemArtifactStore`
  (système de fichiers), jamais dans MinIO.

## 4. Ce qui échoue quand MinIO est absent

| Composant | Effet | Preuve |
|---|---|---|
| `launch.sh serve` | `docker compose up -d --wait` échoue (image introuvable) ⇒ code 2, backend jamais démarré | run 4 (Windows Server + WSL2 réel) |
| `apex doctor` | `service.minio` FAIL (`required: true`) ⇒ `launch.sh` refuse de servir (code 4), même si l'image existait mais le conteneur était arrêté | code |
| API, Gate, Panel, apprentissage | aucun effet fonctionnel | code |

**MinIO est donc obligatoire AU DÉMARRAGE (Compose + doctor) mais n'est utilisé par AUCUNE fonction.**

## 5. Options (pour chacune : impact, risques, données, tests)

### OPTION A — Conserver MinIO tel quel, réparer l'obtention de l'image
Construire localement `minio/minio:latest` depuis les sources officielles (script fourni, identique
à ce que fait la CI), sans changer `docker-compose.yml`.
- Impact : aucun changement de configuration APEX ; ~5 à 10 min de compilation la première fois ;
  image Go (~1 Go) téléchargée pour construire.
- Risques : version non figée (`@latest` des sources) ; compilation à refaire sur chaque machine neuve ;
  licence AGPL (usage local, sans redistribution : sans conséquence) ; rien ne garantit que les
  sources resteront publiques.
- Données : format identique (même produit) ; le volume existant reste compatible.
- Tests : `launch.sh serve` complet sur machine neuve ; healthcheck `mc ready local` ; `apex doctor`
  PASS ; volume existant monté intact.
- Sur une machine qui a déjà l'image en cache : rien à faire, mais ne jamais la supprimer.

### OPTION B — Remplacer l'image par une source maintenue
B1 : même produit, autre distribution (image communautaire reconstruite, ou image « MinIO »
d'un fournisseur tiers). B2 : autre serveur compatible S3 (SeaweedFS, Garage, …).
- Impact : `docker-compose.yml` (image, commande, healthcheck — `mc` absent des alternatives) ;
  B2 : variables d'environnement différentes.
- Risques : B1 dépend d'un tiers (confiance, durée de vie) ; B2 : **format disque différent — le
  volume `minio-data` existant ne serait pas lisible** (migration `mc mirror` nécessaire s'il contient
  des données humaines).
- Données : aucune donnée APEX ; données manuelles éventuelles à migrer avant tout changement.
- Tests : idem A + test de compatibilité S3 (création de bucket, put/get) + test de migration à blanc.

### OPTION C — Rendre la dépendance facultative (elle n'est utilisée par aucune fonction)
Placer le service `minio` dans un profil Compose facultatif (`profiles: ["artifacts-s3"]`) et
`required: false` dans `config.yaml` : `launch.sh` démarre postgres + redis ; MinIO reste
disponible à la demande ; `MinioArtifactStore` reste PLANNED.
- Impact : 2 fichiers (`docker-compose.yml`, `config.yaml`) + docs ; `apex doctor` passe de FAIL à
  WARN quand MinIO est absent ; aucun code applicatif touché.
- Risques : s'écarte de l'intention d'architecture (principe 5 : ArtifactStore S3 pour des workers
  distants) — réversible en réactivant le profil ; si des données humaines existent dans le volume,
  elles restent intactes mais ne sont plus servies par défaut.
- Données : **aucune modification** ; le volume nommé n'est jamais supprimé (Compose ne supprime pas
  un volume d'un service devenu facultatif).
- Tests : `launch.sh serve` sur machine neuve sans image MinIO ⇒ READY/DEGRADED ; `apex doctor` WARN
  (pas FAIL) ; `--profile artifacts-s3` démarre encore MinIO si l'image existe ; volume intact ;
  suites pytest et .NET.

## 6. Recommandation (à valider — rien n'est appliqué)

C à court terme (débloque toute machine neuve sans toucher aux données ni au code applicatif),
combinée à A pour qui veut conserver MinIO disponible. B2 seulement le jour où `MinioArtifactStore`
sera réellement implémenté, avec migration testée à blanc.

## 7. Déjà en place (CI uniquement, à conserver ou retirer selon votre décision)

`setup/packaging/windows/ci/prepare_wsl_backend.sh` construit `minio/minio:latest` depuis les sources
officielles **dans la distribution WSL jetable du runner GitHub**, pour exercer le vrai `launch.sh`
dans la CI. Rien dans APEX (compose, config, données) n'est changé ; aucun effet sur votre PC.
