# ADR-0002 — Le noyau de la phase 1 n'a aucune dépendance applicative

**Statut :** Acceptée — phase 1

## Contexte

`apex doctor` sert à diagnostiquer un environnement cassé. Le cahier des
charges (§70) prévoit Pydantic pour la validation.

## Décision

Les modules `errors`, `obs`, `util`, `config`, `doctor` et `cli` n'utilisent que
la bibliothèque standard. Seule PyYAML est requise, et son absence est
diagnostiquée plutôt que fatale. Pydantic entre en phase 2, pour les schémas de
base de données et d'API, où il apporte un gain réel.

## Conséquence

Un doctor qui dépend de ce qu'il diagnostique est inutile au moment précis où
l'on en a besoin. Effet secondaire favorable : la suite de tests tourne sur
n'importe quelle machine, sans installation.

## Écarté

Tout en Pydantic dès la phase 1 : rendait `apex doctor` inopérant sur un
environnement où l'installation a échoué — c'est-à-dire son cas d'usage.
