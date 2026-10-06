# Validation réelle d'APEX avec un vrai modèle — 06/10/2026

Base : HEAD `2fe10d6`. Le protocole v2 (`docs/PROTOCOLE_V2.md`) n'est pas modifié ; les expériences ci-dessous en
sont distinctes.

## 1. Cartographie avant modification (audit factuel)

| Fonction | Fichier | État constaté | Dépendances | Blocage | Preuve existante |
|---|---|---|---|---|---|
| Contrat `ModelProvider` (`detect`, `generate`) | `apex/models/provider.py` | réel, minimal ; `GenerationRequest` sans graine | — | aucun | tests de contrat |
| Backend Ollama | `apex/models/ollama.py` | réel | serveur Ollama (HTTP) | **exclu comme cible** | runs CI Windows (WSL, 1.5B) |
| Backend OpenAI-compatible | `apex/models/openai_compatible.py` | réel | serveur HTTP externe ou local | serveur externe obligatoire | tests contre serveur HTTP de test |
| Backend natif en processus | `apex/models/backends.py` (`transformers`) | **PLANNED** — aucun code | — | **aucun vrai modèle exécutable sans serveur** | aucune |
| Configuration des modèles | `config.yaml` (`model_roles`, `endpoints`) | **tous les endpoints sont `runtime: ollama`** | Ollama | sans Ollama, aucun candidat physiquement disponible → TaskLoop `BLOCKED` (`provider.selection.failed`), Chat sans modèle | — |
| Appariement rôle ↔ endpoint | `apex/models/registry.py::resolve_candidates` | heuristique « `served_model` commence par la famille » | — | **défaut** : un endpoint `qwen2.5-coder` 1.5B serait apparié aux références 7B de la même famille | — |
| Utilisateur → API/UI → Task/Mission | `apex/api/routes/task_runs.py`, `missions.py`, `frontend/src/pages/TaskRunsPage.tsx` | réel | API, base | aucun | tests API + vitest |
| PLAN | `apex/taskloop/loop.py::_generate_plan` | réel, optionnel | modèle | dépend du modèle | doublure seulement |
| Mémoire/RAG | `apex/memory/{retrieval,indexing,service}.py` | réel, BM25 | base | aucun | doublure seulement |
| Sélection du modèle | `apex/routing/{availability,router}.py` | réel (sonde `detect()`) | backends | voir config | tests |
| Outils | `apex/tools/*` | réel | sandbox | aucun | attaques testées avec du vrai code |
| Sandbox | `apex/sandbox/{linux_namespace,container}.py` | réel ; ici `LinuxNamespaceSandbox` | Linux (namespaces/cgroups) ou Docker | Windows : uniquement via WSL | tests réels |
| Judge | `apex/judge`, `apex/oracles/result_contract.py` | réel, cas cachés hors sandbox | — | aucun | tests réels |
| Experience / retry / adaptation | `apex/taskloop/loop.py`, `apex/experience/*` | réel | base | dépend du modèle | doublure seulement |
| Runtime packagé | `runtime_build/` | contient `apex.models`, `taskloop`, `tools`, `memory` | extras `db,api` | **aucune dépendance d'inférence native déclarée** | 41/41 packaging |

Environnement de CETTE session : 4 CPU, 15 Go de RAM, **aucun GPU**, 6,5 Go de disque libre, `torch` 2.14 installé,
`transformers` absent ; **PyPI accessible, Hugging Face bloqué (403 du proxy)** : aucun poids de modèle n'est
téléchargeable ici. Conséquence : le backend natif est construit et testé ici sur un modèle construit localement
(vrais calculs, poids non entraînés — jamais présenté comme une capacité), et **les mesures avec un vrai modèle
pré-entraîné sont exécutées par GitHub Actions** (runner Linux, CPU, accès Hugging Face), sans Ollama.

Windows/WSL : APEX tourne dans WSL2 (la sandbox exige Linux) ; le backend natif y fonctionne comme sous Linux
(CPU ; CUDA seulement si une roue `torch` CUDA et un GPU exposé à WSL sont présents — non vérifié ici).

Dépendances résiduelles à Ollama trouvées : `config.yaml` (seuls endpoints), contrôles Doctor/Gate (déclarés
`required: false`), backend `OllamaProvider` (conservé comme backend optionnel, jamais requis).

## 2. Protocole de mesure — PRÉ-ENREGISTRÉ (écrit avant tout résultat, commit faisant foi)

Modèle : `Qwen/Qwen2.5-Coder-1.5B-Instruct` (poids publics, révision résolue enregistrée avec chaque résultat),
exécuté par le backend NATIF (CPU du runner Linux GitHub, float32). Échantillonnage : configuration de génération
du modèle (température 0,7, top-p 0,8, top-k 20), graine par appel `(instance, n-ième génération)` : à prompt
identique, deux bras tirent la même réponse (nombres aléatoires communs). Une base neuve par bras (adaptation) ou
par instance (mémoire). Ce sont des preuves **CI sur un modèle 1.5B**, jamais celles de la machine cible ni d'un 7B.

### 2.1 Boucle réelle (smoke)
Critère : chaque maillon observé dans ce qui est persisté — génération par le modèle, plan rédigé, actions JSON
décodées, outils réellement exécutés, sandbox, verdict du Judge, Experience, nouvelle tentative adaptée.

### 2.2 PLAN et adaptation
15 tâches de référence × graines {0, 1, 2, 3} = 60 instances par bras. Bras : A (1 tentative), B (PLAN + 1),
R (3 tentatives, relance à l'identique — contrôle du seul budget), C (3 tentatives, faits d'échec), D (PLAN + 3,
faits d'échec). Critère : réussite (verdict PASS du Judge). Hypothèses principales, McNemar exact bilatéral
apparié, Holm à 5 % : **H1** B vs A (plan) ; **H2** C vs R (adaptation au-delà du simple retry) ; **H3** D vs C
(plan en présence d'adaptation). Secondaires (descriptives) : R vs A, C vs A, D vs A, score moyen du Judge
(fraction de cas cachés réussis), tentatives, durées, corrections réussies (échec puis réussite), erreurs.
Règle : un effet significatif n'est affirmé qu'après **réplication** sur des graines disjointes {10, 11, 12, 13}
(même direction, p < 0,05). Un effet nul ou négatif est rapporté tel quel ; un mécanisme qui dégrade reste
désactivable (il l'est déjà par défaut).

### 2.3 Mémoire
8 paires (tâche de référence source → tâche similaire écrite pour l'occasion, valeurs attendues calculées par
une implémentation de référence) × graines {0, 1, 2} = 24 instances. RUN 1 : la source (bras C). RUN 2 : la
cible, une tentative, avec mémoire (M) puis sans (A), même graine. Critère : réussite de la cible ; McNemar exact
M vs A ; descriptif : score, durée, souvenirs réellement retrouvés (provenance), effet conditionnel à la réussite
du RUN 1.

### 2.4 Outils
5 tâches de référence × graines {0, 1}, bras C sans outils vs C outillé (10 tours) ; 3 projets multi-fichiers
(correction de bug guidée par les tests, implémentation qui exige de lire un autre fichier) ; 8 consignes
hostiles réelles données au modèle (lecture de /etc/passwd, de ../../config.yaml, écriture hors espace, réseau,
boucle infinie, sortie énorme, processus persistant, lien symbolique). Mesures : outils réellement appelés,
actions illisibles, réussite, et pour les consignes hostiles : chaque tentative du modèle et ce que le système
en a fait (refus, délai, coupure), fichier témoin hôte jamais créé.

### 2.5 PLAN corrigé — PRÉ-ENREGISTRÉ (après `real-2`, avant toute mesure de la correction)
Constat `real-2` (§3) : le modèle écrit du code dans 58 plans sur 60 malgré la consigne, et, plan réinjecté,
répond souvent par de la prose commentant ce code (5 `invalid_candidate` en B, 18 en D). Correction (voie PLAN
seulement ; A, R, C inchangés octet pour octet) : blocs de code retirés du plan avant réinjection (un plan fait
uniquement de code devient un plan échoué), consigne de format rappelée en dernier, après le plan et les faits
d'échec. Mesure `real-4` : bras A, B, C, D, graines {0, 1, 2, 3}, même protocole que §2.2. Hypothèses
(McNemar exact, Holm à 5 %) : **H1'** B vs A, **H3'** D vs C. Descriptif : `invalid_candidate`, score du Judge,
durée, comparaison à B et D de `real-2` (mêmes graines), et reproductibilité de A et C entre `real-2` et
`real-4` (même code, même graine). Règle inchangée : un effet n'est affirmé qu'après réplication (graines
{10, 11, 12, 13}) ; une correction qui n'améliore rien est rapportée « MESURÉ : aucun gain ».

## 3. Résultats MESURÉS — `real-2` (06/10/2026, run GitHub Actions 37499709079, commit `5d44296`)

Conditions : `Qwen/Qwen2.5-Coder-1.5B-Instruct` révision `2e1fd397…`, backend NATIF (`native_transformers`,
torch 2.14 CPU, float32), runner Linux 2 vCPU, sandbox `ContainerSandbox` (namespaces indisponibles sur le
runner : repli Docker, isolation vérifiée), **aucun Ollama** (le job échoue s'il en trouve un). Résultats bruts :
`validation/results/real-2/*.json`, analyse : `analysis.json`. Preuve **CI, modèle 1.5B** : ni la machine
cible, ni un 7B.

### 3.1 Boucle réelle (smoke)
| Maillon | Observé (persisté) |
|---|---|
| Génération par le modèle natif | oui — durée, tokens, vitesse (médiane 2,5 tokens/s, 39,5 s par génération) |
| Plan rédigé | oui (événement `task.plan.generated`) |
| Judge (cas cachés hors sandbox) | oui — score = fraction de cas réussis |
| Nouvelle tentative adaptée (faits d'échec) | oui (`roman_to_int` : 2 tentatives, 0,88 → arrêt `no_progress`) |
| Mode outillé, fonction | **le modèle appelle `fs.write` puis soumet ; réussite 1,00** (smoke-1 : 0 outil, soumission immédiate — corrigé, voir §4) |
| Mode outillé, projet multi-fichiers | **`fs.read` puis `fs.write` ; réussite à la 2ᵉ tentative** (912 s) |
Non observé : le modèle n'a jamais appelé `code.run` ni `tests.run` de lui-même.

### 3.2 PLAN et adaptation (15 tâches × graines 0–3 = 60 instances appariées par bras)
| Bras | Réussite | 1re tentative | Score Judge moyen | Tentatives | Corrections | Durée médiane | Erreurs (tentatives) |
|---|---|---|---|---|---|---|---|
| A — 1 tentative | 29/60 (48 %) | 29 | 0,767 | 1,00 | — | 32 s | 31 incorrectes |
| B — PLAN + 1 | 27/60 (45 %) | 27 | 0,680 | 1,00 | — | 175 s | 28 incorrectes, 5 `invalid_candidate` |
| R — 3 tentatives à l'identique | 34/60 (57 %) | 29 | 0,829 | 1,58 | 5 | 48 s | 61 incorrectes |
| C — 3 tentatives + faits d'échec | 38/60 (63 %) | 29 | 0,800 | 1,62 | 9 | 49 s | 59 incorrectes |
| D — PLAN + 3 + faits d'échec | 29/60 (48 %) | 27 | 0,657 | 1,65 | 2 | 220 s | 52 incorrectes, 18 `invalid_candidate` |

Nombres aléatoires communs vérifiés : 1re tentative **identique** en A, R, C sur 60/60 instances (et B = D
sur 60/60). Hypothèses principales (McNemar exact bilatéral, Holm 5 %) :
- **H1 B vs A (plan)** : 5 instances gagnées, 7 perdues, p = 0,77 → **MESURÉ : aucun gain** (score du Judge
  en baisse, durée × 5).
- **H2 C vs R (faits d'échec au-delà du simple retry)** : 5 gagnées, 1 perdue, p = 0,22 → **non significatif** ;
  direction favorable, à confirmer (réplication `real-3`).
- **H3 D vs C (plan avec adaptation)** : 1 gagnée, 10 perdues, p = 0,012, **significatif après Holm : le plan
  DÉGRADE** — à confirmer par la réplication pré-enregistrée (graines 10–13, `real-3`) avant d'être affirmé.
Secondaires (descriptifs) : R vs A 5–0 (p = 0,06) ; C vs A 9–0 (p = 0,004) ; D vs A 6–6. Le gain de C sur A
vient pour l'essentiel du **budget de tentatives** (R en capte 5 sur 9) ; les faits d'échec ajoutent 4
instances nettes, non significatives à ce stade.

Effet du plan par tâche (réussites sur 4 graines, A / B) : `word_count` 2 / 0 et 4 (R, C) / 0 (D), `chunk`
1 / 4, `dedupe_keep_last` 3 / 2 : effet hétérogène, globalement nul seul et négatif avec adaptation.

Cause de la dégradation (lue dans les réponses persistées) : malgré « N'écris AUCUN code », **58 plans sur
60 contiennent du code**, parfois contraire à l'énoncé (`is_palindrome` : le plan retire espaces et
ponctuation que l'énoncé impose de garder) ; réinjecté, ce code est souvent **commenté en prose** par le modèle
au lieu d'une solution (« Le script fourni semble correct… ») → `invalid_candidate`. Correction pré-enregistrée
§2.5, mesurée par `real-4`.
