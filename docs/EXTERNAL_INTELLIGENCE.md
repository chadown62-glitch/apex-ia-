# APEX — External Intelligence Layer (architecture prévue, non implémentée)

**Rien de ce document n'est implémenté.** Il fixe l'emplacement architectural de
chaque élément afin que l'ajout ultérieur ne demande pas de refonte, et
documente les décisions d'anti-doublon prises maintenant.

---

## 1. La règle fondamentale, rendue vérifiable

> Les modèles externes ne doivent pas devenir le cerveau d'APEX.

Formulée ainsi, c'est une intention. Voici sa forme exécutable, en trois
mécanismes dont deux existent déjà.

**(a) Interdiction structurelle dans la boucle autonome — déjà en place.**
`ApexConfig.validate()` refuse de charger une configuration où les rôles
`coder_main`, `judge`, `fast` ou `embed` seraient externes. Les contextes
externes sont une liste blanche fermée de quatre valeurs, et l'évaluation n'en
fait pas partie. Une configuration qui tenterait de faire juger APEX par un
modèle externe ne démarre pas. Tests :
`test_autonomous_roles_cannot_be_external`,
`test_external_contexts_are_whitelisted`.

**(b) Une promotion ne peut jamais être démontrée avec assistance externe.**
Conséquence directe de (a) : les exécutions d'évaluation n'ont pas accès aux
fournisseurs externes. Une amélioration promue l'a donc été sur des capacités
purement locales, par construction et non par discipline.

**(c) L'audit de capacité locale — à implémenter en phase 5.**
Deux bras d'évaluation comparés périodiquement :

```
APEX_LOCAL_ONLY        : aucun appel externe, y compris en amont
APEX_EXTERNAL_ASSISTED : externe autorisé sur ses quatre contextes
```

La métrique qui compte n'est pas l'écart entre les deux, mais sa **dérivée dans
le temps**. Si `LOCAL_ONLY` progresse, APEX apprend réellement. Si seul
`EXTERNAL_ASSISTED` progresse, APEX a appris à consommer un service — et
l'amélioration doit être rejetée comme acquis local.

C'est exactement une ablation (`FULL - EXTERNAL`), donc un **mode de
`EvalRunner`**, pas un moteur distinct.

---

## 2. Anti-doublon : où atterrit chacun des 13 éléments demandés

Confrontation aux 34 abstractions existantes avant toute création.
**Résultat : 0 nouvelle abstraction, 1 nouveau champ, 1 nouveau mode.**

| Demandé | Atterrit dans | Statut |
|---|---|---|
| External Model Registry | `config.models` + `ModelRef` (`quantization: external`) et `ModelEndpoint` (`runtime: external`) | Structure déjà là |
| External Model Router | `ModelProvider` + routage existant, filtré par `ExternalUseGate` | Prévu phase 6 |
| Quotas | `ExternalPolicy.daily_budget_eur` / `monthly_budget_eur` | Déjà en config |
| Coûts | `ModelProvider.estimate_cost` + table `model_runs` | Prévu phases 2 et 6 |
| Logs | `EventSink` + `audit_log` | Prévu phase 2 |
| Permissions | `PolicyEngine` (contexte d'appel) + `SecretBroker` (clés) | Prévu phase 7 |
| Provenance des données | **Nouveau champ** `external_provenance` — voir §3 | À ajouter phase 8 |
| Validation des sorties | Contrats Pydantic de sortie d'agent + `JudgeTier` tier 0 | Prévu phases 6 et 9 |
| Cache | `ArtifactStore`, clé = hash(prompt, `ModelRef`, paramètres) | Réutilisation directe |
| Fallback | Chaîne de `ModelProvider` + disjoncteur `ExternalPolicy` | Déjà spécifié |
| Benchmark external-off | Mode d'ablation de `EvalRunner` | **Nouveau mode**, phase 5 |
| Benchmark external-assisted | Idem, second bras | Phase 5 |
| Mesure du gain local acquis | Métrique dérivée des deux bras ci-dessus | Phase 5 |

Aucun de ces éléments ne justifie une abstraction indépendante. Créer un
`ExternalModelProvider` distinct de `ModelProvider` serait le doublon type :
même contrat, même appelant, seule la politique d'autorisation diffère — et
cette politique a déjà son composant.

---

## 3. Le seul vrai manque : la provenance externe

Un souvenir, une compétence, une tâche générée ou un dataset produit **avec**
l'aide d'un modèle externe n'a pas le même statut qu'un artefact purement local.
Sans traçabilité, l'audit de capacité locale (§1c) devient impossible : on ne
saurait pas quelle part du savoir accumulé dépend d'un service tiers.

Mécanisme, strictement calqué sur `eval_taint` qui a déjà fait ses preuves dans
l'architecture mémoire :

```
external_provenance : bool         -- produit avec assistance externe
external_source     : ModelRef|null -- lequel, version épinglée
```

Règles associées :

- Toute écriture mémoire issue d'une exécution ayant appelé un fournisseur
  externe est marquée, par propagation automatique et non par déclaration de
  l'appelant — même mécanisme que la rédaction des secrets dans le formateur
  de logs.
- Le bras `APEX_LOCAL_ONLY` **filtre à la lecture** les entrées marquées. Sans
  ce filtre, une connaissance obtenue via un modèle externe se retrouverait
  dans le bras censé mesurer l'autonomie locale, et la mesure ne mesurerait
  plus rien.
- La provenance se propage : une compétence distillée depuis des données
  marquées reste marquée.

Coût assumé : à long terme, une part significative de la mémoire sera marquée,
et le bras local en sera d'autant appauvri. C'est le prix d'une mesure honnête,
et c'est précisément l'information recherchée.

---

## 4. Ce qui n'est volontairement pas ajouté maintenant

`config.yaml` ne reçoit **aucun nouveau champ** dans cette itération.

L'addendum de phase 0 a déjà introduit `min_expected_value_to_start`, qui
n'a aucun consommateur avant la phase 18. Un second champ orphelin
(`local_capability_audit`, cadence de l'audit) reproduirait le même défaut :
de la configuration qui a l'air d'agir sans rien piloter. Le champ sera ajouté
en phase 5, en même temps que le code qui le lit.

La règle appliquée : une interface se déclare tôt (elle protège d'une refonte),
un paramètre se déclare tard (il n'a de sens qu'avec son consommateur).

---

## 5. Ordre d'introduction

| Phase | Élément | Dépend de |
|---|---|---|
| 6 | `ModelProvider` externe derrière l'interface commune, disjoncteur, cache | Model Capability Gate |
| 5 | Modes d'ablation `LOCAL_ONLY` / `EXTERNAL_ASSISTED` de `EvalRunner` | Harness d'évaluation |
| 7 | `PolicyEngine` : contexte d'appel externe, `SecretBroker` pour les clés | Registre d'outils |
| 8 | Champs `external_provenance` / `external_source` et filtrage à la lecture | Mémoire |
| 16 | `HypothesisGenerator` — premier usage externe à valeur nette | Experiment Engine |

Le `HypothesisGenerator` est le seul composant où un modèle externe apporte un
gain net et borné : volume d'appels faible, valeur élevée, et surtout résultat
systématiquement vérifié localement par l'expérience qui suit. C'est le profil
d'usage inverse de l'évaluation en masse, où l'externe coûte cher et détruit la
reproductibilité.
