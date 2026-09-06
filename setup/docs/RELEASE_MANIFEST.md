# Release Manifest (Phase 7)

`ApexSetup.Release` (bibliothèque C# indépendante — ne référence NI Core
NI Application NI Cli NI Gui) construit et vérifie un manifeste JSON
décrivant UN artefact de release déjà construit.

## Schéma (`schema_version: 1`)

```json
{
  "schema_version": 1,
  "product_name": "APEX Setup",
  "setup_version": "0.1.0",
  "runtime_version": null,
  "build_commit_sha": "a1b2c3d4...",
  "artifact_file_name": "apex-setup.exe",
  "artifact_size_bytes": 67748044,
  "artifact_sha256": "3f8a...",
  "architecture": "win-x64",
  "created_at_utc": "2026-09-04T18:00:00.0000000+00:00"
}
```

- `runtime_version` : Setup n'embarque toujours aucun binaire Runtime dans
  son propre artefact (voir docs/PACKAGING.md) — mais depuis la pipeline
  Phase 8 (`packaging/release.sh`), ce champ porte le SHA Git complet du
  commit Runtime réellement construit et vérifié anti-fuite (`runtime_build`)
  dans LA MÊME exécution de pipeline, c'est-à-dire le Runtime contre lequel
  ce Setup a été testé. `null` uniquement quand le manifeste est généré hors
  de la pipeline (ex. `apex-setup-release generate` invoqué manuellement
  sans `--runtime-version`) — jamais un numéro de version inventé (le
  Runtime n'a pas de version sémantique, seulement une identité par commit).
- `build_commit_sha` : SHA Git réel du commit source (`git rev-parse
  HEAD`), **uniquement quand ce SHA reflète honnêtement le contenu
  construit** — `null` sinon, jamais approximé. Trois cas produisent
  `null`, tous constatés empiriquement lors de la première exécution
  réelle de `packaging/release.sh` (Phase 8) plutôt que supposés :
  1. le répertoire de travail n'est pas un dépôt Git ;
  2. `git status --porcelain` (dépôt entier) est non vide — HEAD ne
     reflète alors pas nécessairement ce qui a été construit ;
  3. `setup/` lui-même n'est pas suivi par Git (`git ls-files setup`
     vide) — attacher `HEAD` mentirait sur la provenance : ce commit ne
     contient même pas le code source de l'artefact produit.
  À ce stade du projet, `setup/` est entièrement non committé (cas 3) et
  le dépôt est par ailleurs volontairement laissé sale par consigne
  explicite de la session (aucun commit autorisé) — `build_commit_sha` est
  donc `null` dans tout manifeste généré par cette pipeline aujourd'hui.
  Ce n'est pas un bug : c'est le champ qui refuse d'inventer une
  traçabilité qui n'existe pas encore.
- `created_at_utc` : SEUL champ explicitement non déterministe — deux
  builds du même contenu à deux instants différents ne peuvent jamais
  produire le même manifeste bit-à-bit, et ce n'est pas l'objectif
  (`artifact_sha256`/`artifact_size_bytes`, eux, sont déterministes pour
  un artefact identique).

## Vérification (`ReleaseManifestValidator.ValidateAsync`)

Compare le manifeste à l'artefact RÉEL sur disque — jamais une confiance
dans le contenu déclaré. Rejette explicitement (voir
`ReleaseValidationOutcome`, 8 valeurs + `Valid`) :

| Cas | Outcome |
|---|---|
| Manifeste absent/illisible/JSON invalide | `ManifestUnreadable` |
| `schema_version` plus récent que connu | `UnsupportedSchemaVersion` |
| Architecture déclarée ≠ attendue | `ArchitectureMismatch` |
| `setup_version` déclarée ≠ attendue | `VersionMismatch` |
| `artifact_file_name` n'est pas un nom de fichier simple (traversée, chemin absolu, UNC) | `UnsafeArtifactFileName` |
| Fichier artefact absent du répertoire | `ArtifactMissing` |
| Taille réelle ≠ manifeste | `SizeMismatch` |
| SHA-256 réel ≠ manifeste (artefact modifié après coup) | `HashMismatch` |
| Tout correspond | `Valid` |

Testé (`ApexSetup.Release.Tests`, 18 tests) : chacun des 9 cas de rejet
ci-dessus, plus le round-trip JSON, le calcul réel de hash/taille, et
l'absence de toute clé ressemblant à un secret dans le JSON produit.

### `UnsafeArtifactFileName` — trouvé en revue adversariale (Phase 10)

`ValidateAsync` combinait `artifactDirectory` et `manifest.ArtifactFileName`
via un `Path.Combine` NU. Reproduit réellement : un manifeste manipulé avec
`"artifact_file_name": "../secret/private.txt"` (hash/taille recalculés
pour correspondre à ce fichier voisin) faisait répondre `apex-setup-release
validate` par `Valid`, exit 0 — validant un fichier entièrement HORS du
répertoire de distribution attendu. Un chemin absolu
(`/etc/passwd`, `C:\Windows\System32\...`) ou UNC (`\\hôte\partage\...`)
produisait le même contournement. Exactement le défaut déjà documenté et
corrigé côté Core par `Security.PathSafety.SafeCombine`
(`DownloadStage`/`InstallationStage`) — mais jamais porté ici, puisque
`ApexSetup.Release` ne référence délibérément pas `ApexSetup.Core`.
Corrigé par une réimplémentation locale minimale du même principe
(nom de fichier simple, aucun séparateur, jamais absolu/UNC) plus une
vérification a posteriori que le chemin résolu reste bien à l'intérieur du
répertoire attendu — même défense en profondeur à deux niveaux que
`PathSafety.SafeCombine`. Voir
`ValidateAsync_rejects_a_manifest_whose_artifact_file_name_escapes_the_directory_via_dot_dot`
et `ValidateAsync_rejects_an_absolute_or_composed_artifact_file_name`.

## `ApexSetup.Release.Cli` (Phase 8)

`ApexSetup.Release` est une bibliothèque pure, sans point d'entrée.
`ApexSetup.Release.Cli` (assembly `apex-setup-release`) lui donne un point
d'entrée réel pour l'orchestration en pipeline (`packaging/release.sh`) et
l'usage manuel :

```
apex-setup-release generate --artifact <chemin> --out <manifest.json> \
  --product-name <nom> --setup-version <version> --architecture <win-x64|...> \
  [--runtime-version <version>] [--commit-sha <sha>]

apex-setup-release validate --manifest <manifest.json> --artifact-dir <répertoire> \
  --architecture <win-x64|...> --setup-version <version>
```

Codes de sortie : `0` succès/manifeste valide, `1` manifeste invalide ou
artefact modifié (jamais silencieux), `2` usage invalide. Testé
(`ApexSetup.Release.Cli.Tests`, 8 tests) en lançant le VRAI binaire en
sous-processus (même discipline que `ApexSetup.Cli.Tests`) : round-trip
generate→validate, options optionnelles, détection réelle de falsification
(`HashMismatch`), manifeste absent, arguments manquants, artefact manquant,
commande inconnue, aucun argument.

## Pipeline de release (`packaging/release.sh`, Phase 8)

Chaîne complète, dans l'ordre, chacune bloquant la suivante en cas
d'échec (`set -euo pipefail`) :

1. **Source** — capture `git rev-parse HEAD` et l'état sale/propre du
   dépôt ; décide honnêtement si `build_commit_sha` peut être renseigné
   (voir ci-dessus — à ce jour toujours `null`, `setup/` n'étant pas
   committé).
2. **Tests** — `dotnet test` sur la solution Setup complète, puis
   `pytest` sur la suite Runtime complète. Un échec ici arrête tout.
3. **Sécurité (pré-build)** — `packaging/security_scan.sh source` sur
   `src/`, `tests/`, `packaging/`, `docs/` (voir ci-dessous).
4. **Build Runtime** — invoque `python -m runtime_build.build --source
   commit --commit <HEAD>` **tel quel, sans aucune modification** : mode
   RELEASE (`git archive` sur un SHA résolu), jamais le répertoire de
   travail.
5. **Anti-fuite** — invoque `python -m runtime_build.verify` **tel
   quel** sur l'artefact Runtime produit.
6. **Reproductibilité** — vérifiée EMPIRIQUEMENT, jamais affirmée :
   - Runtime : deux builds indépendants depuis le même commit, comparaison
     de `content_hash_sha256` — reproductible par construction
     (`git archive` déterministe), confirmé à chaque exécution.
   - Setup (.NET) : deux `dotnet publish` win-x64 self-contained
     indépendants, comparaison SHA-256 du binaire CLI produit. Constaté
     **reproductible bit-à-bit** dans cet environnement lors des
     exécutions réelles de cette pipeline — mais documenté comme un
     constat empirique de CETTE chaîne d'outils (.NET 8 SDK figé, mêmes
     restaurations NuGet), jamais une garantie générale : `dotnet
     publish` self-contained single-file n'a aucune garantie de
     reproductibilité documentée par Microsoft (métadonnées de bundler,
     horodatages PE) et un résultat différent sur une autre machine/version
     de SDK ne serait pas une régression de ce projet.
7. **Build Setup + hash + manifeste** — `packaging/publish.sh` (Phase 6,
   inchangé) puis `apex-setup-release generate` pour CHAQUE artefact
   (`cli/apex-setup.exe`, `gui/apex-setup-gui.exe` — un manifeste par
   artefact, le schéma Phase 7 ne décrivant qu'un seul artefact à la
   fois), avec `--runtime-version` = le commit Runtime réellement
   construit à l'étape 4.
8. **Validation finale** — `apex-setup-release validate` sur chaque
   manifeste généré ; `security_scan.sh artifact` sur la distribution
   publiée ; absence de tout fichier `.py` et de tout fichier de
   développement (`.git*`, `appsettings.Development.json`, `*.user`)
   dans le paquet livré.

### `packaging/security_scan.sh`

Recherche par motif (clé AWS, bloc de clé privée PEM, jeton/mot de passe
générique) dans le code source (`source`) ou dans les binaires publiés via
`strings` (`artifact`). **Bug réel trouvé et corrigé lors de la première
exécution de la pipeline** : le motif PEM commence par `-----`, que
`grep -E` interprétait comme une liste d'options inconnues — chaque
invocation échouait silencieusement (erreur avalée par `|| true`),
désactivant cette détection sans qu'aucun échec ne soit visible. Corrigé
par `grep -e` (motif explicite) et par une distinction stricte du code de
sortie de `grep` (1 = aucune correspondance, normal ; >1 = échec réel,
jamais traité comme "aucun secret"). Régression :
`packaging/test_security_scan.sh` (5 scénarios réels : répertoire propre,
clé AWS, clé PEM — le cas qui a régressé —, jeton générique, mode
`artifact`), maintenant intégré comme étape de la pipeline elle-même.
