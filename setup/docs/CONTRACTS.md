# Setup Core — contrats

Documentation des types de `ApexSetup.Core` (Phase 1 — « Setup Core
Contracts »). Rien ici n'est un GUI ni un `Setup.exe` : c'est le moteur
testable, indépendant, qui communique avec le Runtime exclusivement via
sous-processus → CLI → JSON.

## Frontière Setup ↔ Runtime

`RuntimeAdapter` est le SEUL point de contact avec le Runtime. Il invoque
`<baseArguments> {version|doctor|gate} --json` via `IRuntimeProcessRunner`
et retourne un `RuntimeCallResult<T>` fortement typé — jamais un import
Python, jamais une lecture de fichier interne au Runtime, jamais une
interprétation du rendu texte humain (`apex doctor` sans `--json`).

En production, `baseArguments`/`executable` pointeront vers
`wsl.exe -- apex` (ou équivalent) ; en test, ils pointent vers
`.venv/bin/python3 -m apex.cli.main` du dépôt DEV — le même code
`RuntimeAdapter`/`RuntimeProcessRunner` fonctionne dans les deux cas,
seule la commande de base change.

## Contrats consommés (existants, non modifiés)

| Contrat C# | Commande Runtime | Champs obligatoires clés | Versionnage |
|---|---|---|---|
| `RuntimeVersionResult` | `apex version --json` | version, phase, phase_name, python, platform, executable | aucun schema_version côté Runtime — stable depuis toujours |
| `RuntimeDoctorResult` | `apex doctor --json` | status, counts, duration_ms, results | `schema_version`/`apex_version`/`generated_at` OPTIONNELS (absents avant l'enrichissement Phase 1 du contrat côté DEV) |
| `RuntimeGateResult` | `apex gate --json` | schema_version, apex_version, generated_at, gate_status, phase_2_authorized, fingerprint, counts, capabilities, hardware, blocking, unresolved, missing_dependencies, checks | `schema_version` toujours présent, contrat mature |

Aucune nouvelle commande Runtime n'a été créée pour cette phase — ces trois
contrats suffisent (voir réserve explicite du 04/09 : ne pas créer
`apex config --json` sans besoin démontré).

## Policy de versionnage (miroir de docs/JSON_CONTRACTS.md côté DEV)

`RuntimeSchemaPolicy.IsKnown(observed, known)` : un `schema_version`
observé **supérieur** à celui connu de ce Setup (`KnownDoctorSchemaVersion`
= 1, `KnownGateSchemaVersion` = 1, compteurs indépendants) fait échouer le
parsing avec `RuntimeCallOutcome.IncompatibleSchemaVersion` — jamais une
tentative de décision automatique sur un contrat inconnu.

## Policy des champs inconnus

**Ignorés.** Un JSON contenant une clé que ce Setup ne connaît pas est
accepté normalement — seuls les champs CONNUS et absents, ou de type
inattendu, ou de valeur d'énumération inconnue font échouer le parsing
(`MissingRequiredField`/`UnexpectedFieldType`/`InvalidEnumValue`). Même
règle que documentée côté Runtime : « un consommateur correctement écrit
ignore les clés qu'il ne connaît pas ».

## Classification des issues d'appel (`RuntimeCallOutcome`)

| Valeur | Signification |
|---|---|
| `Success` | JSON reçu, structurellement valide, version connue |
| `ProcessNotFound` | l'exécutable/l'interpréteur n'a pas pu démarrer |
| `Timeout` | pas de réponse dans le délai imparti — processus tué |
| `NonZeroExitCode` | code de sortie non nul ET stdout vide (un exit code non nul avec un JSON valide, ex. Doctor FAIL, est un `Success` — l'état mesuré fait partie du contrat, pas une erreur de communication) |
| `EmptyStdout` | rien reçu malgré un exit code de succès |
| `InvalidJson` | sortie non-JSON |
| `IncompatibleSchemaVersion` | `schema_version` supérieur à celui connu |
| `MissingRequiredField` | un champ obligatoire du contrat est absent |
| `UnexpectedFieldType` | un champ existe mais avec un type incompatible |
| `InvalidEnumValue` | une valeur de vocabulaire fermé (`severity`/`gate_status`/...) inconnue |

## Vocabulaires — jamais confondus

- Doctor `severity` : `pass`/`warn`/`fail`/`skip` (minuscules).
- Gate `gate_status`/`checks[].status` : `PASS`/`FAIL`/`DEGRADED`/`UNKNOWN`/`SIMULATED` (majuscules).

Ces deux vocabulaires ne sont jamais mappés l'un vers l'autre — Doctor et
Gate ne mesurent pas la même chose (même principe que côté Runtime).

## Hardware Profiles — recommandation, jamais autorité

`HardwareProfileEngine.SelectRecommendedProfile` ne fait jamais autorité
seul. Seul `RuntimeGateResult.Phase2Authorized`/`Status` (mesuré par le
Runtime) décide de ce qui est réellement supporté. Les 4 profils
(`HardwareProfileCatalog`) sont construits UNIQUEMENT à partir des
candidats déjà déclarés dans `config.yaml` du dépôt DEV — traçabilité
documentée dans le fichier source lui-même, aucun modèle inventé.

## Manifeste modèle (nouveau, jamais un champ du Runtime)

`ModelManifestEntry`/`ModelManifestParser` définissent un format séparé,
jamais une modification de `apex.config.schema.ModelRef`. Sélection
déterministe : `ModelCompatibilityChecker.Check` vérifie profil supporté +
VRAM/RAM suffisantes, sans jamais choisir « le plus gros modèle possible ».
