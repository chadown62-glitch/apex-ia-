# ADR-0004 — ComputePool est une abstraction de premier niveau dès la phase 1

**Statut :** Acceptée — phase 1

## Décision

`ComputePool` existe dans la configuration dès maintenant, avec `location`,
`trust_level`, `capabilities`, `gpus`, `cost_per_hour`. Un seul pool est
déclaré (`local-gpu`). Un exemple de pool cloud est présent en commentaire.

## Conséquence

Ajouter un GPU, une machine du réseau ou un worker cloud est une entrée de
configuration, pas une refonte. Le champ `trust_level` est présent avant d'être
utile : il conditionnera l'accès aux secrets et au split HELDOUT. L'ajouter
après coup aurait imposé de réviser toutes les requêtes d'autorisation.

## Coût assumé

Cette abstraction n'apporte rien pendant les premiers mois. C'est un
investissement délibéré contre une refonte transversale ultérieure.
