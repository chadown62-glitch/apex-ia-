# ADR-0006 — L'évaluation des promotions reste locale et reproductible

**Statut :** Acceptée — phase 1

## Contexte

Les modèles externes sont autorisés en complément. La tentation naturelle est
de les employer pour juger, puisqu'ils sont meilleurs.

## Décision

Les rôles `coder_main`, `judge`, `fast` et `embed` doivent être locaux —
contrainte vérifiée par `ApexConfig.validate()`. Les contextes externes sont en
liste blanche fermée : `interactive`, `hypothesis_generation`, `research`,
`judge_calibration`. Toute autre valeur est une erreur de configuration
bloquante, pas un avertissement.

## Conséquence

Trois problèmes évités : coût non borné sur les nuits d'évaluation, perte de
reproductibilité par changement silencieux de version côté fournisseur, et
dépendance d'une expérience de plusieurs jours à la disponibilité d'une API.

## Écarté

Un Judge externe pour toutes les notations : meilleure qualité par run,
historique de scores incomparable au bout de quelques semaines. Un système qui
ne peut pas comparer ses générations ne peut pas évoluer.
