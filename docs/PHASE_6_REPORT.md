# APEX — Rapport de fin de Phase 6 (partiel)

**Phase :** 6 — ModelProvider
**Version :** 0.1.2 + `apex/models/` (contrat + backend Ollama)
**Tests :** 625 exécutés, 623 réussis, 0 échec, 2 sauts explicites documentés
**Volume :** ~650 lignes ajoutées (`apex/models/`, `tests/test_models_*.py`)

Ce rapport suit le même principe que `docs/PHASE_1_REPORT.md`/`docs/PHASE_2_REPORT.md` (verrouillé n°15) : ce qui est réellement implémenté et testé, ce qui n'a pu être que structurel, ce qui reste hors périmètre. Aucun rapport antérieur n'a été modifié.

**Portée délibérément partielle.** Le plan officiel à 22 phases assigne à la phase 6 : `ModelProvider`, `Placement`, un Model Capability Gate complet, et (`docs/EXTERNAL_INTELLIGENCE.md`) un routeur externe avec disjoncteur/cache/estimation de coût. Seul un sous-ensemble a été construit ici — un `ModelProvider` réel pour un backend local (Ollama), sans routage, sans placement, sans coût. Le reste reste `PLANNED`, explicitement.

---

## 1. Réellement implémenté et testé

| Composant | Détail | Tests |
|---|---|---|
| `apex.models.model` | `GenerationRequest`, `GenerationResult`, `ModelCapabilityReport` — dataclasses pures, aucun modèle codé en dur (vérifié par test : aucun champ `model`/`served_model` par défaut) | 6 |
| `apex.models.provider` | Contrat `ModelProvider` (ABC) : `detect()`/`generate()` uniquement — `generate_stream()` délibérément HORS du contrat abstrait, méthode concrète du seul backend réel | couvert indirectement |
| `apex.models.ollama` | `OllamaProvider` — détection HTTP réelle (`GET /api/tags`), génération réelle (`POST /api/generate`), streaming réel (lecture par blocs), annulation coopérative (`threading.Event`), timeout réel | 14 |
| Registre | `ModelProvider` : `PLANNED` → `STUB` (jamais `IMPLEMENTED`) | test d'honnêteté du registre |

### Invariants réellement appliqués par le code

- `generate()`/`generate_stream()` appellent `self.detect()` avant toute tentative et lèvent `ModelProviderError` si le backend n'est pas `AVAILABLE` — jamais un résultat inventé, même patron que `LinuxNamespaceSandbox.execute()`.
- `CapabilityStatus.UNAVAILABLE_ON_DEV`, jamais `UNSUPPORTED`, quand Ollama est injoignable — testé explicitement.
- Aucun secret ni prompt/réponse sensible dans un message d'erreur (`ModelProviderError` ne porte que l'URL de l'endpoint et un résumé borné de la cause) — testé explicitement avec un prompt marqueur.
- `threading.Event` pour l'annulation, PAS `apex.sandbox.model.CancellationToken` — décision explicite (responsabilités différentes : fermer une connexion HTTP n'est pas tuer un groupe de processus noyau), testée explicitement.
- Aucune dépendance externe ajoutée : `urllib` (stdlib) suffit, cohérent avec le noyau stdlib pur (ADR-0002), aucun nouvel extra `pyproject.toml`.

---

## 2. Validé contre un serveur HTTP réel — pas seulement simulé

**Nuance importante, différente des phases 1/2** : contrairement à PostgreSQL (phase 2), aucun démon Ollama n'est disponible dans cet environnement de développement. Le serveur HTTP réel utilisé pour les tests n'est PAS Ollama — c'est un serveur `http.server` de test, réellement lié à un port local, dont les réponses imitent la forme exacte des réponses Ollama documentées. Cela prouve que ce code parle vraiment HTTP/JSON, jamais qu'il a été validé contre Ollama lui-même.

| Élément | Validé ici, réellement | Ce qui ne l'a pas été |
|---|---|---|
| `detect()` sonde un vrai port/HTTP, jamais un résultat câblé | Oui — vrai serveur HTTP local (`http.server`) | Contre un vrai démon Ollama |
| Distinction « injoignable » vs « joignable mais modèle absent » vs « disponible » | Oui, trois cas testés séparément | — |
| `generate()` — mapping des champs (texte, `model_used`, `finish_reason`, tokens) | Oui, contre les formes réelles documentées des réponses Ollama | Que ces formes correspondent exactement à un Ollama réellement installé aujourd'hui |
| Comptage de tokens honnête (`Measurement.REAL`/`UNKNOWN`) | Oui | Valeurs réellement produites par un vrai modèle |
| Timeout réel | Oui (serveur de test qui ne répond pas) | — |
| Annulation coopérative (avant l'appel, en cours de flux) | Oui | — |
| `generate_stream()` — fragments réels, ordre préservé, arrêt sur annulation | Oui | Comportement du streaming réel d'Ollama |
| Aucune fuite de prompt/secret dans une erreur | Oui, testé avec un marqueur | — |
| Appel réel contre un vrai Ollama | **Non** | Reporté explicitement — voir §5 |

**Première action à effectuer sur TARGET_MACHINE (ou tout environnement avec Ollama réellement installé)** : rejouer `tests/test_models_ollama.py::TestRealOllamaIntegration` (actuellement sauté, `@unittest.skipUnless`) pour confirmer que le mapping des champs tient contre un vrai serveur, et étendre ce test si un écart réel apparaît.

---

## 3. Stubs, abstractions et périmètre délibérément non couvert

`ModelProvider` : `PLANNED` → `STUB`. Aucune autre abstraction du registre n'a changé de statut à cette étape.

**Explicitement hors périmètre, tous encore `PLANNED`** : `Placement` (résolution rôle → endpoint — l'appelant fournit toujours un `ModelEndpoint` déjà résolu), `EmbeddingProvider`, `TaskBank`, `EvalRunner`, `OracleProvider`, Model Capability Gate complet (mesure tokens/s), routeur externe/disjoncteur/cache/`estimate_cost` (`docs/EXTERNAL_INTELLIGENCE.md`).

**Aucun raccordement à `MissionEngine`/`AgentRuntime`.** `apex.missions.workloads.research_diagnostic_command()` reste le seul « travail » qu'une mission puisse exécuter — `ModelProvider` existe comme brique isolée et testée, mais rien ne l'appelle depuis l'orchestration de missions. C'est une décision de périmètre explicite (arbitrage humain), pas un oubli : le raccordement est un choix architectural séparé, à valider indépendamment (même discipline que Scheduler↔API, étape 11).

---

## 4. Tests

```
Ran 625 tests — 623 OK, 2 sauts explicites (HELDOUT documenté ; Ollama réel absent)
```

| Fichier | Tests | Portée |
|---|---|---|
| `tests/test_models_model.py` | 6 | Dataclasses pures ; aucun modèle codé en dur |
| `tests/test_models_ollama.py` | 14 + 1 saut | Détection/génération/streaming contre un vrai serveur HTTP de test ; 1 test contre un vrai Ollama, **sauté explicitement** ici, jamais simulé comme passant |

Aucun des 603 tests précédents (baseline étapes 1-11) n'a été modifié ni supprimé. Aucun FAIL masqué.

---

## 5. Risques connus à l'issue de cette étape

| Risque | Gravité | Statut |
|---|---|---|
| Aucun appel réel contre un vrai Ollama | Moyenne | Reporté explicitement — TARGET_MACHINE ou installation autorisée requise |
| Absence de GPU/Ollama sur cette machine de développement | — | **Limitation d'environnement, pas une erreur de code.** `GATE-16` (outils requis) reste `DEGRADED`, honnête, jamais transformé en `PASS` ou contourné. L'architecture (`ModelProvider`, injection de l'endpoint) reste conçue pour fonctionner aussi bien en CPU-only que sur GPU — rien dans le contrat ne suppose un GPU présent |
| Champs de réponse Ollama non couverts par les tests actuels (au-delà de `response`/`model`/`done`/`*_count`/`*_duration`/`done_reason`) | Faible | À étendre si un besoin réel apparaît, pas anticipé |
| Streaming non généralisé au contrat abstrait | Faible, assumé | Décision explicite : généraliser seulement si plusieurs backends en ont réellement besoin |

---

## 6. Porte qualité — verdict

| Critère | État |
|---|---|
| Tests unitaires (dataclasses) | ✅ 6/6 |
| Tests structurels (serveur HTTP de test) | ✅ 14/14 |
| Test d'intégration Ollama réel | ⚠️ Sauté explicitement — Ollama absent sur cette machine, jamais simulé |
| Registre d'abstractions honnête | ✅ `ModelProvider` = `STUB`, jamais `IMPLEMENTED` |
| Aucune fuite de secret/prompt | ✅ testé explicitement |
| Documentation | ✅ notes de registre, ce rapport |
| Régression | ✅ 603 tests précédents inchangés, tous verts |
| Gate matériel/environnement | GATE-06 (Docker) et GATE-14 (cgroups v2) restent `FAIL`, **inchangés par cette étape** — sans rapport avec `ModelProvider` |
| Appel Ollama réel validé | ❌ non exercé sur cette machine (voir §2, §5) |

**Verdict : le sous-ensemble de la phase 6 explicitement dans le périmètre (`ModelProvider` pour un backend local, contrat + `OllamaProvider`) est implémenté et testé structurellement — pas encore validé contre un Ollama réel.** Aucun raccordement à `MissionEngine`/`AgentRuntime` ; `Placement`, `EmbeddingProvider`, `TaskBank`, `EvalRunner`, `OracleProvider` restent intégralement `PLANNED`. La validation contre un vrai Ollama reste le seul point à confirmer sur la machine cible avant de considérer `ModelProvider` au-delà de `STUB`.
