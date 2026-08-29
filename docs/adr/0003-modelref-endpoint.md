# ADR-0003 — ModelRef et ModelEndpoint sont deux types distincts

**Statut :** Acceptée — phase 1

## Décision

`ModelRef` = identité logique versionnée `(family, version, quantization,
capability_class)`, sans aucune information de localisation. `ModelEndpoint` =
point de service physique `(pool_id, runtime, url, served_model, max_context,
vram_gb)`. Le routeur choisit des `ModelRef` ; le placement résout des
`ModelEndpoint`.

## Conséquence

Le même modèle logique peut être servi par Ollama en local aujourd'hui et par
vLLM sur un pool cloud demain sans que le routeur, les génomes ou les empreintes
d'expérience changent — les scores restent comparables de part et d'autre.

## Écarté

Un objet unique contenant l'URL : le routeur se serait lié aux endpoints, et
l'ajout d'un pool aurait imposé une réécriture transversale.
