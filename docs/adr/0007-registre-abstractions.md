# ADR-0007 — Le registre d'abstractions est vérifié par les tests

**Statut :** Acceptée — phase 1

## Contexte

§79 interdit de prétendre qu'un composant existe s'il n'est pas implémenté. Sur
22 phases, la dérive entre l'état déclaré et l'état réel est le mode d'échec le
plus probable — et le plus difficile à détecter, puisque c'est la documentation
qui ment.

## Décision

`apex/interfaces/registry.py` déclare chaque abstraction avec son état réel.
Un test (`test_registry_cannot_overstate_reality`) échoue si une abstraction
marquée `implemented` ou `stub` n'a pas de module importable, ou si une
abstraction `planned` en déclare un. `apex doctor` intègre la même vérification.

## Conséquence

Le registre ne peut pas devenir optimiste avec le temps : la CI casse. Le
rapport de fin de phase (principe 15) est généré depuis cette source et hérite
de la même garantie.

## Écarté

Un tableau en Markdown tenu à la main : se désynchronise en quelques semaines,
et rien ne le signale.
