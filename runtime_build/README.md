# `runtime_build/` — construction de l'artefact APEX Runtime

Outillage de **sélection**, jamais de destruction : APEX DEV reste complet
et inchangé. Ce paquet construit, à partir d'un commit Git précis, une
copie filtrée du dépôt destinée à être distribuée séparément (le futur
« APEX Runtime »).

## Fichiers

- `selection.py` — source unique de vérité : `RUNTIME_PACKAGES` (26
  sous-paquets/fichiers de `apex/`), `RESEARCH_PACKAGES` (15 sous-paquets
  exclus), et `verify_partition_matches_repo()` qui échoue si un nouveau
  sous-paquet de `apex/` est ajouté sans être classé ici.
- `build.py` — construit l'artefact : `git archive <commit>` (jamais une
  copie du répertoire de travail, jamais de fichier non committé) puis
  copie filtrée selon `selection.py`. Usage :
  `python -m runtime_build.build --commit HEAD --out dist/apex-runtime`
- `verify.py` — contrôle anti-fuite (statique + dynamique, voir sa
  docstring). Usage : `python -m runtime_build.verify dist/apex-runtime`
- `pyproject.runtime.toml` — `pyproject.toml` de l'artefact, copié tel
  quel, jamais généré depuis celui de DEV.

## Décisions d'architecture de cette phase

### 1. Migrations Alembic : une seule chaîne officielle

Le Runtime utilise la chaîne de migrations existante (`migrations/`)
**intégralement et dans son ordre officiel** — aucune seconde chaîne
Runtime, aucune migration retirée. Certaines migrations créent des tables
aujourd'hui liées à la recherche (`skills`, `genome_versions`, `tasks`,
`eval_runs`) et sont entrelacées avec les migrations Runtime dans
l'historique — les en extraire romprait la chaîne officielle pour un
bénéfice cosmétique.

**Conséquence assumée** : un schéma Postgres Runtime contient ces tables,
vides et non utilisées. La présence d'une table dans le schéma **n'implique
jamais** que la fonctionnalité correspondante est disponible côté Runtime —
`runtime_build/verify.py` le prouve empiriquement (aucun module recherche
n'est importable depuis l'artefact, indépendamment du schéma DB appliqué) et
`tests/test_runtime_packaging.py::test_migrations_never_import_a_research_module`
vérifie que le code des migrations lui-même ne réfère aucun module
recherche.

Une séparation physique des schémas/bases est explicitement **hors
périmètre** de cette phase.

### 2. `artifacts/` et `memory/` : `runtime-capable / currently unused`

Conservés dans le Runtime, ni supprimés ni déplacés vers Research :
- `apex.artifacts` n'est aujourd'hui importé que par `apex.genome`
  (recherche) — mais son code (stockage adressé par hash, backends
  filesystem/MinIO) est générique et sans dépendance recherche propre.
- `apex.memory` n'est importé par aucun module, recherche ou runtime — un
  paquet orphelin, prêt pour un futur câblage RAG.

Aucune route API, aucun câblage `missions`/`sandbox`/`agents` ne les invoque
aujourd'hui (vérifié par
`tests/test_runtime_packaging.py::test_artifacts_and_memory_packages_are_present_but_unwired_in_api`).
Leur présence dans le paquet ne doit jamais être interprétée comme une
activation de fonctionnalité — c'est une réservation d'espace pour un usage
futur, pas une intégration nouvelle introduite par cette phase.

## Limites connues (constatées, pas contournées)

- **`frontend/dist` n'est jamais inclus.** `frontend/.gitignore` exclut
  `dist/` du suivi Git — `git archive` (utilisé par `build.py` pour la
  reproductibilité depuis un commit précis) ne le voit donc jamais, quel
  que soit l'état du répertoire de travail au moment du build. Embarquer un
  bundle frontend fraîchement construit exigera une étape séparée (build
  frontend + copie hors `git archive`), hors périmètre de cette phase.
- **`build_runtime("HEAD", ...)` reflète le dernier COMMIT, jamais le
  répertoire de travail.** Au moment de cette implémentation, des travaux
  validés des phases précédentes (authentification API, enrichissement du
  contrat JSON Doctor) sont encore non committés — un build depuis `HEAD`
  ne les contient donc pas encore. `tests/test_runtime_packaging.py` le
  documente explicitement plutôt que de le supposer résolu.

## Ce qui n'est PAS fait dans cette phase

Pas de `Setup.exe`, pas d'interface graphique, pas d'installation
automatique de Docker/WSL2/Ollama, pas de téléchargement de modèle, aucun
changement de seuil hardware ni de contrat JSON Doctor/Gate. Ce paquet
produit un répertoire filtré sur disque — l'installation, la configuration
utilisateur et l'orchestration Docker/WSL2/Ollama restent un futur « APEX
Setup », non commencé ici.
