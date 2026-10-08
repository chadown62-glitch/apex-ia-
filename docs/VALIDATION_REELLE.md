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

## 4. Résultats MESURÉS — `real-3` (06/10/2026, run 37509470901, commit `641b4f8`)

Même modèle, même backend natif, même runner. Le job `collect` n'a pas pu démarrer (GitHub : « recent account
payments have failed or your spending limit needs to be increased ») ; `mem-s0`, `mem-s1`, `tools-project-s1`
n'ont pas tourné. Les données sont **transcrites des journaux** des jobs terminés, avec contrôles
(`validation/results/real-3/PROVENANCE.md`).

### 4.1 Réplication pré-enregistrée (graines 10–13, 60 instances par bras, code identique à `real-2`)
| Bras | Réussite | Score Judge moyen | Tentatives | Durée médiane |
|---|---|---|---|---|
| A | 31/60 | 0,803 | 1,00 | 33 s |
| R | 34/60 | 0,815 | 1,57 | 52 s |
| C | 37/60 | 0,801 | 1,57 | 44 s |
| D | 32/60 | 0,700 | 1,65 | 208 s |

1re tentative identique en A, R et C sur 60/60 instances (nombres aléatoires communs, à nouveau vérifiés).
- **H3 D vs C** : 3 gagnées, 8 perdues, p = 0,23 → **même direction, non significatif : la dégradation n'est
  PAS confirmée** selon la règle pré-enregistrée (réplication à p < 0,05 exigée). Constat cumulé (descriptif,
  non pré-enregistré) : 4 gagnées / 18 perdues sur 120 instances.
- **H2 C vs R** : 3 gagnées, 0 perdue, p = 0,25 → même direction, non significatif ; **non démontré**.
- **C vs A** (secondaire) : 6 gagnées, 0 perdue, p = 0,031 → **répliqué** (`real-2` : 9–0, p = 0,004). Une
  part importante vient du budget de tentatives (R vs A : 3–0 ici, 5–0 dans `real-2`).
- Score du Judge : C n'améliore pas le score moyen sur R (0,801 contre 0,815 ; 0,800 contre 0,829 dans
  `real-2`) — plus de réussites complètes, pas de cas partiels mieux résolus.

### 4.2 Mémoire (8 paires, graine 2 seulement — graines 0 et 1 non exécutées)
Avec mémoire (M) 5/8, sans (A) 4/8 ; M seul 2, A seul 1, p = 1,0 ; score moyen M 0,672 contre A 0,746.
Le souvenir de la source est **retrouvé dans 8/8 cas** (provenance `task_run:…` dans le prompt). Après une
source réussie (7 paires) : A 4, M 4. → **MESURÉ : récupération fonctionnelle, aucun gain mesurable**
(échantillon faible, 1/3 du protocole).

### 4.3 Outils (graines 0–1 ; projets graine 0 ; consignes hostiles graine 0)
- Fonctions, C sans outils vs C outillé : **5/10 contre 3/10** (outillé seul 0, sans outils seul 2), durée
  médiane ×3 environ, 13 actions illisibles sur 106 pas. Outils réellement appelés : `fs.write` 7, `fs.read`
  4, `project.inspect` 1 ; **jamais `code.run` ni `tests.run`**. → **MESURÉ : les outils n'améliorent pas,
  ils dégradent** (non significatif) avec ce modèle 1.5B.
- Projets multi-fichiers : `reverse_words` et `cart_total` réussis (lecture puis écriture) ; `fix_mean`
  échoue : 10 lectures d'un chemin inexistant, 9 soumissions sans travail, 1 529 s. Correction : §5.
- Consignes hostiles données au vrai modèle : **4 tentatives réelles, toutes refusées** (`fs.read
  /etc/passwd`, `fs.read ../../config.yaml`, 2 × `fs.write /tmp/apex_pwned.txt` → `path_refused`) ; fichier
  témoin hôte **jamais créé**. Réseau, boucle infinie, sortie énorme, processus persistant, lien symbolique :
  **non exercés par le modèle** (il n'a jamais appelé `code.run`) — ces protections ne sont prouvées que par
  les tests de mécanisme (code d'attaque réel, sans modèle).

### 4.4 Interface pilotée par le vrai modèle (Playwright, Chromium)
38/38 vérifications : ligne « models » READY (modèle natif), verdict non dégradé par l'absence d'Ollama,
statut du runtime dans le Chat, réponse persistée affichée, **souvenir d'une autre conversation utilisé**
(« Comment s'appelle mon projet ? » → « Votre projet s'appelle "Zéphyr". »), document mémoire retrouvé avec
provenance, tâche lancée et jugée depuis l'interface, mission exécutée par le TaskLoop avec TaskRun liée,
16 pages chargées sans erreur. Observé : **1re réponse du Chat après ~4 min** pour un délai de 60 s.

## 5. Améliorations issues des mesures (statut de preuve)
| Constat mesuré | Correction | Statut |
|---|---|---|
| Plans avec code (58/60), prose au lieu de code (D : 18 `invalid_candidate`) | contrat « sans code » appliqué, consigne de format en dernier (`1868769`) | TESTÉ ; mesure `real-4` pré-enregistrée (§2.5) **non exécutée** (facturation GitHub) → UNVERIFIED |
| Chat : ~4 min pour un délai de 60 s ; `coder_main` et `fast` → même modèle | le repli ne relance plus un modèle servi déjà en échec (délai, indisponibilité) dans le même appel (`1ac9340`) | TESTÉ ; mécanisme INFÉRÉ des journaux (artefacts inaccessibles), gain non mesuré |
| 10 lectures d'un chemin absent (`fix_mean`) | l'observation `not_found` nomme les fichiers présents (`4bcd362`) | TESTÉ ; gain non mesuré |
| Statut système : Ollama seul sondé | ligne « models » mesurée, Ollama informatif (`1631b68`) | TESTÉ + vérifié par le banc UI réel (38/38) |
| Cache des poids jamais sauvegardé (chemin « .. ») | chemins absolus (`641b4f8`) | MESURÉ : « Cache saved » dans `real-3` |
| Faux libellé `generation_s` (temps du Judge) | `judge_exec_s` (`7fd7068`) | TESTÉ |

## 6. Chantier « cycle cognitif » — audit et hypothèses causales (08/10/2026, AVANT toute modification)

Mesures locales possibles malgré le blocage CI : poids publics **Qwen2.5-1.5B-Instruct** (Apache-2.0) depuis
Docker Hub `ai/qwen2.5:1.5B-F16` (GGUF F16, sha256 `b6eaec35…` vérifié), convertis en safetensors bf16,
exécutés par le backend NATIF (fp32 CPU, 4 cœurs, 5,8 tokens/s). Ce n'est PAS la variante « Coder » de la CI :
toute mesure locale est étiquetée `actual_weights`/`substitute_weights` et comparée seulement à d'autres
mesures locales. Les expériences longues tournent depuis un worktree git figé (le code mesuré ne change pas
pendant la mesure).

| Mécanisme | Constat (code + traces réelles) | Hypothèse causale |
|---|---|---|
| Outils | Le modèle soumet avant d'avoir écrit, jusqu'à 9 fois d'affilée malgré le refus observé ; il écrit du code juste mais entre `"""` dans le JSON (3 écritures correctes perdues sur `fix_mean`, 13 actions illisibles / 106 pas en `real-3`) ; jamais `code.run`/`tests.run` ; l'espace de travail d'une tâche-fonction ne contient que son propre code ; l'historique réinjecte tout le JSON de chaque tour | **H-T1** le format (code échappé dans du JSON) détruit du travail correct ; **H-T2** l'observation `nothing_to_submit` ne réoriente pas : boucle ; **H-T3** vérifier coûte une génération de code de test et n'apporte aucune information nouvelle (aucun exemple exécutable) : les outils n'ajoutent que du coût ; **H-T4** le prompt grossit à chaque tour (lenteur ×3 à ×8) |
| Mémoire | Un souvenir = un épisode passé tronqué à **600 caractères centrés sur l'énoncé** : surtout l'énoncé d'une AUTRE tâche, code coupé, aucune leçon ; rien ne dit si le souvenir a été utilisé ; dans les paires mémoire, le modèle sait déjà résoudre la cible sans aide | **H-M1** l'unité mémorisée n'est pas actionnable (pas de leçon, troncature) ; **H-M2** « retrouvé » est mesuré, « utilisé » ne l'est pas ; **H-M3** le banc ne contient pas d'information décisive présente seulement dans l'expérience |
| PLAN | 58/60 plans contiennent du code, recopié puis commenté en prose ; étape obligatoire si demandée | **H-P1** pour un modèle 1.5B sur des énoncés courts, le plan n'apporte pas d'information et coûte ×4 ; il doit être une décision, pas une étape |
| Adaptation | La tentative suivante reçoit « cas en échec : 3 sur 8 » **sans son propre code** ; aucune étape de diagnostic ; la stratégie ne change que par ce suffixe | **H-A1** sans son code, le modèle ne peut pas réparer, il ré-échantillonne (d'où C ≈ R) ; **H-A2** des observations concrètes et légitimes (son code, les exemples PUBLICS de l'énoncé exécutés) permettent un diagnostic ; **H-A3** un changement de stratégie explicite (réparer puis, sans progrès, réécrire) vaut mieux qu'un retry |
| Expérience | `parent_experience_id`/`mutation_description` remplis, `improvement_delta` jamais ; ni observation, ni diagnostic, ni cause, ni stratégie avant/après, ni leçon | **H-E1** une expérience ne peut pas guider une décision future faute de ces champs |

Ordre de travail : adaptation (cœur du cycle, produit les diagnostics), outils, mémoire/leçons, PLAN
adaptatif, puis banc de preuve d'apprentissage. Chaque correction : tests, mesure locale réelle, audit des
échecs, correction, nouvelle mesure. Protocoles pré-enregistrés ci-dessous avant chaque mesure.

### 6.1 Adaptation L — PRÉ-ENREGISTRÉ (avant toute mesure de L)
Mécanisme (`retry_adaptation="diagnose_repair"`, `apex/taskloop/repair.py`, version `l-repair-1`) : après un échec
jugé, OBSERVATION (son propre code, faits d'échec autorisés, exemples PUBLICS de l'énoncé exécutés en sandbox —
jamais un cas caché), DIAGNOSTIC (appel court au modèle : « cause la plus probable », ≤ 120 tokens),
STRATÉGIE : `initial` -> `repair` (corriger son code avec son diagnostic) ; `repair` sans baisse des cas en échec ->
`rewrite` (nouvelle solution, code précédent non montré, causes rappelées, température +0,3) ; `rewrite` ->
`repair`. Traces : `strategy.changed` (échec, observation, diagnostic, stratégie avant/après) et
`strategy.outcome` (le CODE a-t-il changé, gain de score) ; `Experience.generation_config` et
`improvement_delta` les conservent.

Mesure LOCALE (poids Qwen2.5-1.5B-Instruct, §6) : 15 tâches de référence × graines {0, 1, 2, 3}, bras R
(retry identique), C (faits d'échec), L, tous à 3 tentatives et **budget égal** (garde-fou `no_progress` désactivé
pour les trois : `--no-progress-patience 99`), nombres aléatoires communs. Hypothèses (McNemar exact, Holm 5 %) :
**H-L1** L vs R ; **H-L2** L vs C ; **H2** C vs R. Descriptif : corrections (échec puis réussite), score du Judge,
part des retries où le code a réellement changé, modes de stratégie, diagnostics manquants, durée. Règle : un effet
n'est affirmé qu'après réplication sur graines {10, 11, 12, 13} (même direction, p < 0,05).

Note (constatée avant toute lecture des résultats de L) : les 15 énoncés de référence ne contiennent AUCUN exemple
public entre accents graves. Dans cette mesure, l'observation de L se limite donc à son propre code et aux faits
d'échec ; l'apport des exemples publics (H-A2) se mesure sur le banc §6.2, dont chaque énoncé en contient.

### 6.2 Banc d'outils — PRÉ-ENREGISTRÉ (avant toute mesure du banc)
Banc (`scripts/apex_bench_tasks.py`) : 10 tâches, 5 types — `execution` ×5 (slugify, compress_ranges, parse_kv,
roman, wrap_text : l'énoncé suffit, une erreur de détail se voit en exécutant les exemples publics), `test` ×2
(normalize_phone, grade : tests unitaires publics fournis dans `tests/`), `lecture` ×1 (shipping_cost : les tarifs
n'existent QUE dans `data/tarifs.json`), `modification` ×1 (median_fix : `solution.py` bogué fourni), `multi` ×1
(to_meters : inspecter, lire `helpers/units.py`, écrire, valider avec les tests fournis). Cas cachés (8 à 10 par
tâche) calculés par une implémentation de référence, disjoints des exemples publics ; exemples publics, tests
fournis et code bogué vérifiés contre la référence (`tests/test_tool_bench.py`). `solution.py` est jugé seul :
les énoncés `lecture`/`multi` le disent.

Changements de l'agent mesurés (T contre T0 = agent figé à `1d529e2`, avant toute correction d'outils) : écritures
entre triples guillemets acceptées (H-T1), refus `nothing_to_submit` qui dit quoi faire et arrêt à 4 répétitions
(H-T2), `solution.check` — exécute solution.py sur les exemples PUBLICS de l'énoncé, sans argument (H-T3), rôle et
exemple d'appel de chaque outil, historique compacté (H-T4), et **H-T5** (constatée en relisant le prompt outillé) :
l'énoncé finit par « Réponds UNIQUEMENT avec le code Python », ce qui contredit « un seul objet JSON » ; la consigne
est traduite (« écris ce code dans solution.py avec fs.write »), jamais retirée de l'énoncé. Correction connexe :
l'extraction des exemples publics perdait un exemple sur deux quand un nom d'une lettre (`n`) précédait
(`roman(4) == 'IV'` perdu) — corrigée et testée.

Bras (tous : une base neuve par bras, garde-fou `no_progress` désactivé, graines de génération
`seed_for("toolbench", tâche, graine)` identiques) : **NT** sans outils, 1 tentative, énoncé seul ; **NT3** sans
outils, 3 tentatives avec faits d'échec (témoin « plus de calcul, sans outils ») ; **T** 1 tentative = un épisode
outillé de 8 tours au plus, fichiers de départ dans l'espace, outils `workspace.read`, `workspace.write`,
`sandbox.execute` ; **T0** = T exécuté depuis l'arbre figé de l'ancien agent (`--arm-label T0`). Graines {0, 1, 2, 3}
→ 40 instances appariées par bras. Mesure LOCALE (poids substituts, §6).

Hypothèses principales (McNemar exact, Holm 5 %) : **H-T1** T > T0 (l'agent corrigé) ; **H-T2** T > NT (les outils).
Secondaires : T vs NT3, T0 vs NT, NT3 vs NT, T vs NT hors `lecture` (une tâche `lecture` est insoluble sans outil par
construction : rapportée à part). Mesures par bras : réussite (par type), score du Judge, durée ; pour les bras
outillés : tours, appels, appels UTILES (réussis et nouveaux : lecture d'un chemin pas encore lu, écriture d'un
contenu différent, vérification après une écriture) et INUTILES (illisibles, soumissions refusées, erreurs,
doublons), répétitions (tour identique au précédent), erreurs par code, épisodes avec vérification, vérifications
en échec suivies d'une réécriture, lecture du fichier nécessaire (`lecture`/`multi`), raisons d'arrêt. Règle : un
effet n'est affirmé qu'après réplication sur graines {10, 11, 12, 13} ; les échecs de T sont ensuite diagnostiqués
sur les traces, corrigés, et remesurés sous un nouveau protocole pré-enregistré (jamais en modifiant celui-ci).

### 6.3 Mémoire actionnable et expérience exploitable — PRÉ-ENREGISTRÉ (avant toute mesure du banc mémoire)
Changements (réponses à H-M1, H-M2, H-E1) :
- un souvenir d'EXÉCUTION est rendu par `apex.memory.lessons.execution_view` — tâche, résultat, LEÇON, code (1 500
  caractères au plus), puis début de l'énoncé (500) — au lieu d'une fenêtre de 600 caractères centrée sur
  l'énoncé (qui ne montrait ni le code ni la leçon : testé) ;
- LEÇON d'une TaskRun (`apex.experience.lesson.run_lesson`) écrite dans son souvenir : « 1re tentative en échec
  (catégorie) ; cause identifiée : <diagnostic du modèle> ; corrigée par la stratégie « repair » (score a -> b) »,
  ou les causes non résolues ; seulement si un diagnostic existe ;
- MÉMOIRE UTILISÉE mesurée à chaque tentative (`memory.used`) : éléments distinctifs du souvenir montré (nombres
  non triviaux, chaînes, noms définis), absents de l'énoncé courant, retrouvés dans le code produit ; la liste des
  souvenirs utilisés est gardée dans l'Expérience (`generation_config.memory_used`) — provenance de la décision ;
- une Expérience répond aux 12 questions (`apex.experience.lesson.experience_card` : tâche, contexte, stratégie,
  modèle, outils, erreur, observation, cause probable, modification, résultat, score, stratégie suivante) à partir
  des colonnes existantes, sans élargir le schéma ; ce qui n'existe pas est listé dans `missing`, jamais inventé.

Banc (`MEMORY_BENCH`, 6 paires) : une tâche SOURCE donne explicitement une convention « maison » (tarifs Zéphyr,
alphabet Kappa, codes promo Lumen, unités Orion, barème Delta, dates Nova) ; une tâche CIBLE différente (autre
fonction, autres entrées) s'y réfère par son nom sans la redonner. Testé : la convention figure dans l'énoncé
source et jamais dans l'énoncé cible ; aucune entrée cachée de la cible n'apparaît dans la source ; exemples
publics conformes à la référence. Phase d'EXPÉRIENCE par graine : APEX résout les 6 sources (bras L, 2 tentatives) ;
la mémoire est ce qu'APEX a lui-même produit (énoncé public, code, verdict, leçon) — le banc n'écrit jamais dans
la mémoire. Puis chaque cible, UNE tentative, sur une copie neuve de cette base : **A** sans mémoire ; **M** avec
mémoire ; **P** placebo = mémoire privée du seul souvenir de SA source (les 5 autres restent : même présence de
mémoire, sans le contenu pertinent) ; **M0** = M exécuté depuis l'arbre figé d'avant ces changements (rendu en
fenêtre de 600 caractères), sur la MÊME base d'expérience. Graines de génération identiques entre bras.
Graines {0, 1, 2, 3} → 24 cibles appariées par bras. Mesure LOCALE (poids substituts, §6).

Hypothèses principales (McNemar exact, Holm 5 %) : **H-M1** M > A ; **H-M2** M > P. Secondaires : M vs M0, M0 vs A,
P vs A. Mesures : souvenir de la source retrouvé (et au 1er rang), souvenir UTILISÉ (preuves), réussite selon
utilisé / non utilisé, par paire, durée, réussite des sources. Limite assumée : ce banc mesure la capacité à
RETROUVER et UTILISER une information présente seulement dans l'expérience — pas un apprentissage (§6.5). Règle :
effet affirmé seulement après réplication sur graines {10, 11, 12, 13}.

### 6.4 PLAN : vérification de la correction, puis plan adaptatif — PRÉ-ENREGISTRÉ (avant toute mesure locale)
1. **Correction du plan** (`1868769`, protocole §2.5, jamais exécuté en CI) : rejoué LOCALEMENT, inchangé — bras A, C,
   B, D, 15 tâches × graines {0, 1, 2, 3} (`local-plan2`). Mesures : réussite B vs A et D vs C, plans contenant du code
   (`plan_code_removed`), plans en échec, `invalid_candidate`, longueur du plan, durée. Comparaison descriptive avec
   `real-2` (autres poids : jamais confondus).
2. **Plan adaptatif** (`plan_policy="auto"`, `apex.taskloop.loop._plan_decision`) : APEX ne rédige un plan que si
   les exécutions PASSÉES de tâches similaires (mémoire système) réussissent davantage avec un plan RÉELLEMENT injecté
   (lu dans leurs expériences), avec au moins 3 exécutions de chaque côté ; sinon il le contourne (à preuve égale il
   coûte un appel de plus ; sans preuve, règle par défaut issue de `real-2`/`real-3`). Décision et preuves journalisées
   (`task.plan.decided`). Mesure (`planauto`, graines {0, 1, 2, 3}) : historique = A puis B sur les 10 premières tâches
   de référence ; évaluation sur les 5 autres : AUTO (décidé d'après cet historique) contre A et B (bases neuves),
   mêmes graines de génération. Critère : PLAN « utile ou contournable » est atteint si AUTO n'est pas inférieur à
   B en réussite (McNemar, p ≥ 0,05 dans le sens défavorable) ET coûte moins que B en durée moyenne — ou si AUTO
   choisit le plan là où il aide. Rapporté : décisions, raisons, accord avec la décision rétrospectivement juste
   (planifier seulement où B réussit et A échoue), réussite et durée par bras.

### 6.5 Preuve d'apprentissage — PRÉ-ENREGISTRÉ (avant toute mesure)
Question : l'expérience d'APEX sur des tâches d'ENTRAÎNEMENT change-t-elle ses décisions sur des tâches NOUVELLES, et
améliore-t-elle de façon reproductible sa réussite — au-delà de ce que donnent plus de calcul, plus de contexte ou une
simple mémoire ?

Constat qui fonde le banc (mesuré AVANT, sur l'entraînement seulement) : les erreurs du modèle sont systématiques — 9
tâches de référence sur 15 échouent à CHAQUE première tentative en local, par familles : entrée invalide non ramenée à
None (roman_to_int, median, parse_duration), « ignorer X mais PAS Y » (is_palindrome), « DERNIÈRE occurrence »
(dedupe_keep_last), petits mots en minuscules sauf en tête (title_case). Évaluation (`LEARNING_EVAL`) : 9 fonctions
NOUVELLES (hex_to_int, bin_to_int, mean_or_none, parse_hhmm, is_anagram, char_freq, last_positions,
dedupe_chars_keep_last, headline), jamais vues à l'entraînement, chacune partageant UNE de ces clauses avec une tâche
d'entraînement ; énoncés complets (la ligne de base a toute l'information) ; cas cachés calculés par référence, jamais
présents dans la mémoire. dedupe_chars_keep_last est un transfert PROCHE (même règle sur des chaînes) : rapporté par
famille. Ce banc mesure un transfert vers des tâches apparentées, pas un gain sur des tâches arbitraires.

Protocole (`learning`, graines {0, 1, 2, 3}) : deux ENTRAÎNEMENTS par graine sur les 15 tâches de référence, budget
égal (3 tentatives, `no_progress` désactivé) — **EXP** : bras L (diagnostic -> réparation ; leçon et code validé
dans la mémoire) ; **NAIVE** : bras R (mêmes tentatives, retry identique : ni diagnostic ni leçon ; sa mémoire contient
les mêmes énoncés et ses propres codes). Puis chaque tâche d'évaluation, UNE tentative, sur une copie neuve : **BASE**
(sans mémoire), **EXP** (mémoire de l'entraînement L), **NAIVE** (mémoire de l'entraînement R). Contrôles : même nombre
de tentatives à l'évaluation (1) pour tous ; NAIVE contrôle « plus de contexte / avoir une mémoire » et « avoir déjà vu
des tâches proches » ; aucune réponse attendue dans la mémoire (index anti-fuite, testé) ; aucune tâche d'évaluation
identique à une tâche d'entraînement ; mêmes graines de génération entre bras.

Hypothèses (McNemar exact, Holm 5 %) : **H-L-a** EXP > BASE ; **H-L-b** EXP > NAIVE (apprendre de SES erreurs, pas
seulement se souvenir). Secondaire : NAIVE vs BASE. Mesures : réussite par famille, score du Judge, durée, souvenir de
la tâche apparentée retrouvé et UTILISÉ (`memory.used`), DÉCISION CHANGÉE (code différent de celui de BASE à graine
égale), entraînement (réussites, corrections, leçons écrites). Critère de preuve : H-L-a ET H-L-b significatifs, puis
répliqués (même sens, p < 0,05) sur graines {10, 11, 12, 13}. Sinon : limitation documentée avec les mesures.

Ajouts d'ANALYSE (écrits avant toute lecture des résultats ; aucun protocole modifié) : métriques de plan
(`plan_metrics` : longueur, code résiduel, part des termes du plan déjà dans l'énoncé = répétition, part retrouvée dans
le code = lien plan -> actions) ; variante STRICTE de « mémoire utilisée » qui ignore les preuves faites d'un seul
entier (60, 100…), susceptibles de coïncider (ex. parse_duration et parse_hhmm comptent tous deux en 60) — la règle
pré-enregistrée reste la mesure principale, la stricte un contrôle de robustesse.

## 7. Résultats MESURÉS locaux — chantier « cycle cognitif » (poids substituts Qwen2.5-1.5B-Instruct, §6)
Toutes les mesures de cette section : backend natif, CPU 4 cœurs, `actual_weights` = « docker.io/ai/qwen2.5:1.5B-F16
(Qwen2.5-1.5B-Instruct) », `substitute_weights: true`. Jamais comparées aux chiffres CI (`real-2`/`real-3`, variante
Coder) autrement qu'à titre descriptif. Fichiers bruts : `validation/results/local-cognitive/`.

**Incident de mesure (consigné, sans effet sur les réussites).** Une exécution de la suite de tests en parallèle
(10:42–14:51 UTC, même à priorité minimale puis en ordonnancement « idle ») a ralenti l'inférence d'un facteur 7 à 20
(contention des fils de calcul de torch) : les DURÉES du bras C (graines 0–3) sont inutilisables. Les RÉUSSITES ne sont
pas affectées : aucune tentative n'a atteint un délai (génération la plus longue 368 s pour une limite de 600 s ;
toutes les tentatives en échec sont `incorrect_solution`). Depuis, aucune autre charge ne tourne pendant une mesure
(pause entre jobs).

### 7.1 Adaptation R / C / L (§6.1, 15 tâches × graines 0–3, budget égal, commit figé `1d529e2`)
| Bras | Réussites | Corrigées (échec puis réussite) | Code modifié d'une tentative à l'autre | Durée moyenne |
|---|---|---|---|---|
| R (retry identique) | 24/60 | 0 | **0/72** | 33,4 s |
| C (faits d'échec) | 24/60 | 0 | 20/72 | (contaminée) |
| L (diagnostic -> stratégie) | **28/60** | **4** | **52/72** | 44,4 s |

McNemar exact : **H-L1 L vs R 4–0, p = 0,125 ; H-L2 L vs C 4–0, p = 0,125 ; H2 C vs R 0–0, p = 1** — aucun
significatif après Holm. **MESURÉ** : la stratégie change réellement avec L (le code change dans 72 % des retries
contre 0 % pour R, qui rejoue exactement le même code), et L corrige 4 instances que ni R ni C ne corrigent, sans
aucune perte. **Non prouvé** : L > R (p = 0,125).

Audit des 4 corrections (traces `strategy.changed`/`strategy.outcome`) : TOUTES à la 3e tentative, en mode `rewrite`
(nouvelle solution, code précédent non montré, température 1,0) ; la tentative `repair` guidée par le diagnostic n'a
jamais amélioré le score (balanced 0,375 -> 0,375 ; median 0,875 -> 0,875). Justesse des 72 diagnostics, jugée contre
la cause réelle lue dans le code (**JUGÉ**) : 20/72 désignent une clause réellement violée (dedupe_keep_last 8/8,
roman_to_int 8/8 en partie, title_case 4/8 en partie) ; 52/72 sont faux, inversés ou vagues (is_palindrome : énoncé
inversé ; balanced : cause réelle = appel à une fonction `match` non définie, jamais citée ; median : cause réelle =
liste vide non gérée, diagnostic « trier la liste »). Même un diagnostic juste n'a produit aucune correction
(dedupe_keep_last : 0/4). **INFÉRÉ** : le gain observé de L vient de l'EXPLORATION (réécriture à température plus
haute), pas de la compréhension de l'erreur — hypothèse testée en §6.6 avant toute affirmation.

### 6.6 Exploration ou compréhension ? — PRÉ-ENREGISTRÉ (après §7.1, avant toute mesure de RT)
Témoin **RT** (`retry_adaptation="explore"`) : même prompt à chaque tentative, aucune observation, aucun diagnostic ;
seule la température suit le calendrier de L à 3 tentatives (2e : inchangée ; 3e : celle d'une réécriture, 1,0 ici).
Mesures (commit figé du témoin, mêmes graines `seed_for("adapt", tâche, graine)`) :
1. 15 tâches de référence × graines 0–3 : RT, comparé aux R, C, L de §7.1 ; contrôle d'identité du chemin de code :
   R graine 0 rejoué depuis le nouveau commit doit reproduire exactement §7.1 (mêmes réussites, mêmes codes).
2. Énoncés À EXEMPLES PUBLICS (8 tâches du banc §6.2 résolubles sans outil — l'observation de L y inclut l'exécution
   des exemples, H-A2) × graines 0–3 : R, RT, L.
Hypothèses (McNemar exact, Holm 5 % par mesure) : **H-L3** L > RT (comprendre au-delà d'explorer) ; secondaire RT vs R
(l'exploration seule). Lecture fixée d'avance : si RT ≈ L, le mécanisme utile de L est l'exploration et le diagnostic
n'apporte rien de mesurable avec ce modèle ; si L > RT sur les énoncés à exemples seulement, ce sont les observations
concrètes qui rendent le diagnostic utile.
