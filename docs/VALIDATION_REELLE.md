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
