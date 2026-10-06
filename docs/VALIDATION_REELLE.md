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
