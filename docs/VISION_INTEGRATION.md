# APEX — Intégration de la Master Architecture Specification

Ce document confronte la vision de référence à l'architecture réellement
construite, sans rien réécrire de la phase 1. Il produit trois choses : la
cartographie vision → abstractions existantes, la liste des écarts réels, et le
brief de la phase 2.

---

## 1. Verdict global

La vision et l'architecture en place sont **compatibles à environ 85 %**. Les
divergences ne portent pas sur les principes — qui sont identiques — mais sur
trois points concrets traités en §3.

Le recouvrement était attendu : la vision et l'addendum de phase 0 partagent
les mêmes contraintes (24 Go, WSL2, local-first, mesure avant conclusion).
Le point intéressant n'est donc pas ce qui coïncide, mais ce que la vision
ajoute et que le registre ne couvrait pas.

---

## 2. Cartographie vision → existant (§58 : ne pas créer de doublon)

Chaque composant de la vision a été confronté aux 29 abstractions existantes
avant toute création. Résultat : **24 fusions, 5 ajouts**.

### 2.1 Fusionné dans une abstraction existante — aucun ajout

| Vision | Absorbé par | Raison |
|---|---|---|
| §14 Ablation testing | `EvalRunner` | Un mode d'exécution (désactiver un composant du Genome), pas un moteur distinct |
| §16 Critic + Verifier | `JudgeTier` | Le Verifier est le tier 0/1 ; le Critic est un rôle d'agent défini dans le Genome |
| §21 Memory + RAG | `MemoryProvider` + `EmbeddingProvider` | Le RAG est la stratégie de récupération, déjà paramétrée par le Genome |
| §22 Experience Replay | `MemoryProvider` + `TaskBank` | La sélection intelligente est une politique de récupération, pas un store |
| §23 Continual learning | Split `REGRESSION` + `EvalRunner` | Le catastrophic forgetting *est* une régression ; le mécanisme existe |
| §24 Synthetic data | `TaskGenerator` + `ArtifactStore` | Un dataset est un artefact adressé par contenu |
| §25 Teacher / distillation | `ExternalUseGate` + `TaskGenerator` | Cas d'usage externe déjà en liste blanche (`hypothesis_generation`) |
| §26 LoRA / adapters | `ArtifactStore` + `ModelRef` | **Voir §3.1 — le point d'architecture le plus important de cette intégration** |
| §31 Pareto | `SelectionStrategy` | Le front de Pareto est déjà la stratégie prévue |
| §33 Background learning | `ComputeBudget` + `Placement` | `reserve_fraction` existe déjà pour protéger l'interactif |
| §39 Lab / Prod | `GenomeStore` | Deux pointeurs (`stable`, `candidate`) sur un store versionné. Pas deux infrastructures |
| §41 Content-addressed workspaces | `ArtifactStore` | Décidé en phase 0, déjà au registre |
| §42 Knowledge lifecycle | `MemoryProvider` | `provenance`, `confidence`, `last_verified`, `expires_at` sont au schéma |
| §51 Planner / Supervisor / Request Understanding | Genome + `AgentRuntime` | Des rôles d'agent sont des données, pas des classes (ADR-0001) |
| §52 Executor / Researcher | Genome + `Placement` | Une séparation de rôles et de budgets, pas de code |

Les neuf autres (§4 Weakness, §5 Curriculum, §12 Evals, §13 Holdout, §17
Multi-agent, §18 Model Router, §19–20 Tools, §47 Anti-overfitting) mappent
directement sur des abstractions existantes sans discussion.

### 2.2 Ajouté — 5 abstractions, registre porté de 29 à 34

| Nouvelle | Vision | Pourquoi rien d'existant ne convenait |
|---|---|---|
| `SkillRegistry` | §10, §11 | **Oubli réel du registre initial.** Une compétence a son propre cycle de promotion, ses benchmarks et sa généalogie — la loger dans le Genome mélangerait deux durées de vie |
| `PromotionGovernor` | §53 | L'`ExperimentEngine` exécute et mesure ; lui confier aussi l'autorité de promotion revient à laisser le système se juger seul. Hors de portée de toute mutation, comme le Policy Engine |
| `HypothesisGenerator` | §29 | L'`ExperimentEngine` teste des hypothèses, il n'en propose pas. Seul composant où un modèle externe apporte un gain net et borné |
| `CostGainEstimator` | §28, §34, §44 | Trois sections de la vision, **une seule boucle** : estimer un coût et un gain, mesurer le réel, corriger l'estimateur. Une abstraction, pas trois |
| `ConfidenceCalibrator` | §30 | Pilote l'escalade de raisonnement et de modèle. Une confiance non calibrée contre les issues observées est un nombre décoratif |

---

## 3. Écarts réels entre la vision et l'architecture en place

### 3.1 LoRA rompt la séparation Core/Genome — et voici la résolution

**C'est le point le plus important de ce document.**

ADR-0001 pose que l'évolution ne mute que des données déclaratives, ce qui rend
le rollback trivial et la sécurité tractable. Un adaptateur LoRA (§26) est un
tenseur de poids appris : ni déclaratif, ni inspectable, ni diffable. Introduit
naïvement, il fait sauter la garantie centrale de l'architecture.

Résolution retenue, sans créer d'abstraction :

- L'adaptateur est un **artefact adressé par contenu** dans l'`ArtifactStore`.
- Le Genome ne contient jamais de poids : il porte un `adapter_ref` (un hash).
  Il reste donc entièrement déclaratif, diffable et réversible.
- `ModelRef` gagne un champ optionnel `adapter_ref`. Deux génomes qui ne
  diffèrent que par ce hash sont comparables par le protocole apparié habituel.
- Le rollback reste un changement de pointeur ; l'artefact précédent survit.
- Un adaptateur franchit **les mêmes portes G0–G9** qu'une mutation de prompt.

Conséquence assumée : un adaptateur n'est pas auditable comme un prompt l'est.
On sait *qu'il* change le comportement, on ne lit pas *comment*. C'est la raison
pour laquelle la vision a raison de placer LoRA en P2 et d'exiger (§26) qu'on
démontre d'abord qu'aucune combinaison prompt + RAG + outillage + workflow ne
suffit. Le coût n'est pas le temps GPU d'entraînement : c'est la perte
d'inspectabilité.

### 3.2 Le budget en task-runs devient un budget en valeur attendue

L'addendum de phase 0 exprimait le budget nocturne en minutes-GPU. La vision
(§28, §44) demande de raisonner en **gain attendu / coût**, ce qui est plus
juste : engager 4 h de GPU sur une expérience à faible espérance est un
gaspillage même si le budget le permet.

`config.yaml` porte déjà `min_expected_value_to_start`. Le champ existe mais
n'a aucun consommateur : il attend le `CostGainEstimator` (phase 18). Jusque-là,
le budget reste temporel, et c'est documenté comme tel plutôt que simulé.

### 3.3 Un Governor indépendant, mais indépendant de quoi ?

La vision (§53) exige que la décision de promotion échappe au système évalué.
La difficulté pratique : sur une machine unique, le Governor tourne sur le même
hôte, lit la même base et peut appeler les mêmes modèles.

Ce qu'on peut réellement garantir, et qui sera implémenté :

- Le Governor est du **Core**, jamais du Genome — aucune mutation ne l'atteint.
- Il lit les résultats d'évaluation, jamais les génomes candidats : il ne peut
  pas être influencé par ce qu'il juge.
- Ses critères sont dans `policies/`, signés, hors du dépôt de configuration
  mutable.
- Il écrit dans `audit_log`, en INSERT seul.

Ce qu'on **ne peut pas** garantir sur cette machine : une indépendance
matérielle. Un défaut du Core affecte le Governor comme le reste. C'est une
limite honnête, à lever le jour où un second hôte existe — et le champ
`trust_level` de `ComputePool` est déjà là pour ce jour-là.

---

## 4. Impact sur le plan de phases

La priorisation P0/P1/P2/P3 de la vision est compatible avec le plan en 22
phases. P0 correspond aux phases 1 à 10, P1 aux phases 11 à 19, P2 aux phases
20 à 22, P3 au-delà du programme actuel.

Trois ajustements :

| Phase | Ajustement | Motif |
|---|---|---|
| 6 | Ajout d'un **Model Capability Gate** | §59 : mesurer VRAM réelle et tokens/s par modèle avant d'engager un budget dessus |
| 11 | `SkillRegistry` introduit ici | Absent du plan initial, exigé par §10–11 |
| 16 | `PromotionGovernor` + `HypothesisGenerator` | §53 et §29 |
| 18 | `CostGainEstimator` | §28, §34, §44 |

Le RL (§49) reste hors programme : il exige une fonction objectif fiable, et la
phase 0 a établi qu'on ne saura pas mesurer un effet de 5 points avant
plusieurs nuits. Introduire du RL sur un signal de cette qualité produirait du
reward hacking, pas de l'apprentissage.

---

## 5. Brief de la Phase 2 — Base de données

Conformément à §63, avant implémentation.

**Objectif.** Persistance durable de l'état, avec les frontières de confiance
inscrites dans le SGBD plutôt que dans le code applicatif.

**Dépendances.** Gate exécuté sur la machine cible et `.apex/environment.json`
produit avec `phase_2_authorized: true` (ou exceptions datées explicites) ;
extra Python `db` ; PostgreSQL 16 + pgvector démarré.

**Abstractions concernées.** `EventSink` (stub → implémenté), `CheckpointStore`
(défini), `PolicyEngine` et `SecretBroker` (schéma seulement), `ComputePool`
(table + `trust_level`).

**Fichiers.** `apex/db/` (modèles, dépôts, session), `migrations/`,
`tests/test_db_*.py`. Aucun fichier existant réécrit ; `apex/obs/logging.py`
gagne un sink additionnel.

**Critères de réussite.**
1. Migrations aller **et retour** testées sur base éphémère.
2. Un test qui *tente* `DELETE` sur `audit_log` avec le rôle applicatif et
   vérifie l'échec. Une politique non testée n'est pas une politique.
3. Un test qui vérifie que le rôle worker ne peut pas lire le split `HELDOUT`.
4. Partitionnement mensuel en place dès la création sur les cinq tables à forte
   cardinalité.
5. `EventSink` passe de `stub` à `implemented` au registre — donc le test
   d'honnêteté du registre doit continuer à passer.

**Risques.** Le partitionnement rétrofité est douloureux, d'où sa présence dès
la première migration. La révocation de privilèges est spécifique à PostgreSQL
et non testable hors instance réelle : elle sera marquée `SIMULATED` tant
qu'elle n'aura pas tourné contre le conteneur.

**Réel / simulé.** Tout le code de la phase 2 sera écrit sans PostgreSQL
disponible dans cet environnement. Les modèles et migrations seront testables
par inspection de schéma ; les garanties de permissions ne le seront pas. Ce
découpage sera explicité dans le rapport de phase, comme en phase 1.

**Impacts.** VRAM : nul. Tokens : nul. Performance : écritures groupées
nécessaires dès que N workers coexisteront (phase 6). Complexité : modérée et
concentrée — c'est la phase où l'on paie d'avance la sécurité des phases 12 à 21.

---

## 6. État réel du dépôt

```
34 abstractions   2 implémentées · 1 stub · 2 définies · 29 planifiées
97 tests          97 verts
```

Le Gate est terminé et testé ; la mesure sur la machine cible reste le seul
livrable manquant de la phase 1. Rien de la phase 1 n'a été réécrit : les
seules modifications sont l'ajout de `apex/gate/`, cinq entrées au registre, et
le champ `measured` sur `ModelEndpoint`.
