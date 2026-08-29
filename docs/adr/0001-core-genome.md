# ADR-0001 — Séparation Core / Genome

**Statut :** Acceptée — phase 0, mise en œuvre à partir de la phase 4

## Décision

Le **Core** est du code Python, versionné par Git, modifiable uniquement par un
humain. Le **Genome** est une configuration déclarative, sérialisable et
versionnée : prompts, ordres d'outils, seuils, politiques de routage, paramètres
de récupération mémoire. L'évolution ne mute que le Genome ; aucun opérateur de
mutation ne produit ni ne modifie du code exécuté par le Core.

## Conséquence

Le rollback devient un changement de pointeur (principe 8 et 10 satisfaits à
coût quasi nul). La reproductibilité devient un hash. Le schéma fermé du Genome
interdit par construction qu'un agent élargisse ses propres permissions (§45),
plutôt que de compter sur une surveillance a posteriori.

## Écarté

Auto-modification de code sous revue automatique : impossible à borner, à
tester exhaustivement et à annuler de façon fiable.
