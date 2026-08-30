# ADR-0011 — AgentRuntime et CodeIntelligence comme abstractions PLANNED

**Statut :** Acceptée — conception (phase de définition ; implémentation à une
phase ultérieure, voir `docs/COGNITIVE_ARCHITECTURE.md` §13)

## Contexte

`docs/COGNITIVE_ARCHITECTURE.md` conçoit un système de clones spécialisés
(Research/Learning/Developer/Experiment/Critic/Security/Benchmark/Teacher/
Integrator) et une capacité de compréhension du dépôt par APEX lui-même.
`docs/VISION_INTEGRATION.md` §2.1 mentionnait déjà « Genome + AgentRuntime »
en prose pour les rôles Planner/Supervisor/Executor/Researcher, sans jamais
déclarer `AgentRuntime` au registre — incohérence avec ADR-0007 (un nom
utilisé pour justifier une décision doit exister formellement). Aucune
abstraction ne couvre la compréhension du dépôt par APEX.

Confrontation aux 34 abstractions existantes avant toute création (méthode
imposée par ADR-0007, déjà appliquée par `docs/EXTERNAL_INTELLIGENCE.md` et
`docs/VISION_INTEGRATION.md`) : aucune des deux capacités ne se fond dans une
abstraction existante sans la dénaturer.

- Un clone est une composition de `ComputePool`, d'un rôle SQL scopé, de
  `SandboxDefaults` et de `PolicyEngine` — mais aucun de ces éléments ne
  porte d'identité, de cycle de vie ni de traçabilité propre à une instance
  d'exécution spécialisée. C'est un manque réel, pas un doublon.
- La compréhension du dépôt par APEX ne recoupe aucune abstraction
  existante : le registre décrit déjà l'état du code (ADR-0007) mais ne le
  *comprend* pas — `CodeIntelligence` consomme le registre comme donnée
  d'entrée, elle ne le remplace pas.

## Décision

Deux abstractions ajoutées au registre (`apex/interfaces/registry.py`),
toutes deux `PLANNED`, `module=None` (imposé par
`test_planned_abstractions_declare_no_module` — aucune ne déclare de module
tant qu'aucune implémentation n'existe) :

```python
Abstraction(
    "AgentRuntime",
    "instanciation, cycle de vie et isolation d'un clone spécialisé",
    phase_defined=3, phase_implemented=7,
    status=ImplStatus.PLANNED,
    fallback="exécution directe dans le Core (interactif uniquement)",
    notes="Comble un manque signalé par VISION_INTEGRATION.md §2.1 (« Genome "
          "+ AgentRuntime » utilisé en prose sans déclaration formelle). "
          "Neuf types de clones spécifiés dans docs/COGNITIVE_ARCHITECTURE.md "
          "§2 : aucun ne peut jamais modifier le Core (ADR-0001) ni obtenir "
          "de droit d'écriture sur main.",
),
Abstraction(
    "CodeIntelligence",
    "compréhension du dépôt APEX par lui-même : carte, dépendances, "
    "code↔test↔doc, détection d'incohérences et de lacunes",
    phase_defined=7, phase_implemented=12,
    status=ImplStatus.PLANNED,
    fallback="analyse manuelle par un humain",
    notes="Lecture et proposition seulement — ne modifie jamais le dépôt. "
          "Toute modification proposée passe par un Developer Clone "
          "(AgentRuntime) sous supervision complète : GATE, PromotionGovernor "
          "humain. Voir docs/COGNITIVE_ARCHITECTURE.md §8.",
),
```

`phase_defined`/`phase_implemented` sont des propositions de conception, à
confirmer au moment où le plan de phases est effectivement ajusté
(`docs/COGNITIVE_ARCHITECTURE.md` §13) — cette ADR ne renumérote pas le plan
à 22 phases.

## Conséquence

Le compteur d'abstractions passe de 34 à 36. Le résumé (`apex interfaces`)
affichera deux `PLANNED` supplémentaires ; aucun changement de comportement
observable tant qu'aucun module n'est implémenté — `test_registry_cannot_
overstate_reality` et `test_planned_abstractions_declare_no_module` continuent
de passer sans modification, précisément parce qu'aucune des deux ne déclare
de `module`.

`AgentRuntime` devient le point d'ancrage unique de tout le système de
clones : aucune future abstraction « Clone » séparée ne doit être créée sans
d'abord vérifier qu'elle n'est pas absorbable ici.

## Écarté

**Une abstraction distincte par type de clone** (`ResearchClone`,
`DeveloperClone`, etc.) : rejeté — même contrat (`AgentRuntime.spawn`), même
cycle de vie, seule la configuration (rôle SQL, permissions, réseau) diffère.
Exactement le doublon type qu'`ADR-0007` et `docs/EXTERNAL_INTELLIGENCE.md`
§2 refusent déjà ailleurs.

**Fusionner `CodeIntelligence` dans `WeaknessDetector`** : envisagé puis
écarté — `WeaknessDetector` détecte des lacunes de *compétence* d'un modèle
sur une tâche donnée (domaine d'apprentissage), `CodeIntelligence` analyse la
*structure d'un dépôt de code*. Le mécanisme de détection est apparenté
(d'où `phase_implemented=12`, la même phase), mais l'objet observé et les
sorties produites sont de nature différente — les fusionner masquerait cette
différence plutôt que de la clarifier.

**Déclarer `AgentRuntime`/`CodeIntelligence` `DEFINED` plutôt que `PLANNED`** :
rejeté à ce stade — `DEFINED` signifierait qu'une interface Python existe
déjà (comme `ComputePool`/`LanguageToolchain`), ce qui n'est pas le cas ici :
seule la conception existe. Passer à `DEFINED` interviendra quand
l'interface sera réellement écrite, pas avant — c'est la distinction que le
registre existe pour préserver (ADR-0007).
