# Première boucle d'exécution réelle — preuves

Chaîne prouvée : TASK → TaskRun → modèle sélectionné par le routage → génération Ollama → exécution en
sandbox → résultat → Judge (Oracle, tests cachés) → Experience → persistance → redémarrage.
Point d'entrée : `POST /api/v1/tasks/{id}/runs` (`apex/taskloop/runs.py`, boucle existante
`apex.taskloop.loop.run_task_loop`) ; observation : `setup/packaging/windows/ci/first_real_loop.py`, qui ne
fait que piloter l'API réelle et relire la base.

**Deux niveaux de preuve, jamais confondus.** A (CI) ne prouve rien sur B (machine cible).

## A. Preuve CI — run 23 (commit 8281b92), Windows Server 2025 + WSL2 réel

Environnement MESURÉ : 2 processeurs, 3 913 Mo de mémoire, 1 024 Mo de swap, aucun GPU. Le modèle de
référence 7B n'y tient pas (run 22 : aucune réponse en 15 min, puis `Wsl/Service/0x8007274c`) ; la CI utilise
donc un VRAI modèle réduit, `qwen2.5-coder:1.5b-instruct`, par une configuration de déploiement dérivée de
`config.yaml` (`APEX_CONFIG`, mode « reduced » de `prepare_real_model.sh`). APEX démarré par le Panel publié
(`APEX.exe --start` → `launch.sh` → Startup Gate : `startup_authorized=True`, `phase_2_authorized=False`).

| # | Maillon | Résultat | Mesure |
|---|---|---|---|
| 1 | Task | MESURÉ | `500093ec-3226-41ef-80e0-0ac993b4391e` (is_palindrome, 12 cas cachés) |
| 2 | TaskRun | MESURÉ | `86afd3a0-4de2-4fbe-ba1e-373c71326b1e`, « running » puis `failed / no_progress` en ~30 s |
| 3 | Modèle | MESURÉ | routage : `qwen2.5-coder:1.5b-instruct:q4_k_m` (Ollama 0.35.1, sonde physique) |
| 4 | Génération | MESURÉ | 2 réponses réelles, 126 et 140 jetons ; Ollama : chargement 9,4 s, 32 jetons en 1,3 s |
| 5 | Sandbox | MESURÉ | ContainerSandbox, `python:3.12-slim`, réseau coupé, uid 65534, racine en lecture seule ; jamais HARDENED |
| 6 | Résultat | MESURÉ | code 0 en 1,25 s, sortie `APEX_RESULT` capturée |
| 7 | Judge | JUGÉ | Oracle : FAIL, « 3/12 cas incorrects » ×2 — le modèle a retiré espaces/ponctuation, contre l'énoncé |
| 8 | Experience | MESURÉ | `3d4bc20d-…` (fitness 0,1999), `08da80a6-…` (fitness 0,1999), empreintes distinctes |
| 9 | Persistance après redémarrage | **NON PROUVÉE au run 23** | `APEX.exe --stop` a échoué (`Wsl/Service/0x8007274c`) : la relecture a eu lieu dans le MÊME processus (pid 10285). Défaut du script corrigé : la phase exige désormais un changement de processus, sinon BLOCKED. Seul fait mesuré après un vrai redémarrage : la TaskRun `aafb13b0-…`, écrite par le pid 10285, relue par le pid 12115. |
| 10 | Récupération | MESURÉ | TaskRun `aafb13b0-…` coupée par l'arrêt → « interrupted / process_restarted » après redémarrage (pid 10285 → 12115), jamais reprise |

## B. Preuve machine cible — à exécuter par l'utilisateur

Environnement : Windows + WSL2, Ollama avec `qwen2.5-coder:7b-instruct`, `gemma4:12b`, `bge-m3`, RTX 4060 Ti
8 Go, 16 Go de RAM. **Non exécutée à ce jour : UNVERIFIED.**

```
powershell -ExecutionPolicy Bypass -File setup\packaging\windows\Prouver-Boucle-APEX.ps1 -AllowRestart
```

(dans le kit de validation : `Prouver-Boucle-APEX.ps1`). Le script n'installe rien, ne modifie ni `.env` ni
`config.yaml`, arrête et redémarre APEX par le Panel installé, et écrit le journal complet dans
`Documents\APEX-preuve-boucle-<date>\journal.txt`. Il affiche pour chaque tentative : modèle sélectionné,
durée de l'appel au modèle et durées rapportées par Ollama (chargement, prompt, génération), Task ID,
TaskRun ID, code généré, isolation et image de la sandbox, résultat, verdict de l'Oracle et nombre de cas
réussis, Experience ID et fitness, puis la relecture après redémarrage et l'exécution interrompue.

Exécuter n'est pas apprendre : une Experience enregistre ce qui s'est passé ; rien ici ne modifie le
comportement d'APEX d'une exécution à l'autre.

## Audit : RUN 1 → Experience → RUN 2 influencé par l'expérience ? (04/10/2026, lecture du code)

Rien n'a été construit : état constaté.

- **RUN 1 → Experience** : réel. Le TaskLoop écrit une `Experience` par tentative jugée (code, empreinte,
  verdict, catégorie d'échec, fitness) — prouvé en CI (A).
- **Entre deux tentatives d'une même TaskRun** : le prompt est IDENTIQUE (`GenerationRequest(prompt=prompt)`,
  `apex/taskloop/loop.py`), sans température ni retour d'échec ; `apex.recovery` décide seulement RETRY /
  REASSIGN / BLOCK. Au run 23, la 2e tentative différait par le seul aléa de l'échantillonnage.
- **Entre deux TaskRuns** : `run_task_loop` ne LIT aucune `Experience`. Un RUN 2 sur la même tâche repart de zéro.
- **Existant qui lit l'expérience** (à réutiliser, jamais dupliquer) :
  - `apex.metalearner.adaptive_solution_loop.decide_next_strategies` (`apex solve`) : lit les `Experience`
    persistées de la génération précédente et mute la température + un rappel générique dans le prompt
    (`_INCORRECT_SOLUTION_HINT`, `_INVALID_CANDIDATE_HINT`) ; propriété TESTÉE : sans ces lignes, retour à la
    stratégie de repli. Il ne transmet PAS le détail de l'Oracle (cas échoués), et passe par
    `EvalRunnerService`, pas par les TaskRuns.
  - `apex.metalearner.recommendation_engine` : lit les `Experience` du TaskLoop et RECOMMANDE sans exécuter.
- **Non démontré (UNVERIFIED)** : qu'une décision tirée de l'expérience améliore la performance d'un vrai
  modèle sur les TaskRuns — aucune mesure de ce type n'a été produite dans ce chantier. Aucune preuve
  aujourd'hui que « APEX apprend » ; seulement que la décision de `decide_next_strategies` DÉPEND de
  l'expérience persistée (test d'ablation).

Prochain chantier logique : brancher les TaskRuns sur la règle existante (`decide_next_strategies`), puis
une expérience pré-enregistrée avec un vrai modèle — RUN 1, puis RUN 2 avec l'expérience contre RUN 2 sans
(ablation), sur plusieurs tâches et graines — pour mesurer si l'expérience change la performance.
